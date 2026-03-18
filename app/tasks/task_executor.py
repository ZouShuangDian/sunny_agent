"""
异步任务执行函数。

被 create_task 工具入队后由 Worker 消费。
默认复用 run_agent_pipeline() 共享执行链路；
deep_research 类型通过 DeepResearchExecutor 执行（当前 import Mock/Perplexity，可切换）。
"""

import asyncio
import json
import time
import uuid as _uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import update

from app.cache.redis_client import RedisKeys, redis_client
from app.db.engine import async_session
from app.db.models.async_task import AsyncTask
from app.execution.pipeline import run_agent_pipeline
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import Message
from app.memory.working_memory import WorkingMemory
from app.notify import NotificationType, notify_user
# 开发调试用 mock 模式，回放录制事件，不消耗 token
# 上线时切回：from app.tasks.deep_research_perplexity import DeepResearchExecutor
from app.tasks.deep_research_mock import DeepResearchExecutor

log = structlog.get_logger()

# 独立实例，避免引用 pipeline.py 的私有变量
_chat_persistence = ChatPersistence(async_session)


async def execute_async_task(
    ctx: dict,
    *,
    task_id: str,
    session_id: str,
    usernumb: str,
    user_id: str,
    input_text: str,
    task_type: str = "deep_research",
) -> None:
    """arq 任务函数：执行异步 Agent 任务"""

    trace_id = f"task_{task_id[:8]}_{_uuid.uuid4().hex[:8]}"
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id, task_id=task_id, usernumb=usernumb, task_type=task_type,
    )

    # 任务描述前 30 字符，用于通知标题
    task_brief = input_text[:30] + ("..." if len(input_text) > 30 else "")

    # Step 1: 状态 → running（原子 UPDATE WHERE status='pending'，防止取消竞态）
    async with async_session() as db:
        result = await db.execute(
            update(AsyncTask)
            .where(AsyncTask.id == task_id, AsyncTask.status == "pending")
            .values(status="running", started_at=datetime.now(timezone.utc))
            .returning(AsyncTask.id)
        )
        if not result.scalar_one_or_none():
            log.info("任务状态已变更，跳过执行", task_id=task_id)
            return
        await db.commit()

    try:
        # Step 2: 按 task_type 分发执行策略
        if task_type == "deep_research":
            _blocks_json, reply = await _execute_deep_research(
                task_id=task_id, session_id=session_id, query=input_text,
            )
            actual_sid = session_id
        else:
            reply, actual_sid = await run_agent_pipeline(
                usernumb=usernumb,
                user_id=user_id,
                input_text=input_text,
                session_id=session_id,
                trace_id=trace_id,
                source="async_task",
            )

        # Step 3: 状态 → completed
        await asyncio.shield(_update_task_status(
            task_id,
            status="completed",
            result_summary=reply[:500] if reply else None,
        ))

        # Step 4: 通知用户
        await asyncio.shield(notify_user(
            usernumb,
            notify_type=NotificationType.TASK_COMPLETED,
            title=f"后台任务「{task_brief}」已完成",
            content=reply[:200] if reply else "任务执行完成",
            session_id=actual_sid,
            task_id=task_id,
        ))

        log.info("异步任务执行成功", task_id=task_id, reply_len=len(reply))

    except asyncio.CancelledError:
        log.warning("异步任务执行超时（arq job_timeout）")
        await asyncio.shield(_update_task_status(
            task_id, status="timeout", error="执行时间超过限制",
        ))
        await asyncio.shield(notify_user(
            usernumb,
            notify_type=NotificationType.TASK_FAILED,
            title=f"后台任务「{task_brief}」执行超时",
            content="任务执行时间超过限制，已被终止",
            session_id=session_id,
            task_id=task_id,
        ))
        raise

    except Exception as e:
        log.exception("异步任务执行失败")
        await asyncio.shield(_update_task_status(
            task_id, status="failed", error=str(e)[:500],
        ))
        await asyncio.shield(notify_user(
            usernumb,
            notify_type=NotificationType.TASK_FAILED,
            title=f"后台任务「{task_brief}」执行失败",
            content=str(e)[:200],
            session_id=session_id,
            task_id=task_id,
        ))

    finally:
        structlog.contextvars.unbind_contextvars("trace_id", "task_id", "usernumb")


async def _update_task_status(
    task_id: str,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> None:
    """回写任务状态"""
    values: dict = {"status": status}
    if result_summary is not None:
        values["result_summary"] = result_summary
    if error is not None:
        values["error_message"] = error
    if status in ("completed", "failed", "timeout"):
        values["completed_at"] = datetime.now(timezone.utc)

    async with async_session() as db:
        await db.execute(
            update(AsyncTask).where(AsyncTask.id == task_id).values(**values)
        )
        await db.commit()


# ── 深度研究执行逻辑 ──

async def _execute_deep_research(task_id: str, session_id: str, query: str) -> tuple[str, str]:
    """
    通过 DeepResearchExecutor 执行深度研究，处理进度推送和结果持久化。

    职责：
    - 接收 executor 输出的标准化事件（stage + detail）
    - push_event 推送给前端 + 从事件中累积 sources 等数据
    - 研究完成后组装 blocks JSON 存入 Message.content
    - 返回 (blocks_json, plain_text) 二元组

    Returns:
        (blocks_json, plain_text)：blocks_json 已写入 DB，plain_text 供外层做 summary 和通知
    """
    event_key = RedisKeys.task_events(task_id)
    event_counter = 0
    usernumb = structlog.contextvars.get_contextvars().get("usernumb", "")

    # 研究过程累积数据（从 _on_raw_event 收集，用于最终组装 blocks）
    all_sources: list[dict] = []
    search_round_count = 0
    urls_fetched_count = 0

    async def push_event(data: dict) -> None:
        """Redis List（断线补偿）+ Pub/Sub（实时推送）双写"""
        nonlocal event_counter
        event_counter += 1
        event = {"event_id": event_counter, **data}
        payload = json.dumps(event, ensure_ascii=False)
        await redis_client.rpush(event_key, payload)
        await redis_client.publish(RedisKeys.task_channel(task_id), payload)

    async def _on_raw_event(event: dict) -> None:
        """推送标准化事件给前端 + 累积研究过程数据用于 blocks 组装"""
        nonlocal search_round_count, urls_fetched_count
        stage = event.get("stage", "")
        detail = event.get("detail", {})

        # 从事件中累积数据
        if stage == "searching":
            search_round_count = detail.get("round", search_round_count)
        elif stage == "search_done":
            all_sources.extend(detail.get("results", []))
        elif stage == "read_done":
            urls_fetched_count += detail.get("count", 0)
            all_sources.extend(detail.get("contents", []))

        # 推送给前端
        await push_event(event)

    try:
        executor = DeepResearchExecutor()
        result = await executor.execute(query=query, on_progress=_on_raw_event)

        # 组装 blocks JSON（一条 message 存多个 block）
        blocks = [
            {
                "type": "text",
                "content": "即将开始研究，预计需要 5-10 分钟，完成后会通知您。",
            },
            {
                "type": "research_process",
                "title": "已搜集和分析资料",
                "search_rounds": search_round_count,
                "sources_count": len(all_sources),
                "urls_fetched": urls_fetched_count,
                "status": "completed",
            },
        ]
        # 来源列表（最多 20 个，避免存储过大）
        if all_sources:
            blocks.append({
                "type": "research_sources",
                "sources": all_sources[:20],
            })
        # 研究报告正文
        blocks.append({
            "type": "research_report",
            "title": query[:100],
            "content": result.content,
            "char_count": len(result.content),
        })

        blocks_json = json.dumps(blocks, ensure_ascii=False)
        plain_text = result.content  # 纯文本，供 summary 和通知使用

        # 写入 messages 表 + WorkingMemory
        msg = Message(
            role="assistant",
            content=blocks_json,
            timestamp=time.time(),
            message_id=str(_uuid.uuid4()),
            intent_primary="deep_research",
        )
        await _chat_persistence.save_message(session_id, msg)
        memory = WorkingMemory(redis_client)
        await memory.append_message(session_id, msg)

        await push_event({
            "stage": "done",
            "detail": {
                "session_id": session_id,
                "message_id": msg.message_id,
                "title": query[:50],
            },
        })

        return blocks_json, plain_text

    except TimeoutError as e:
        # error → 任务 SSE 关闭进度卡片；外层 notify_user → 通知中心推送 Toast，两者互补
        await push_event({"stage": "error", "detail": {"message": f"请求超时: {e}", "retryable": True}})
        raise
    except Exception as e:
        error_msg = str(e)[:500]
        retryable = "timeout" in error_msg.lower() or "429" in error_msg
        await push_event({"stage": "error", "detail": {"message": error_msg, "retryable": retryable}})
        raise
    finally:
        await redis_client.expire(event_key, 3600)
