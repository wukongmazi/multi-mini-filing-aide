# -*- coding: utf-8 -*-
# ============================================================
# multi-mini-filing-aide · 集中配置模块
# 配置项优先级（由高到低）：
#   1. 页面设置（写入 SQLite config 表，前端 /api/settings 持久化）
#   2. 环境变量（含 .env，由 load_dotenv 注入 os.environ）
#   3. 代码内置默认值
# 本模块自带 SQLite 访问，避免与 main.py 循环导入。
# ============================================================
import os
import shutil
import sqlite3

# ---------- 默认配置 ----------
DEFAULTS = {
    "QODER_PAT": "",
    "QODER_API_BASE": "https://api.qoder.com/api/v1/cloud",
    "QODER_MODEL": "ultimate",
    "QODER_WEBHOOK_SIGNING_SECRET": "",
    "WECHAT_WEBHOOK_URL": "",
    "DINGTALK_WEBHOOK_URL": "",
    "DINGTALK_SIGN_SECRET": "",
    "FEISHU_WEBHOOK_URL": "",
    "FEISHU_SIGN_SECRET": "",
    "SCHEDULER_ENABLED": "false",
    "SCHEDULER_HOUR": "9",
    "ACCESS_PASSWORD": "",
}

# 密钥类字段：GET 时脱敏；POST 时若传回掩码值（以 **** 开头）视为「未修改」
SECRET_KEYS = {
    "QODER_PAT",
    "QODER_WEBHOOK_SIGNING_SECRET",
    "DINGTALK_SIGN_SECRET",
    "FEISHU_SIGN_SECRET",
    "ACCESS_PASSWORD",
}

# ============================================================
# 数据库落点解析（持久化关键）
# 魔搭创空间（ModelScope Studio）默认镜像层在每次重启后会被重置，写入 /app 下
# 的文件会丢失；官方持久化卷挂在 /mnt/workspace。因此数据库必须落在持久化卷，
# 否则「页面修改 ACCESS_PASSWORD 等设置」重启后即回退到环境变量旧值。
# 解析优先级：
#   1. 环境变量 MMA_DATA_DIR（用户显式指定持久化目录）
#   2. /mnt/workspace/multi-mini-filing-aide（魔搭官方持久化卷，自动启用）
#   3. backend/data（本地/无持久化卷时的兜底，行为同旧版）
# 选中目录会做「可写探针」，不可写则顺延到下一个候选，保证一定能落盘。
# ============================================================
def resolve_data_dir():
    env = os.getenv("MMA_DATA_DIR")
    candidates = []
    if env:
        candidates.append(env)
    if os.path.isdir("/mnt/workspace"):
        candidates.append(os.path.join("/mnt/workspace", "multi-mini-filing-aide"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".writetest")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return d
        except Exception:
            continue
    # 兜底：直接返回最后一个候选（即使不可写也给出确定路径，便于排错）
    d = candidates[-1]
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _legacy_db_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "app.db")


def _maybe_migrate(src, dst):
    """升级兼容：把旧的 backend/data/app.db 迁移到持久化目录，避免用户数据丢失。
    仅在『旧库存在且新库尚不存在』时拷贝一次。"""
    try:
        if src != dst and os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            print(f"[DB-MIGRATE] 已迁移旧数据库 {src} -> {dst}")
    except Exception as e:
        print(f"[DB-MIGRATE] 迁移失败（忽略，使用新位置）: {e}")


DATA_DIR = resolve_data_dir()
DB_PATH = os.path.join(DATA_DIR, "app.db")
_maybe_migrate(_legacy_db_path(), DB_PATH)

_overrides = {}  # 内存缓存：key -> value


def _db_get(key):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    except Exception as e:
        print(f"[SETTINGS-DB-ERR] _db_get({key}) 失败: {e} (DB_PATH={DB_PATH})")
        return None
    finally:
        if conn:
            conn.close()


def _db_set(key, value):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)", (key, value))
        conn.commit()
    except Exception as e:
        print(f"[SETTINGS-DB-ERR] _db_set({key}) 失败: {e} (DB_PATH={DB_PATH})")
    finally:
        if conn:
            conn.close()


def _db_del(key):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("DELETE FROM config WHERE key=?", (key,))
        conn.commit()
    except Exception as e:
        print(f"[SETTINGS-DB-ERR] _db_del({key}) 失败: {e} (DB_PATH={DB_PATH})")
    finally:
        if conn:
            conn.close()


def get_setting(key):
    """按优先级 内存缓存 → 页面设置(DB) → 环境变量 → 默认值 取配置值。

    MASTER_PASSWORD 为「环境变量专属」的主人密码：仅读 os.getenv，不进 DB、
    不进内存缓存、不进快照/设置接口，前端不可见也不可改。
    """
    if key == "MASTER_PASSWORD":
        return os.getenv("MASTER_PASSWORD", "")
    if key in _overrides:
        return _overrides[key]
    dbv = _db_get(key)
    if dbv is not None:
        # 页面设置已「显式存在」（含空字符串）：以页面为准，不回落环境变量。
        # 仅当页面设置「从未配置」（DB 无行）时才回落到环境变量 / 默认值。
        if dbv != "":
            _overrides[key] = dbv
        return dbv
    envv = os.getenv(key, "")
    if envv:
        return envv
    return DEFAULTS.get(key, "")


def set_setting(key, value):
    """写入页面设置（内存 + DB），优先级高于环境变量。

    任意值（含空字符串）都会显式落库：页面设置一旦存在即以页面为准，
    不再回落到环境变量（符合「页面设置 > 环境变量」的优先级约定）。
    若要回落到环境变量 / 默认值，请使用 delete_setting（如「清空页面设置」按钮）。
    """
    value = "" if value is None else str(value)
    _overrides[key] = value
    _db_set(key, value)


def delete_setting(key):
    _overrides.pop(key, None)
    _db_del(key)


def mask_secret(value):
    """密钥脱敏：纯圆点 + 位数提示，不暴露任何真实字符。"""
    if not value:
        return ""
    n = len(value)
    # 短密钥（≤8位）全遮；长密钥显示圆点+位数
    dots = "\u2022" * min(n, 16)
    return f"{dots} ({n}\u4f4d)" if n > 8 else dots


def snapshot(mask=True):
    """返回全部配置；mask=True 时对密钥字段脱敏。"""
    out = {}
    for k in DEFAULTS:
        v = get_setting(k)
        out[k] = mask_secret(v) if (mask and k in SECRET_KEYS) else v
    return out


def apply_batch(data, reset=False):
    """批量保存页面设置。

    reset=True：清空全部页面设置，回退到环境变量 / 默认值。
    否则：遍历 data，密钥字段若传回掩码值（以 **** 开头）视为未修改，保持原值。
    """
    if reset:
        for k in DEFAULTS:
            delete_setting(k)
        return snapshot(mask=True)
    cur = snapshot(mask=True)
    for k, v in (data or {}).items():
        if k not in DEFAULTS:
            continue
        if k in SECRET_KEYS and isinstance(v, str) and v.startswith("\u2022"):
            continue  # 未修改（掩码值以 • 开头），保留已存值
        set_setting(k, v)
    return snapshot(mask=True)


def is_scheduler_enabled():
    return get_setting("SCHEDULER_ENABLED") == "true"
