# ============================================================
# multi-mini-filing-aide · 知识库与规则引擎（服务器端）
# 来源：实施方案核实过的五平台备案事实（data.js 同款语料，服务端为单一事实源）
# 说明：本文件不依赖任何外部不确定数据源，全部为确定性规则，
#       保证「无 Qoder PAT」时系统仍可真实运行；Qoder 仅作增强层。
# ============================================================

PLATFORMS = {
    "wechat":   {"name": "微信小程序",     "color": "#34f5c5", "require": True},
    "alipay":   {"name": "支付宝小程序",   "color": "#3b82f6", "require": True},
    "douyin":   {"name": "抖音小程序",  "color": "#fb7185", "require": True},
    "baidu":    {"name": "百度智能小程序", "color": "#22d3ee", "require": True},
    "kuaishou": {"name": "快手小程序",     "color": "#fbbf24", "require": True},
}

PLATFORM_DETAIL = {
    "wechat": {
        "title": "平台备案 + 工信部 ICP 双重机制",
        "points": [
            "未备案无法上线，接口权限受控",
            "需同时通过平台备案与工信部 ICP 备案",
            "微信对类目资质审核严格，命名需与主体关联",
        ],
    },
    "alipay": {
        "title": "主体审核 + 域名合规 + 业务资质 + ICP",
        "points": [
            "官方不强调「备案」二字，但实质要求主体审核、域名合规、业务资质、ICP 备案",
            "按统一口径：无论主体/类型均需完成备案",
            "金融、医疗等特殊类目需补充行业资质",
        ],
    },
    "douyin": {
        "title": "通用备案 + 微短剧附加材料",
        "points": [
            "通用备案流程：平台初审 → 工信部核验 → 管局终审",
            "微短剧类额外需「成本配置比例报告 + 片酬承诺书」",
            "电商、教育、医疗类目资质要求更高",
        ],
    },
    "baidu": {
        "title": "服务器与域名必须 ICP 备案",
        "points": [
            "服务器与域名必须完成 ICP 备案，否则几乎过不了审",
            "智能小程序对主体一致性要求高",
            "搜索流量入口与备案状态强相关",
        ],
    },
    "kuaishou": {
        "title": "快手 ICP 代备案服务",
        "points": [
            "快手提供 ICP 代备案服务",
            "流程：平台初审 1–2 天 → 工信部短信核验 24h → 管局 1–20 天",
            "材料含主体/负责人证件、核验单、承诺书、人脸核验",
            "个体工商户必须为法定代表人；各省负责人要求有差异",
            "备案号格式：[省份]ICP备[号]-[N]X",
        ],
    },
}

COMMON = {
    "timeline": [
        {"stage": "平台初审", "days": "1–2 工作日"},
        {"stage": "工信部短信核验", "days": "24 小时内必须完成", "critical": True},
        {"stage": "属地管局终审", "days": "1–20 工作日"},
    ],
    "materials": ["营业执照", "法人 / 负责人身份证", "负责人实名手机号", "人脸核验"],
    "rejectReasons": [
        "材料模糊 / 反光、不清晰",
        "三证合一信息未更新",
        "名称与主体无关联",
        "服务类目选错",
        "前后信息不一致",
        "授权书 / 承诺书填写不规范（如快手要求法定代表人手写正楷、鲜章禁 PS）",
    ],
}

MATERIAL_FIELDS = [
    {"key": "license",   "label": "营业执照（统一社会信用代码）", "type": "text", "placeholder": "如 91110000XXXXXXXXXX"},
    {"key": "legalName", "label": "法定代表人姓名", "type": "text", "placeholder": "与营业执照一致"},
    {"key": "legalId",   "label": "法人身份证号", "type": "text", "placeholder": "18 位"},
    {"key": "contactPhone", "label": "负责人实名手机号", "type": "tel", "placeholder": "需本人实名"},
    {"key": "appName",   "label": "小程序名称", "type": "text", "placeholder": "需与主体有关联"},
    {"key": "category",  "label": "服务类目", "type": "select", "options": ["电商", "教育", "医疗健康", "金融", "工具", "内容/资讯", "微短剧", "其他"]},
    {"key": "domain",    "label": "已备案域名", "type": "text", "placeholder": "需已完成 ICP 备案"},
    {"key": "faceDone",  "label": "人脸核验已完成", "type": "checkbox"},
    {"key": "authLetter", "label": "授权书/承诺书已规范填写", "type": "checkbox"},
]

POLICY_KB = [
    {"q": ["要不要备案", "需要备案吗", "个人", "展示类", "是否必须", "都要备"],
     "a": "按统一合规口径：只要是小程序，无论主体性质（企业/个人/个体工商户）或类型（展示/交易/内容），均须完成备案。自 2023 年工信部信管〔2023〕105 号及《反电信网络诈骗法》落地后，各平台备案已全面收紧，宽松口径反而带来合规风险。快手官方指引亦明确「未履行备案手续的，不得从事互联网信息服务」。",
     "src": "工信部信管〔2023〕105 号 / 快手《ICP 备案指引》"},
    {"q": ["时效", "多久", "几天", "时间", "周期"],
     "a": "标准时效：平台初审 1–2 工作日 → 工信部短信核验（24 小时内必须完成，超时作废需重提）→ 属地管局终审 1–20 工作日。整体约 3–20 工作日。快手代备案同此流程。",
     "src": "各平台备案流程说明 / 快手《ICP 备案指引》"},
    {"q": ["短信核验", "24小时", "核验超时"],
     "a": "工信部短信核验必须在收到核验短信后 24 小时内完成，超时则备案订单作废，需重新提交。这是最容易被漏盯的硬 deadline，建议提交后立即安排负责人查收短信并完成核验。",
     "src": "工信部备案短信核验规则"},
    {"q": ["材料", "被打回", "驳回", "拒", "需要什么"],
     "a": "通用材料：营业执照、法人/负责人身份证、负责人实名手机号、人脸核验。高频驳回点：材料模糊/反光、三证合一信息未更新、名称与主体无关联、服务类目选错、信息前后不一致、授权书/承诺书填写不规范（快手要求法定代表人手写正楷、鲜章禁 PS）。",
     "src": "各平台备案实操教程 / 快手《ICP 备案指引》"},
    {"q": ["微信", "双重备案"],
     "a": "微信小程序实行「平台备案 + 工信部 ICP 备案」双重机制，未备案无法上线、接口权限受控。除平台侧备案外，域名与服务器也必须完成工信部 ICP 备案。",
     "src": "微信小程序备案政策"},
    {"q": ["支付宝", "alipay"],
     "a": "支付宝官方不强调「备案」二字，但实质要求主体审核、域名合规、业务资质、ICP 备案。按统一口径，无论主体/类型均需完成备案，金融、医疗等特殊类目需补充行业资质。",
     "src": "支付宝小程序备案政策"},
    {"q": ["抖音", "微短剧", "douyin"],
     "a": "抖音小程序均需备案。通用备案外，微短剧类额外需提交「成本配置比例报告 + 片酬承诺书」。电商、教育、医疗类目资质要求更高。",
     "src": "抖音小程序备案政策"},
    {"q": ["快手", "kuaishou"],
     "a": "快手提供 ICP 代备案服务。流程：平台初审 1–2 天 → 工信部短信核验 24h → 管局 1–20 天。材料含主体/负责人证件、核验单、承诺书、人脸核验；个体工商户必须为法定代表人，各省负责人要求有差异；备案号格式 [省份]ICP备[号]-[N]X。",
     "src": "快手《ICP 备案指引》open.kuaishou.com/docs/operate/Hierarchy/icp/guide.html"},
    {"q": ["备案号", "格式"],
     "a": "备案号格式为 [省份]ICP备[号]-[N]X，例如「京ICP备12345678号-1X」。备案完成后需在小程序前端悬挂备案号，并保持可点击跳转工信部备案查询页。",
     "src": "工信部备案号规范 / 快手《ICP 备案指引》"},
]

CHANNELS = {
    "wechat":   {"name": "企业微信", "icon": "💬"},
    "dingtalk": {"name": "钉钉",     "icon": "🔔"},
    "feishu":   {"name": "飞书",     "icon": "📨"},
}


# ---------- 工具 ----------
def day_diff(a, b):
    """b - a 天数（按日期，忽略时分秒）"""
    try:
        ms = (to_dt(b) - to_dt(a))
        return ms.days
    except Exception:
        return 0

def to_dt(s):
    from datetime import datetime, time as dtime
    d = datetime.strptime(s, "%Y-%m-%d")
    return d

def today_iso():
    from datetime import date
    return date.today().isoformat()


# ---------- 诊断 ----------
def diagnosis(platform_keys, biz, subject):
    keys = [k for k in platform_keys if k in PLATFORMS]
    if not keys:
        return {"error": "请至少选择一个平台"}
    rows = []
    for k in keys:
        p = PLATFORMS[k]
        d = PLATFORM_DETAIL[k]
        rows.append({
            "key": k, "name": p["name"], "color": p["color"],
            "require": "均需备案", "title": d["title"], "points": d["points"],
        })
    special = ""
    if biz == "微短剧":
        special = "⚠ 微短剧：额外需「成本配置比例报告 + 片酬承诺书」"
    subj_note = ""
    if subject == "个体工商户":
        subj_note = "个体工商户：快手要求必须为法定代表人"
    conclusion = f"统一结论：所有小程序均需备案（主体：{subject} / 业务：{biz}）"
    return {"conclusion": conclusion, "rows": rows, "special": special, "subjNote": subj_note}


# ---------- 材料预审 ----------
def precheck(fields):
    risks = []
    if not fields.get("license"):
        risks.append("营业执照（统一社会信用代码）缺失")
    if not fields.get("legalName"):
        risks.append("法定代表人姓名缺失")
    if not fields.get("legalId") or len(str(fields.get("legalId", "")).strip()) < 15:
        risks.append("法人身份证号不完整（应 18 位）")
    if not fields.get("contactPhone"):
        risks.append("负责人实名手机号缺失")
    if not fields.get("appName"):
        risks.append("小程序名称缺失")
    cat = fields.get("category")
    if cat in ("电商", "教育", "医疗健康", "金融", "微短剧"):
        risks.append(f"类目「{cat}」属高审核强度，需补充对应行业资质")
    if not fields.get("domain"):
        risks.append("已备案域名未填写（上线前置）")
    if not fields.get("faceDone"):
        risks.append("人脸核验尚未完成")
    if not fields.get("authLetter"):
        risks.append("授权书/承诺书未规范填写（手写正楷、鲜章禁 PS）")
    score = max(0, 100 - len(risks) * 16)
    if score >= 85:
        level = "低风险"
    elif score >= 60:
        level = "中风险"
    else:
        level = "高风险"
    return {"score": score, "level": level, "risks": risks}


# ---------- 进度阶段 ----------
def stage_of(r):
    if r.get("gov"):
        d = day_diff(r["gov"], today_iso())
        if d > 20:
            return {"nm": "管局终审超期", "p": 100, "warn": True}
        if d >= 0:
            return {"nm": "管局终审中", "p": 80}
        return {"nm": "已上线", "p": 100, "done": True}
    if r.get("verify"):
        d = day_diff(r["verify"], today_iso())
        if d > 20:
            return {"nm": "管局待受理（核验已完成）", "p": 65, "warn": True}
        return {"nm": "管局待受理", "p": 60}
    if r.get("submit"):
        d = day_diff(r["submit"], today_iso())
        if d > 2:
            return {"nm": "平台初审中（核验待办）", "p": 35, "warn": True}
        return {"nm": "平台初审中", "p": 30}
    return {"nm": "未提交", "p": 5}


# ---------- 风险计算 ----------
def compute_risks(records):
    out = []
    for r in records:
        p = PLATFORMS.get(r.get("platform"), {})
        name = p.get("name", r.get("platform", ""))
        label = f"{r.get('name','')}（{name}）"
        if r.get("submit") and not r.get("verify"):
            d = day_diff(r["submit"], today_iso())
            if d >= 1:
                out.append({"lvl": "warn", "t": f"{label}提交已 {d} 天，短信核验待完成", "d": "24h 内必须核验，超时作废"})
        if r.get("verify") and not r.get("gov"):
            d = day_diff(r["verify"], today_iso())
            if d > 20:
                out.append({"lvl": "bad", "t": f"{label}管局终审已 {d} 天", "d": "超过 1–20 工作日常规区间，建议催办"})
        if not r.get("submit"):
            out.append({"lvl": "info", "t": f"{label}尚未提交", "d": "尽快启动备案以免上线延误"})
    return out


# ---------- 问答 ----------
def answer(q):
    ql = (q or "").lower()
    for item in POLICY_KB:
        if any(k.lower() in ql for k in item["q"]):
            return {"a": item["a"], "src": item["src"]}
    return None
