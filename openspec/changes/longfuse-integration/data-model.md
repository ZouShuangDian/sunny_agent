# Data Model: Langfuse 集成

**Date**: 2026-03-13

---

## 新增表

### 1. `sunny_agent.langfuse_config`

存储 Langfuse 服务配置和初始化状态。单行表（singleton）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PK, DEFAULT 1 | 固定为 1（单行） |
| service_mode | VARCHAR(20) | NOT NULL, DEFAULT 'none' | `builtin` / `external` / `none` |
| langfuse_host | VARCHAR(500) | | Langfuse 服务地址 |
| public_key | VARCHAR(200) | | Langfuse Public Key |
| encrypted_secret_key | TEXT | | 加密存储的 Secret Key |
| admin_email | VARCHAR(200) | | Langfuse 管理员邮箱 |
| project_id | VARCHAR(100) | | Langfuse 项目 ID |
| project_name | VARCHAR(200) | | Langfuse 项目名称 |
| initialized | BOOLEAN | DEFAULT FALSE | 是否已完成初始化 |
| initialized_at | TIMESTAMP(TZ) | | 初始化时间 |
| last_health_check | TIMESTAMP(TZ) | | 最后一次健康检查时间 |
| last_health_status | VARCHAR(20) | | `healthy` / `unhealthy` / `unknown` |
| created_at | TIMESTAMP(TZ) | DEFAULT NOW() | |
| updated_at | TIMESTAMP(TZ) | DEFAULT NOW() | |

**约束**:
- `CHECK (id = 1)` — 确保单行
- `CHECK (service_mode IN ('builtin', 'external', 'none'))`

---

## 修改表

### 无

Trace 原始数据存储在 Langfuse（ClickHouse），SunnyAgent 不存储 Trace 数据副本。用量统计通过 Langfuse API 实时查询 + Redis 缓存实现。

---

## 新增配置项（Settings / .env）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| LANGFUSE_HOST | str | "" | Langfuse 服务地址 |
| LANGFUSE_PUBLIC_KEY | str | "" | Langfuse Public Key |
| LANGFUSE_SECRET_KEY | str | "" | Langfuse Secret Key |
| LANGFUSE_ADMIN_EMAIL | str | "" | Langfuse 管理员邮箱 |
| LANGFUSE_ADMIN_PASSWORD | str | "" | Langfuse 管理员密码 |
| LANGFUSE_ENABLED | bool | False | 是否启用 Langfuse 追踪 |
| LANGFUSE_SAMPLE_RATE | float | 1.0 | 采样率（0.0~1.0） |
| LANGFUSE_FLUSH_INTERVAL | int | 5 | 异步上报刷新间隔（秒） |
| ENCRYPTION_KEY | str | "" | Fernet 对称加密密钥，用于加密 Secret Key 等敏感字段。首次部署时自动生成并写入 .env |
| LANGFUSE_PII_PATTERNS | str | "" | 自定义 PII 脱敏正则模式（JSON 数组格式），追加到内置模式列表 |

### 配置优先级（Source of Truth）

```
启动时读取配置优先级:
  1. 数据库 langfuse_config 表（如果记录存在且 initialized=True）→ 运行时权威来源
  2. .env 文件（首次启动或数据库无记录时）→ 种子值，读取后写入数据库

运行时配置变更（通过管理 API）:
  - 仅写入数据库 langfuse_config 表
  - 不修改 .env 文件（.env 仅用于首次初始化和 Docker Compose 环境变量注入）
```

---

## Redis 缓存 Key 设计

| Key Pattern | TTL | 说明 |
|-------------|-----|------|
| `sunny:langfuse:health` | 30s | Langfuse 健康状态缓存 |
| `sunny:langfuse:usage:{date}:{user_id}` | 5min | 用户日维度用量缓存 |
| `sunny:langfuse:usage:summary:{start}:{end}:{user_id}` | 5min | 用量汇总缓存 |

---

## 实体关系

```
┌─────────────────┐
│ langfuse_config  │  singleton (id=1)
│ (sunny_agent DB) │
└────────┬────────┘
         │ langfuse_host, public_key, secret_key
         ▼
┌─────────────────┐      ┌──────────────┐
│  Langfuse Server │◀─────│ LiteLLM      │
│  (ClickHouse)    │      │ Callback     │
│                  │      │ (auto trace) │
│  - Trace         │      └──────────────┘
│  - Span          │
│  - Generation    │      ┌──────────────┐
│  - Score         │◀─────│ @observe()   │
│                  │      │ (manual span)│
└─────────────────┘      └──────────────┘
```

---

## Secret Key 加密方案

使用 `cryptography.fernet.Fernet` 对称加密存储 `encrypted_secret_key` 字段。

```python
# app/utils/crypto.py

from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings

def _get_fernet() -> Fernet:
    """获取 Fernet 实例，加密密钥从 ENCRYPTION_KEY 环境变量获取"""
    key = get_settings().ENCRYPTION_KEY
    if not key:
        raise RuntimeError("ENCRYPTION_KEY 未配置，无法加解密敏感数据")
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt_secret(plaintext: str) -> str:
    """加密明文，返回 base64 编码的密文"""
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt_secret(ciphertext: str) -> str:
    """解密密文，返回明文"""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("解密失败：ENCRYPTION_KEY 不匹配或密文已损坏")

def generate_encryption_key() -> str:
    """生成新的 Fernet 加密密钥（首次部署时使用）"""
    return Fernet.generate_key().decode()
```

**密钥管理规则**:
- `ENCRYPTION_KEY` 必须在 `.env` 中配置，首次部署时由 `generate_encryption_key()` 自动生成
- 密钥轮换：生成新密钥后需重新加密所有已存储的 `encrypted_secret_key`
- 生产环境建议通过 Docker Secrets 或密钥管理服务（如 Vault）注入，而非 .env 明文存储

---

## SQLAlchemy Model 定义

```python
# app/db/models/langfuse_config.py

class LangfuseConfig(Base):
    __tablename__ = "langfuse_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    service_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none",
        comment="builtin / external / none"
    )
    langfuse_host: Mapped[str | None] = mapped_column(String(500))
    public_key: Mapped[str | None] = mapped_column(String(200))
    encrypted_secret_key: Mapped[str | None] = mapped_column(Text,
        comment="Fernet 加密存储的 Secret Key")
    admin_email: Mapped[str | None] = mapped_column(String(200))
    project_id: Mapped[str | None] = mapped_column(String(100))
    project_name: Mapped[str | None] = mapped_column(String(200))
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    initialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_status: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_langfuse_config_singleton"),
        {"schema": "sunny_agent"},
    )
```
