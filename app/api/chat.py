"""
/chat 对话接口：串联意图理解 + 护栏校验 + 执行层全链路

端点：
- POST /chat        — 非流式 JSON 响应
- POST /chat/stream — SSE 流式响应

Plugin 命令在意图管线内部处理（快速路径），不再需要独立的处理函数。
所有请求统一通过 L3 ReAct 引擎执行。
"""

import asyncio
import json
import time
import uuid
from contextvars import Token as CtxToken

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.cache.redis_client import get_redis
from app.config import get_settings
from app.db.engine import async_session
from app.execution.router import ExecutionRouter
from app.execution.schemas import ExecutionResult
from app.guardrails.schemas import IntentDetail, IntentResult
from app.guardrails.validator import GuardrailsValidator
from app.intent.clarify_handler import ClarifyHandler, ClarifyResult
from app.intent.context_builder import ContextBuilder
from app.intent.context_strategy import (
    AnalysisStrategy,
    MinimalStrategy,
    QueryStrategy,
)
from app.intent.intent_engine import IntentEngine
from app.intent.output_assembler import OutputAssembler
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import L3Step, LastIntent, Message
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
clarify_handler = ClarifyHandler()
output_assembler = OutputAssembler()
guardrails = GuardrailsValidator()
llm_client = LLMClient()
execution_router = ExecutionRouter(llm_client)
chat_persistence = ChatPersistence(async_session)
output_validator = OutputValidator(llm_client)

# W4：Context Strategy 无状态单例（应用启动时实例化一次，所有请求共享）
_context_strategies: dict[str, object] = {
    "greeting": MinimalStrategy(),
    "general_qa": MinimalStrategy(),
    "writing": MinimalStrategy(),
    "query": QueryStrategy(),
    "analysis": AnalysisStrategy(),
}


# ── 请求/响应模型 ──

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # 首次对话不传，服务端生成


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent_result: IntentResult | None = None  # 结构化结果（调试用）
    needs_clarify: bool = False
    clarify_question: str | None = None
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

    # 加载历史消息
    conv_history = await memory.get_history(session_id)
    history_messages = [
        {"role": m.role, "content": m.content}
        for m in conv_history.messages[-10:]
        if m.role in ("user", "assistant")
    ]

    raw_input = user_context.strip() if user_context.strip() else f"执行 {plugin_name}:{command_name}"
    intent_result = IntentResult(
        route="deep_l3",
        complexity="complex",
        confidence=1.0,
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
        route="deep_l3",
        complexity="simple",
        confidence=1.0,
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
) -> tuple[IntentResult, ClarifyResult, CtxToken | None]:
    """
    公共意图理解管线，/chat 和 /chat/stream 共享。
    返回 (final_intent_result, clarify_result, plugin_token)。

    Plugin 命令在此内部处理：跳过 LLM 意图分析，直接构造 synthetic intent。
    """
    # Plugin 命令快速路径：跳过 LLM 意图分析，构造 synthetic intent
    if message.startswith("/") and ":" in message.split()[0]:
        intent_result, plugin_token = await _build_plugin_intent(
            message, user, session_id, memory, trace_id,
        )
        if intent_result is not None:
            # Plugin 命令不走追问
            return intent_result, ClarifyResult(needs_clarify=False), plugin_token
        # Plugin 未找到 → 直接返回错误（不降级到普通意图分析）
        error_result = _build_plugin_error_result(message, session_id, trace_id)
        return error_result, ClarifyResult(needs_clarify=False), None

    # 正常意图分析流程
    # 阶段 1：基础上下文（一次 Redis 读取）→ 意图分析
    context_builder = ContextBuilder(memory, strategies=_context_strategies)
    basic_context = await context_builder.build(
        user_input=message,
        user=user,
        session_id=session_id,
    )

    # 意图引擎（LLM）
    intent_engine = IntentEngine(llm_client)
    intent_result = await intent_engine.analyze(
        user_input=message,
        context=basic_context,
    )

    # 阶段 2：增量加载扩展上下文（复用 basic_context，零次 Redis 读取）
    context = await context_builder.enrich(
        base_context=basic_context,
        user_input=message,
        session_id=session_id,
        intent_hint=intent_result.intent_primary,
    )

    # 追问检查
    clarify_result = clarify_handler.check_and_clarify(intent_result)

    # 输出组装
    assembled = output_assembler.assemble(
        user_input=message,
        intent_result=intent_result,
        context=context,
        clarify=clarify_result,
        user=user,
        session_id=session_id,
        trace_id=trace_id,
    )

    # 护栏校验
    guardrails_output = guardrails.validate(
        raw_json=intent_result.raw_json,
        raw_input=message,
        session_id=session_id,
        trace_id=trace_id,
    )

    final_result = assembled if not guardrails_output.fell_back else guardrails_output.result

    # 保存本轮意图快照（用于连续追问判断）
    await memory.save_last_intent(
        session_id,
        LastIntent(
            primary=final_result.intent.primary,
            sub_intent=final_result.intent.sub_intent,
            route=final_result.route,
            complexity=final_result.complexity,
            confidence=final_result.confidence,
            needs_clarify=final_result.needs_clarify,
            clarify_question=final_result.clarify_question,
        ),
    )

    return final_result, clarify_result, None  # 非 Plugin 路径 plugin_token=None


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

@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
):
    """主对话入口：意图理解 + 护栏校验 + 执行层"""
    start_time = time.time()
    trace_id = get_trace_id()
    memory = WorkingMemory(redis)
    session_id = body.session_id or str(uuid.uuid4())
    await _init_session(session_id, user, memory, body.message)

    # 意图管线（Plugin 命令在管线内部处理）
    final_result, clarify_result, plugin_token = await _run_intent_pipeline(
        message=body.message, user=user, session_id=session_id,
        memory=memory, redis=redis, trace_id=trace_id,
    )

    try:
        # 追问
        if clarify_result.needs_clarify and clarify_result.question:
            return ChatResponse(
                session_id=session_id, reply=clarify_result.question,
                needs_clarify=True, clarify_question=clarify_result.question,
            )

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
                "confidence": final_result.confidence,
                "complexity": final_result.complexity,
                **({"iterations": exec_result.iterations, "is_degraded": exec_result.is_degraded}
                   if exec_result and exec_result.iterations > 0 else {}),
                **({"token_usage": exec_result.token_usage}
                   if exec_result and exec_result.token_usage else {}),
            },
        )

        return ChatResponse(
            session_id=session_id, reply=reply_text,
            intent_result=final_result,
            context_usage=exec_result.context_usage if exec_result else None,
        )
    finally:
        # Plugin ContextVar 精确还原（非 Plugin 路径 plugin_token=None，跳过 reset）
        if plugin_token is not None:
            reset_plugin_context(plugin_token)


# ── POST /chat/stream — SSE 流式响应 ──

def _sse_event(event: str, data: str | dict) -> str:
    """格式化一条 SSE 事件"""
    data_str = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {data_str}\n\n"


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

            yield _sse_event("status", {"phase": "understanding", "session_id": session_id})

            # 返回三元组：Plugin 路径返回 plugin_token，非 Plugin 路径 plugin_token=None
            final_result, clarify_result, plugin_token = await _run_intent_pipeline(
                message=body.message, user=user, session_id=session_id,
                memory=memory, redis=redis, trace_id=trace_id,
            )

            try:
                yield _sse_event("status", {
                    "phase": "intent_done",
                    "route": final_result.route,
                    "intent": final_result.intent.primary,
                })

                # 追问
                if clarify_result.needs_clarify and clarify_result.question:
                    yield _sse_event("clarify", {"question": clarify_result.question, "session_id": session_id})
                    yield _sse_event("done", {"session_id": session_id})
                    return

                # 执行（统一 L3）
                yield _sse_event("status", {"phase": "executing"})
                _uid_token = set_user_id(user.usernumb)
                reply_chunks: list[str] = []
                finish_meta: dict = {}
                try:
                    async for event in execution_router.execute_stream(
                        intent_result=final_result, session_id=session_id,
                    ):
                        evt_type = event["event"]
                        evt_data = event["data"]
                        if evt_type == "delta":
                            reply_chunks.append(evt_data)
                            yield _sse_event("delta", evt_data)
                        elif evt_type in ("thought", "tool_call", "tool_result", "context_usage"):
                            yield _sse_event(evt_type, evt_data)
                        elif evt_type == "finish":
                            finish_meta = evt_data if isinstance(evt_data, dict) else {}
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
                        "confidence": final_result.confidence,
                        **({"iterations": finish_meta["iterations"], "is_degraded": finish_meta["is_degraded"]}
                           if "iterations" in finish_meta else {}),
                        **({"token_usage": finish_meta["token_usage"]}
                           if "token_usage" in finish_meta else {}),
                    },
                )
                yield _sse_event("done", {"session_id": session_id, "duration_ms": duration_ms, **finish_meta})
            finally:
                # Plugin ContextVar 精确还原（非 Plugin 路径 plugin_token=None，跳过 reset）
                if plugin_token is not None:
                    reset_plugin_context(plugin_token)

        except Exception as e:
            log.error("SSE 流式处理异常", error=str(e), exc_info=True)
            yield _sse_event("error", {"message": "处理请求时发生错误，请稍后重试。"})
            yield _sse_event("done", {"session_id": body.session_id or "", "error": True})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
