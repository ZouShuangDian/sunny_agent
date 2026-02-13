"""
FastAPI 应用主入口
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 将项目根目录添加到 python path，以便直接运行 main.py 时能找到 app 模块
sys.path.append(str(Path(__file__).resolve().parent.parent))

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app
from sqlalchemy import text

from app.cache.redis_client import redis_client
from app.config import get_settings
from app.db.engine import async_session, engine
from app.intent.codebook_service import CodebookService
from app.observability.logging_config import setup_logging
from app.observability.metrics_middleware import MetricsMiddleware
from app.observability.request_logger import RequestLoggerMiddleware

settings = get_settings()

# 初始化日志（在 import 时就生效）
setup_logging(env=settings.ENV)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """应用生命周期：启动时预检依赖服务，关闭时清理资源"""
    log.info("应用启动", env=settings.ENV, app=settings.APP_NAME)

    # ── Warm-up：Fail Fast，依赖不可用时拒绝启动 ──
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("PG 连接正常")

    await redis_client.ping()
    log.info("Redis 连接正常")

    # ── 码表缓存预热：避免启动后缓存击穿 ──
    try:
        async with async_session() as db:
            codebook_svc = CodebookService(redis_client, db)
            count = await codebook_svc.warm_cache()
            log.info("码表缓存预热完成", count=count)
    except Exception as e:
        # 预热失败不阻止启动，但记录警告
        log.warning("码表缓存预热失败，将降级为逐条回源", error=str(e), exc_info=True)

    yield

    # 关闭数据库连接池
    await engine.dispose()
    # 关闭 Redis 连接池
    await redis_client.aclose()
    log.info("应用关闭，资源已释放")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

# ── 中间件（执行顺序：从下往上注册，从上往下执行） ──
app.add_middleware(RequestLoggerMiddleware)
app.add_middleware(MetricsMiddleware)

# ── Prometheus 指标端点 ──
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# ── 路由注册 ──
from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.security.login import router as auth_router

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)


if __name__ == "__main__":
    import uvicorn
    # 允许直接运行 python app/main.py 启动服务
    # 如果配置中没有设置端口，默认使用 8000
    port = getattr(settings, "APP_PORT", 8000)
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
