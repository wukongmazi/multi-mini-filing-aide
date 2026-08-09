# -*- coding: utf-8 -*-
# ============================================================
# multi-mini-filing-aide · Qoder Cloud Agents 客户端（CAS REST API）· 多 Agent 版
# 仅在设置了 QODER_PAT 时启用真实调用；否则 run_agent 返回 None，
# 由调用方回退到 kb.py 的规则引擎。
#
# 严格依据已装 cloud-agents 技能文档（api.qoder.com/api/v1/cloud）：
#  - Agent 字段用 `system`（非 `prompt`），不含 environment_id
#  - 发消息必须包 `events` 数组，type=`user.message`，无 role
#  - agent.message 的 content 是数组 [{text,type}]，取 content[0].text
#  - 停止信号用 session.status_idle
#  - 资源清理：DELETE /sessions、DELETE /agents
#
# 多 Agent 设计：
#  - AGENT_REGISTRY 注册每个业务模块对应的「专属 Agent」（name + system）
#  - 每个 Agent 在本地持久化 ONE Session（.qoder_state.json 按 agent name 键），
#    实现「一个 Agent 对应一个 Session」复用 —— 不自动删除，尊重用户控制
#  - 回放自愈：实测「紧接复用同一 Session 再发新问题」时 Qoder 可能只回放历史，
#    本函数检测该情况（复用返回与上次相同）后自动新建 Session 重试一次
#  - 未配置 PAT / 失败 / 超时 返回 None，由调用方回退 kb.py 规则引擎
# ============================================================
import os
import json
import time
import httpx

# 读取项目根目录的 .env（backend 的上一级），确保独立运行/被导入都能拿到 QODER_PAT
try:
    from dotenv import load_dotenv
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_root, ".env"))
except Exception:
    pass

# 配置改为动态读取（优先级：页面设置 > 环境变量 > 默认），由 settings 模块统一托管
from settings import get_setting
def _base(): return get_setting("QODER_API_BASE").rstrip("/")
def _pat():  return get_setting("QODER_PAT").strip()
def _model(): return get_setting("QODER_MODEL")

# ---------- 多 Agent 注册表：每个业务模块一个聚焦的专属 Agent ----------
AGENT_REGISTRY = {
    "diagnosis": {
        "name": "filing-diagnosis-agent",
        "system": (
            "你是「小程序备案诊断专家」，服务于运营，覆盖微信、支付宝、抖音、快手、百度五大平台。"
            "规则：1. 仅依据工信部与各平台官方备案规则作答，禁止编造任何条款；"
            "2. 用一句话指出最容易踩坑的备案注意点；"
            "3. 结论必须附官方来源（如工信部信管〔2023〕105号、各平台《ICP备案指引》）；"
            "4. 面向运营可执行，给出下一步动作。"
        ),
    },
    "precheck": {
        "name": "filing-precheck-agent",
        "system": (
            "你是「小程序备案材料预审专家」。给定一组备案材料字段"
            "（营业执照、法定代表人、法人身份证、负责人实名手机号、小程序名称、服务类目、已备案域名、人脸核验、授权书/承诺书），"
            "判断材料完整性与合规风险，指出缺失项；"
            "对电商/教育/医疗健康/金融/微短剧等高审核强度类目提示需补充的行业资质；"
            "给出可执行的补正建议。仅依据官方规则，禁止编造，附来源。"
        ),
    },
    "progress": {
        "name": "filing-progress-agent",
        "system": (
            "你是「小程序备案进度巡检专家」。给定若干小程序的备案阶段与超时风险清单，"
            "生成一段面向运营的巡检播报：哪些临近 24h 短信核验 deadline、哪些管局终审已超期、下一步动作建议。"
            "时效规则：平台初审1-2工作日、工信部短信核验24h内必须完成（超时作废）、管局终审1-20工作日。"
            "仅依据官方规则，禁止编造。"
        ),
    },
    "qa": {
        "name": "filing-qa-agent",
        "system": (
            "你是「小程序备案政策问答专家」，覆盖微信、支付宝、抖音、快手、百度五大平台的小程序 ICP 备案。"
            "规则：1. 仅依据工信部与五平台官方备案规则作答，禁止编造；"
            "2. 结论末尾必须附来源（工信部信管〔2023〕105号、各平台《ICP备案指引》）；"
            "3. 信息不确定时明确说明并建议人工核实，不得臆测；"
            "4. 回答简洁、面向运营可执行。"
        ),
    },
}

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".qoder_state.json")
_env_id = None


def _log(m):
    # Windows 终端默认 GBK，含 ⚠ 等非 ASCII 字符会 UnicodeEncodeError；
    # 降级 replace，避免日志打印异常导致 run_agent 被 except 捕获而返回 None
    try:
        print(f"[qoder] {m}", flush=True)
    except UnicodeEncodeError:
        print(f"[qoder] {m}".encode("gbk", "replace").decode("gbk"), flush=True)
    except Exception:
        pass


def _headers():
    return {"Authorization": f"Bearer {_pat()}", "Content-Type": "application/json"}


def _load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def _get_agent_state(name):
    return _load_state().get("agents", {}).get(name, {})


def _put_agent_state(name, **kv):
    st = _load_state()
    ag = st.setdefault("agents", {})
    a = ag.setdefault(name, {})
    a.update(kv)
    _save_state(st)


def _ensure_env():
    global _env_id
    if _env_id:
        return _env_id
    st = _load_state()
    if st.get("env_id"):
        _env_id = st["env_id"]
        return _env_id
    try:
        with httpx.Client(timeout=30, verify=False) as c:
            r = c.get(f"{_base()}/environments", headers=_headers())
            if r.status_code == 200:
                arr = r.json().get("data") or []
                if isinstance(arr, list) and arr:
                    _env_id = arr[0].get("id")
                    st = _load_state(); st["env_id"] = _env_id; _save_state(st)
                    _log(f"复用已有 env: {_env_id}")
                    return _env_id
            r = c.post(f"{_base()}/environments", headers=_headers(), json={
                "name": "filing-aide-env",
                "config": {"type": "cloud", "networking": {"type": "unrestricted"}},
            })
            if r.status_code in (200, 201):
                body = r.json()
                _env_id = body.get("id") or (body.get("data") or {}).get("id")
                if _env_id:
                    st = _load_state(); st["env_id"] = _env_id; _save_state(st)
                    _log(f"新建 env: {_env_id}")
                    return _env_id
            _log(f"env 创建失败 HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"_ensure_env 异常: {e}")
    return None


def _ensure_agent(name, system):
    """按 name 复用已有 Agent，缺失才新建（依据官方：POST /agents {name, system, model}）。"""
    a = _get_agent_state(name)
    if a.get("id"):
        return a["id"]
    try:
        with httpx.Client(timeout=30, verify=False) as c:
            r = c.get(f"{_base()}/agents", headers=_headers())
            if r.status_code == 200:
                arr = r.json().get("data") or []
                for x in arr:
                    if x.get("name") == name:
                        aid = x.get("id")
                        _put_agent_state(name, id=aid)
                        _log(f"复用已有 agent: {aid}")
                        return aid
            r = c.post(f"{_base()}/agents", headers=_headers(), json={
                "name": name, "system": system, "model": _model(),
            })
            if r.status_code in (200, 201):
                body = r.json()
                aid = body.get("id") or (body.get("data") or {}).get("id")
                if aid:
                    _put_agent_state(name, id=aid)
                    _log(f"新建 agent: {aid}")
                    return aid
            _log(f"agent 创建/查找失败 HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"_ensure_agent 异常: {e}")
    return None


def _ensure_session(name, agent_id, env_id):
    """一个 Agent 对应一个 Session：优先复用已持久化的 session_id，缺失才新建。"""
    a = _get_agent_state(name)
    sid = a.get("session_id")
    if sid:
        return sid, True  # reused=True
    try:
        with httpx.Client(timeout=30, verify=False) as c:
            r = c.post(f"{_base()}/sessions", headers=_headers(),
                       json={"agent": agent_id, "environment_id": env_id})
            if r.status_code in (200, 201):
                body = r.json()
                sid = body.get("id") or (body.get("data") or {}).get("id")
                if sid:
                    _put_agent_state(name, session_id=sid)
                    _log(f"新建 session {sid} for {name}")
                    return sid, False
            _log(f"session 创建失败 HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"_ensure_session 异常: {e}")
    return None, False


def _extract_text(ev):
    """agent.message 的 content 是数组 [{text,type}]；兼容字符串兜底。"""
    content = ev.get("content")
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "".join(parts)
    if isinstance(content, str):
        return content
    return ""


def _send_and_read(c, sess_id, user_prompt, timeout):
    """向 session 发送一条 user.message 并流式读取，返回 (last_text, ok)。"""
    r = c.post(f"{_base()}/sessions/{sess_id}/events", headers=_headers(),
               json={"events": [{"type": "user.message",
                                 "content": [{"text": user_prompt, "type": "text"}]}]})
    if r.status_code not in (200, 201, 202):
        _log(f"events 发送失败 HTTP {r.status_code} {r.text[:200]}")
        return None, False
    last_text = None
    deadline = time.time() + timeout
    with c.stream("GET", f"{_base()}/sessions/{sess_id}/events/stream",
                  headers={**_headers(), "Accept": "text/event-stream"}) as resp:
        for line in resp.iter_lines():
            if time.time() > deadline:
                _log(f"SSE 超时({timeout}s), 有文本: {bool(last_text)}")
                break
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "{}":
                continue
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            etype = ev.get("type")
            if etype == "agent.message":
                t = _extract_text(ev)
                if t:
                    last_text = t
                    _log(f"文本片段: {t[:80]}")
            elif etype == "session.status_idle":
                _log(f"idle, 有文本: {bool(last_text)}")
                break
            elif etype == "session.error":
                _log(f"session.error: {ev.get('error')}")
                break
    return last_text, True


def run_agent(role="qa", user_prompt="", timeout=120) -> str | None:
    """按 role 调用对应专属 Agent；该 Agent 持久化一个 Session 并复用（不自动删除）。
    返回 agent 文本；未配置 PAT / 失败 / 超时 一律返回 None（调用方回退规则引擎）。

    回放自愈：实测「紧接复用同一 Session 再发新问题」时 Qoder 可能只回放历史
    （返回与上次相同的答案）。本函数检测该情况——若复用返回与上次该 session 文本
    完全相同，则自动丢弃旧 session、新建一个再问一次，拿到针对新问题的回答；
    正常间隔复用（不回放）不受影响，不触发重建，依旧「一个 Agent 对应一个 Session」。
    """
    if not _pat():
        _log("run_agent 跳过: 未配置 QODER_PAT")
        return None
    reg = AGENT_REGISTRY.get(role)
    if not reg:
        _log(f"未知 role: {role}")
        return None
    name, system = reg["name"], reg["system"]
    t0 = time.time()
    try:
        env = _ensure_env()
        agent_id = _ensure_agent(name, system)
        if not env or not agent_id:
            _log("run_agent 失败: 无法获取 env/agent")
            return None
        with httpx.Client(timeout=max(timeout + 15, 90), verify=False) as c:
            sess_id, reused = _ensure_session(name, agent_id, env)
            if not sess_id:
                return None
            _log(f"role={role} agent={name} session={sess_id} reused={reused}")

            last_text, ok = _send_and_read(c, sess_id, user_prompt, timeout)
            if not ok:
                # session 失效：清旧 id，新建重试
                _log("session 失效，重建重试")
                _put_agent_state(name, session_id=None)
                sess_id2, _ = _ensure_session(name, agent_id, env)
                if not sess_id2:
                    return None
                sess_id = sess_id2
                last_text, ok = _send_and_read(c, sess_id, user_prompt, timeout)

            # 回放检测：复用且返回与上次相同 → 重建新 session 再问一次
            prev = _get_agent_state(name).get("last_text")
            if reused and prev and last_text and last_text.strip() == prev.strip():
                _log("回放检测命中：复用返回与上次相同，重建 session 重试")
                _put_agent_state(name, session_id=None)
                sess_id3, _ = _ensure_session(name, agent_id, env)
                if sess_id3:
                    sess_id = sess_id3
                    last_text, ok = _send_and_read(c, sess_id, user_prompt, timeout)

            # 持久化本次文本（供下次回放检测）
            _put_agent_state(name, last_text=(last_text or "")[:4000])
            _log(f"完成, 耗时 {time.time()-t0:.1f}s, 有文本: {bool(last_text)}")
            return last_text or None
    except httpx.TimeoutException as e:
        _log(f"超时({time.time()-t0:.1f}s): {e}")
        return None
    except Exception as e:
        _log(f"异常({time.time()-t0:.1f}s): {type(e).__name__}: {e}")
        return None
    # 注意：不删除 session，会话保留在后台，由用户手动清理。


# ============================================================
# Qoder Deployment 管理（B：用云端 Deployment 替代本地 APScheduler 做每日巡检）
# Deployment = 把某个 Agent 绑到 cron，Qoder 云端按时自动跑；
# 我们只在「巡检」场景用 progress-agent 的 Deployment 做云端定时触发，
# 真正的数据计算与通知仍在本地 do_inspect() 完成（Agent 运行在云端、无本地数据访问权）。
# ============================================================
def _get_dep_state(role):
    return _load_state().get("deployments", {}).get(role, {})


def _put_dep_state(role, **kv):
    st = _load_state()
    deps = st.setdefault("deployments", {})
    d = deps.setdefault(role, {})
    d.update(kv)
    _save_state(st)


def _dep_initial_events():
    return [{
        "type": "user.message",
        "content": [{
            "type": "text",
            "text": ("你是小程序备案进度巡检专家。请基于当前备案进度数据，生成今日进度巡检播报："
                     "重点指出临近 24h 短信核验 deadline 的小程序、管局终审已超期项，并给出下一步动作建议。"
                     "若无可参考数据，请说明无法获取本地登记表，无需编造。")
        }]
    }]


def ensure_deployment(role="progress", hour=9, enabled=True):
    """确保存在一个 Qoder Deployment：绑 progress-agent + cron(hour) + env。
    幂等：state 已有 id 则校验/更新调度与启停状态，否则 POST 创建。
    返回 deployment id（str）或 None。"""
    if not _pat():
        _log("ensure_deployment 跳过: 未配置 QODER_PAT")
        return None
    try:
        env = _ensure_env()
        reg = AGENT_REGISTRY.get(role)
        if not reg:
            _log(f"ensure_deployment 未知 role: {role}")
            return None
        agent_id = _ensure_agent(reg["name"], reg["system"])
        if not env or not agent_id:
            _log("ensure_deployment 失败: 无法获取 env/agent")
            return None
        schedule = {"type": "cron", "expression": f"0 {hour} * * *", "timezone": "Asia/Shanghai"}
        st = _get_dep_state(role)
        dep_id = st.get("id")
        # 已存在：校验状态/调度，必要时更新
        if dep_id:
            try:
                with httpx.Client(timeout=30, verify=False) as c:
                    r = c.get(f"{_base()}/deployments/{dep_id}", headers=_headers())
                    if r.status_code == 200:
                        body = r.json()
                        cur_expr = (body.get("schedule") or {}).get("expression")
                        if cur_expr != schedule["expression"]:
                            c.post(f"{_base()}/deployments/{dep_id}", headers=_headers(),
                                   json={"schedule": schedule})
                            _log(f"Deployment {dep_id} 调度已更新为 {schedule['expression']}")
                        # 根据期望状态执行启停，并记录操作后的实际状态
                        effective_status = body.get("status")
                        if enabled and effective_status != "active":
                            _resume_deployment(role, dep_id)
                            effective_status = "active"
                        elif (not enabled) and effective_status == "active":
                            _pause_deployment(role, dep_id)
                            effective_status = "paused"
                        _put_dep_state(role, id=dep_id, status=effective_status,
                                       hour=hour,
                                       next_runs=(body.get("schedule") or {}).get("upcoming_runs_at"))
                        return dep_id
                    else:
                        _log(f"Deployment {dep_id} 查询失败 HTTP {r.status_code}，尝试重建")
            except Exception as e:
                _log(f"Deployment 校验异常: {e}")
        # 不存在：创建（POST /deployments）
        with httpx.Client(timeout=30, verify=False) as c:
            r = c.post(f"{_base()}/deployments", headers=_headers(), json={
                "name": "filing-progress-deployment",
                "agent": agent_id,
                "environment_id": env,
                "schedule": schedule,
                "initial_events": _dep_initial_events(),
            })
            if r.status_code in (200, 201):
                body = r.json()
                dep_id = body.get("id")
                if dep_id:
                    _put_dep_state(role, id=dep_id, status=body.get("status"),
                                   hour=hour,
                                   next_runs=(body.get("schedule") or {}).get("upcoming_runs_at"))
                    _log(f"新建 Deployment: {dep_id} ({schedule['expression']})")
                    if not enabled:
                        _pause_deployment(role, dep_id)
                    return dep_id
            _log(f"Deployment 创建失败 HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"ensure_deployment 异常: {e}")
    return None


def _pause_deployment(role, dep_id=None):
    dep_id = dep_id or _get_dep_state(role).get("id")
    if not dep_id:
        return False
    try:
        with httpx.Client(timeout=30, verify=False) as c:
            r = c.post(f"{_base()}/deployments/{dep_id}/pause", headers=_headers())
            if r.status_code in (200, 201, 204):
                _put_dep_state(role, status="paused")
                _log(f"Deployment {dep_id} 已暂停")
                return True
    except Exception as e:
        _log(f"pause 异常: {e}")
    return False


def _resume_deployment(role, dep_id=None):
    dep_id = dep_id or _get_dep_state(role).get("id")
    if not dep_id:
        return False
    try:
        with httpx.Client(timeout=30, verify=False) as c:
            r = c.post(f"{_base()}/deployments/{dep_id}/unpause", headers=_headers())
            if r.status_code in (200, 201, 204):
                _put_dep_state(role, status="active")
                _log(f"Deployment {dep_id} 已恢复")
                return True
            # 回退：用 merge-patch 置 status=active
            r2 = c.post(f"{_base()}/deployments/{dep_id}", headers=_headers(), json={"status": "active"})
            if r2.status_code == 200:
                _put_dep_state(role, status="active")
                return True
    except Exception as e:
        _log(f"resume 异常: {e}")
    return False


def get_deployment_status(role="progress"):
    """返回 Deployment 状态：优先实时查询 Qoder API，失败则回退本地缓存。"""
    st = _get_dep_state(role)
    dep_id = st.get("id")
    if dep_id and _pat():
        try:
            with httpx.Client(timeout=15, verify=False) as c:
                r = c.get(f"{_base()}/deployments/{dep_id}", headers=_headers())
                if r.status_code == 200:
                    body = r.json()
                    live_status = body.get("status")
                    live_next = (body.get("schedule") or {}).get("upcoming_runs_at")
                    # 同步缓存（避免前后状态不一致）
                    if live_status and live_status != st.get("status"):
                        _put_dep_state(role, status=live_status)
                    return {
                        "id": dep_id,
                        "status": live_status or st.get("status"),
                        "hour": st.get("hour"),
                        "next_runs": live_next or st.get("next_runs"),
                        "role": role,
                    }
        except Exception as e:
            _log(f"get_deployment_status 实时查询失败，回退缓存: {e}")
    return {
        "id": st.get("id"),
        "status": st.get("status"),
        "hour": st.get("hour"),
        "next_runs": st.get("next_runs"),
        "role": role,
    }


def read_session_output(sess_id, timeout=15):
    """读取已完成 Session 的 Agent 输出文本（Webhook 回传后取巡检播报）。
    返回最后一段 agent.message 文本，失败返回 None。"""
    if not sess_id:
        return None
    try:
        with httpx.Client(timeout=timeout, verify=False) as c:
            r = c.get(f"{_base()}/sessions/{sess_id}/events", headers=_headers())
            if r.status_code == 200:
                body = r.json()
                evs = body.get("data") or body.get("events") or []
                last = None
                for ev in evs:
                    if isinstance(ev, dict) and ev.get("type") == "agent.message":
                        t = _extract_text(ev)
                        if t:
                            last = t
                return last
    except Exception as e:
        _log(f"read_session_output 异常: {e}")
    return None


# ============================================================
# Qoder Webhook 端点管理（POST /webhook_endpoints CRUD）
# 支持在系统内创建/查看/删除 Webhook 端点，订阅 Qoder 事件。
# ============================================================

# 可订阅的事件类型清单
WEBHOOK_EVENT_TYPES = [
    "session.status_idled",      # Session 完成（核心：触发巡检）
    "agent.created",             # Agent 创建
    "session.created",           # Session 创建
    "session.thread_idled",      # Thread 完成
]


def list_webhook_endpoints():
    """列出当前环境已创建的所有 Webhook 端点。"""
    if not _pat():
        return []
    try:
        with httpx.Client(timeout=20, verify=False) as c:
            r = c.get(f"{_base()}/webhook_endpoints", headers=_headers())
            if r.status_code == 200:
                body = r.json()
                arr = body.get("data") or []
                if isinstance(arr, list):
                    return arr
            _log(f"list_webhook_endpoints HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"list_webhook_endpoints 异常: {e}")
    return []


def create_webhook_endpoint(url, events=None, description=None):
    """创建一个 Webhook 端点并订阅指定事件。

    注意：Qoder 的 signing_secret 不由调用方传入，而是在创建成功后由
    Qoder 在响应体中**返回一次**（该密钥用于后续投递验签）。本函数
    把完整响应返回给调用方，由调用方负责保存 signing_secret。

    Args:
        url: 回调地址（如 http://127.0.0.1:7860/api/qoder-webhook）
        events: 订阅事件列表，默认 [session.status_idled]
        description: 端点描述（可选，便于管理识别）
    Returns: 端点信息 dict（含一次性 signing_secret）或 None
    """
    if not _pat():
        _log("create_webhook_endpoint 跳过: 未配置 PAT")
        return None
    if not url:
        _log("create_webhook_endpoint 跳过: url 为空")
        return None
    events = events or ["session.status_idled"]
    try:
        payload = {
            "url": url,
            "events": events,
        }
        if description:
            payload["description"] = description
        with httpx.Client(timeout=20, verify=False) as c:
            r = c.post(f"{_base()}/webhook_endpoints", headers=_headers(), json=payload)
            if r.status_code in (200, 201):
                body = r.json()
                ep = (body.get("data") or body)
                if isinstance(ep, dict) and ep.get("id"):
                    _log(f"创建 Webhook 端点成功: {ep['id']} → {url}")
                    return ep
            _log(f"create_webhook_endpoint 失败 HTTP {r.status_code} {r.text[:300]}")
    except Exception as e:
        _log(f"create_webhook_endpoint 异常: {e}")
    return None


def delete_webhook_endpoint(endpoint_id):
    """删除指定 Webhook 端点。返回 bool。"""
    if not _pat() or not endpoint_id:
        return False
    try:
        with httpx.Client(timeout=15, verify=False) as c:
            r = c.delete(f"{_base()}/webhook_endpoints/{endpoint_id}", headers=_headers())
            if r.status_code in (200, 201, 204):
                _log(f"删除 Webhook 端点成功: {endpoint_id}")
                return True
            _log(f"delete_webhook_endpoint 失败 HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _log(f"delete_webhook_endpoint 异常: {e}")
    return False


if __name__ == "__main__":
    # 简单自检：直接跑一句，验证链路
    print("测试 run_agent(role=qa) ...")
    out = run_agent("qa", "一句话说明：微信小程序备案是否强制？")
    print("结果:", (out or "None(已回退)")[:200])
