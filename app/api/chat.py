"""
/chat 对话接口：串联意图理解 + 护栏校验 + 执行层全链路

端点：
- POST /chat        — 非流式 JSON 响应
- POST /chat/stream — SSE 流式响应

所有用户输入统一走 IntentEngine（包括 greeting），不再有预分类拦截。

Week 7 新增：
- ChatPersistence 冷存储集成（PG write-behind）
- assistant 消息携带 tool_calls（W7）
- reasoning_trace 独立传入 PG（W6）
"""

import json
import time
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.cache.redis_client import get_redis
from app.config import get_settings
from app.db.engine import async_session
from app.execution.router import ExecutionRouter
from app.guardrails.schemas import IntentResult
from app.guardrails.validator import GuardrailsValidator
from app.intent.clarify_handler import ClarifyHandler
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
from app.memory.schemas import LastIntent, Message
from app.memory.working_memory import WorkingMemory
from app.observability.context import get_trace_id
from app.validator.output_validator import OutputValidator
from app.validator.schemas import ValidatorInput
from app.security.audit import audit_logger
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(tags=["对话"])
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
# Week 9 桩实现：codebook/knowledge/history Service 均为 None，Phase 3 替换
_context_strategies: dict[str, object] = {
    "greeting": MinimalStrategy(),
    "general_qa": MinimalStrategy(),
    "writing": MinimalStrategy(),
    "query": QueryStrategy(),       # Phase 3 注入 codebook_service, knowledge_service
    "analysis": AnalysisStrategy(),  # Phase 3 注入 codebook_service, knowledge_service, history_service
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


# ── 公共意图管线 ──

async def _run_intent_pipeline(
    message: str,
    user: AuthenticatedUser,
    session_id: str,
    memory: WorkingMemory,
    redis: aioredis.Redis,
    trace_id: str,
) -> tuple[IntentResult, "ClarifyResult"]:
    """
    公共意图理解管线，/chat 和 /chat/stream 共享。
    返回 (final_intent_result, clarify_result)。
    """
    from app.intent.clarify_handler import ClarifyResult

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

    return final_result, clarify_result


# ── 公共会话初始化（含 PG 回源） ──

async def _init_session(
    session_id: str,
    user: AuthenticatedUser,
    memory: WorkingMemory,
    first_message: str,
) -> None:
    """
    初始化会话：Redis 优先，miss 时尝试从 PG 回源。

    变更 1（Week 7）：增加 PG 回源逻辑。
    """
    if await memory.exists(session_id):
        return

    if settings.CHAT_PERSIST_ENABLED:
        # Redis miss → 尝试从 PG 恢复（load_history 已还原 tool_calls → ToolCall 对象）
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

    # 1. 初始化会话（含 PG 回源）
    memory = WorkingMemory(redis)
    session_id = body.session_id or str(uuid.uuid4())
    await _init_session(session_id, user, memory, body.message)

    # 2. 意图管线
    final_result, clarify_result = await _run_intent_pipeline(
        message=body.message,
        user=user,
        session_id=session_id,
        memory=memory,
        redis=redis,
        trace_id=trace_id,
    )

    # 3. 执行层
    exec_result = None
    if clarify_result.needs_clarify and clarify_result.question:
        reply_text = clarify_result.question
    else:
        exec_result = await execution_router.execute(
            intent_result=final_result,
            session_id=session_id,
        )

        # M06 输出校验（执行层输出 → 校验 → 最终回复）
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

    # 4. 记录消息（user 消息在执行完成后写入，避免意图分析时产生重复）
    user_msg = Message(
        role="user",
        content=body.message,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
    )
    await memory.append_message(session_id, user_msg)
    if settings.CHAT_PERSIST_ENABLED:
        chat_persistence.save_message_background(session_id, user_msg)

    # 变更 3（W7）：将执行层返回的 tool_calls 赋值到 Message
    assistant_msg = Message(
        role="assistant",
        content=reply_text,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
        intent_primary=final_result.intent.primary,
        route=final_result.route,
        tool_calls=exec_result.tool_calls if exec_result and exec_result.tool_calls else None,
    )
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)

    # 变更 4（W6）：assistant 消息持久化——携带 reasoning_trace
    if settings.CHAT_PERSIST_ENABLED:
        # reasoning_trace 从 ExecutionResult 提取，不走 Message（W6 决策）
        trace_data = (
            exec_result.reasoning_trace
            if exec_result and exec_result.reasoning_trace
            else None
        )
        chat_persistence.save_message_background(
            session_id, assistant_msg, reasoning_trace=trace_data,
        )

    # 5. 审计日志
    duration_ms = int((time.time() - start_time) * 1000)
    audit_metadata = {
        "intent": final_result.intent.primary,
        "confidence": final_result.confidence,
        "complexity": final_result.complexity,
    }
    # L3 额外审计字段
    if exec_result and exec_result.iterations > 0:
        audit_metadata["iterations"] = exec_result.iterations
        audit_metadata["is_degraded"] = exec_result.is_degraded
        if exec_result.token_usage:
            audit_metadata["token_usage"] = exec_result.token_usage

    audit_logger.log_background(
        trace_id=trace_id,
        user_id=user.id,
        usernumb=user.usernumb,
        action="chat",
        route=final_result.route,
        input_text=body.message,
        duration_ms=duration_ms,
        metadata=audit_metadata,
    )

    return ChatResponse(
        session_id=session_id,
        reply=reply_text,
        intent_result=final_result,
        needs_clarify=clarify_result.needs_clarify,
        clarify_question=clarify_result.question,
    )


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
            # 1. 初始化会话（含 PG 回源）
            memory = WorkingMemory(redis)
            session_id = body.session_id or str(uuid.uuid4())
            await _init_session(session_id, user, memory, body.message)

            # 2. 意图理解阶段
            yield _sse_event("status", {"phase": "understanding", "session_id": session_id})

            final_result, clarify_result = await _run_intent_pipeline(
                message=body.message,
                user=user,
                session_id=session_id,
                memory=memory,
                redis=redis,
                trace_id=trace_id,
            )

            yield _sse_event("status", {
                "phase": "intent_done",
                "route": final_result.route,
                "intent": final_result.intent.primary,
            })

            # 3. 追问 → 直接返回追问内容
            if clarify_result.needs_clarify and clarify_result.question:
                yield _sse_event("clarify", {
                    "question": clarify_result.question,
                    "session_id": session_id,
                })
                yield _sse_event("done", {"session_id": session_id})
                return

            # 4. 执行阶段
            yield _sse_event("status", {"phase": "executing", "route": final_result.route})

            reply_chunks: list[str] = []
            async for event in execution_router.execute_stream(
                intent_result=final_result,
                session_id=session_id,
            ):
                evt_type = event["event"]
                evt_data = event["data"]

                if evt_type == "delta":
                    reply_chunks.append(evt_data)
                    yield _sse_event("delta", evt_data)
                elif evt_type == "tool_call":
                    yield _sse_event("tool_call", evt_data)
                elif evt_type == "tool_result":
                    yield _sse_event("tool_result", evt_data)
                elif evt_type == "finish":
                    pass  # 下面统一发 done

            # 5. 记录消息（user 消息在执行完成后写入，避免意图分析时产生重复）
            user_msg = Message(
                role="user",
                content=body.message,
                timestamp=time.time(),
                message_id=str(uuid.uuid4()),
            )
            await memory.append_message(session_id, user_msg)
            if settings.CHAT_PERSIST_ENABLED:
                chat_persistence.save_message_background(session_id, user_msg)

            reply_text = "".join(reply_chunks)
            assistant_msg = Message(
                role="assistant",
                content=reply_text,
                timestamp=time.time(),
                message_id=str(uuid.uuid4()),
                intent_primary=final_result.intent.primary,
                route=final_result.route,
                # 流式模式下 tool_calls 暂不携带（execute_stream 未返回完整 ToolCall 列表）
                # 后续 D8 实现 L3 流式时完善
            )
            await memory.append_message(session_id, assistant_msg)
            await memory.increment_turn(session_id)

            # 持久化
            if settings.CHAT_PERSIST_ENABLED:
                chat_persistence.save_message_background(session_id, assistant_msg)

            # 6. 审计日志
            duration_ms = int((time.time() - start_time) * 1000)
            audit_logger.log_background(
                trace_id=trace_id,
                user_id=user.id,
                usernumb=user.usernumb,
                action="chat_stream",
                route=final_result.route,
                input_text=body.message,
                duration_ms=duration_ms,
                metadata={
                    "intent": final_result.intent.primary,
                    "confidence": final_result.confidence,
                },
            )

            yield _sse_event("done", {"session_id": session_id, "duration_ms": duration_ms})

        except Exception as e:
            log.error("SSE 流式处理异常", error=str(e), exc_info=True)
            yield _sse_event("error", {"message": "处理请求时发生错误，请稍后重试。"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
