"""
/chat 对话接口：直接构造 IntentResult + 执行层全链路

端点：
- POST /chat        — 非流式 JSON 响应
- POST /chat/stream — SSE 流式响应

Plugin 命令在意图管线内部处理（快速路径），不再需要独立的处理函数。
所有请求统一通过 L3 ReAct 引擎执行。
"""

import asyncio
import time
import uuid
from contextvars import Token as CtxToken

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.api.response import ApiResponse, ok
from app.streaming.events import SSEEvent, format_sse
from app.cache.redis_client import get_redis
from app.config import get_settings
from app.db.engine import async_session
from app.execution.router import ExecutionRouter
from app.execution.schemas import ExecutionResult
from app.guardrails.schemas import IntentDetail, IntentResult
from app.intent.context_builder import ContextBuilder
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import L3Step, Message
from app.memory.working_memory import WorkingMemory
from app.observability.context import get_trace_id
from app.validator.output_validator import OutputValidator
from app.validator.schemas import ValidatorInput
from app.execution.user_context import reset_user_id, set_user_id
from app.execution.plugin_context import (
    PluginCommandContext,
    reset_plugin_context,
    set_plugin_context,
)
from app.plugins.service import plugin_service
from app.security.audit import audit_logger
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api", tags=["对话"])
log = structlog.get_logger()
settings = get_settings()

# ── 单例组件（无状态，可复用） ──
llm_client = LLMClient()
execution_router = ExecutionRouter(llm_client)
chat_persistence = ChatPersistence(async_session)
output_validator = OutputValidator(llm_client)


# ── 请求/响应模型 ──

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # 首次对话不传，服务端生成


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    context_usage: dict | None = None  # 上下文用量


# ── 公共辅助函数 ──

async def _prune_l3_steps(session_id: str) -> None:
    """
    Level 1 DB 剪枝：从 l3_steps 尾部累加 token 估算，
    超出 PRUNE_PROTECT_TOKENS 的步骤标记为 compacted=True。
    """
    try:
        steps = await chat_persistence.load_l3_steps(session_id)
        if not steps:
            return

        prune_protect = settings.PRUNE_PROTECT_TOKENS
        accumulated = 0
        to_compact: list = []

        for step in reversed(steps):
            if step.compacted:
                accumulated += 10
                continue
            token_est = len(step.content) // 2
            if accumulated + token_est <= prune_protect:
                accumulated += token_est
            else:
                to_compact.append(step.id)

        if to_compact:
            await chat_persistence.mark_steps_compacted(to_compact)
            log.debug(
                "L3 DB 剪枝完成",
                session_id=session_id,
                compacted_count=len(to_compact),
            )
    except Exception as e:
        log.warning("L3 DB 剪枝失败", session_id=session_id, error=str(e))


def _save_compaction_genesis_block(session_id: str, summary_content: str) -> None:
    """Level 2 摘要写入 PG 作为 genesis block。"""
    genesis_msg = Message(
        role="assistant",
        content=summary_content,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
        is_compaction=True,
    )
    chat_persistence.save_message_background(session_id, genesis_msg)
    log.info("Level 2 genesis block 已写入 PG", session_id=session_id)


async def _record_conversation(
    session_id: str,
    memory: WorkingMemory,
    user_content: str,
    reply_text: str,
    intent_primary: str,
    route: str,
    exec_result: ExecutionResult | None = None,
) -> tuple[Message, Message]:
    """
    统一消息记录：user + assistant → Redis + PG。
    返回 (user_msg, assistant_msg) 供后续使用。
    """
    user_msg = Message(
        role="user", content=user_content,
        timestamp=time.time(), message_id=str(uuid.uuid4()),
    )
    await memory.append_message(session_id, user_msg)
    if settings.CHAT_PERSIST_ENABLED:
        chat_persistence.save_message_background(session_id, user_msg)

    assistant_msg = Message(
        role="assistant", content=reply_text,
        timestamp=time.time(), message_id=str(uuid.uuid4()),
        intent_primary=intent_primary, route=route,
        tool_calls=exec_result.tool_calls if exec_result and exec_result.tool_calls else None,
    )
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)

    if settings.CHAT_PERSIST_ENABLED:
        trace_data = exec_result.reasoning_trace if exec_result and exec_result.reasoning_trace else None
        chat_persistence.save_message_background(session_id, assistant_msg, reasoning_trace=trace_data)

    return user_msg, assistant_msg


def _post_process_execution(
    session_id: str,
    message_id: str,
    exec_result: ExecutionResult | None = None,
    compaction_summary: str | None = None,
    l3_steps_override: list[dict] | None = None,
) -> None:
    """
    统一执行后处理：l3_steps 持久化 + DB 剪枝 + genesis block。

    参数说明：
    - exec_result: 非流式路径传入完整 ExecutionResult（含 l3_steps）
    - l3_steps_override: 流式路径传入（l3_steps 通过 finish 事件传递）
    - compaction_summary: 显式传入摘要内容（通过返回值/事件传递，避免并发竞态）
    """
    if not settings.CHAT_PERSIST_ENABLED:
        return

    # l3_steps 持久化 + Level 1 DB 剪枝
    l3_steps = None
    if exec_result and exec_result.l3_steps:
        l3_steps = exec_result.l3_steps
    elif l3_steps_override:
        l3_steps = l3_steps_override

    if l3_steps:
        l3_step_objs = [L3Step(**s) for s in l3_steps]
        chat_persistence.save_l3_steps_background(session_id, message_id, l3_step_objs)
        asyncio.create_task(_prune_l3_steps(session_id))

    # Level 2 genesis block 持久化
    if compaction_summary:
        _save_compaction_genesis_block(session_id, compaction_summary)


# ── Plugin 命令处理（集成到意图管线）──

async def _build_plugin_intent(
    message: str,
    user: AuthenticatedUser,
    session_id: str,
    memory: WorkingMemory,
    trace_id: str,
) -> tuple[IntentResult, CtxToken] | tuple[None, None]:
    """
    Plugin 命令检测 + synthetic IntentResult 构造。

    返回值：
    - 成功：(IntentResult, plugin_context_token)
    - 失败：(None, None)
    """
    cmd_part, _, user_context = message[1:].partition(" ")
    plugin_name, _, command_name = cmd_part.partition(":")
    plugin_name, command_name = plugin_name.strip(), command_name.strip()

    if not plugin_name or not command_name:
        return None, None

    info = await plugin_service.get_user_command(plugin_name, command_name, user.usernumb)
    if info is None:
        return None, None

    try:
        command_content = plugin_service.read_command_content(info)
    except FileNotFoundError:
        return None, None

    plugin_skills = plugin_service.scan_plugin_skills(info)

    # 设置 PluginCommandContext ContextVar，返回 Token 供调用方 reset
    plugin_ctx = PluginCommandContext(
        plugin_name=plugin_name,
        command_name=command_name,
        command_md_content=command_content,
        plugin_skills=plugin_skills,
    )
    plugin_token = set_plugin_context(plugin_ctx)

    # 加载历史消息（统一使用 to_llm_messages，含 compaction 节点包装）
    context_builder = ContextBuilder(memory)
    history_messages = await context_builder.load_history_messages(session_id)

    raw_input = user_context.strip() if user_context.strip() else f"执行 {plugin_name}:{command_name}"
    intent_result = IntentResult(
        intent=IntentDetail(
            primary="plugin_command",
            sub_intent=f"{plugin_name}:{command_name}",
            user_goal=f"执行 Plugin 命令 /{plugin_name}:{command_name}",
        ),
        raw_input=raw_input,
        session_id=session_id,
        trace_id=trace_id,
        history_messages=history_messages,
    )
    return intent_result, plugin_token


def _build_plugin_error_result(message: str, session_id: str, trace_id: str) -> IntentResult:
    """Plugin 命令未找到时，构造一个直接返回错误提示的 IntentResult。"""
    cmd_part = message.split()[0][1:]  # 去掉 /
    plugin_name, _, command_name = cmd_part.partition(":")
    return IntentResult(
        intent=IntentDetail(
            primary="plugin_command",
            sub_intent="not_found",
            user_goal=f"未找到 Plugin 命令 /{plugin_name}:{command_name}",
        ),
        raw_input=f"未找到 Plugin 命令 `/{plugin_name}:{command_name}`，请确认 Plugin 已上传且命令名正确。",
        session_id=session_id,
        trace_id=trace_id,
        history_messages=[],
    )


# ── 公共意图管线 ──

async def _run_intent_pipeline(
    message: str,
    user: AuthenticatedUser,
    session_id: str,
    memory: WorkingMemory,
    redis: aioredis.Redis,
    trace_id: str,
) -> tuple[IntentResult, CtxToken | None]:
    """
    公共意图管线，/chat 和 /chat/stream 共享。
    返回 (intent_result, plugin_token)。

    Plugin 命令在此内部处理：跳过意图分析，直接构造 synthetic intent。
    非 Plugin 路径：加载历史 + 直接构造 IntentResult。
    """
    # Plugin 命令快速路径
    if message.startswith("/") and ":" in message.split()[0]:
        intent_result, plugin_token = await _build_plugin_intent(
            message, user, session_id, memory, trace_id,
        )
        if intent_result is not None:
            return intent_result, plugin_token
        # Plugin 未找到 → 直接返回错误
        return _build_plugin_error_result(message, session_id, trace_id), None

    # 非 Plugin：加载历史 + 直接构造 IntentResult
    context_builder = ContextBuilder(memory)
    history_messages = await context_builder.load_history_messages(session_id)

    intent_result = IntentResult(
        intent=IntentDetail(primary="general", user_goal=message),
        raw_input=message,
        session_id=session_id,
        trace_id=trace_id,
        history_messages=history_messages,
    )
    return intent_result, None


# ── 公共会话初始化（含 PG 回源） ──

async def _init_session(
    session_id: str,
    user: AuthenticatedUser,
    memory: WorkingMemory,
    first_message: str,
) -> None:
    """初始化会话：Redis 优先，miss 时尝试从 PG 回源。"""
    if await memory.exists(session_id):
        return

    if settings.CHAT_PERSIST_ENABLED:
        pg_history = await chat_persistence.load_history(session_id)
        if pg_history and pg_history.messages:
            await memory.init_session(session_id, user.id, user.usernumb)
            for msg in pg_history.messages:
                await memory.append_message(session_id, msg)
            log.info(
                "会话从 PG 恢复",
                session_id=session_id,
                msg_count=len(pg_history.messages),
            )
            return

    # 全新会话
    await memory.init_session(session_id, user.id, user.usernumb)
    if settings.CHAT_PERSIST_ENABLED:
        chat_persistence.ensure_session_background(session_id, user.id, first_message)


# ── POST /chat — 非流式 JSON 响应 ──

@router.post("/chat", response_model=ApiResponse[ChatResponse])
async def chat(
    body: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
):
    """主对话入口：直接构造意图 + 执行层"""
    start_time = time.time()
    trace_id = get_trace_id()
    memory = WorkingMemory(redis)
    session_id = body.session_id or str(uuid.uuid4())
    await _init_session(session_id, user, memory, body.message)

    # 意图管线（Plugin 命令在管线内部处理）
    final_result, plugin_token = await _run_intent_pipeline(
        message=body.message, user=user, session_id=session_id,
        memory=memory, redis=redis, trace_id=trace_id,
    )

    try:
        # 执行（统一 L3）
        _uid_token = set_user_id(user.usernumb)
        try:
            exec_result = await execution_router.execute(
                intent_result=final_result, session_id=session_id,
            )
        finally:
            reset_user_id(_uid_token)

        # 输出校验
        if settings.OUTPUT_VALIDATOR_ENABLED and exec_result.tool_calls:
            validator_out = await output_validator.validate(ValidatorInput(
                execution_output=exec_result.reply,
                tool_calls=exec_result.tool_calls,
                reasoning_trace=exec_result.reasoning_trace,
                enable_hallucination=settings.OUTPUT_VALIDATOR_HALLUCINATION,
            ))
            reply_text = validator_out.validated_output
        else:
            reply_text = exec_result.reply

        # 统一消息记录
        _, assistant_msg = await _record_conversation(
            session_id, memory, body.message, reply_text,
            final_result.intent.primary, final_result.route, exec_result,
        )

        # 统一执行后处理（compaction_summary 通过返回值传递，避免并发竞态）
        _post_process_execution(
            session_id, assistant_msg.message_id,
            exec_result=exec_result,
            compaction_summary=exec_result.compaction_summary if exec_result else None,
        )

        # 审计
        duration_ms = int((time.time() - start_time) * 1000)
        audit_logger.log_background(
            trace_id=trace_id, user_id=user.id, usernumb=user.usernumb,
            action="chat", route=final_result.route,
            input_text=body.message, duration_ms=duration_ms,
            metadata={
                "intent": final_result.intent.primary,
                **({"iterations": exec_result.iterations, "is_degraded": exec_result.is_degraded}
                   if exec_result and exec_result.iterations > 0 else {}),
                **({"token_usage": exec_result.token_usage}
                   if exec_result and exec_result.token_usage else {}),
            },
        )

        return ok(data=ChatResponse(
            session_id=session_id, reply=reply_text,
            context_usage=exec_result.context_usage if exec_result else None,
        ))
    finally:
        # Plugin ContextVar 精确还原（非 Plugin 路径 plugin_token=None，跳过 reset）
        if plugin_token is not None:
            reset_plugin_context(plugin_token)


# ── POST /chat/stream — SSE 流式响应 ──

_sse_event = format_sse  # 本地别名，保持调用处代码不变

# done 事件字段白名单：内部持久化字段（l3_steps / compaction_summary）不透传给前端
_DONE_FIELDS = {"iterations", "llm_calls", "is_degraded", "token_usage"}


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
):
    """SSE 流式对话入口"""

    async def event_generator():
        start_time = time.time()
        trace_id = get_trace_id()
        try:
            memory = WorkingMemory(redis)
            session_id = body.session_id or str(uuid.uuid4())
            await _init_session(session_id, user, memory, body.message)

            # 返回二元组：Plugin 路径返回 plugin_token，非 Plugin 路径 plugin_token=None
            final_result, plugin_token = await _run_intent_pipeline(
                message=body.message, user=user, session_id=session_id,
                memory=memory, redis=redis, trace_id=trace_id,
            )

            try:
                # 执行（统一 L3）
                yield _sse_event(SSEEvent.STATUS, {"phase": "executing"})
                _uid_token = set_user_id(user.usernumb)
                reply_chunks: list[str] = []
                finish_meta: dict = {}
                try:
                    async for event in execution_router.execute_stream(
                        intent_result=final_result, session_id=session_id,
                    ):
                        evt_type = event["event"]
                        evt_data = event["data"]
                        if evt_type == SSEEvent.DELTA:
                            reply_chunks.append(evt_data.get("content", ""))
                            yield _sse_event(SSEEvent.DELTA, evt_data)
                        elif evt_type in (SSEEvent.TOOL_CALL, SSEEvent.TOOL_RESULT, SSEEvent.CONTEXT_USAGE):
                            yield _sse_event(evt_type, evt_data)
                        elif evt_type == SSEEvent.FINISH:
                            finish_meta = evt_data if isinstance(evt_data, dict) else {}
                        else:
                            log.debug("execute_stream 产生了未处理的事件类型，已丢弃", evt_type=evt_type)
                finally:
                    reset_user_id(_uid_token)

                reply_text = "".join(reply_chunks)

                # 统一消息记录
                _, assistant_msg = await _record_conversation(
                    session_id, memory, body.message, reply_text,
                    final_result.intent.primary, final_result.route,
                )

                # 统一执行后处理（流式路径通过 finish_meta 传入）
                _post_process_execution(
                    session_id, assistant_msg.message_id,
                    l3_steps_override=finish_meta.get("l3_steps"),
                    compaction_summary=finish_meta.get("compaction_summary"),
                )

                # 审计（与非流式路径对称）
                duration_ms = int((time.time() - start_time) * 1000)
                audit_logger.log_background(
                    trace_id=trace_id, user_id=user.id, usernumb=user.usernumb,
                    action="chat_stream", route=final_result.route,
                    input_text=body.message, duration_ms=duration_ms,
                    metadata={
                        "intent": final_result.intent.primary,
                        **({"iterations": finish_meta["iterations"], "is_degraded": finish_meta["is_degraded"]}
                           if "iterations" in finish_meta else {}),
                        **({"token_usage": finish_meta["token_usage"]}
                           if "token_usage" in finish_meta else {}),
                    },
                )
                done_meta = {k: finish_meta[k] for k in _DONE_FIELDS if k in finish_meta}
                yield _sse_event(SSEEvent.DONE, {"session_id": session_id, "duration_ms": duration_ms, **done_meta})
            finally:
                # Plugin ContextVar 精确还原（非 Plugin 路径 plugin_token=None，跳过 reset）
                if plugin_token is not None:
                    reset_plugin_context(plugin_token)

        except Exception as e:
            log.error("SSE 流式处理异常", error=str(e), exc_info=True)
            yield _sse_event(SSEEvent.ERROR, {"message": "处理请求时发生错误，请稍后重试。"})
            yield _sse_event(SSEEvent.DONE, {"session_id": body.session_id or "", "error": True})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
