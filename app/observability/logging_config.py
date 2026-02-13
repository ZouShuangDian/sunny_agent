"""
结构化日志配置：structlog + contextvars 自动注入 trace_id
- 开发环境：彩色文本输出
- 生产环境：JSON 输出（便于 Loki/ELK 解析）
"""

import logging
import sys

import structlog


def setup_logging(env: str = "development") -> None:
    """初始化结构化日志"""

    # 共享处理器链
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,  # 自动合并 trace_id 等上下文
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if env == "production":
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 让 SQLAlchemy / uvicorn 的日志也走 structlog 格式
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
