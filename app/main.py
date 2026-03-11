"""
FastAPI 应用主入口
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 将项目根目录添加到 python path，以便直接运行 main.py 时能找到 app 模块
sys.path.append(str(Path(__file__).resolve().parent.parent))

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.cache.redis_client import redis_client
from app.config import get_settings
from app.db.engine import async_session, engine
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

    yield

    # ── Graceful Shutdown：等待后台 create_task 完成，防止消息/审计日志丢失 ──
    pending = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    if pending:
        log.info("等待后台任务完成", count=len(pending))
        # 最多等 10 秒，超时则取消（避免 Pod 被 SIGKILL 强杀）
        done, not_done = await asyncio.wait(pending, timeout=10)
        if not_done:
            log.warning("后台任务超时未完成，强制取消", count=len(not_done))
            for task in not_done:
                task.cancel()

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

# ── 全局异常处理器：统一响应信封 ──
from app.api.response import fail  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException):
    """HTTPException → 统一信封（code = HTTP 状态码 × 100）"""
    return fail(
        code=exc.status_code * 100,
        message=str(exc.detail),
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    """Pydantic 请求校验错误 → 统一信封（code=42200）"""
    errors = [
        {
            "field": ".".join(str(loc) for loc in err["loc"]),
            "message": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    return fail(code=42200, message="请求参数校验失败", status_code=422, data=errors)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    """未捕获异常 → 统一信封（code=50000）"""
    log.error("未捕获异常", error=str(exc), exc_info=True)
    message = str(exc) if settings.ENV == "development" else "服务器内部错误，请稍后重试"
    return fail(code=50000, message=message, status_code=500)


# ── 中间件（执行顺序：从下往上注册，从上往下执行） ──
app.add_middleware(RequestLoggerMiddleware)
app.add_middleware(MetricsMiddleware)
# CORS 最后注册 = 最外层，确保 preflight OPTIONS 请求在所有中间件之前被处理
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus 指标端点 ──
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# ── 路由注册 ──
from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.api.files import router as files_router
from app.api.plugins import router as plugins_router
from app.api.projects import router as projects_router
from app.api.project_files import router as project_files_router
from app.api.sessions import router as sessions_router
from app.security.login import router as auth_router
from app.api.users import router as users_router
from app.api.roles import router as roles_router
from app.api.sessions import router as sessions_router
from app.api.skills import router as skills_router
from app.api.cron_jobs import router as cron_jobs_router

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(plugins_router)
app.include_router(skills_router)
app.include_router(projects_router)
app.include_router(project_files_router)
app.include_router(sessions_router)
app.include_router(users_router)
app.include_router(roles_router)
app.include_router(sessions_router)
app.include_router(cron_jobs_router)


if __name__ == "__main__":
    import uvicorn
    # 允许直接运行 python app/main.py 启动服务
    # 如果配置中没有设置端口，默认使用 8000
    port = getattr(settings, "APP_PORT", 8000)
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
