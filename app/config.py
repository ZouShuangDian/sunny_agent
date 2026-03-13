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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30 * 48
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 天
    
    # ── SSO 配置 ──
    SSO_VALIDATE_URL: str = "https://sso.sunnyoptical.cn/serviceValidate"
    SSO_LOGIN_URL: str = "https://sso.sunnyoptical.cn/login"

    # ── LLM（OpenAI 协议兼容） ──
    LLM_DEFAULT_MODEL: str = "openai/deepseek-ai/DeepSeek-V3"  # LiteLLM 格式：openai/{model}
    LLM_API_KEY: str = ""  # API Key
    LLM_API_BASE: str | None = None  # 自定义 API 端点
    LLM_TIMEOUT: int = 60  # LLM 调用超时（秒）

    # ── LLM 流式 ──
    LLM_STREAM_TIMEOUT: int = 30  # LLM 流式调用超时（秒）

    # ── 博查搜索 ──
    BOCHA_API_KEY: str = ""  # 博查 Web Search API Key
    BOCHA_API_URL: str = "https://api.bochaai.com/v1/web-search"  # 博查 API 端点（可替换为私有部署地址）

    # ── Rerank 模型 ──
    RERANK_API_BASE: str = ""
    RERANK_API_KEY: str = ""
    RERANK_MODEL: str = "Bge-reranker-v2-m3"

    # ── Sandbox Service（沙箱代码执行） ──
    SANDBOX_SERVICE_URL: str = "http://localhost:8020"  # sandbox-service HTTP 地址
    SANDBOX_HOST_VOLUME: str = "/Users/zoushuangdian/docker/volumes/sunny_agent"  # 宿主机挂载根目录
    SANDBOX_BASH_TIMEOUT: int = 30  # bash_tool 单次执行超时（秒）

    # ── 工具注册中心 ──
    DEFAULT_TOOL_TIMEOUT_MS: int = 60_000  # 工具默认超时（毫秒），BaseTool 引用此值

    # ── L3 执行层 ──
    L3_MAX_ITERATIONS: int = 30        # ReAct 循环最大步数
    L3_TIMEOUT_SECONDS: float = 600.0  # L3 整体超时（秒）
    L3_MAX_LLM_CALLS: int = 50          # LLM 调用次数上限

    # ── Context 压缩（上下文窗口管理） ──
    MODEL_CONTEXT_LIMIT: int = 98_000        # 模型上下文 token 输入上限（实测精确值）
    COMPACTION_BUFFER: int = 20_000          # Level 2 触发阈值（剩余空间低于此值触发摘要）
    PRUNE_PROTECT_TOKENS: int = 20_000       # 保护区 token 数（最近步骤不被剪枝）
    HISTORY_TOKEN_BUDGET: int = 60_000       # 历史消息加载预算（独立于保护区）
    COMPACTION_MAX_TOKENS: int = 2_000       # 摘要生成 max_tokens

    # ── 冷存储（聊天记录持久化） ──
    CHAT_PERSIST_ENABLED: bool = True       # 是否启用 PG 冷存储
    CHAT_PERSIST_WRITE_BEHIND: bool = True  # write-behind 异步写入（False 则同步）

    # ── 工作记忆 ──
    WORKING_MEMORY_TTL: int = 1800  # 工作记忆 TTL（秒），默认 30min
    WORKING_MEMORY_MAX_TURNS: int = 20  # 对话历史最大保留轮次

    # ── M06 输出校验 ──
    OUTPUT_VALIDATOR_ENABLED: bool = False        # 是否启用输出校验器（全局开关）
    OUTPUT_VALIDATOR_HALLUCINATION: bool = False  # 是否启用幻觉检测（额外 LLM 调用）

    # ── arq Worker ──
    ARQ_QUEUE_NAME: str = "sunny:queue"
    ARQ_MAX_JOBS: int = 10               # 单实例最大并发任务数
    ARQ_JOB_TIMEOUT: int = 600           # 单任务超时（秒）
    ARQ_MAX_TRIES: int = 1               # 不自动重试（失败直接标记 failed）

    # ── Cron 调度 ──
    MAX_CRON_JOBS_PER_USER: int = 20     # 单用户最大定时任务数
    CRON_SCAN_INTERVAL: int = 1          # Scanner 扫描间隔（分钟），默认每分钟
    CRON_MIN_INTERVAL_MINUTES: int = 30  # 定时任务最小触发间隔（分钟），防止资源滥用
    # 外部 Webhook 服务配置（由 feishu-sunnyagent-api 项目提供）
    # Webhook URL: https://larkchannel.51dnbsc.top/webhook
    # 消息队列: Redis List "feishu:webhook:queue"

    # ── 通知 ──
    NOTIFICATION_RETENTION_DAYS: int = 30  # 已读通知保留天数（清理策略）
    SSE_HEARTBEAT_SECONDS: int = 30        # SSE 心跳间隔（秒）

    # ── CORS ──
    CORS_ORIGINS: list[str] = ["*"]  # 允许的跨域来源，生产环境建议改为具体域名列表

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
