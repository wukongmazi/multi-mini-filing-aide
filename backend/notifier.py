# ============================================================
# multi-mini-filing-aide · 三通道通知（企业微信 / 钉钉 / 飞书 群机器人）
# 配置了对应 Webhook URL 才真实发送；否则 demo 模式下仅记录，不报错。
# ============================================================
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx

from kb import CHANNELS
from settings import get_setting

# channel -> (webhook_url 配置键, sign_secret 配置键)；优先级：页面设置 > 环境变量 > 默认
_CHANNEL_KEYS = {
    "wechat": ("WECHAT_WEBHOOK_URL", None),
    "dingtalk": ("DINGTALK_WEBHOOK_URL", "DINGTALK_SIGN_SECRET"),
    "feishu": ("FEISHU_WEBHOOK_URL", "FEISHU_SIGN_SECRET"),
}


def _post_wechat(url, text):
    # 企业微信原生支持 markdown 类型（标题 #、加粗 **、引用 >、链接、行内代码）
    return httpx.post(url, json={"msgtype": "markdown", "markdown": {"content": text}}, timeout=10)


def _post_dingtalk(url, secret, text):
    if secret:
        ts = str(round(time.time() * 1000))
        string_to_sign = f"{ts}\n{secret}"
        sign = base64.b64encode(
            hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
        ).decode()
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"
    # 钉钉 markdown 类型需要 title + text
    return httpx.post(url, json={"msgtype": "markdown",
                                 "markdown": {"title": "小程序备案巡检播报", "text": text}}, timeout=10)


def _post_feishu(url, secret, text):
    # 飞书自定义机器人无顶层 markdown 类型，用 interactive 卡片承载 markdown 元素
    body = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "小程序备案巡检播报"},
            },
            "elements": [{"tag": "markdown", "content": text}],
        },
    }
    if secret:
        ts = str(round(time.time()))
        string_to_sign = ts + "\n" + secret
        sign = base64.b64encode(
            hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
        ).decode()
        body["timestamp"] = ts
        body["sign"] = sign
    return httpx.post(url, json=body, timeout=10)


def send(channel: str, message: str) -> dict:
    """发送单通道。返回 {channel,name,sent,mode,error}。"""
    cfg = CHANNELS.get(channel)
    if not cfg:
        return {"channel": channel, "name": channel, "sent": False, "mode": "error", "error": "未知通道"}
    uk, sk = _CHANNEL_KEYS.get(channel, (None, None))
    url = get_setting(uk) if uk else ""
    secret = get_setting(sk) if sk else ""
    if not url:
        return {"channel": channel, "name": cfg["name"], "sent": False, "mode": "demo",
                "error": "未配置 Webhook URL（演示模式）"}
    try:
        if channel == "wechat":
            _post_wechat(url, message)
        elif channel == "dingtalk":
            _post_dingtalk(url, secret, message)
        elif channel == "feishu":
            _post_feishu(url, secret, message)
        return {"channel": channel, "name": cfg["name"], "sent": True, "mode": "live"}
    except Exception as e:  # 网络/格式异常不影响主流程
        return {"channel": channel, "name": cfg["name"], "sent": False, "mode": "error", "error": str(e)[:120]}


def send_all(message: str, enabled: dict) -> list:
    results = []
    for ch in ("wechat", "dingtalk", "feishu"):
        if enabled.get(ch):
            results.append(send(ch, message))
    return results
