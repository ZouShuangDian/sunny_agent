"""
Langfuse 配置管理器：连接验证 + 配置持久化（DB 优先，env 回退）
"""

import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.langfuse_config import LangfuseConfig
from app.utils.crypto import decrypt_secret, encrypt_secret


class LangfuseManager:
    """Manages Langfuse configuration (DB-first, env fallback)."""

    async def validate_connection(
        self, host: str, public_key: str, secret_key: str
    ) -> dict:
        """Validate connection to a Langfuse instance by checking its health endpoint."""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{host.rstrip('/')}/api/public/health",
                    auth=(public_key, secret_key),
                )

            latency_ms = round((time.monotonic() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "valid": True,
                    "version": data.get("version", "unknown"),
                    "latency_ms": latency_ms,
                }
            else:
                return {
                    "valid": False,
                    "message": f"HTTP {resp.status_code}",
                    "latency_ms": latency_ms,
                }
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            latency_ms = round((time.monotonic() - start) * 1000)
            return {"valid": False, "message": str(exc), "latency_ms": latency_ms}
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000)
            return {"valid": False, "message": str(exc), "latency_ms": latency_ms}

    async def get_config(self, db: AsyncSession) -> dict:
        """Read Langfuse configuration from DB, fallback to env defaults."""
        result = await db.execute(
            select(LangfuseConfig).where(LangfuseConfig.id == 1)
        )
        config = result.scalar_one_or_none()

        if config is None:
            return {
                "enabled": False,
                "langfuse_host": None,
                "langfuse_public_key": None,
                "sample_rate": 1.0,
                "flush_interval": 5,
                "pii_patterns": "",
            }

        # Decrypt secret_key for internal use (never return to frontend)
        return {
            "enabled": config.enabled,
            "langfuse_host": config.langfuse_host,
            "langfuse_public_key": config.langfuse_public_key,
            "sample_rate": config.sample_rate,
            "flush_interval": config.flush_interval,
            "pii_patterns": config.pii_patterns,
        }

    async def get_decrypted_config(self, db: AsyncSession) -> dict | None:
        """Read config with decrypted secret_key (for internal service use only)."""
        result = await db.execute(
            select(LangfuseConfig).where(LangfuseConfig.id == 1)
        )
        config = result.scalar_one_or_none()

        if config is None or not config.enabled:
            return None

        try:
            secret_key = decrypt_secret(config.langfuse_secret_key)
        except (ValueError, RuntimeError):
            return None

        return {
            "host": config.langfuse_host,
            "public_key": config.langfuse_public_key,
            "secret_key": secret_key,
            "sample_rate": config.sample_rate or 1.0,
            "flush_interval": config.flush_interval or 5,
        }

    async def save_config(self, config_data: dict, db: AsyncSession) -> None:
        """Persist Langfuse configuration to DB (singleton row).

        config_data keys: enabled, langfuse_host, langfuse_public_key,
                          langfuse_secret_key (plaintext), sample_rate,
                          flush_interval, pii_patterns
        """
        result = await db.execute(
            select(LangfuseConfig).where(LangfuseConfig.id == 1)
        )
        existing = result.scalar_one_or_none()

        # Encrypt secret_key if provided
        secret_key_plain = config_data.pop("langfuse_secret_key", None)
        encrypted_secret = None
        if secret_key_plain:
            encrypted_secret = encrypt_secret(secret_key_plain)

        if existing is None:
            record = LangfuseConfig(id=1, **config_data)
            if encrypted_secret:
                record.langfuse_secret_key = encrypted_secret
            db.add(record)
        else:
            for key, value in config_data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            if encrypted_secret:
                existing.langfuse_secret_key = encrypted_secret

        await db.commit()
