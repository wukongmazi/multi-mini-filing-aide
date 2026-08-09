# ============================================================
# multi-mini-filing-aide · 后端主程序（FastAPI）
# 架构（按用户决策：魔搭侧调度器 + 演示模式）：
#   1. 定时触发：云端 Qoder Deployment（cron，Asia/Shanghai）每日整点巡检为主；
#      本地兜底线程（整点末尾）为辅，二者共用「当天去重」杜绝同日双推；
#      另有「立即巡检」手动入口，始终可用
#   2. 多 Agent：调用 Qoder Cloud Agents（CAS API）跑诊断/进度/风险；
#      无 PAT 时回退 kb.py 规则引擎，系统照常可运行
#   3. Webhook 回调：暴露 /api/webhook 入站端点（供 QoderWake / 外部 cron /
#      前端测试按钮触发巡检），并支持 HMAC 验签
#   4. 三通道通知：企微/钉钉/飞书 群机器人，真实 POST（演示模式仅记录）
#   5. 持久化：SQLite（records / config / inspections）
#   6. 前端：同容器静态托管（魔搭 Docker 端口 7860）
# ============================================================
import os
import json
import time
import sqlite3
import threading
import hmac
import hashlib
from datetime import datetime, date, timedelta
from contextlib import asynccontextmanager

try:
    from dotenv import load_dotenv
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _ROOT = os.path.dirname(_HERE)               # 项目根（与 .env.example 同级）
    load_dotenv(os.path.join(_ROOT, ".env"))    # 必须在 import qoder_client 之前执行
except Exception:
    pass

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import hashlib

import kb
from kb import CHANNELS
from notifier import send_all
from qoder_client import (
    run_agent, AGENT_REGISTRY,
    ensure_deployment, get_deployment_status, read_session_output,
    list_webhook_endpoints, create_webhook_endpoint, delete_webhook_endpoint,
    WEBHOOK_EVENT_TYPES, _log,
)
import settings

# ---------- 路径 ----------
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)          # 前端根目录
# 数据库路径统一由 settings 解析：自动落到魔搭持久化卷 /mnt/workspace，避免重启丢配置
DB_PATH = settings.DB_PATH

# ---------- 数据库 ----------
_lock = threading.Lock()

# ---------- 租户隔离（按 QODER_PAT 划分数据归属）----------
# 设计：当前生效的 QODER_PAT（参数设置 > 环境变量 > 空）派生稳定租户标识
# tenant_id = sha256(PAT)。所有「用户提交的数据」（records / inspections /
# agent_events）写入时打标、读取时按当前 tenant_id 过滤；QODER_PAT 本身只做
# 派生，绝不入库作明文、绝不进任何响应。他人录入自己的 QODER_PAT 即切换为另一
# 租户，只能看到自己的数据；空 PAT 时退化为固定常量（单租户 / 演示模式）。
def current_qoder_pat():
    """当前生效的 QODER_PAT（参数设置 > 环境变量 > 空）。"""
    return (settings.get_setting("QODER_PAT") or "").strip()

def current_tenant_id():
    """稳定租户标识：sha256(当前 QODER_PAT)。"""
    return hashlib.sha256(current_qoder_pat().encode("utf-8")).hexdigest()

# ---------- 巡检串行 / 去重状态 ----------
_INSPECT_LOCK = threading.Lock()          # 串行化 do_inspect，避免并发调用 Qoder Agent / 重复推送
_last_auto_date = {"d": None}            # 当天是否已「云端自动巡检」过（Qoder Deployment 触发），
                                         # 由 _try_auto_inspect 负责去重，避免 Webhook 重试/重复推送
_last_session_ts = {}                    # session_id -> 时间戳，5 分钟内同会话去重（防 Qoder 双事件/重试）
_last_inspect_ts = {"t": 0.0}            # 最近一次「实际执行」巡检的时间戳（全局冷却用）
_INSPECT_COOLDOWN = 180                  # 任意来源两次实际巡检的最小间隔（秒），防手动/自动重叠双推
_local_sched_thread = None               # 本地兜底定时巡检后台线程（辅助，非权威）
_local_sched_stop = False                # 本地兜底线程停止标志


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT, name TEXT, submit TEXT, verify TEXT, gov TEXT,
            created_at TEXT, tenant_id TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at TEXT, summary TEXT, brief TEXT,
            risks_count INTEGER, notify TEXT, tenant_id TEXT)""")
        # Agent 活动审计（Qoder Webhook 事件留痕）
        c.execute("""CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, event_type TEXT, resource_type TEXT,
            resource_id TEXT, agent_role TEXT, detail TEXT, tenant_id TEXT)""")
        # 兼容旧库：补 brief 列（存储 progress-agent 智能播报）
        try:
            c.execute("ALTER TABLE inspections ADD COLUMN brief TEXT")
        except Exception:
            pass
        # 兼容旧库：三张用户数据表补 tenant_id 列（数据按 QODER_PAT 隔离）
        for tbl in ("records", "inspections", "agent_events"):
            try:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN tenant_id TEXT")
            except Exception:
                pass
        # 种子演示数据（仅当配置了真实 QODER_PAT 时才注入，打标当前租户）。
        # 关键：QODER_PAT 为空（用户主动清空 / 未配置）时绝不注入演示数据，
        # 否则空租户会被塞满 demo，导致「清空 PAT 后面板看似有数据」、导出不提示空数据。
        cur = c.execute("SELECT COUNT(*) AS n FROM records")
        if cur.fetchone()["n"] == 0 and current_qoder_pat():
            seed = [
                ("wechat", "门店会员小程序", -12, -11, -8),
                ("kuaishou", "精选电商快手版", -3, None, None),
                ("douyin", "微短剧内容号", -25, -24, None),
                ("alipay", "缴费工具", -6, -5, -2),
                ("baidu", "服务小程序", None, None, None),
            ]
            tid = current_tenant_id()
            for plat, name, s, v, g in seed:
                c.execute(
                    "INSERT INTO records (platform,name,submit,verify,gov,created_at,tenant_id) VALUES (?,?,?,?,?,?,?)",
                    (plat, name,
                     iso_offset(s), iso_offset(v), iso_offset(g),
                     datetime.now().isoformat(), tid))
        # 回填：现有数据归属「当前 PAT 对应租户」（仅 NULL 行，幂等；
        # 进程重启后由当前 QODER_PAT 派生，保证原主人历史数据不丢）
        backfill_tenant_once(c)


def iso_offset(n):
    if n is None:
        return ""
    d = date.today()
    from datetime import timedelta
    return (d + timedelta(days=n)).isoformat()


def backfill_tenant_once(c=None):
    """将三张用户数据表中 tenant_id 为 NULL 的历史行回填为「当前 PAT 对应租户」。
    幂等：仅改 NULL 行；用于旧库升级 / 进程重启后恢复原主人数据归属。"""
    own = c is None
    if own:
        c = get_conn()
    try:
        tid = current_tenant_id()
        for tbl in ("records", "inspections", "agent_events"):
            try:
                c.execute(f"UPDATE {tbl} SET tenant_id=? WHERE tenant_id IS NULL", (tid,))
            except Exception:
                pass
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def get_config(key, default=None):
    with get_conn() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_config(key, value):
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)", (key, value))


def get_records():
    tid = current_tenant_id()
    with get_conn() as c:
        # 进度看板列表按「登记时间(created_at)」降序：未填登记时间的沉底，同日按插入倒序；
        # 仅本租户数据（tenant_id = 当前 QODER_PAT 派生）
        rows = c.execute(
            "SELECT * FROM records WHERE tenant_id=? "
            "ORDER BY COALESCE(NULLIF(created_at,''), '0000-00-00 00:00:00') DESC, id DESC",
            (tid,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_channels_status():
    out = []
    for ch, cfg in CHANNELS.items():
        url = settings.get_setting(f"{ch.upper()}_WEBHOOK_URL").strip()
        enabled = get_config(f"channel_{ch}_enabled")
        if enabled is None:
            enabled = "true" if url else "false"
            set_config(f"channel_{ch}_enabled", enabled)
        out.append({
            "key": ch, "name": cfg["name"], "icon": cfg["icon"],
            "configured": bool(url), "enabled": enabled == "true",
        })
    return out


def get_channels_enabled():
    return {s["key"]: s["enabled"] for s in get_channels_status()}


# ---------- 巡检核心 ----------
def build_inspect_message(risks):
    lines = ["【小程序备案 AI 中枢 · 定时巡检】"]
    real = [r for r in risks if r["lvl"] != "info"]
    if real:
        for r in real:
            lines.append(f"• {r['t']}（{r['d']}）")
    else:
        lines.append("本轮巡检：各小程序备案进度正常，无超时风险。")
    lines.append("— 数据由登记表日期实时计算，来源：各平台开放平台官方备案规则")
    return "\n".join(lines)


def do_inspect(agent_brief=None):
    """执行一次巡检：计算风险 → 生成播报 → 推送启用通道 → 落库。返回结果 dict。
    agent_brief：若由 Qoder Webhook 回传的 Session 输出提供播报文本，则直接使用，
    避免重复调用 Agent；为 None 时若有 PAT 则本地再调 progress Agent 生成智能播报。
    推送与落库内容：优先使用 progress-agent 智能播报（brief），无则回退基础版清单。
    内部以 _INSPECT_LOCK 串行，避免并发巡检同时调用 Qoder Agent / 重复推送。
    全局冷却：任意来源（云端自动/手动）刚跑过 _INSPECT_COOLDOWN 秒内则跳过，防手动点击与
    云端定时巡检/Webhook 重叠造成双推。"""
    nowt = time.time()
    with _INSPECT_LOCK:
        # 全局短冷却：刚跑过则跳过（不推送、不落库），返回 skipped
        if nowt - _last_inspect_ts["t"] < _INSPECT_COOLDOWN:
            ago = int(nowt - _last_inspect_ts["t"])
            print(f"[inspect] 冷却期内跳过（{ago}s 前刚巡检过），避免重复推送")
            return {"ok": True, "skipped": f"cooldown:{ago}s",
                    "message": f"刚刚已于 {ago} 秒前巡检过，请稍后再手动触发", "risks_count": None}
        records = get_records()
        risks = kb.compute_risks(records)
        enabled = get_channels_enabled()
        message = build_inspect_message(risks)
        # 优先使用 Qoder 回传的 agent_brief；否则有 PAT 时本地调 progress Agent 生成智能播报
        qoder_brief = agent_brief
        if not qoder_brief and settings.get_setting("QODER_PAT"):
            try:
                risk_text = "\n".join(
                    f"- {r['t']}（{r['d']}）" for r in risks if r["lvl"] != "info") or "无超时风险"
                prompt = ("以下为本次小程序备案巡检的风险清单，请生成一段面向运营的巡检播报"
                          "（哪些临近 deadline、哪些已超期、下一步动作建议）：\n" + risk_text)
                ans = run_agent("progress", prompt, timeout=60)
                if ans:
                    qoder_brief = ans
            except Exception as e:
                print(f"[inspect] Qoder 增强失败: {e}")
        # 推送内容：优先智能播报，无则回退基础版
        push_message = qoder_brief or message
        notify = send_all(push_message, enabled)
        real_count = len([r for r in risks if r["lvl"] != "info"])
        _last_inspect_ts["t"] = nowt          # 标记实际执行时间（冷却用）
        # 手动巡检不再标记 _last_auto_date：避免「手动一次」就吃掉当天的云端自动巡检；
        # 云端自动巡检的去重由 _try_auto_inspect 单独负责，手动仅受 180s 全局冷却约束。
        with get_conn() as c:
            c.execute(
                "INSERT INTO inspections (ran_at,summary,brief,risks_count,notify,tenant_id) VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(), message, qoder_brief, real_count, JSONstr(notify),
                 current_tenant_id()))
        result = {"summary": message, "brief": qoder_brief, "risks": risks,
                  "notify": notify, "risks_count": real_count}
        if qoder_brief:
            result["qoderBrief"] = qoder_brief
        return result


def JSONstr(o):
    import json
    return json.dumps(o, ensure_ascii=False)


# ---------- Agent 活动审计 ----------
def log_agent_event(event_type, resource_type=None, resource_id=None, agent_role=None, detail=None):
    with get_conn() as c:
        c.execute(
            "INSERT INTO agent_events (ts,event_type,resource_type,resource_id,agent_role,detail,tenant_id) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), event_type, resource_type, resource_id, agent_role,
             JSONstr(detail) if detail is not None else None, current_tenant_id()))


def get_agent_events(limit=50):
    tid = current_tenant_id()
    with get_conn() as c:
        rows = c.execute("SELECT * FROM agent_events WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                         (tid, min(limit, 200))).fetchall()
    return [dict(r) for r in rows]


# ---------- 生命周期：巡检调度（云端 Deployment 为主，本地兜底线程为辅）----------
def _ensure_local_scheduler():
    """启动本地兜底定时巡检线程（若未运行）。仅作为云端 Deployment 的辅助/兜底：
    只在「云端当天尚未巡检」且「目标整点小时临近末尾」时触发，确保云端优先。"""
    global _local_sched_thread, _local_sched_stop
    if _local_sched_thread is None or not _local_sched_thread.is_alive():
        _local_sched_stop = False
        _local_sched_thread = threading.Thread(target=_local_scheduler_loop,
                                               name="local-scheduler", daemon=True)
        _local_sched_thread.start()
        print("[scheduler] 本地兜底定时巡检线程已启动（辅助/兜底，非权威）")


def _local_scheduler_loop():
    """本地辅助/兜底定时巡检线程：每 30s 轮询。
    仅在「目标整点小时的最后 15 分钟（>=45 分）」且「当天云端尚未巡检」时触发——
    给云端 Webhook（整点触发、随即回传）留出优先窗口；本地只在云端失效
    （无 PAT / Webhook 未投递 / 网络异常）时于整点末尾兜底执行一次。
    进程存活即生效，不依赖 natapp 隧道 / Webhook 回调。"""
    while not _local_sched_stop:
        try:
            if settings.is_scheduler_enabled():
                h = int(settings.get_setting("SCHEDULER_HOUR") or "9")
                now = time.localtime()
                today = time.strftime("%Y-%m-%d", now)
                # 仅「目标整点小时 + 临近末尾(>=45分)」触发：给云端 Webhook 优先窗口；
                # 云端若已跑，_last_auto_date 已占位，这里会被同日去重拦下
                if now.tm_hour == h and now.tm_min >= 45 and _last_auto_date["d"] != today:
                    print(f"[local-scheduler] 整点末尾兜底：云端今日未巡检，执行每日巡检")
                    try:
                        _try_auto_inspect("local-scheduler(aux)")
                    except Exception as e:
                        print(f"[local-scheduler] 巡检异常: {e}")
            time.sleep(30)
        except Exception as e:
            print(f"[local-scheduler] 循环异常: {e}")
            time.sleep(30)


def start_scheduler():
    """应用启动：优先初始化云端 Deployment（权威），并常驻本地兜底线程。
    定时巡检双路（共用 _try_auto_inspect 的「当天去重」，天然不会同日双推）：
      · 云端 Deployment（主）：cron 在 SCHEDULER_HOUR 整点触发 progress-agent，
        session 空闲后 Webhook 回传，经 _try_auto_inspect 去重执行；
      · 本地兜底线程（辅）：仅当云端当天未巡检、且临近整点末尾时兜底触发一次。
    另外从 DB 恢复「当日是否已巡检」，避免进程重启后本地兜底重复云端已完成的巡检。"""
    try:
        # 恢复当日去重状态（跨重启），防云端已跑后本地兜底同日重复
        saved = get_config("LAST_AUTO_INSPECT_DATE")
        if saved:
            _last_auto_date["d"] = saved
        h = int(settings.get_setting("SCHEDULER_HOUR") or "9")
        if settings.is_scheduler_enabled():
            dep_id = ensure_deployment(role="progress", hour=h, enabled=True)
            if dep_id:
                print(f"[scheduler] 云端 Deployment {dep_id} 已启用，每日 {h}:00 (Asia/Shanghai) 巡检（主）")
            else:
                print("[scheduler] 无法创建 Qoder Deployment（检查 PAT/网络）；"
                      "定时巡检将仅由本地兜底线程执行")
        else:
            # 未启用：确保云端 Deployment 处于 paused（若已存在），本地线程仅空转
            try:
                dep = get_deployment_status("progress")
                if dep.get("id"):
                    ensure_deployment(role="progress", hour=h, enabled=False)
            except Exception as e:
                print(f"[scheduler] 暂停 Deployment 失败: {e}")
            print("[scheduler] 定时巡检未启用（页面设置已关闭），可用「立即巡检」手动触发")
        # 本地兜底线程常驻（内部按 enabled/小时/末尾/去重 判断），保证云端失效时仍能巡检
        _ensure_local_scheduler()
    except Exception as e:
        print("[scheduler] 初始化异常：", e)


def set_scheduler_enabled(enabled):
    """运行时启停每日巡检（无需重启）：云端 Deployment 直接 active/paused，
    本地兜底线程常驻，仅翻转开关。"""
    h = int(settings.get_setting("SCHEDULER_HOUR") or "9")
    try:
        ensure_deployment(role="progress", hour=h, enabled=enabled)
        _ensure_local_scheduler()
    except Exception as e:
        print(f"[scheduler] 启停失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    # ---- 启动诊断：打印密码来源与 token 前缀（仅前 8 字符，不泄露完整 hash）----
    mp = _master_password()
    ap = _access_password()
    at = _admin_token()
    nt = _normal_token()
    print(f"[AUTH-BOOT] MASTER_PASSWORD source={'env' if mp else 'NONE'} len={len(mp)} "
          f"admin_token_prefix={at[:8] if at else 'NONE'}")
    print(f"[AUTH-BOOT] ACCESS_PASSWORD source="
          f"{'db' if (ap and settings._db_get('ACCESS_PASSWORD') is not None) else 'env' if ap else 'NONE'} "
          f"len={len(ap)} normal_token_prefix={nt[:8] if nt else 'NONE'}")
    # ---- 持久化诊断：确认数据库落在持久化卷，避免『改了设置重启丢失』----
    print(f"[AUTH-BOOT] DB_PATH={settings.DB_PATH} "
          f"persistent={'yes(/mnt/workspace)' if settings.DB_PATH.startswith('/mnt/workspace') else 'no-local-or-custom'}")
    yield


app = FastAPI(title="multi-mini-filing-aide", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- 访问密码鉴权（双身份：主人 MASTER_PASSWORD / 访客 ACCESS_PASSWORD）----------
# 普通 token = sha256(ACCESS_PASSWORD)；admin token = sha256("admin:" + MASTER_PASSWORD)
# MASTER_PASSWORD 仅来自环境变量，前端不可见、不可改，优先级高于 ACCESS_PASSWORD。
# 这样即使访客改掉库里的普通密码，主人用环境变量密码永远能进并改回。
def _access_password():
    return settings.get_setting("ACCESS_PASSWORD") or ""

def _master_password():
    return settings.get_setting("MASTER_PASSWORD") or ""

def _normal_token():
    pw = _access_password()
    return hashlib.sha256(pw.encode("utf-8")).hexdigest() if pw else None

def _admin_token():
    pw = _master_password()
    return hashlib.sha256(("admin:" + pw).encode("utf-8")).hexdigest() if pw else None

def _auth_required():
    # 任一密码非空即要求鉴权
    return _normal_token() is not None or _admin_token() is not None


def _resolve_provided_token(request):
    """多渠道解析客户端传入的 token，必须与 AuthMiddleware 完全一致：
    Authorization: Bearer <tok> / 自定义头 X-Access-Token / 查询参数 ?token=。
    公网网关（如魔搭）常剥离标准 Authorization 头，若不兼容 X-Access-Token，
    则 admin 身份永远解析不到 —— 这正是「改了访问密码提示成功、却用新密码登不进」
    的根因（密码被静默丢弃）。"""
    auth = request.headers.get("Authorization", "")
    provided = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    if not provided:
        provided = (request.headers.get("X-Access-Token") or "").strip()
    if not provided:
        provided = (request.query_params.get("token") or "").strip()
    return provided

# 细粒度写权限：访问密码(ACCESS_PASSWORD)与「清空全部设置(reset)」仅管理员可写，
# 其余参数（QODER_PAT / 通知通道 / 调度等）非管理员也可改。鉴权在 api_post_settings
# 内按 token 身份逐字段执行，故不再把整个 /api/settings 列入全局敏感名单。
_SENSITIVE = set()

# 免校验白名单：健康检查、登录、登出、Qoder Webhook 回调（自带 HMAC 验签）、API 文档
_AUTH_WHITELIST = {
    "/api/health",
    "/api/login",
    "/api/logout",
    "/api/qoder-webhook",
    "/api/qoder-webhook/selftest",
    "/docs",
    "/openapi.json",
    "/redoc",
}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        if path in _AUTH_WHITELIST:
            return await call_next(request)
        n_tok, a_tok = _normal_token(), _admin_token()
        if n_tok is None and a_tok is None:
            # 未配置任何密码：放行（保持现状，不锁死）
            return await call_next(request)
        # token 支持多渠道：标准 Authorization: Bearer / 自定义头 X-Access-Token /
        # 查询参数 ?token= —— 魔搭等公网网关常剥离标准 Authorization 头，自定义头
        # 可绕过，避免「login 成功但后续 API 全 401」的问题。
        provided = _resolve_provided_token(request)
        if provided and (provided == n_tok or provided == a_tok):
            # 基础鉴权通过；敏感写操作若已配 master，则要求 admin token
            if (request.method, path) in _SENSITIVE and a_tok is not None and provided != a_tok:
                return JSONResponse(status_code=403, content={"detail": "需要管理员令牌才能执行此操作", "code": "NEED_ADMIN"})
            return await call_next(request)
        # ---- 诊断：打印 401 时的 token 不匹配详情（仅前缀，不泄露完整 hash）----
        print(f"[AUTH-REJECT] path={path} method={request.method} "
              f"provided_prefix={provided[:8] if provided else 'EMPTY'} "
              f"expect_normal={n_tok[:8] if n_tok else 'NONE'} "
              f"expect_admin={a_tok[:8] if a_tok else 'NONE'}")
        return JSONResponse(status_code=401, content={"detail": "未授权：请先登录", "code": "NO_AUTH"})


app.add_middleware(AuthMiddleware)


# ---------- 安全 JSON 解析（兼容 UTF-8 / GBK，避免非 UTF-8 请求体直接 500）----------
async def _json(req: Request):
    raw = await req.body()
    if not raw:
        raise HTTPException(400, "请求体为空")
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")


# ---------- API ----------
@app.get("/api/health")
def health():
    return {"status": "ok", "qoder_mode": "live" if settings.get_setting("QODER_PAT") else "demo",
            "auth_required": _auth_required(),
            "time": datetime.now().isoformat()}


# ---------- 访问密码登录 / 登出 ----------
@app.post("/api/login")
async def api_login(req: Request):
    if not _auth_required():
        raise HTTPException(400, "系统尚未配置访问密码")
    body = await _json(req)
    pw = body.get("password") or ""
    # 优先识别管理员（环境变量主人密码 MASTER_PASSWORD）
    a_tok = _admin_token()
    if a_tok and hashlib.sha256(("admin:" + pw).encode("utf-8")).hexdigest() == a_tok:
        print(f"[AUTH-LOGIN] admin OK token_prefix={a_tok[:8]}")
        return {"ok": True, "token": a_tok, "is_admin": True}
    n_tok = _normal_token()
    if n_tok and hashlib.sha256(pw.encode("utf-8")).hexdigest() == n_tok:
        print(f"[AUTH-LOGIN] normal OK token_prefix={n_tok[:8]}")
        return {"ok": True, "token": n_tok, "is_admin": False}
    raise HTTPException(401, "访问密码错误")


@app.post("/api/logout")
def api_logout():
    # stateless：服务端无会话状态，前端自行清除本地 token 即可
    return {"ok": True}


@app.get("/api/platforms")
def api_platforms():
    return {
        "platforms": kb.PLATFORMS,
        "platform_detail": kb.PLATFORM_DETAIL,
        "common": kb.COMMON,
        "material_fields": kb.MATERIAL_FIELDS,
        "channels": CHANNELS,
    }


@app.get("/api/overview")
def api_overview():
    records = get_records()
    risks = kb.compute_risks(records)
    warn = len([r for r in risks if r["lvl"] != "info"])
    done = len([r for r in records if kb.stage_of(r).get("done")])
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM inspections WHERE tenant_id=?",
                      (current_tenant_id(),)).fetchone()["n"]
    channels_on = len([s for s in get_channels_status() if s["enabled"]])
    return {
        "total": len(records), "warn": warn, "done": done,
        "scan": n, "qoder_mode": "live" if settings.get_setting("QODER_PAT") else "demo",
        "channels_enabled": channels_on,
    }


@app.get("/api/records")
def api_records():
    return get_records()


@app.post("/api/records")
async def api_record_create(req: Request):
    body = await _json(req)
    plat = body.get("platform")
    name = (body.get("name") or "").strip()
    if plat not in kb.PLATFORMS:
        raise HTTPException(400, "platform 非法")
    if not name:
        raise HTTPException(400, "name 不能为空")
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO records (platform,name,submit,verify,gov,created_at,tenant_id) VALUES (?,?,?,?,?,?,?)",
            (plat, name, body.get("submit", "") or "", body.get("verify", "") or "",
             body.get("gov", "") or "", datetime.now().isoformat(), current_tenant_id()))
        rid = cur.lastrowid
    return {"id": rid, **body}


@app.delete("/api/records/{rid}")
def api_record_delete(rid: int):
    with get_conn() as c:
        # 仅允许删除「本租户」记录，防止越权删他人数据
        c.execute("DELETE FROM records WHERE id=? AND tenant_id=?", (rid, current_tenant_id()))
    return {"ok": True}


@app.get("/api/channels")
def api_channels():
    return get_channels_status()


@app.post("/api/channels/{key}")
async def api_channel_toggle(key: str, req: Request):
    if key not in CHANNELS:
        raise HTTPException(400, "未知通道")
    body = await _json(req)
    enabled = bool(body.get("enabled", False))
    set_config(f"channel_{key}_enabled", "true" if enabled else "false")
    return {"ok": True, "enabled": enabled}


@app.post("/api/diagnose")
async def api_diagnose(req: Request):
    body = await _json(req)
    keys = body.get("platforms", [])
    biz = body.get("biz", "电商 / 交易")
    subject = body.get("subject", "企业")
    result = kb.diagnosis(keys, biz, subject)
    if "error" in result:
        raise HTTPException(400, result["error"])
    # 可选增强：有 PAT 时让专属诊断 Agent 生成补充提示（失败不影响主结论）
    if settings.get_setting("QODER_PAT"):
        prompt = (f"针对平台 {keys}、业务 {biz}、主体 {subject}，"
                  f"用一句话补充最容易踩坑的备案注意点（依据官方规则，附来源）。")
        import asyncio
        try:
            extra = await asyncio.wait_for(
                asyncio.to_thread(run_agent, "diagnosis", prompt), timeout=60)
            if extra:
                result["qoderNote"] = extra
        except Exception as e:
            print(f"[diagnose] Qoder 增强失败(不影响主结论): {e}")
    return result


@app.post("/api/precheck")
async def api_precheck(req: Request):
    body = await _json(req)
    fields = body.get("fields", {})
    # 本地规则引擎（确定性校验）：保留代码，始终计算，作为 demo 回退 + 结构化参考
    local = kb.precheck(fields)
    if settings.get_setting("QODER_PAT"):
        # 真实调用专属预审 Agent，返回其真实结果作为主结论
        prompt = (
            "以下为小程序备案材料字段（JSON），请判断材料完整性与合规风险，"
            "指出缺失项、高风险项；对电商/教育/医疗健康/金融/微短剧等高审核强度类目"
            "提示需补充的行业资质；给出可执行的补正建议。"
            "仅依据工信部与各平台官方备案规则，禁止编造，每条结论附来源。\n"
            + json.dumps(fields, ensure_ascii=False)
        )
        import asyncio
        try:
            ans = await asyncio.wait_for(
                asyncio.to_thread(run_agent, "precheck", prompt), timeout=60)
            if ans:
                return {
                    "source": "Qoder Cloud Agents（filing-precheck-agent）",
                    "answer": ans,
                    "local": local,  # 结构化校验仍附带，供前端按需参考展示
                }
        except Exception as e:
            print(f"[precheck] Qoder 调用失败，回退本地规则引擎: {e}")
    # 无 PAT / Agent 失败：回退本地规则引擎结论（demo 模式照常可用）
    return {
        "source": "本地规则引擎（未接入 Qoder 或 Agent 调用失败）",
        "answer": None,
        "local": local,
    }


@app.get("/api/risks")
def api_risks():
    return kb.compute_risks(get_records())


@app.post("/api/inspect")
def api_inspect():
    return do_inspect()


@app.get("/api/inspections")
def api_inspections(limit: int = 50):
    """返回巡检历史（含 progress-agent 智能播报 brief）。"""
    with get_conn() as c:
        rows = c.execute(
            "SELECT id,ran_at,summary,brief,risks_count,notify FROM inspections "
            "WHERE tenant_id=? ORDER BY id DESC LIMIT ?", (current_tenant_id(), min(limit, 200))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["notify"] = json.loads(d["notify"]) if d["notify"] else []
        except Exception:
            d["notify"] = []
        out.append(d)
    return {"inspections": out}


@app.delete("/api/inspections/{iid}")
def api_inspection_delete(iid: int):
    """删除一条巡检播报历史（含其智能播报）。"""
    with get_conn() as c:
        c.execute("DELETE FROM inspections WHERE id=? AND tenant_id=?", (iid, current_tenant_id()))
    return {"ok": True}


@app.post("/api/notify/test")
def api_notify_test():
    enabled = get_channels_enabled()
    message = "【测试】小程序备案 AI 中枢 通知通道自检，如收到说明配置可达。"
    return {"notify": send_all(message, enabled)}


@app.post("/api/webhook")
async def api_webhook(req: Request):
    """手动 / 演示触发巡检（前端「模拟 Qoder Webhook 回调」按钮）。
    内部可信端点，不做签名校验；真正来自 Qoder 的回调走 /api/qoder-webhook。"""
    result = do_inspect()
    result["event_id"] = f"evt_{int(datetime.now().timestamp())}"
    result["source"] = "manual"
    return result


# ---------- Qoder Webhook 接收（HMAC-SHA256 验签 + 事件路由 + 审计）----------
def verify_qoder_signature(raw_body: bytes, header: str, secret: str) -> bool:
    """校验 Qoder Webhook 信封签名：header=`t=<unix>,v1=<hmac_sha256(secret,"<t>.<raw>")>`。
    含时间戳重放窗口（600s）防护。"""
    if not secret or not header:
        return False
    parts = {}
    for kv in header.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.strip()] = v.strip()
    t, v1 = parts.get("t"), parts.get("v1")
    if not t or not v1:
        return False
    try:
        if abs(int(time.time()) - int(t)) > 600:
            return False
    except Exception:
        return False
    msg = (t + "." + raw_body.decode("utf-8", "replace")).encode("utf-8")
    computed = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, v1)


# 真实会触发巡检的 Qoder 事件类型
_KNOWN_TRIGGERS = {"session.status_idled", "session.thread_idled"}


def _try_auto_inspect(source, session_id=None, brief=None):
    """统一的「自动巡检」去重入口：云端 Deployment（Webhook 触发）与本地兜底线程都经此调用。
    去重规则：① 同一天已有自动巡检（无论云端或本地）→ 跳过（避免同日双推 IM）；
             ② 同一 session_id 5 分钟内 → 跳过（防 Qoder 双事件/重试）。
    云端优先：本地兜底线程仅在「整点末尾且云端当天未跑」时触发，故云端通常先占位、
    本地自然被同日去重拦下；仅当云端失效（无 PAT / Webhook 未投递）时本地兜底生效。
    手动巡检（/api/inspect）不走此函数，不记「今日已跑」，仅受 180s 全局冷却约束。
    返回结果 dict（可能含 skipped）。"""
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime())
    if session_id and _last_session_ts.get(session_id, 0) > now - 300:
        return {"ok": True, "skipped": "duplicate session within 5min",
                "event": "auto", "source": source}
    # ── 用锁把「检查→占位→（持久化）→执行」包成原子操作 ──
    # 先占位再执行，可杜绝双路并发都通过检查而各跑一次（历史 11:01 双推根因）。
    with _INSPECT_LOCK:
        if _last_auto_date["d"] == today:
            return {"ok": True, "skipped": "already auto-inspected today",
                    "event": "auto", "source": source}
        _last_auto_date["d"] = today          # 先占位：声明今天已跑
        try:
            set_config("LAST_AUTO_INSPECT_DATE", today)   # 持久化：跨重启去重，避免云端已跑后本地兜底重复
        except Exception:
            pass
    result = do_inspect(agent_brief=brief)     # 再执行（此时其他调用者会被上面的检查拦住）
    if session_id:
        _last_session_ts[session_id] = now
    result["source"] = source
    return result


def handle_qoder_event(envelope: dict) -> dict:
    """处理一条 Qoder Webhook 事件信封：写审计 + 必要时触发巡检。返回摘要。

    Qoder 真实信封结构：顶层 type 通常为 "event"，真实事件类型在 data.type；
    自测/旧结构：顶层 type 直接是 "session.status_idled"。两者都兼容。"""
    data = envelope.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    top_type = envelope.get("type")
    data_type = data.get("type")
    # 优先取命中触发器集合的事件类型（兼容两种信封结构）
    etype = None
    for cand in (data_type, top_type):
        if cand in _KNOWN_TRIGGERS:
            etype = cand
            break
    if etype is None:
        etype = data_type or top_type
    rid = data.get("id") or envelope.get("id")
    # 资源类型推断
    if etype and etype.startswith("agent."):
        rtype = "agent"
    elif etype and etype.startswith("session.thread"):
        rtype = "thread"
    else:
        rtype = "session"
    role = "progress" if (etype and ("session" in etype or "thread" in etype)) else None
    log_agent_event(etype, rtype, rid, role, data)
    # Session 完成 → 触发巡检（经 _try_auto_inspect 去重入口：云端优先、本地兜底）
    if etype in _KNOWN_TRIGGERS:
        brief = None
        if rid and settings.get_setting("QODER_PAT"):
            try:
                brief = read_session_output(rid)
            except Exception as e:
                print(f"[webhook] 读 session 输出失败: {e}")
        return _try_auto_inspect("Qoder Webhook (session.idled)", session_id=rid, brief=brief)
    return {"ok": True, "event": etype, "resource_id": rid}


@app.post("/api/qoder-webhook")
async def api_qoder_webhook(req: Request):
    """Qoder Cloud Agents Webhook 接收端点（注册到 Qoder 的 webhook_endpoints）。
    校验 HMAC-SHA256 签名后，按事件类型写审计、并在 Session 完成时触发巡检。"""
    secret = settings.get_setting("QODER_WEBHOOK_SIGNING_SECRET").strip()
    raw = await req.body()
    if secret:
        sig = req.headers.get("Webhook-Signature", "")
        if not verify_qoder_signature(raw, sig, secret):
            raise HTTPException(401, "Webhook 签名校验失败")
    try:
        envelope = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise HTTPException(400, "非法 JSON 信封")
    return handle_qoder_event(envelope)


@app.post("/api/qoder-webhook/selftest")
def api_qoder_webhook_selftest():
    """后端自测：用已配置的签名密钥构造一条 session.status_idled 事件，走完整验签+处理闭环。"""
    secret = settings.get_setting("QODER_WEBHOOK_SIGNING_SECRET").strip()
    if not secret:
        raise HTTPException(400, "未配置 QODER_WEBHOOK_SIGNING_SECRET，无法自测签名")
    envelope = {"type": "session.status_idled",
                "data": {"id": "sess_selftest_" + str(int(datetime.now().timestamp())), "type": "session"}}
    raw = json.dumps(envelope).encode("utf-8")
    t = str(int(time.time()))
    v1 = hmac.new(secret.encode(), (t + "." + raw.decode()).encode(), hashlib.sha256).hexdigest()
    sig = f"t={t},v1={v1}"
    if not verify_qoder_signature(raw, sig, secret):
        raise HTTPException(500, "自测签名构造失败")
    return handle_qoder_event(envelope)


@app.post("/api/qa")
async def api_qa(req: Request):
    body = await _json(req)
    q = (body.get("q") or "").strip()
    if not q:
        raise HTTPException(400, "问题为空")
    # 1) 先走本地知识库（快，确定性）
    hit = kb.answer(q)
    if hit:
        return hit
    # 2) 未知问题：有 PAT 时尝试 Qoder（放线程池避免阻塞事件循环，60s 超时）
    if settings.get_setting("QODER_PAT"):
        import asyncio
        try:
            ans = await asyncio.wait_for(
                asyncio.to_thread(run_agent, "qa", q),
                timeout=60)
            if ans:
                return {"a": ans, "src": "Qoder Cloud Agents（基于官方备案规则）"}
        except asyncio.TimeoutError:
            print("[qa] Qoder 查询超时(60s)，回退本地兜底")
        except Exception as e:
            print(f"[qa] Qoder 查询异常: {e}")
    # 3) 兜底：本地建议
    return {
        "a": "未匹配到明确答案。可试试：都要备？时效多久？微信/支付宝/抖音/快手备案？微短剧材料？短信核验超时？接入 Qoder 后由知识库 RAG 精确作答。",
        "src": "知识库未命中 + Qoder 未返回",
    }


# ---------- 参数设置（页面设置 > 环境变量 > 默认值）----------
@app.get("/api/settings")
def api_get_settings():
    return settings.snapshot(mask=True)


@app.post("/api/settings")
async def api_post_settings(req: Request):
    body = await _json(req)
    reset = bool(body.get("reset", False))
    data = body.get("settings", {}) if isinstance(body, dict) else {}
    # ── 双身份细粒度校验（服务端强制，不依赖前端隐藏）──
    # token 多渠道解析必须与 AuthMiddleware 一致（Authorization / X-Access-Token / ?token=）：
    # 魔搭等公网网关会剥离标准 Authorization 头，若只认 Authorization，则 admin 身份
    # 永远解析不到 → can_change_ap=False → ACCESS_PASSWORD 被静默丢弃 →
    # 「保存提示成功、却用新密码登不进」。改用 _resolve_provided_token 统一解析。
    provided = _resolve_provided_token(req)
    a_tok = _admin_token()
    is_admin = (a_tok is not None and provided == a_tok)
    if reset:
        # 清空全部设置属高危操作，仅管理员可执行
        if a_tok is not None and not is_admin:
            return JSONResponse(status_code=403,
                                content={"detail": "需要管理员令牌才能执行此操作", "code": "NEED_ADMIN"})
        return settings.apply_batch(data, reset=True)
    # 访问密码仅管理员可改；未配置 MASTER_PASSWORD 时无管理员概念，允许本人改
    can_change_ap = (a_tok is None) or is_admin
    if not can_change_ap and "ACCESS_PASSWORD" in data:
        data.pop("ACCESS_PASSWORD")  # 非管理员硬塞也丢弃，不落库
    return settings.apply_batch(data, reset=False)


@app.get("/api/scheduler")
def api_get_scheduler():
    h = settings.get_setting("SCHEDULER_HOUR") or "9"
    dep = get_deployment_status("progress")
    return {"enabled": settings.is_scheduler_enabled(), "hour": int(h),
            "deployment_id": dep.get("id"), "deployment_status": dep.get("status"),
            "next_runs": dep.get("next_runs")}


@app.post("/api/scheduler")
async def api_post_scheduler(req: Request):
    body = await _json(req)
    enabled = bool(body.get("enabled", False))
    # 支持设置巡检小时（0-23）
    hour = body.get("hour")
    if hour is not None:
        try:
            hour = max(0, min(23, int(hour)))
            settings.set_setting("SCHEDULER_HOUR", str(hour))
        except (ValueError, TypeError):
            pass
    settings.set_setting("SCHEDULER_ENABLED", "true" if enabled else "false")
    set_scheduler_enabled(enabled)
    dep = get_deployment_status("progress")
    return {"enabled": enabled, "hour": int(settings.get_setting("SCHEDULER_HOUR") or "9"),
            "deployment_id": dep.get("id"), "deployment_status": dep.get("status"),
            "next_runs": dep.get("next_runs")}


@app.get("/api/deployments")
def api_deployments():
    """返回进度巡检 Deployment 的云端状态（id/status/hour/next_runs）。"""
    return get_deployment_status("progress")


@app.get("/api/agent-events")
def api_agent_events(limit: int = 50):
    """返回 Agent 活动审计事件（最新在前）。"""
    return get_agent_events(min(limit, 200))


@app.delete("/api/agent-events/{eid}")
def api_agent_event_delete(eid: int):
    """删除一条 Agent 活动审计事件。"""
    with get_conn() as c:
        c.execute("DELETE FROM agent_events WHERE id=? AND tenant_id=?", (eid, current_tenant_id()))
    return {"ok": True}


# ---------- Webhook 端点管理（Qoder webhook_endpoints CRUD）----------
@app.get("/api/webhook-endpoints")
def api_list_webhook_endpoints():
    """列出 Qoder 上已创建的 Webhook 端点。

    注意：Qoder 的 signing_secret 仅在「创建」时返回一次，后续 GET 列表
    不会返回该字段，故此处无需也不能脱敏。
    """
    eps = list_webhook_endpoints()
    return {"endpoints": eps, "available_events": WEBHOOK_EVENT_TYPES}


@app.post("/api/webhook-endpoints")
async def api_create_webhook_endpoint(req: Request):
    """创建 Qoder Webhook 端点。

    仅提交 url + events（+ 可选 description）。Qoder 会在响应中**返回一次**
    signing_secret，该密钥用于后续投递验签——本接口自动将其写入
    QODER_WEBHOOK_SIGNING_SECRET（参数设置），使 /api/qoder-webhook 验签闭环生效。
    signing_secret 仅在本次创建响应中返回给前端（仅显示一次）。
    """
    body = await _json(req)
    url = (body.get("url") or "").strip()
    events = body.get("events") or ["session.status_idled"]
    description = (body.get("description") or "").strip() or None
    if not url:
        raise HTTPException(400, "url 不能为空")
    # 校验事件类型
    invalid = [e for e in events if e not in WEBHOOK_EVENT_TYPES]
    if invalid:
        raise HTTPException(400, f"不支持的事件类型: {invalid}")
    try:
        ep = create_webhook_endpoint(url, events, description)
        if not ep:
            raise HTTPException(500, "创建 Webhook 端点失败（检查 PAT 和网络）")
        # 关键：把 Qoder 返回的 signing_secret 自动保存到参数设置，供后续验签
        returned_secret = ep.get("signing_secret")
        if returned_secret:
            settings.set_setting("QODER_WEBHOOK_SIGNING_SECRET", returned_secret)
            _log("Webhook 端点签名密钥已自动写入 QODER_WEBHOOK_SIGNING_SECRET")
        log_agent_event("webhook.endpoint_created", "webhook_endpoint", ep.get("id"), None,
                        {"url": url, "events": events})
        # 把 signing_secret 原样返回（仅此一次，前端仅展示一次）
        return ep
    except HTTPException:
        raise
    except Exception as e:
        _log(f"api_create_webhook_endpoint 异常: {e}")
        raise HTTPException(500, f"创建失败：{e}")


@app.delete("/api/webhook-endpoints/{ep_id}")
def api_delete_webhook_endpoint(ep_id: str):
    """删除指定的 Qoder Webhook 端点。"""
    ok = delete_webhook_endpoint(ep_id)
    if not ok:
        raise HTTPException(404, f"删除失败：端点 {ep_id} 不存在或无权限")
    log_agent_event("webhook.endpoint_deleted", "webhook_endpoint", ep_id, None, None)
    return {"ok": True}


# ---------- 静态前端托管（必须在 API 路由之后挂载）----------
app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
