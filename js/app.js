/* ============================================================
   multi-mini-filing-aide · 前端交互（API 驱动）
   所有数据 / 计算 / 触发均走后端 FastAPI；UI 与科技感保持不变。
   ============================================================ */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const PLAT_KEYS = Object.keys(PLATFORMS);

const state = {
  records: [],
  channels: {},
  risks: [],
  selectedDiag: new Set(),
};

/* ---------- 工具 ---------- */
function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

// 安全地把 Agent 返回的 Markdown 渲染为 HTML（marked 解析 + DOMPurify 净化，防 XSS）；
// 若库未加载或内容不是 Markdown，则降级为纯文本转义显示。
function md(text) {
  if (!text) return "";
  const src = String(text);
  if (window.marked) {
    const raw = marked.parse(src, { breaks: true, gfm: true });
    return window.DOMPurify ? DOMPurify.sanitize(raw) : raw;
  }
  return esc(src);
}

function getToken() { try { return localStorage.getItem("mma_token") || ""; } catch (e) { return ""; } }
function setToken(t) { try { if (t) localStorage.setItem("mma_token", t); else localStorage.removeItem("mma_token"); } catch (e) {} }

async function api(path, opts) {
  const headers = Object.assign({ "Content-Type": "application/json; charset=utf-8" }, (opts && opts.headers) || {});
  const tk = getToken();
  if (tk) {
    headers["Authorization"] = "Bearer " + tk;
    headers["X-Access-Token"] = tk;   // 双渠道：绕过公网网关对 Authorization 头的剥离
  }
  const res = await fetch(path, Object.assign({ headers }, opts || {}));
  if (res.status === 401) {
    // 未授权 → 弹出登录层（不抛错打断其它逻辑）
    showLogin();
    throw new Error("未授权，请先登录");
  }
  if (!res.ok) {
    let m = {};
    try { m = await res.json(); } catch (e) {}
    throw new Error(m.detail || ("HTTP " + res.status));
  }
  return res.json();
}

/* ---------- 访问密码登录（方案 A）---------- */
function showLogin() {
  const ov = $("#loginOverlay");
  if (ov) ov.classList.add("show");
  const inp = $("#loginPwd");
  if (inp) { inp.value = ""; setTimeout(() => inp.focus(), 50); }
}
function hideLogin() { const ov = $("#loginOverlay"); if (ov) ov.classList.remove("show"); }
async function doLogin() {
  const inp = $("#loginPwd");
  const pwd = (inp && inp.value) || "";
  const err = $("#loginErr");
  if (err) err.textContent = "";
  if (!pwd) { if (err) err.textContent = "请输入访问密码"; return; }
  const btn = $("#btnLogin");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ password: pwd }),
    });
    if (r.ok) {
      const d = await r.json();
      setToken(d.token);
      try { localStorage.setItem("mma_is_admin", d.is_admin ? "true" : "false"); } catch (e) {}
      hideLogin();
      inlineNotify("🔓", "登录成功", d.is_admin ? "管理员已登录" : "欢迎使用多平台小程序备案 AI 中枢", "ok");
      bootData();
    } else {
      let m = {}; try { m = await r.json(); } catch (e) {}
      if (err) err.textContent = (m.detail || ("HTTP " + r.status));
    }
  } catch (e) {
    if (err) err.textContent = "登录请求失败：" + e.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}
function doLogout() {
  setToken("");
  try { localStorage.removeItem("mma_is_admin"); } catch (e) {}
  inlineNotify("🔒", "已退出", "清除本地登录态", "info");
  showLogin();
}

function toast(icon, title, desc) {
  const wrap = $("#toastWrap");
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<div class="t-ic">${icon}</div><div><div class="t-t">${esc(title)}</div><div class="t-d">${esc(desc)}</div></div>`;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateX(30px)"; setTimeout(() => el.remove(), 300); }, 4800);
}

/* ---------- 内联通知（内容区右上角，替代轻量 toast）----------
 * kind: "ok" | "err" | "info" —— 控制边框色
 * 自动 3.5s 后淡出消失，无需手动关闭
 */
function inlineNotify(icon, title, desc, kind) {
  kind = kind || "info";
  const wrap = $("#inlineNotify");
  if (!wrap) { toast(icon, title, desc); return; } /* 降级到右下角 toast */
  const el = document.createElement("div");
  el.className = `inl-n ${kind}`;
  el.innerHTML = `<div class="inl-ic">${icon}</div><div class="inl-body"><div class="inl-t">${esc(title)}</div>${desc ? `<div class="inl-d">${esc(desc)}</div>` : ""}</div>`;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.animation = "inlOut 0.25s ease forwards";
    setTimeout(() => el.remove(), 260);
  }, 3500);
}

/* ---------- 居中结果弹窗（替代提交类 toast：登记/保存/删除等操作的明确结果）----------
 * kind: "success" | "error" | "info" —— 控制图标圆圈配色
 * 需点击「确定」或点遮罩 / 按 Esc 关闭（与右下角 toast 自动消失不同，结果更醒目）
 */
function showResultModal(icon, title, desc, kind) {
  kind = kind || "info";
  const root = $("#modalRoot");
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal result-modal" role="dialog" aria-modal="true">
      <div class="r-icon ${kind}">${icon}</div>
      <div class="r-title">${esc(title)}</div>
      <div class="r-desc">${esc(desc)}</div>
      <div class="modal-actions">
        <button class="btn btn-primary" id="rModalOk">确定</button>
      </div>
    </div>`;
  root.appendChild(overlay);
  const okBtn = overlay.querySelector("#rModalOk");
  let done = false;
  const finish = () => {
    if (done) return; done = true;
    document.removeEventListener("keydown", onKey);
    overlay.classList.add("closing");
    setTimeout(() => overlay.remove(), 180);
  };
  const onKey = (e) => { if (e.key === "Escape") finish(); };
  okBtn.addEventListener("click", finish);
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) finish(); });
  document.addEventListener("keydown", onKey);
  requestAnimationFrame(() => overlay.classList.add("show"));
  okBtn.focus();
}

/* ---------- 通用确认弹窗（替代原生 confirm/alert，返回 Promise<boolean>）----------
 * opts: { title, message, detail, confirmText, cancelText, type('danger'|'warn'|'info'), icon }
 */
function confirmDialog(opts = {}) {
  return new Promise((resolve) => {
    const type = opts.type || "danger";
    const icon = opts.icon || (type === "danger" ? "🗑️" : type === "warn" ? "⚠️" : "ℹ️");
    const title = opts.title || "确认操作";
    const message = opts.message || "";
    const detail = opts.detail ? `<div class="modal-detail">${esc(opts.detail)}</div>` : "";
    const confirmText = opts.confirmText || "确认删除";
    const cancelText = opts.cancelText || "取消";
    const root = $("#modalRoot");

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-icon ${type}">${icon}</div>
        <div class="modal-title">${esc(title)}</div>
        <div class="modal-msg">${message}</div>
        ${detail}
        <div class="modal-actions">
          <button class="btn btn-cancel" id="modalCancel">${esc(cancelText)}</button>
          <button class="btn ${type === "danger" ? "btn-danger" : "btn-primary"}" id="modalOk">${esc(confirmText)}</button>
        </div>
      </div>`;
    root.appendChild(overlay);

    const modal = overlay.querySelector(".modal");
    const okBtn = overlay.querySelector("#modalOk");
    const cancelBtn = overlay.querySelector("#modalCancel");
    let done = false;
    const finish = (val) => {
      if (done) return;
      done = true;
      document.removeEventListener("keydown", onKey);
      overlay.classList.add("closing");
      setTimeout(() => { overlay.remove(); }, 180);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === "Escape") finish(false);
      else if (e.key === "Enter") { e.preventDefault(); finish(true); }
    };
    okBtn.addEventListener("click", () => finish(true));
    cancelBtn.addEventListener("click", () => finish(false));
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) finish(false); });
    document.addEventListener("keydown", onKey);
    // 入场动画
    requestAnimationFrame(() => overlay.classList.add("show"));
    okBtn.focus();
  });
}

/* ---------- 配置说明弹层（右上角「配置说明」按钮）----------
 * 展示「使用系统前的参数配置说明」，复用 modal-overlay/modal 体系。
 */
function openConfigHelp() {
  const root = $("#modalRoot");
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal cfg-help" role="dialog" aria-modal="true">
      <div class="modal-icon info">⚙️</div>
      <div class="modal-title">使用系统前 · 参数配置说明</div>
      <div class="cfg-help-body">
        <p>本系统所有能力都依赖「参数设置」。使用前请先确认以下配置，按优先级 <code>页面设置 &gt; 环境变量</code> 生效。下方变量可在部署平台（如魔搭）的「环境变量」中配置，也可登录后在左侧「参数设置」页填写。</p>

        <h4>🔐 1. 访问密码（建议最先配置）</h4>
        <ul>
          <li><span class="req">必配</span><code>MASTER_PASSWORD</code>：管理员密码，<b>仅能来自环境变量</b>，前端不可见。不配则线上无管理员，无法修改访问密码。</li>
          <li><span class="opt">建议</span><code>ACCESS_PASSWORD</code>：访客访问密码。留空 = 关闭鉴权（任何人可进）。</li>
        </ul>

        <h4>🤖 2. Qoder Cloud Agents（AI 增强，可选）</h4>
        <ul>
          <li><span class="opt">可选</span><code>QODER_PAT</code>：Qoder 访问令牌。<b>留空 = 演示模式</b>，仅用本地规则引擎；填入后即接入 Qoder 多 Agent（备案诊断 / 材料预审 / 进度巡检 / 政策问答）。</li>
          <li><span class="opt">可选</span><code>QODER_API_BASE</code> / <code>QODER_MODEL</code>：一般无需改动，保留默认即可。</li>
        </ul>

        <h4>🔔 3. 通知通道 Webhook（巡检播报推送，可选）</h4>
        <ul>
          <li><span class="opt">可选</span>企业微信 / 钉钉 / 飞书 的 <code>Webhook URL</code>；钉钉、飞书还需对应的 <code>签名密钥</code>。至少启用一个，巡检播报才会推送。</li>
        </ul>

        <h4>⏰ 4. 定时巡检（可选）</h4>
        <ul>
          <li><span class="opt">可选</span><code>SCHEDULER_ENABLED=true</code> 开启每日自动巡检，<code>SCHEDULER_HOUR</code> 设整点。云端 Deployment 为主、本地兜底为辅。</li>
        </ul>

        <h4>📌 5. 配置方式与优先级</h4>
        <ul>
          <li>部署时在平台「环境变量」里配置最稳妥；也可登录后在左侧「参数设置」页填写（页面设置优先级更高）。</li>
          <li>保存后即时生效。</li>
        </ul>

        <div class="cfg-help-tip">💡 <b>最小可用配置</b>：只配 <code>MASTER_PASSWORD</code> + <code>ACCESS_PASSWORD</code> 即可使用（规则引擎模式）。想启用 AI 备案诊断 / 材料预审 / 进度巡检 / 政策问答，再补 <code>QODER_PAT</code> 与通知通道即可。</div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-primary" id="cfgHelpClose">我知道了</button>
      </div>
    </div>`;
  root.appendChild(overlay);
  const closeBtn = overlay.querySelector("#cfgHelpClose");
  let done = false;
  const finish = () => {
    if (done) return; done = true;
    document.removeEventListener("keydown", onKey);
    overlay.classList.add("closing");
    setTimeout(() => overlay.remove(), 180);
  };
  const onKey = (e) => { if (e.key === "Escape") finish(); };
  closeBtn.addEventListener("click", finish);
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) finish(); });
  document.addEventListener("keydown", onKey);
  requestAnimationFrame(() => overlay.classList.add("show"));
}

/* ---------- 按钮 loading 守卫（置灰 + 防重复提交） ---------- */
function setBtnLoading(btn, on) {
  if (!btn) return;
  if (on) { btn.classList.add("is-loading"); btn.disabled = true; }
  else { btn.classList.remove("is-loading"); btn.disabled = false; }
}
async function withBtn(btn, fn) {
  if (!btn || btn.disabled) return; // 已在加载中，防重复提交
  setBtnLoading(btn, true);
  try { return await fn(); }
  finally { setBtnLoading(btn, false); }
}

/* ---------- 时钟 ---------- */
function tickClock() {
  const d = new Date();
  const p = n => String(n).padStart(2, "0");
  $("#clock").textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/* ---------- 导航 ---------- */
function switchView(v) {
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === v));
  $$(".view").forEach(s => s.classList.toggle("active", s.id === "view-" + v));
  if (v === "settings") loadSettings();
  if (v === "agentlog") loadAgentLog();
  if (v === "risk") loadInspections();
}
$$(".nav-item").forEach(n => n.addEventListener("click", () => switchView(n.dataset.view)));
$$("[data-jump]").forEach(b => b.addEventListener("click", () => switchView(b.dataset.jump)));

/* ---------- 数据结构加载 ---------- */
async function loadAll() {
  try {
    const [ov, recs, chs, risks] = await Promise.all([
      api("/api/overview"), api("/api/records"), api("/api/channels"), api("/api/risks"),
    ]);
    state.records = recs;
    state.risks = risks;
    state.channels = Object.fromEntries(chs.map(c => [c.key, c]));
    renderOverview(ov);
    renderProgress();
    renderRisk(risks);
    renderChannels(chs);
  } catch (e) {
    inlineNotify("⚠️", "加载失败", "无法连接后端：" + e.message, "err");
  }
}

/* ---------- 总览 ---------- */
function renderOverview(ov) {
  $("#mTotal").textContent = ov.total;
  $("#mWarn").textContent = ov.warn;
  $("#mDone").textContent = ov.done;
  $("#mScan").textContent = ov.scan;
  const qd = $("#qoderDot"), qs = $("#qoderStat");
  if (ov.qoder_mode === "live") { qd.classList.remove("off"); qs.textContent = "QAC 云脑 · 实时"; }
  else { qd.classList.add("off"); qs.textContent = "QAC 云脑 · 演示模式"; }
  const on = ov.channels_enabled;
  $("#notifyStat").textContent = `通知通道 ${on}/3`;
  $("#notifyDot").classList.toggle("off", on === 0);
}

/* ---------- 诊断台 ---------- */
function renderDiagPlatforms() {
  const box = $("#diagPlatforms");
  box.innerHTML = PLAT_KEYS.map(k => {
    const p = PLATFORMS[k];
    return `<div class="chip" data-k="${k}"><span class="pdot" style="background:${p.color}"></span>${p.name}</div>`;
  }).join("");
  $$("#diagPlatforms .chip").forEach(c => c.addEventListener("click", () => {
    const k = c.dataset.k;
    if (state.selectedDiag.has(k)) { state.selectedDiag.delete(k); c.classList.remove("on"); }
    else { state.selectedDiag.add(k); c.classList.add("on"); }
  }));
}
async function genDiagnosis() {
  const keys = [...state.selectedDiag];
  if (!keys.length) { showResultModal("⚠️", "请选择平台", "至少选择一个目标平台", "info"); return; }
  const biz = $("#diagBiz").value, subj = $("#diagSubject").value;
  const box = $("#diagResult");
  box.innerHTML = `<div class="pill info" style="display:inline-block">🤖 正在调用诊断 Agent 生成诊断报告，请稍候…</div>`;
  try {
    const r = await api("/api/diagnose", { method: "POST", body: JSON.stringify({ platforms: keys, biz, subject: subj }) });
    const rows = r.rows.map(d => `<tr>
      <td class="plat"><span class="pdot" style="background:${d.color};width:10px;height:10px;border-radius:50%"></span>${d.name}</td>
      <td><span class="pill bad">${d.require}</span></td>
      <td>${d.title}</td>
      <td>${d.points.map(x => `<div class="tag">${esc(x)}</div>`).join("")}</td>
    </tr>`).join("");
    const special = r.special ? `<div class="pill warn">⚠ ${esc(r.special)}</div>` : "";
    const subjNote = r.subjNote ? `<div class="pill info">${esc(r.subjNote)}</div>` : "";
    const qoderNote = r.qoderNote ? `<div class="pill info" style="margin-top:10px">🤖 QAC 补充：<div class="md" style="margin-top:6px">${md(r.qoderNote)}</div></div>` : "";
    $("#diagResult").innerHTML = `
      <div class="pill ok" style="margin-bottom:10px">✓ ${esc(r.conclusion)}</div>
      ${special}${subjNote}${qoderNote}
      <div class="divider"></div>
      <table class="tbl"><thead><tr><th>平台</th><th>备案要求</th><th>核心要点</th><th>材料 / 流程差异</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:12px">时效：平台初审 1–2 工作日 → 工信部短信核验 24h 内 → 管局终审 1–20 工作日。来源：各平台开放平台官方文档。</p>`;
  } catch (e) { inlineNotify("⚠️", "诊断失败", e.message, "err"); }
}

/* ---------- 材料预审台 ---------- */
function fillMatPlatform() {
  const sel = $("#matPlatform");
  sel.innerHTML = PLAT_KEYS.map(k => `<option value="${k}">${PLATFORMS[k].name}</option>`).join("");
  renderMatFields();
}
function fillPrPlatform() {
  const sel = $("#prPlatform");
  sel.innerHTML = PLAT_KEYS.map(k => `<option value="${k}">${PLATFORMS[k].name}</option>`).join("");
}
function renderMatFields() {
  $("#matFields").innerHTML = MATERIAL_FIELDS.map(f => {
    let ctrl;
    if (f.type === "select") ctrl = `<select id="mf_${f.key}">${f.options.map(o => `<option>${o}</option>`).join("")}</select>`;
    else if (f.type === "checkbox") ctrl = `<label class="chip" style="cursor:pointer"><input type="checkbox" id="mf_${f.key}" style="width:auto;margin-right:6px"/>${f.label}</label>`;
    else ctrl = `<input id="mf_${f.key}" type="${f.type}" placeholder="${esc(f.placeholder || "")}" />`;
    return `<div class="field">${f.type !== "checkbox" ? `<label class="fld">${f.label}</label>` : ""}${ctrl}</div>`;
  }).join("");
}
async function precheckMaterial() {
  const fields = {};
  MATERIAL_FIELDS.forEach(f => {
    const e = $("#mf_" + f.key);
    if (!e) return;
    fields[f.key] = (e.type === "checkbox") ? e.checked : e.value.trim();
  });
  const box = $("#matResult");
  box.innerHTML = `<div class="pill info" style="display:inline-block">🤖 正在调用预审 Agent 审核材料，请稍候…</div>`;
  try {
    const r = await api("/api/precheck", { method: "POST", body: JSON.stringify({ fields }) });
    const lo = r.local || {};
    if (r.answer) {
      // 主结果：预审 Agent 的真实返回（Markdown 渲染）
      let html = `<div class="pill ok" style="margin-bottom:10px">✓ 预审完成</div>`
        + `<div class="md agent-result">${md(r.answer)}</div>`
        + `<div class="src" style="margin-top:10px">📎 来源：${esc(r.source)}</div>`;
      // 本地规则引擎结构化校验作为参考附加（代码保留、照常计算）
      if (lo.risks && lo.risks.length) {
        html += `<div class="divider"></div>`
          + `<div style="font-size:12px;color:var(--text-dim);margin-bottom:6px">本地规则引擎结构化校验（参考）：合规评分 ${lo.score} · ${lo.level}</div>`
          + lo.risks.map(x => `<div class="pill warn" style="display:block;margin-bottom:5px;font-size:12px">· ${esc(x)}</div>`).join("");
      }
      box.innerHTML = html;
    } else {
      // 无 PAT / Agent 失败：回退本地规则引擎结论（demo 模式）
      const map = { "低风险": "ok", "中风险": "warn", "高风险": "bad" };
      const cls = map[lo.level] || "info";
      box.innerHTML = `<div class="pill ${cls}" style="font-size:13px;padding:6px 14px">合规评分 ${lo.score} · ${lo.level}</div>`
        + (lo.risks && lo.risks.length
          ? `<div class="divider"></div><div style="font-size:12.5px;color:var(--text-dim);margin-bottom:8px">预判驳回点（提交前请修复）：</div>`
            + lo.risks.map(x => `<div class="pill bad" style="display:block;margin-bottom:6px">✕ ${esc(x)}</div>`).join("")
          : `<div class="divider"></div><div class="pill ok">✓ 未发现明显驳回风险，可提交</div>`)
        + `<div class="src" style="margin-top:8px">📎 来源：${esc(r.source)}</div>`;
    }
  } catch (e) { inlineNotify("⚠️", "预审失败", e.message, "err"); }
}

/* ---------- 进度看板 ---------- */
function stageOf(r) {
  // 复用后端口径：前端仅做展示渲染，阶段由后端 /api/risks /api/records 已含；此处用于本地进度环估算
  if (r.gov) return { nm: "管局终审中", p: 80 };
  if (r.verify) return { nm: "管局待受理", p: 60 };
  if (r.submit) return { nm: "平台初审中", p: 30 };
  return { nm: "未提交", p: 5 };
}
function fmtCreatedAt(v) {
  // 后端 created_at 形如 "2026-08-07T10:00:25.488530"，截取到分钟展示；非法/空返回 —
  if (!v) return "—";
  const s = String(v).replace("T", " ").slice(0, 16);
  return s || "—";
}
function renderProgress() {
  const list = $("#prList");
  if (!state.records.length) { list.innerHTML = `<div class="empty">暂无备案记录，先在上方登记</div>`; return; }
  list.innerHTML = state.records.map((r, i) => {
    const st = stageOf(r), p = PLATFORMS[r.platform] || {};
    return `<div class="rec">
      <div class="top">
        <div class="plat-tag"><span class="pdot" style="background:${p.color || '#888'};width:10px;height:10px;border-radius:50%"></span>${esc(r.name)} · ${p.name || r.platform}</div>
        <span class="pill info">${st.nm}</span>
      </div>
      <div style="display:flex;gap:18px;align-items:center">
        <div class="ring" style="--p:${st.p}"><div class="num">${st.p}%</div></div>
        <div style="font-size:12px;color:var(--text-dim);line-height:1.8">
          提交：${r.submit || "—"}<br/>核验：${r.verify || "—"}<br/>管局受理：${r.gov || "—"}<br/>登记：${fmtCreatedAt(r.created_at)}
        </div>
        <div style="margin-left:auto"><button class="btn btn-ghost" data-del="${r.id}" style="padding:7px 12px;font-size:12px">删除</button></div>
      </div>
    </div>`;
  }).join("");
  $$("#prList [data-del]").forEach(b => b.addEventListener("click", () => deleteRecord(b.dataset.del)));
}
async function addRecord() {
  const platform = $("#prPlatform").value;
  const name = $("#prName").value.trim();
  if (!name) { showResultModal("⚠️", "请填名称", "小程序名称不能为空", "info"); return; }
  try {
    await api("/api/records", { method: "POST", body: JSON.stringify({
      platform, name,
      submit: $("#prSubmit").value || "", verify: $("#prVerify").value || "", gov: $("#prGov").value || "",
    }) });
    $("#prName").value = ""; $("#prSubmit").value = ""; $("#prVerify").value = ""; $("#prGov").value = "";
    showResultModal("✓", "已登记", `${name} 进度已记录`, "success");
    await loadAll();
  } catch (e) { showResultModal("⚠️", "登记失败", e.message, "error"); }
}
async function deleteRecord(id) {
  const ok = await confirmDialog({
    title: "删除进度记录",
    message: "确认删除这条备案进度记录吗？删除后无法恢复。",
    detail: "记录 ID：" + id,
    confirmText: "删除",
  });
  if (!ok) return;
  try { await api("/api/records/" + id, { method: "DELETE" }); await loadAll(); showResultModal("✓", "删除成功", "进度记录已删除", "success"); }
  catch (e) { showResultModal("⚠️", "删除失败", e.message, "error"); }
}

/* ---------- 风险提醒中心 ---------- */
function renderRisk(risks) {
  const badge = $("#riskBadge");
  badge.textContent = risks.length;  // 角标显示全部可见条目数（不再排除 info 级别）
  const list = $("#riskList");
  if (!risks.length) { list.innerHTML = `<div class="empty">当前无风险项 ✓</div>`; return; }
  const order = { bad: 0, warn: 1, info: 2 };
  list.innerHTML = risks.sort((a, b) => order[a.lvl] - order[b.lvl]).map(r =>
    `<div class="pill ${r.lvl}" style="display:block;margin-bottom:10px;white-space:normal;line-height:1.5">● ${esc(r.t)}<div style="font-weight:400;opacity:.8;margin-top:3px">${esc(r.d)}</div></div>`
  ).join("");
}
function renderChannels(chs) {
  $("#chanList").innerHTML = chs.map(c => `
    <div class="chan">
      <div class="ico">${c.icon}</div>
      <div class="meta"><div class="n">${c.name}</div><div class="s">${c.configured ? "已配置 · 真实推送" : "未配置 · 演示模式"}</div></div>
      <div class="switch ${c.enabled ? "on" : ""}" data-ch="${c.key}"></div>
    </div>`).join("");
  $$("#chanList .switch").forEach(s => s.addEventListener("click", () => toggleChannel(s.dataset.ch, s)));
}
async function toggleChannel(key, el) {
  const cur = state.channels[key] && state.channels[key].enabled;
  const next = !cur;
  try {
    await api("/api/channels/" + key, { method: "POST", body: JSON.stringify({ enabled: next }) });
    el.classList.toggle("on", next);
    state.channels[key].enabled = next;
    const on = Object.values(state.channels).filter(c => c.enabled).length;
    $("#notifyStat").textContent = `通知通道 ${on}/3`;
    $("#notifyDot").classList.toggle("off", on === 0);
  } catch (e) { showResultModal("⚠️", "切换失败", e.message, "error"); }
}

/* ---------- 巡检播报历史 ---------- */
function fmtTime(iso) {
  if (!iso) return "—";
  try {
    const dt = new Date(iso);
    const p = n => String(n).padStart(2, "0");
    return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`;
  } catch { return iso; }
}
async function loadInspections() {
  const el = $("#inspectHistory");
  if (!el) return;
  try {
    const d = await api("/api/inspections?limit=30");
    const list = d.inspections || [];
    if (!list.length) {
      el.innerHTML = `<div class="empty">暂无巡检记录。点击「模拟 Qoder 定时巡检」或「模拟 Webhook 回调」触发一次，播报会留存在这里。</div>`;
      return;
    }
    el.innerHTML = list.map((it, i) => {
      const hasBrief = !!it.brief;
      const body = hasBrief ? it.brief : (it.summary || "");
      const time = fmtTime(it.ran_at);
      const pushTags = (it.notify || []).map(n => {
        const icon = { wechat: "💬", dingtalk: "🔔", feishu: "📨" }[n.channel] || "📡";
        const st = n.mode === "live" ? "已发送" : n.mode === "error" ? "失败" : "演示";
        return `<span class="tag">${icon} ${esc(n.name)}·${st}</span>`;
      }).join(" ") || `<span class="tag">未推送（通道未启用）</span>`;
      return `<div class="insp-item">
        <div class="insp-head" data-idx="${i}">
          <div>
            <div class="insp-time">${time}</div>
            <div class="insp-meta">风险 ${it.risks_count} 项 · ${pushTags}</div>
          </div>
          <div class="insp-actions">
            <span class="insp-badge ${hasBrief ? "brief" : "basic"}">${hasBrief ? "🤖 智能播报" : "📋 基础清单"}</span>
            <button class="icon-del" data-del="${it.id}" title="删除该播报">🗑</button>
          </div>
        </div>
        <div class="insp-body" id="inspBody${i}" style="display:none">
          <div class="insp-brief md">${md(body)}</div>
        </div>
      </div>`;
    }).join("");
    $$("#inspectHistory .insp-head").forEach(h => h.addEventListener("click", () => {
      const b = $("#inspBody" + h.dataset.idx);
      if (b) b.style.display = b.style.display === "none" ? "block" : "none";
    }));
    $$("#inspectHistory .icon-del").forEach(b => b.addEventListener("click", (ev) => {
      ev.stopPropagation();  // 避免触发展开/收起
      deleteInspection(b.dataset.del);
    }));
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

async function deleteInspection(id) {
  const ok = await confirmDialog({
    title: "删除巡检播报",
    message: "确认删除这条巡检播报历史吗？删除后无法恢复。",
    detail: "播报 ID：" + id,
    confirmText: "删除",
  });
  if (!ok) return;
  try { await api("/api/inspections/" + id, { method: "DELETE" }); await loadInspections(); showResultModal("✓", "删除成功", "巡检播报已删除", "success"); }
  catch (e) { showResultModal("⚠️", "删除失败", e.message, "error"); }
}

/* ---------- 巡检 / Webhook 触发 ---------- */
async function runInspect(endpoint, label) {
  try {
    const r = await api(endpoint, { method: "POST" });
    if (r && r.skipped) {
      // 被冷却/去重拦截：刚跑过，提示即可，不重复推送
      inlineNotify("⏳", label + "未重复执行", r.message || "刚刚已巡检过，请稍后再试", "info");
      await loadInspections();
      return;
    }
    inlineNotify("⚡", label, "巡检完成，已依据启用通道推送", "ok");
    (r.notify || []).forEach(n => {
      const icon = { wechat: "💬", dingtalk: "🔔", feishu: "📨" }[n.channel] || "📡";
      const desc = n.mode === "live" ? "已真实发送" : n.mode === "error" ? ("发送失败：" + (n.error || "")) : "演示模式（未配置真实 Webhook）";
      inlineNotify(icon, `${n.name} 提醒`, desc, "err");
    });
    await loadAll();
    await loadInspections();
  } catch (e) { inlineNotify("⚠️", label + "失败", e.message, "err"); }
}
function simulateWebhook() { return runInspect("/api/webhook", "Qoder Webhook 回调"); }
function runInspectNow() {
  const st = $("#inspectNowStatus");
  if (st) st.textContent = "巡检中…";
  return runInspect("/api/inspect", "立即巡检")
    .finally(() => { if (st) st.textContent = ""; });
}

/* ---------- 政策问答 ---------- */
async function sendQA() {
  const inp = $("#qaInput"); const q = inp.value.trim();
  if (!q) return;
  const chat = $("#qaChat");
  chat.insertAdjacentHTML("beforeend", `<div class="msg me">${esc(q)}</div>`);
  inp.value = "";
  inp.disabled = true;
  $("#btnQa").disabled = true;
  // loading 占位
  const lid = "loading_" + Date.now();
  chat.insertAdjacentHTML("beforeend", `<div id="${lid}" class="msg ai"><span class="typing">🤖 正在查询知识库与 Qoder Agent，请稍候...</span></div>`);
  chat.scrollTop = chat.scrollHeight;
  try {
    const hit = await api("/api/qa", { method: "POST", body: JSON.stringify({ q }) });
    const loading = $(`#${lid}`);
    if (loading) loading.remove();
    chat.insertAdjacentHTML("beforeend", `<div class="msg ai"><div class="md">${md(hit.a)}</div><div class="src">📎 来源：${esc(hit.src)}</div></div>`);
  } catch (e) {
    const loading = $(`#${lid}`);
    if (loading) loading.innerHTML = `<span style="color:var(--danger)">⚠️ 请求失败：${esc(e.message)}</span>`;
    else
      chat.insertAdjacentHTML("beforeend", `<div class="msg ai" style="color:var(--danger)">⚠️ 问答请求失败：${esc(e.message)}</div>`);
  } finally {
    inp.disabled = false;
    $("#btnQa").disabled = false;
    inp.focus();
    chat.scrollTop = chat.scrollHeight;
  }
}

/* ---------- 绑定 & 启动 ---------- */
function bind() {
  $("#btnDiag").addEventListener("click", () => withBtn($("#btnDiag"), genDiagnosis));
  $("#matPlatform").addEventListener("change", renderMatFields);
  $("#btnMat").addEventListener("click", () => withBtn($("#btnMat"), precheckMaterial));
  $("#btnPrAdd").addEventListener("click", () => withBtn($("#btnPrAdd"), addRecord));
  $("#btnWebhook").addEventListener("click", () => withBtn($("#btnWebhook"), simulateWebhook));
  $("#btnSimScan").addEventListener("click", () => withBtn($("#btnSimScan"), () => runInspect("/api/inspect", "Qoder 定时巡检")));
  $("#btnRefreshInspect").addEventListener("click", () => withBtn($("#btnRefreshInspect"), () => loadInspections()));
  $("#btnRunInspectNow").addEventListener("click", () => withBtn($("#btnRunInspectNow"), runInspectNow));
  $("#btnQa").addEventListener("click", sendQA);
  $("#qaInput").addEventListener("keydown", e => { if (e.key === "Enter") sendQA(); });
  $$(".hint .tag").forEach(t => t.addEventListener("click", () => { $("#qaInput").value = t.dataset.q; sendQA(); }));
  // 区域导出（诊断 / 预审 / 进度看板 / 风险清单）
  $$(".xbtn").forEach(b => b.addEventListener("click", () => {
    const fn = () => exportContent(b.dataset.x, b.dataset.target, b.dataset.title);
    withBtn(b, fn);
  }));
  // 参数设置
  $("#btnSaveSettings").addEventListener("click", () => withBtn($("#btnSaveSettings"), async () => {
    const patBefore = ($("#set_QODER_PAT") || {}).value || "";   // 保存前表单里的 PAT（脱敏或新值）
    try {
      await saveSettings();
      showResultModal("✓", "设置已保存", "已写入本地数据库，优先级高于环境变量", "success");
      // 改了 QODER_PAT → 切换了数据租户，内存里的旧数据需刷新页面才能加载新账号数据
      if (patBefore !== _loadedQoderPat) {
        inlineNotify("🔄", "已切换 QODER_PAT", "正在刷新以加载该账号数据…", "info");
        setTimeout(() => location.reload(), 900);
      }
    }
    catch (e) { showResultModal("⚠️", "保存失败", e.message, "error"); }
  }));
  $("#btnResetSettings").addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "清空页面设置",
      message: "确认清空所有页面设置吗？系统将回退到环境变量 / .env 的默认值（密钥需重新填入）。",
      confirmText: "清空并恢复默认",
      type: "warn",
      icon: "♻️",
    });
    if (!ok) return;
    SET_FIELDS.forEach(k => { const el = $("#set_" + k); if (el) el.value = ""; });
    withBtn($("#btnResetSettings"), async () => {
      try {
        await api("/api/settings", { method: "POST", body: JSON.stringify({ reset: true }) });
        localStorage.removeItem("mma_settings");
        showResultModal("✓", "已恢复默认", "页面设置已清空，回退到环境变量", "success");
        loadSettings();
      } catch (e) { showResultModal("⚠️", "操作失败", e.message, "error"); }
    });
  });
  // 定时巡检开关：切换即时生效并持久化（含右上角状态同步）
  $("#setScheduler").addEventListener("change", async () => {
    try {
      const r = await toggleScheduler();
      const h = r.hour || 9;
      inlineNotify("⚡", r.enabled ? "定时巡检已开启" : "定时巡检已关闭",
        r.enabled ? `每日 ${String(h).padStart(2,"0")}:00 自动巡检生效` : "仅手动 / Webhook 触发");
    } catch (e) {
      $("#setScheduler").checked = !$("#setScheduler").checked; // 失败回滚
      updateSchedChip(!$("#setScheduler").checked, parseInt($("#setSchedHour").value) || 9);
      showResultModal("⚠️", "切换失败", e.message, "error");
    }
  });
  // 巡检时间变更即时保存
  $("#setSchedHour").addEventListener("change", async () => {
    if (!$("#setScheduler").checked) return;  // 关闭时不发请求
    try { await toggleScheduler(); }
    catch (e) { showResultModal("⚠️", "设置失败", e.message, "error"); }
  });
  // Agent 活动视图：复制 Webhook 地址 / 签名自测 / 刷新
  $("#btnCopyWh").addEventListener("click", () => copyWh());
  $("#btnWhSelftest").addEventListener("click", () => withBtn($("#btnWhSelftest"), selftestWh));
  $("#btnRefreshLog").addEventListener("click", () => withBtn($("#btnRefreshLog"), () => loadAgentLog(true)));
  // Webhook 端点管理
  $("#btnCreateWhEp").addEventListener("click", () => withBtn($("#btnCreateWhEp"), createWhEndpoint));
  $("#btnRefreshWhEps").addEventListener("click", () => withBtn($("#btnRefreshWhEps"), loadWhEndpoints));
  // 访问密码登录
  $("#btnLogin").addEventListener("click", doLogin);
  $("#loginPwd").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
  $("#btnLogout").addEventListener("click", doLogout);
  $("#btnConfigHelp").addEventListener("click", openConfigHelp);
  const btnLoginCfg = $("#btnLoginConfigHelp");
  if (btnLoginCfg) btnLoginCfg.addEventListener("click", openConfigHelp);
}

async function init() {
  tickClock(); setInterval(tickClock, 1000);
  renderDiagPlatforms(); fillMatPlatform(); fillPrPlatform(); renderMatFields();
  bind();
  // 鉴权预检：若系统要求密码且本地无 token → 显示登录层，暂不加载数据
  try {
    const h = await fetch("/api/health").then(r => r.json());
    if (h.auth_required && !getToken()) {
      showLogin();
      return;
    }
  } catch (e) { /* 健康检查失败按无鉴权处理，继续加载 */ }
  bootData();
}

function bootData() {
  loadAll();
  loadSettings();
}
document.addEventListener("DOMContentLoaded", init);

/* ---------- 区域导出：PDF / Markdown / HTML ---------- */
// 导出文档专用浅色样式（内联进导出的 HTML/PDF，离线自包含，独立于深空主题）
const EXPORT_CSS = `
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; max-width: 900px; margin: 24px auto; padding: 0 24px; background: #fff; color: #1a2233; line-height: 1.7; }
.export-doc { background: #fff; color: #1a2233; }
.export-doc h1 { font-size: 23px; border-bottom: 2px solid #2b6cb0; padding-bottom: 8px; margin-bottom: 6px; color: #1a2233; }
.export-doc .export-meta { color: #7a869a; font-size: 12px; margin-bottom: 20px; }
.export-doc h2 { font-size: 18px; margin: 22px 0 8px; color: #1a2233; }
.export-doc h3 { font-size: 15px; margin: 16px 0 6px; color: #1a2233; }
.export-doc h4 { font-size: 13px; margin: 12px 0 4px; color: #2b6cb0; }
.export-doc p { margin: 8px 0; color: #2a3346; }
.export-doc table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13px; }
.export-doc th, .export-doc td { border: 1px solid #cbd5e0; padding: 8px 11px; text-align: left; vertical-align: top; color: #1a2233; }
.export-doc th { background: #edf2f7; font-weight: 600; }
.export-doc .pill { display: inline-block; padding: 4px 11px; border-radius: 999px; font-size: 12px; margin: 3px 0; background: #edf2f7; border: 1px solid #cbd5e0; color: #1a2233; }
.export-doc .tag { display: inline-block; padding: 2px 8px; margin: 2px; border-radius: 5px; background: #edf2f7; border: 1px solid #cbd5e0; font-size: 12px; color: #1a2233; }
.export-doc .tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.export-doc .tbl th, .export-doc .tbl td { border: 1px solid #cbd5e0; padding: 8px 11px; text-align: left; color: #1a2233; }
.export-doc .tbl th { background: #edf2f7; }
.export-doc .md { font-size: 14px; line-height: 1.75; color: #1a2233; }
.export-doc .md h1, .export-doc .md h2, .export-doc .md h3 { color: #1a2233; }
.export-doc .md code { background: #f0f3f8; border: 1px solid #d0d7e2; border-radius: 4px; padding: 1px 5px; font-size: 12px; color: #1a2233; }
.export-doc .md blockquote { border-left: 3px solid #2b6cb0; margin: 10px 0; padding: 6px 14px; background: #f7fafc; color: #444; }
.export-doc .md pre { background: #f5f7fa; border: 1px solid #d0d7e2; border-radius: 8px; padding: 12px 14px; overflow-x: auto; }
.export-doc .md pre code { background: none; border: none; padding: 0; }
.export-doc .src { margin-top: 10px; font-size: 11px; color: #7a869a; border-top: 1px dashed #cbd5e0; padding-top: 6px; }
.export-doc .ring .num { font-weight: 700; color: #1a2233; }
`;
// 克隆目标区域并去掉交互按钮（如进度看板的删除按钮），空/占位内容返回 null
function getCleanClone(targetId) {
  const el = document.getElementById(targetId);
  if (!el) return null;
  // 有 .empty 占位元素 = 还没生成真实内容
  if (el.querySelector(".empty")) return null;
  const clone = el.cloneNode(true);
  clone.querySelectorAll("button").forEach(b => b.remove());
  if (!clone.textContent.trim()) return null;
  return clone;
}
// 轻量 DOM→Markdown 转换器（覆盖本项目渲染用到的元素）
function nodeToMd(node) {
  if (node.nodeType === 3) {
    const t = node.textContent.replace(/\s+/g, " ").trim();
    return t ? t + " " : "";
  }
  if (node.nodeType !== 1) return "";
  const tag = node.tagName.toLowerCase();
  const cls = (node.className || "").toString();
  const inner = () => Array.from(node.childNodes).map(nodeToMd).join("");
  // 进度看板 .rec 卡片 → 转为结构化 Markdown（表格行风格）
  if (cls.includes("rec")) {
    const nameEl = node.querySelector(".plat-tag");
    const stageEl = node.querySelector(".pill");
    const ringEl = node.querySelector(".ring .num");
    const infoDiv = node.querySelector("div[style*='line-height']");
    const name = nameEl ? nameEl.textContent.trim() : "";
    const stage = stageEl ? stageEl.textContent.trim() : "";
    const pct = ringEl ? ringEl.textContent.trim() : "";
    let lines = "### " + name + "\n\n";
    lines += "| 字段 | 内容 |\n| --- | --- |\n";
    lines += "| 阶段 | " + stage + (pct ? " (" + pct + ")" : "") + " |\n";
    if (infoDiv) {
      infoDiv.innerHTML.split(/<br\s*\/?>/i).forEach(s => {
        const clean = s.replace(/<[^>]+>/g, "").trim();
        if (clean && clean !== "—") lines += "| " + clean.replace(/：/, "| ") + " |\n";
        else if (clean === "—") lines += "| " + s.replace(/<[^>]+>/g, "").trim().split("：")[0] + " | — |\n";
      });
    }
    return lines + "\n";
  }
  switch (tag) {
    case "h1": return "\n# " + node.textContent.trim() + "\n\n";
    case "h2": return "\n## " + node.textContent.trim() + "\n\n";
    case "h3": return "\n### " + node.textContent.trim() + "\n\n";
    case "h4": return "\n#### " + node.textContent.trim() + "\n\n";
    case "p": return "\n" + inner().trim() + "\n\n";
    case "br": return "\n";
    case "hr": return "\n---\n\n";
    case "blockquote": return "\n> " + inner().replace(/\n/g, "\n> ").trim() + "\n\n";
    case "ul": return "\n" + Array.from(node.children).map(li => "- " + nodeToMd(li).trim()).join("\n") + "\n\n";
    case "ol": return "\n" + Array.from(node.children).map((li, i) => (i + 1) + ". " + nodeToMd(li).trim()).join("\n") + "\n\n";
    case "li": return inner();
    case "table": return tableToMd(node) + "\n\n";
    case "code": return "`" + node.textContent.trim() + "`";
    case "strong": case "b": return "**" + inner().trim() + "**";
    case "em": case "i": return "*" + inner().trim() + "*";
    case "a": return "[" + inner().trim() + "](" + (node.getAttribute("href") || "") + ")";
    default: return inner();
  }
}
function tableToMd(table) {
  const rows = Array.from(table.querySelectorAll("tr"));
  if (!rows.length) return "";
  const lines = rows.map(tr => "| " + Array.from(tr.children).map(c => c.textContent.trim().replace(/\|/g, "\\|")).join(" | ") + " |");
  const ncol = rows[0].children.length;
  const sep = "| " + Array(ncol).fill("---").join(" | ") + " |";
  return [lines[0], sep, ...lines.slice(1)].join("\n");
}
function htmlToMarkdown(root) {
  const md = Array.from(root.childNodes).map(nodeToMd).join("");
  return md.replace(/\n{3,}/g, "\n\n").replace(/[ \t]+\n/g, "\n").trim() + "\n";
}
function buildDocHtml(title, bodyHtml) {
  return '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">' +
    '<title>' + esc(title) + '</title><style>' + EXPORT_CSS + '</style></head><body>' +
    '<div class="export-doc"><h1>' + esc(title) + '</h1>' +
    '<div class="export-meta">生成时间：' + new Date().toLocaleString("zh-CN") + '</div>' +
    bodyHtml + '</div></body></html>';
}
function download(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 120);
}
function exportContent(format, targetId, title) {
  // 实时数据兜底：即便 DOM 渲染异常，空数据也提示，绝不静默导出空文件
  if (targetId === "prList" && (!state.records || !state.records.length)) {
    inlineNotify("⚠️", "暂无可导出内容", "进度看板暂无备案记录，请先在上方登记", "info"); return;
  }
  if (targetId === "riskList" && (!state.risks || !state.risks.length)) {
    inlineNotify("⚠️", "暂无可导出内容", "当前无风险项，无需导出", "info"); return;
  }
  const clone = getCleanClone(targetId);
  if (!clone) { inlineNotify("⚠️", "暂无可导出内容", "请先生成或加载对应数据", "info"); return; }
  const stamp = new Date().toISOString().slice(0, 10);
  const fname = title + "_" + stamp;
  if (format === "md") {
    const mdText = "# " + title + "\n\n> 生成时间：" + new Date().toLocaleString("zh-CN") + "\n\n" + htmlToMarkdown(clone);
    download(fname + ".md", mdText, "text/markdown;charset=utf-8");
    inlineNotify("✓", "已导出 Markdown", fname + ".md", "ok");
    return;
  }
  const doc = buildDocHtml(title, clone.innerHTML);
  if (format === "html") {
    download(fname + ".html", doc, "text/html;charset=utf-8");
    inlineNotify("✓", "已导出 HTML", fname + ".html", "ok");
  } else if (format === "pdf") {
    const w = window.open("", "_blank");
    if (!w) { inlineNotify("⚠️", "导出失败", "浏览器拦截了弹出窗口，请允许弹窗后重试", "err"); return; }
    w.document.open(); w.document.write(doc); w.document.close();
    w.addEventListener("load", () => { setTimeout(() => { w.focus(); w.print(); }, 350); });
    inlineNotify("🖨️", "已唤起打印", "在打印对话框中选择「另存为 PDF」", "info");
  }
}

/* ---------- 参数设置（页面设置 > 环境变量 > 默认值） ---------- */
const SET_FIELDS = [
  "QODER_PAT", "QODER_API_BASE", "QODER_MODEL", "QODER_WEBHOOK_SIGNING_SECRET",
  "WECHAT_WEBHOOK_URL", "DINGTALK_WEBHOOK_URL", "DINGTALK_SIGN_SECRET",
  "FEISHU_WEBHOOK_URL", "FEISHU_SIGN_SECRET",
];
// 参数设置加载时的 QODER_PAT 基线（脱敏值）：用于判断保存时是否切换了 PAT，
// 切了 PAT 即切换数据租户，需刷新页面重取该账号数据；未动则无需刷新。
let _loadedQoderPat = "";
// 访问密码 ACCESS_PASSWORD 不纳入普通设置批量保存：仅管理员可改，由 saveSettings 单独处理
function getIsAdmin() { try { return localStorage.getItem("mma_is_admin") === "true"; } catch (e) { return false; } }
function renderAccessPwdField(masked) {
  const wrap = document.getElementById("set_ACCESS_PASSWORD_wrap");
  const input = document.getElementById("set_ACCESS_PASSWORD");
  const info = document.getElementById("set_ACCESS_PASSWORD_info");
  if (!wrap || !input || !info) return;
  if (getIsAdmin()) {
    input.style.display = "";
    input.disabled = false;
    input.value = "";  // 不预填旧值，避免误提交掩码
    info.textContent = "你拥有管理员权限，可修改访问密码（留空 = 关闭鉴权）。保存后立即生效。";
  } else {
    input.style.display = "none";
    const set = !!(masked && String(masked).trim() !== "");
    info.textContent = set ? "● 已设置（访客无修改权限，需用环境变量 MASTER_PASSWORD 以管理员身份登录后才能修改）" : "未设置（系统当前开放访问）";
  }
}
function _lsGet(k, d) { try { return (JSON.parse(localStorage.getItem("mma_settings") || "{}")[k]) ?? d; } catch (e) { return d; } }
function loadSettings() {
  api("/api/settings").then(s => {
    SET_FIELDS.forEach(k => { const el = $("#set_" + k); if (el && s[k] != null) el.value = s[k]; });
    _loadedQoderPat = (s.QODER_PAT || "").trim();   // 记录本次加载的 PAT 基线（脱敏值）
    renderAccessPwdField(s.ACCESS_PASSWORD);
    const rb = $("#btnResetSettings"); if (rb) rb.style.display = getIsAdmin() ? "" : "none";
    try { localStorage.setItem("mma_settings", JSON.stringify(s)); } catch (e) {}
  }).catch(() => {
    SET_FIELDS.forEach(k => { const el = $("#set_" + k); if (el) el.value = _lsGet(k, ""); });
  });
  // 加载调度状态（开关 + 小时 + 右上角 chip + 风险视图内部 Deployment 状态）
  api("/api/scheduler").then(r => {
    $("#setScheduler").checked = !!r.enabled;
    $("#setSchedHour").value = r.hour || 9;
    $("#schedHourRow").style.display = r.enabled ? "" : "none";
    updateSchedChip(r.enabled, r.hour || 9);
    renderDepMini(r);
  }).catch(() => {
    $("#setScheduler").checked = localStorage.getItem("mma_sched") === "true";
    updateSchedChip($("#setScheduler").checked, parseInt($("#setSchedHour").value) || 9);
  });
}
/** 更新右上角巡检状态 chip */
function updateSchedChip(enabled, hour) {
  const chip = $("#schedChip");
  if (!chip) return;
  if (enabled) {
    chip.style.display = "";
    $("#schedDot").classList.remove("off");
    $("#schedStat").textContent = "巡检 " + String(hour).padStart(2, "0") + ":00";
  } else {
    chip.style.display = "none";
  }
}
function saveSettings() {
  const data = {};
  SET_FIELDS.forEach(k => { const el = $("#set_" + k); if (el) data[k] = el.value.trim(); });
  // 仅管理员可提交访问密码（留空 = 清空/关闭鉴权）
  if (getIsAdmin()) {
    const ap = $("#set_ACCESS_PASSWORD");
    if (ap) data.ACCESS_PASSWORD = ap.value;
  }
  try {
    const ls = JSON.parse(localStorage.getItem("mma_settings") || "{}");
    Object.assign(ls, data); localStorage.setItem("mma_settings", JSON.stringify(ls));
  } catch (e) {}
  return api("/api/settings", { method: "POST", body: JSON.stringify({ settings: data }) });
}
function toggleScheduler() {
  const on = $("#setScheduler").checked;
  const hour = parseInt($("#setSchedHour").value) || 9;
  localStorage.setItem("mma_sched", on ? "true" : "false");
  $("#schedHourRow").style.display = on ? "" : "none";
  updateSchedChip(on, hour);
  return api("/api/scheduler", { method: "POST", body: JSON.stringify({ enabled: on, hour }) });
}

/* ---------- Agent 活动审计（Deployment + Webhook 回传） ---------- */
function renderDepMini(r) {
  const el = $("#depMini");
  if (!el) return;
  if (!r.deployment_id) { el.textContent = "尚未创建 Qoder Deployment（开启上方开关即自动创建）。"; return; }
  const st = r.deployment_status === "active" ? "运行中" : (r.deployment_status || "未知");
  const next = (r.next_runs && r.next_runs[0]) ? new Date(r.next_runs[0]).toLocaleString("zh-CN") : "—";
  el.innerHTML = `Deployment <span class="ev-type">${esc(r.deployment_id)}</span> · 状态 <b>${st}</b> · 下次运行 ${next}`;
}

function renderDepStatus(dep) {
  const el = $("#depStatus");
  if (!el) return;
  if (!dep || !dep.id) {
    el.innerHTML = `<div class="dep-row"><span class="k">Deployment</span><span class="v">尚未创建（在「风险提醒中心」开启自动巡检即自动创建）</span></div>`;
    return;
  }
  const stMap = { active: "运行中", paused: "已暂停" };
  const st = stMap[dep.status] || (dep.status || "未知");
  const next = (dep.next_runs && dep.next_runs[0]) ? new Date(dep.next_runs[0]).toLocaleString("zh-CN") : "—";
  el.innerHTML = `
    <div class="dep-row"><span class="k">Deployment ID</span><span class="v mono">${esc(dep.id)}</span></div>
    <div class="dep-row"><span class="k">状态</span><span class="v"><b>${st}</b></span></div>
    <div class="dep-row"><span class="k">绑定 Agent</span><span class="v">filing-progress-agent</span></div>
    <div class="dep-row"><span class="k">调度</span><span class="v">每日 ${String(dep.hour || 9).padStart(2, "0")}:00 (Asia/Shanghai)</span></div>
    <div class="dep-row"><span class="k">下次运行</span><span class="v">${next}</span></div>`;
}

function renderAgentLog(events) {
  const el = $("#agentLog");
  if (!el) return;
  if (!events || !events.length) {
    el.innerHTML = `<div class="empty">暂无事件。开启自动巡检或点击「签名回环自测」后会在此留痕。</div>`;
    return;
  }
  const icon = t => {
    if (t && t.startsWith("agent.")) return "🤖";
    if (t && t.startsWith("session.thread")) return "🧵";
    if (t && t.startsWith("session.")) return "💬";
    if (t === "webhook.test") return "🧪";
    return "📡";
  };
  el.innerHTML = events.map(e => {
    const time = e.ts ? new Date(e.ts).toLocaleString("zh-CN") : "";
    const role = e.agent_role ? `<span class="ev-role">${esc(e.agent_role)}</span>` : "";
    const rid = e.resource_id ? `<span class="s">资源：${esc(e.resource_id)}</span>` : "";
    return `<div class="agent-ev">
      <div class="ico">${icon(e.event_type)}</div>
      <div class="meta">
        <div class="t"><span class="ev-type">${esc(e.event_type || "—")}</span>${role}</div>
        ${rid}
      </div>
      <div class="time">${time}</div>
      <button class="icon-del ev-del" data-del="${e.id}" title="删除该事件">🗑</button>
    </div>`;
  }).join("");
  $$("#agentLog .icon-del").forEach(b => b.addEventListener("click", () => deleteAgentEvent(b.dataset.del)));
}

async function deleteAgentEvent(id) {
  const ok = await confirmDialog({
    title: "删除事件记录",
    message: "确认删除这条事件时间线记录吗？删除后无法恢复。",
    detail: "事件 ID：" + id,
    confirmText: "删除",
  });
  if (!ok) return;
  try { await api("/api/agent-events/" + id, { method: "DELETE" }); await loadAgentLog(false); showResultModal("✓", "删除成功", "事件记录已删除", "success"); }
  catch (e) { showResultModal("⚠️", "删除失败", e.message, "error"); }
}

async function loadAgentLog(showToast) {
  try {
    const [events, dep] = await Promise.all([
      api("/api/agent-events?limit=50"),
      api("/api/deployments"),
    ]);
    renderAgentLog(events);
    renderDepStatus(dep);
    const wu = $("#whUrl");
    if (wu && !wu.value) wu.value = location.origin + "/api/qoder-webhook";
    // 加载 Webhook 端点列表
    await loadWhEndpoints();
    if (showToast) inlineNotify("🔄", "已刷新", "事件时间线已更新", "ok");
  } catch (e) {
    inlineNotify("⚠️", "加载失败", "无法连接后端：" + e.message, "err");
  }
}

function copyWh() {
  const wu = $("#whUrl");
  if (!wu) return;
  if (!wu.value) wu.value = location.origin + "/api/qoder-webhook";
  navigator.clipboard.writeText(wu.value).then(
    () => inlineNotify("📋", "已复制", "Webhook 接收地址已复制到剪贴板", "ok"),
    () => inlineNotify("⚠️", "复制失败", "请手动选择复制", "err")
  );
}

async function selftestWh() {
  try {
    const r = await api("/api/qoder-webhook/selftest", { method: "POST" });
    if (r.source) {
      inlineNotify("✅", "签名回环通过", "验签 + 事件处理闭环正常，巡检已触发", "ok");
    } else {
      inlineNotify("✅", "事件已记录", (r.event || "事件") + " 已写入审计时间线", "ok");
    }
    await loadAgentLog();
  } catch (e) {
    inlineNotify("⚠️", "自测失败", e.message, "err");
  }
}

/* ---------- Webhook 端点管理（Qoder webhook_endpoints CRUD） ---------- */
const WH_EP_EVENTS = []; // 由 loadWhEndpoints 填充可用事件列表

function renderWhEpEvents() {
  const box = $("#whEpEvents");
  if (!box) return;
  box.innerHTML = WH_EP_EVENTS.map(e => {
    const label = e.replace(/\./g, " → ").replace(/(session|agent|thread|webhook)/g, m =>
      ({ session: "会话", agent: "智能体", thread: "线程", webhook: "Webhook" }[m] || m));
    return `<span class="chip" data-ev="${esc(e)}">${esc(e)}</span>`;
  }).join("");
  // 默认选中 session.status_idled
  $$("#whEpEvents .chip").forEach(c => {
    c.classList.toggle("on", c.dataset.ev === "session.status_idled");
    c.addEventListener("click", () => c.classList.toggle("on"));
  });
}

function renderWhEpList(eps) {
  const el = $("#whEpList");
  if (!el) return;
  if (!eps || !eps.length) {
    el.innerHTML = `<div class="empty" style="margin-top:8px">暂无端点。在上方填写信息后点击「创建端点」。</div>`;
    return;
  }
  el.innerHTML = `<table class="tbl" style="font-size:12px">
    <thead><tr><th>URL</th><th>订阅事件</th><th>状态</th><th>操作</th></tr></thead><tbody>` +
    eps.map(ep => `<tr>
      <td style="word-break:break-all;max-width:260px">${esc(ep.url || "—")}</td>
      <td>${(ep.events || []).map(e => `<span class="tag">${esc(e)}</span>`).join(" ")}</td>
      <td>${ep.active === false ? "已停用" : "生效中"}</td>
      <td><button class="btn btn-ghost" data-del-ep="${esc(ep.id || "")}" style="padding:4px 10px;font-size:11px">删除</button></td>
    </tr>`).join("") + `</tbody></table>`;
  $$("#whEpList [data-del-ep]").forEach(b => b.addEventListener("click", async () => {
    const epId = b.dataset.delEp;
    if (!epId) return;
    const ok = await confirmDialog({
      title: "删除 Webhook 端点",
      message: "确认删除该 Webhook 端点吗？删除后 Qoder 将不再向该地址推送事件。",
      detail: "端点 ID：" + epId,
    });
    if (!ok) return;
    deleteWhEndpoint(epId);
  }));
}

async function loadWhEndpoints(showToast) {
  try {
    const r = await api("/api/webhook-endpoints");
    // 缓存可用事件类型
    if (r.available_events && r.available_events.length) {
      WH_EP_EVENTS.length = 0;
      WH_EP_EVENTS.push(...r.available_events);
      renderWhEpEvents();
    }
    renderWhEpList(r.endpoints || []);
    // 预填 URL
    const urlInput = $("#whEpUrl");
    if (urlInput && !urlInput.value) urlInput.value = location.origin + "/api/qoder-webhook";
    if (showToast) inlineNotify("🔄", "已刷新", "Webhook 端点列表已更新", "ok");
  } catch (e) {
    inlineNotify("⚠️", "加载失败", e.message, "err");
  }
}

async function createWhEndpoint() {
  const url = ($("#whEpUrl") || {}).value.trim();
  if (!url) { showResultModal("⚠️", "请填地址", "回调 URL 不能为空", "info"); return; }
  const events = $$("#whEpEvents .chip.on").map(c => c.dataset.ev);
  if (!events.length) { showResultModal("⚠️", "请选事件", "至少选择一个订阅事件", "info"); return; }
  const desc = ($("#whEpDesc") || {}).value.trim() || undefined;
  try {
    const ep = await api("/api/webhook-endpoints", {
      method: "POST",
      body: JSON.stringify({ url, events, description: desc }),
    });
    inlineNotify("✅", "端点已创建", `ID: ${ep.id || "?"} → ${url}`, "ok");
    // 一次性展示 Qoder 返回的 signing_secret（仅此一次，已自动存入参数设置）
    const once = $("#whEpSecretOnce");
    if (once && ep.signing_secret) {
      once.style.display = "";
      once.innerHTML = `<div class="pill warn" style="display:block;margin-bottom:10px;white-space:normal;line-height:1.5">
        ⚠️ <b>签名密钥（仅显示这一次）</b>：${esc(ep.signing_secret)}
        <div style="font-weight:400;opacity:.85;margin-top:4px">已自动写入「参数设置 → QODER_WEBHOOK_SIGNING_SECRET」，Qoder 后续投递事件将用此密钥签名，本系统据此验签。请妥善保存；若丢失只能删除重建端点。</div>
      </div>`;
    } else if (once) {
      once.style.display = "none";
      once.innerHTML = "";
    }
    await loadWhEndpoints();
    // 同步刷新审计时间线
    await loadAgentLog();
  } catch (e) {
    inlineNotify("⚠️", "创建失败", e.message, "err");
  }
}

async function deleteWhEndpoint(epId) {
  try {
    await api(`/api/webhook-endpoints/${encodeURIComponent(epId)}`, { method: "DELETE" });
    inlineNotify("🗑️", "已删除", `端点 ${epId} 已从 Qoder 移除`, "ok");
    await loadWhEndpoints();
    await loadAgentLog();
  } catch (e) {
    inlineNotify("⚠️", "删除失败", e.message, "err");
  }
}
