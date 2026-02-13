"""
全局配置模块：通过 pydantic-settings 读取 .env 环境变量
"""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置，从 .env 文件加载"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 数据库 ──
    DATABASE_URL: str
    DB_SCHEMA: str = "sunny_agent"

    DB_ECHO: bool = False  # 打印 SQL 日志，调试时可在 .env 设为 true

    # ── 连接池 ──
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # ── Redis ──
    REDIS_URL: str
    REDIS_MAX_CONNECTIONS: int = 10

    # ── JWT ──
    JWT_SECRET: str = "dev-secret-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 天

    # ── LLM（OpenAI 协议兼容） ──
    LLM_DEFAULT_MODEL: str = "openai/deepseek-ai/DeepSeek-V3"  # LiteLLM 格式：openai/{model}
    LLM_API_KEY: str = ""  # API Key
    LLM_API_BASE: str | None = None  # 自定义 API 端点
    LLM_TIMEOUT: int = 60  # LLM 调用超时（秒）

    # ── 码表缓存 ──
    CODEBOOK_CACHE_TTL: int = 3600  # 码表缓存 TTL（秒），默认 1h

    # ── L1 执行层 ──
    TEMPLATE_CACHE_TTL: int = 3600  # Prompt 模板缓存 TTL（秒）
    LLM_STREAM_TIMEOUT: int = 30  # LLM 流式调用超时（秒）
    L1_OUTPUT_DIR: str = "/tmp/sunny_outputs"  # L1 工具输出目录

    # ── 博查搜索 ──
    BOCHA_API_KEY: str = ""  # 博查 Web Search API Key

    # ── Milvus 向量数据库 ──
    MILVUS_URI: str = "http://127.0.0.1:19530"
    MILVUS_DB: str = "sunny_agent"
    MILVUS_USER: str = "root"
    MILVUS_PASSWORD: str = ""

    # ── Embedding 模型 ──
    EMBEDDING_API_BASE: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIM: int = 1024  # bge-m3 输出维度
    EMBEDDING_TIMEOUT: int = 30

    # ── Rerank 模型 ──
    RERANK_API_BASE: str = ""
    RERANK_API_KEY: str = ""
    RERANK_MODEL: str = "Bge-reranker-v2-m3"

    # ── Prompt 检索 ──
    PROMPT_SEARCH_THRESHOLD: float = 0.5  # Milvus 向量检索相似度阈值
    PROMPT_SEARCH_TOP_K: int = 3  # 检索返回 top-K 条结果

    # ── 工作记忆 ──
    WORKING_MEMORY_TTL: int = 1800  # 工作记忆 TTL（秒），默认 30min
    WORKING_MEMORY_MAX_TURNS: int = 20  # 对话历史最大保留轮次

    # ── 应用 ──
    ENV: str = "development"  # development | production
    APP_NAME: str = "agent-sunny"
    APP_PORT: int = 8000

    @model_validator(mode="after")
    def _check_production_secret(self) -> "Settings":
        """生产环境强制要求配置安全的 JWT_SECRET"""
        if self.ENV == "production" and (
            self.JWT_SECRET.startswith("dev-") or len(self.JWT_SECRET) < 32
        ):
            raise ValueError(
                "生产环境 JWT_SECRET 不能使用默认值，"
                "且长度必须 >= 32 位。请在 .env 中配置安全的密钥。"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """单例获取配置（带缓存）"""
    return Settings()
