"""
Langfuse client singleton module.

Provides get_langfuse(), configure_langfuse(), and shutdown_langfuse().
Configuration is loaded from DB at app startup via configure_langfuse().
"""

from __future__ import annotations

import os

from langfuse import Langfuse

# === 兼容补丁：剥离 langfuse v3 不支持的 sdk_integration 参数 ===
_orig_langfuse_init = Langfuse.__init__


def _compat_langfuse_init(self, *args, **kwargs):
    kwargs.pop("sdk_integration", None)
    return _orig_langfuse_init(self, *args, **kwargs)


Langfuse.__init__ = _compat_langfuse_init

_langfuse: Langfuse | None = None
_config: dict | None = None

_ENV_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


def configure_langfuse(config: dict | None) -> None:
    """Set Langfuse config from DB. Call this at app startup or when config changes.

    Args:
        config: Dict with keys: host, public_key, secret_key, sample_rate, flush_interval.
                Pass None to disable.
    """
    global _langfuse, _config

    # If config changed, reset the singleton
    if _langfuse is not None:
        _langfuse.flush()
        _langfuse = None

    _config = config

    # 同步设置环境变量，供 litellm 内部 Langfuse callback 使用
    if config:
        os.environ["LANGFUSE_PUBLIC_KEY"] = config["public_key"]
        os.environ["LANGFUSE_SECRET_KEY"] = config["secret_key"]
        os.environ["LANGFUSE_HOST"] = config["host"]
    else:
        for key in _ENV_KEYS:
            os.environ.pop(key, None)


def get_langfuse() -> Langfuse | None:
    """Return the Langfuse singleton, or None when not configured."""
    global _langfuse

    if _langfuse is not None:
        return _langfuse

    if _config is None:
        return None

    _langfuse = Langfuse(
        public_key=_config["public_key"],
        secret_key=_config["secret_key"],
        host=_config["host"],
        sample_rate=_config.get("sample_rate", 1.0),
        flush_interval=_config.get("flush_interval", 5),
    )

    return _langfuse


def shutdown_langfuse() -> None:
    """Flush pending data and reset the singleton."""
    global _langfuse, _config

    if _langfuse is not None:
        _langfuse.flush()
        _langfuse = None
    _config = None
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
