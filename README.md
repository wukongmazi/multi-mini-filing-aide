# 多平台小程序备案 AI 中枢

面向运营的多平台（微信 / 支付宝 / 抖音 / 快手 / 百度）小程序备案合规 WEB 工具。
前端为深空数据中枢风格（霓虹渐变 + 玻璃拟态 + 动态网格），后端为 FastAPI 真实服务
（SQLite 持久化 + 规则引擎 + 可选 Qoder Cloud Agents 多 Agent 推理 + 三通道通知 + 定时巡检）。

## 解决什么问题

运营同学可能经常要同时盯 **微信 / 支付宝 / 抖音 / 快手 / 百度** 五个平台的小程序备案，但现实中这些工作大多散落在表格、群聊截图里：

- **政策分散、口径不一**：各平台备案规则、材料清单、驳回高频点各不相同，靠人脑记忆或临时搜文档，容易漏项、踩坑。
- **材料驳回风险靠事后才发现**：提交前没有系统化的预审，被退回才补，既耽误上线又消耗对接精力。
- **巡检全靠人工定时翻看**：每天 / 每周手动核对进度、找异常，重复枯燥且容易漏推、漏看。
- **进度与风险没有统一视图**：谁家卡在审核中、哪家临期、哪条记录有风险，缺少一处可看、可追溯的看板。
- **问答零散、答案无沉淀**：政策类问题零散在聊天里，换个场景又得重新问一遍。

本项目就是把这些痛点收拢到一个 WEB 工具里：用**规则引擎**做五平台诊断与材料预审、用**知识库**沉淀政策问答、用**定时巡检 + 三通道推送**把风险主动送到群里、用**进度看板**把多平台备案状态一屏掌控；可选接入 Qoder 多 Agent 做增强推理与 RAG 问答，失败时自动回退规则引擎，保证可用不崩。

## 它不是原型

- 数据走 **SQLite 真实存储**，不再存内存。
- 诊断 / 材料预审 / 风险计算 / 政策问答 全部由 **后端规则引擎** 真实计算。
- 巡检由 **后端真实触发**：云端 Qoder Deployment 每日定时（cron，Asia/Shanghai）+ Qoder Webhook 回传（主），本地兜底线程作为辅助（云端失效时于整点末尾补位）；三路共用去重，绝不同日双推。同时保留 `/api/webhook` 与 `/api/qoder-webhook` 两个入站端点（供外部 cron / QoderWake / 前端按钮调用）。
- 通知 **真实 POST** 到企业微信 / 钉钉 / 飞书群机器人（未配置时自动降级为演示模式，不报错）。
- 配置 `QODER_PAT` 后，诊断与问答会调用 **Qoder Cloud Agents** 多 Agent 增强（失败自动回退规则引擎，系统不崩溃）。

## 目录结构

```
multi-mini-filing-aide/
├─ index.html            前端入口（深空科技风）
├─ css/styles.css        设计系统（主题 / 组件 / 响应式）
├─ js/data.js            静态元数据（平台 / 字段 / 通道展示用）
├─ js/app.js             前端交互（全部 API 驱动）
├─ backend/
│  ├─ main.py            FastAPI 主程序（API + 调度 + 静态托管）
│  ├─ settings.py        配置与持久化（DB 落点解析 / 读写缓存）
│  ├─ kb.py              知识库与规则引擎（服务器端单一事实源）
│  ├─ qoder_client.py    Qoder Cloud Agents 客户端（CAS API，令牌取自参数设置/环境变量）
│  ├─ notifier.py        三通道通知（企微/钉钉/飞书 群机器人）
│  ├─ requirements.txt
├─ Dockerfile            魔搭创空间部署（端口 7860）
├─ .dockerignore
└─ .gitignore            
```

## 界面与入口

打开首页即进入「总览驾驶舱」。顶部右侧（顶栏）常驻两个按钮：

- **⚙️ 配置说明**：点击弹出「使用系统前 · 参数配置说明」弹层，分节讲解 `MASTER_PASSWORD` / `ACCESS_PASSWORD`、`QODER_PAT`、三通道 Webhook、定时巡检等参数及优先级，并给出「最小可用配置」提示。
- **⏻ 退出登录**：退出当前会话。

**未登录时也能看配置说明**：登录弹层（输入访问密码的界面）底部有一个「⚙️ 使用前的参数配置说明」链接按钮，点击同样弹出上述配置说明，方便使用者在输密码前先了解需要准备哪些参数。该弹层始终浮于登录层之上，关闭后仍回到登录界面。

## 本地运行

```bash
# 1) 安装依赖（推荐用 WorkBuddy 管理的 Python 隔离 venv）
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt

# 2) 启动（默认 7860，与魔搭一致）
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 7860
# 浏览器打开 http://127.0.0.1:7860
```

未配置任何密钥即可完整运行（演示模式）。填入 `.env` 后重启即变真实。

## 环境变量（.env）

| 变量 | 作用 | 不填的后果 |
|---|---|---|
| `MASTER_PASSWORD` | **主人（管理员）密码**。**仅来自环境变量**，前端不可见、不可改。配置后派生 admin token，可执行改访问密码、重置设置等敏感操作 | 不配置则无管理员概念；凭 `ACCESS_PASSWORD` 登录的本人可改自身密码 |
| `ACCESS_PASSWORD` | 普通访客访问密码（存数据库，参数设置页可改）。非管理员**看不到也无法修改**此项 | 不配置则无需登录即可使用 |
| `QODER_PAT` | Qoder Cloud Agents 令牌；**同时作为数据隔离标识**（`tenant_id = sha256(PAT)`），并用于巡检 / 部署调用 | 置空后工作区真正为空、回落规则引擎；切换 PAT 即切换数据视图（前端保存后自动刷新） |
| `QODER_API_BASE` | CAS API 基址（默认 `https://api.qoder.com/api/v1/cloud`） | 同左 |
| `QODER_WEBHOOK_SIGNING_SECRET` | 入站 Qoder Webhook 验签密钥（HMAC-SHA256） | 不验签 |
| `WECHAT_WEBHOOK_URL` | 企业微信群机器人地址 | 该通道演示模式 |
| `DINGTALK_WEBHOOK_URL` / `DINGTALK_SIGN_SECRET` | 钉钉机器人 + 加签 | 同上 |
| `FEISHU_WEBHOOK_URL` / `FEISHU_SIGN_SECRET` | 飞书机器人 + 签名 | 同上 |
| `PORT` | 服务端口（魔搭要求 7860） | 默认 7860 |

> 优先级约定：**「页面参数设置」>「环境变量」>「内置默认值」**（含 `QODER_PAT`）。
> 当页面把某项**显式置空保存**时，以页面为准、不再回落环境变量（例如清空 `QODER_PAT` 即真正清空，不会被 `.env` 顶回）。只有「页面从未配置」（含点「清空页面设置」真删行）才回落环境变量 / 默认值。

## 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/login` · `/api/logout` | 登录（返回 token 与 is_admin）/ 登出 |
| GET | `/api/health` | 健康检查 + qoder_mode |
| GET | `/api/overview` | 总览指标（按当前租户过滤） |
| GET | `/api/records` · POST · DELETE `/api/records/{id}` | 备案记录 CRUD（按租户隔离，删除校验归属） |
| GET | `/api/channels` · POST `/api/channels/{key}` | 通知通道状态与开关 |
| POST | `/api/diagnose` | 五平台诊断（含可选 Qoder 增强） |
| POST | `/api/precheck` | 材料驳回风险预判 |
| GET | `/api/risks` | 实时风险清单 |
| POST | `/api/inspect` | 手动触发一次巡检（计算+推送+落库，不受同日去重限制） |
| GET | `/api/inspections` · DELETE `/api/inspections/{iid}` | 巡检播报历史（按租户隔离 + 可删） |
| GET | `/api/agent-events` · DELETE `/api/agent-events/{eid}` | 事件时间线（按租户隔离 + 可删） |
| POST | `/api/qoder-webhook` · `/api/qoder-webhook/selftest` | Qoder Webhook 回传（触发巡检）/ 自测 |
| POST | `/api/webhook` | 通用入站 Webhook（外部 cron / 前端按钮） |
| POST | `/api/notify/test` | 通道自检 |
| POST | `/api/qa` | 政策问答（规则 / Qoder RAG） |
| GET/POST | `/api/settings` · GET/POST `/api/scheduler` | 参数设置 / 定时巡检开关 |
| GET | `/api/deployments` | 云端 Deployment 状态查询 |

> 鉴权：除白名单（health / login / logout / qoder-webhook / docs 等）外，所有 `/api/*`
> 需携带访问令牌。令牌支持多渠道：**`Authorization: Bearer <token>`**、自定义头 **`X-Access-Token: <token>`**、或查询参数 **`?token=`**——
> 后者用于绕过部分公网网关（如魔搭）剥离标准 `Authorization` 头导致的 401。敏感写操作（改访问密码、重置设置）额外要求 **admin token**，
> 后端会以同样的多渠道方式解析后再判定管理员身份。
> 所有响应**绝不返回 `QODER_PAT` 明文**，参数设置页仅显示脱敏值。

## 定时巡检链路（云端 Deployment 为主，本地兜底为辅）

```
Qoder Deployment（主）：cron 0 {SCHEDULER_HOUR}:00 (Asia/Shanghai)
   └─> progress-agent 运行 → session 空闲
         └─> Qoder Webhook 回传 (/api/qoder-webhook)
               └─> _try_auto_inspect(): 先占位 + 持久化「今日已巡检」→ do_inspect()
                     └─> 计算风险 + 三通道 Markdown 推送 + 落库 inspections

本地兜底线程（辅）：每 30s 轮询，仅当「目标整点小时末尾(>=45分) 且 云端当天未巡检」时触发
   └─> _try_auto_inspect()（云端通常已先占位，故本地被同日去重拦下；仅云端失效时补位一次）

手动巡检（/api/inspect）：随时可用，不受「今日去重」限制，仅受 180s 全局冷却约束
```

去重三道防线（经验证零双推）：① 当天自动巡检去重（云端/本地共用原子「检查→占位→执行」）；② 同 Webhook session 5 分钟内去重；③ 任意来源 180s 全局冷却。去重状态持久化到 DB，进程重启后不会让本地兜底重复云端已完成的巡检。

## 访问控制与多租户隔离

- **双身份鉴权**：`MASTER_PASSWORD`（仅环境变量，前端不可见不可改，派生 admin token）与 `ACCESS_PASSWORD`（数据库普通密码，参数设置可改）。配置任一密码即启用登录。
- **非管理员权限**：不可见、也不可修改访问密码（服务端强制；即使改请求体硬塞也会被丢弃）；其余参数（QODER_PAT / 通知通道 / 调度时间等）均可改。**清空全部设置（reset）仍限管理员**，防止他人一键清空配置。未配置 `MASTER_PASSWORD` 时，凭 `ACCESS_PASSWORD` 登录的本人可改自身密码。
- **数据隔离（轻量多租户）**：所有用户数据按 `tenant_id = sha256(QODER_PAT)` 过滤。他人录入自己的 `QODER_PAT` 只能看到自己的数据；原始 PAT **只以脱敏形式出现在参数设置页，绝不进入任何业务响应**。删除操作均带 `AND tenant_id=?`，杜绝越权删。库里的 `QODER_PAT`、通知通道、调度配置为**全局共享**（顺序多租户模型）。
- **空 PAT 工作区**：清空 `QODER_PAT` 保存后，工作区真正为空（不再回落 `.env`）；刷新页面后空面板导出（PDF/MD/HTML）会提示「暂无可导出内容」，不会静默导出空文件。
- **演示数据**：仅当配置了真实 `QODER_PAT` 时才注入示例小程序记录，避免污染空工作区 / 空租户。

## 持久化

后端 `settings.py` 通过 `resolve_data_dir()` 自动解析 SQLite 落点，优先级为：

1. 环境变量 `MMA_DATA_DIR`（自定义覆盖）
2. 魔搭创空间持久化卷 `/mnt/workspace/multi-mini-filing-aide`（容器重启后数据保留）
3. 兜底 `backend/data`（本地开发；注意该目录已被 `.gitignore` / `.dockerignore` 排除，不会进镜像）

升级部署时，若旧库在兜底路径，会在进程启动时自动迁移到新落点，**不丢历史数据**。DB 写入失败会在日志打印 `[SETTINGS-DB-ERR]`，便于排查。

## 部署到魔搭创空间

最小可用流程：

1. 仓库根含 `Dockerfile`（已暴露 7860）。
2. 创空间选择 **Docker** 类型，启动命令 `uvicorn main:app --host 0.0.0.0 --port 7860`（Dockerfile 已内置）。
3. 在创空间环境变量中填入上述密钥（不填也能跑演示模式）。
4. 部署成功自动获得公网地址；`/api/webhook` 即公网可访问，可被 QoderWake / 外部 cron 回调。
