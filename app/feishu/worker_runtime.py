import asyncio
import json
import time

import structlog
from arq import create_pool
from arq.cron import cron
from arq.connections import RedisSettings

from app.cache.redis_client import FeishuRedisKeys, redis_client
from app.config import get_settings
from app.tasks.session_archive import archive_feishu_sessions

settings = get_settings()
logger = structlog.get_logger()

PROCESSING_QUEUE_WARN_THRESHOLD = 20
PROCESSING_QUEUE_WARN_INTERVAL_SECONDS = 60


async def startup(ctx):
    logger.info("Feishu Worker starting up")

    logger.info("Pre-initializing execution pipeline...")
    start_time = time.time()

    from app.execution import pipeline

    _ = pipeline._execution_router
    _ = pipeline._llm_client

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info("Execution pipeline pre-initialized", elapsed_ms=elapsed_ms)

    ctx["message_transfer_task"] = asyncio.create_task(message_transfer_loop())


async def shutdown(ctx):
    logger.info("Feishu Worker shutting down")

    if "message_transfer_task" in ctx:
        ctx["message_transfer_task"].cancel()

    from app.feishu.client import close_all_feishu_clients

    await close_all_feishu_clients()


async def message_transfer_loop():
    from app.feishu.tasks import process_feishu_message

    logger.info(
        "Message transfer loop started",
        source_queue=FeishuRedisKeys.EXTERNAL_WEBHOOK_QUEUE,
        target_queue=FeishuRedisKeys.ARQ_QUEUE,
    )
    arq_pool = await create_pool(WorkerSettings.redis_settings)
    last_backlog_warn_at = 0.0

    try:
        while True:
            try:
                last_backlog_warn_at = await _maybe_warn_processing_backlog(last_backlog_warn_at)
                message_json, source_queue = await _claim_next_transfer_message()
                if not message_json:
                    continue

                try:
                    feishu_event = json.loads(message_json)
                    message = _convert_feishu_event_to_message(feishu_event)

                    event_id = message.get("event_id")
                    message_id = message.get("message_id")
                    processed_key = FeishuRedisKeys.processed(event_id, message_id)

                    if await redis_client.exists(processed_key):
                        logger.info(
                            "Message already processed, acking processing queue item",
                            event_id=event_id,
                            message_id=message_id,
                            source_queue=source_queue,
                        )
                        await _ack_transfer_message(message_json)
                        continue

                    await arq_pool.enqueue_job(
                        process_feishu_message.__name__,
                        message,
                        _queue_name=FeishuRedisKeys.ARQ_QUEUE,
                    )
                    try:
                        await redis_client.setex(processed_key, 86400, "1")
                    except Exception as marker_err:
                        logger.error(
                            "Failed to write processed marker after ARQ enqueue",
                            event_id=event_id,
                            message_id=message_id,
                            error=str(marker_err),
                        )
                    await _ack_transfer_message(message_json)

                    logger.info(
                        "Message transferred to ARQ queue",
                        event_id=event_id,
                        message_id=message_id,
                        source_queue=source_queue,
                    )

                except json.JSONDecodeError as exc:
                    logger.error(
                        "Failed to parse message JSON",
                        error=str(exc),
                        message_preview=message_json[:200] if len(message_json) > 200 else message_json,
                    )
                    await _ack_transfer_message(message_json)

                except Exception as exc:
                    logger.error(
                        "Failed to transfer message into ARQ",
                        error=str(exc),
                        event_id=event_id if 'event_id' in locals() else "unknown",
                        source_queue=source_queue,
                    )
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.info("Message transfer loop cancelled")
                break
            except Exception as exc:
                logger.error("Message transfer error", error=str(exc))
                await asyncio.sleep(0.1)
    finally:
        await arq_pool.close()


async def _claim_next_transfer_message() -> tuple[str | None, str | None]:
    pending_message = await redis_client.lindex(FeishuRedisKeys.PROCESSING_QUEUE, -1)
    if pending_message:
        return pending_message, FeishuRedisKeys.PROCESSING_QUEUE

    moved_message = await redis_client.brpoplpush(
        FeishuRedisKeys.EXTERNAL_WEBHOOK_QUEUE,
        FeishuRedisKeys.PROCESSING_QUEUE,
        timeout=1,
    )
    if moved_message:
        return moved_message, FeishuRedisKeys.EXTERNAL_WEBHOOK_QUEUE
    return None, None


async def _ack_transfer_message(message_json: str) -> None:
    await redis_client.lrem(FeishuRedisKeys.PROCESSING_QUEUE, 1, message_json)


async def _maybe_warn_processing_backlog(last_warn_at: float) -> float:
    now = time.monotonic()
    if now - last_warn_at < PROCESSING_QUEUE_WARN_INTERVAL_SECONDS:
        return last_warn_at

    try:
        backlog_size = await redis_client.llen(FeishuRedisKeys.PROCESSING_QUEUE)
    except Exception as exc:
        logger.warning("Failed to inspect processing queue backlog", error=str(exc))
        return last_warn_at

    if backlog_size >= PROCESSING_QUEUE_WARN_THRESHOLD:
        logger.warning(
            "Feishu processing backlog is growing",
            queue=FeishuRedisKeys.PROCESSING_QUEUE,
            backlog_size=backlog_size,
            warn_threshold=PROCESSING_QUEUE_WARN_THRESHOLD,
        )
        return now

    return last_warn_at


def _convert_feishu_event_to_message(feishu_event: dict) -> dict:
    header = feishu_event.get("header", {})
    event = feishu_event.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", {})

    content_str = message.get("content", "{}")
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        content = {"text": content_str}

    return {
        "event_id": header.get("event_id", ""),
        "message_id": message.get("message_id", ""),
        "event_type": header.get("event_type", ""),
        "app_id": header.get("app_id", ""),
        "open_id": sender_id.get("open_id", ""),
        "chat_id": message.get("chat_id", ""),
        "chat_type": message.get("chat_type", "p2p"),
        "msg_type": message.get("message_type", "text"),
        "content": content,
        "mentions": message.get("mentions", []),
        "create_time": header.get("create_time", ""),
        "_raw_event": feishu_event,
    }


class WorkerSettings:
    from app.feishu.tasks import process_feishu_message

    functions = [process_feishu_message, archive_feishu_sessions]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = FeishuRedisKeys.ARQ_QUEUE

    max_jobs = 10
    job_timeout = 300
    retry_jobs = True
    max_tries = 3
    keep_result = 3600

    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = [
        cron(
            archive_feishu_sessions,
            hour=settings.FEISHU_SESSION_ARCHIVE_CUTOFF_HOUR,
            minute=settings.FEISHU_SESSION_ARCHIVE_CUTOFF_MINUTE,
        )
    ] if settings.FEISHU_SESSION_ARCHIVE_ENABLED else []
