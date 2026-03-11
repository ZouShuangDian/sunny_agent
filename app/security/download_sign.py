"""
文件下载签名工具 — HMAC 签名临时链接

生成侧：present_files 工具调用 sign_download_url() 生成带签名的下载 URL
校验侧：download 端点调用 verify_download_sig() 验证签名

密钥派生：HMAC(JWT_SECRET, "download-sign") 作为签名密钥，与 JWT 密钥隔离
"""

import hashlib
import hmac
import time
from urllib.parse import quote, urlencode

from app.config import get_settings

settings = get_settings()

# 从 JWT_SECRET 派生独立的下载签名密钥，避免与 JWT 共用同一密钥
_SIGN_KEY = hmac.new(
    settings.JWT_SECRET.encode(),
    b"download-sign",
    hashlib.sha256,
).digest()

# 签名有效期（秒），默认 5 分钟
DOWNLOAD_LINK_TTL = 300


def sign_download_url(path: str, ttl: int = DOWNLOAD_LINK_TTL) -> str:
    """
    生成带 HMAC 签名的下载 URL。

    返回格式：/api/files/download?path=...&expires=...&sig=...
    """
    expires = int(time.time()) + ttl
    sig = _make_sig(path, expires)
    qs = urlencode({"path": path, "expires": expires, "sig": sig})
    return f"/api/files/download?{qs}"


def verify_download_sig(path: str, expires: int, sig: str) -> str | None:
    """
    校验下载签名。

    返回 None 表示合法，返回错误描述字符串表示校验失败。
    """
    if time.time() > expires:
        return "下载链接已过期"

    expected = _make_sig(path, expires)
    if not hmac.compare_digest(sig, expected):
        return "签名无效"

    return None


def _make_sig(path: str, expires: int) -> str:
    """计算 HMAC-SHA256 签名"""
    message = f"{path}\n{expires}"
    return hmac.new(_SIGN_KEY, message.encode(), hashlib.sha256).hexdigest()
