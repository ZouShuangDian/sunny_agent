"""
/chat 对话接口：串联意图理解 + 护栏校验 + 执行层全链路

端点：
- POST /chat        — 非流式 JSON 响应
- POST /chat/stream — SSE 流式响应

所有用户输入统一走 IntentEngine（包括 greeting），不再有预分类拦截。
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
from app.execution.router import ExecutionRouter
from app.guardrails.schemas import IntentResult
from app.guardrails.validator import GuardrailsValidator
from app.intent.clarify_handler import ClarifyHandler
from app.intent.context_builder import ContextBuilder
from app.intent.intent_engine import IntentEngine
from app.intent.output_assembler import OutputAssembler
from app.llm.client import LLMClient
from app.memory.schemas import LastIntent, Message
from app.memory.working_memory import WorkingMemory
from app.observability.context import get_trace_id
from app.security.audit import audit_logger
from app.security.auth import AuthenticatedUser, get_current_user

# 注意：CodebookService 已从 Master 层移除，下沉至 Sub-Agent / Tool 层

router = APIRouter(tags=["对话"])
log = structlog.get_logger()

# ── 单例组件（无状态，可复用） ──
clarify_handler = ClarifyHandler()
output_assembler = OutputAssembler()
guardrails = GuardrailsValidator()
llm_client = LLMClient()
execution_router = ExecutionRouter(llm_client)


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

    # 上下文组装（无状态：仅加载 memory + 用户信息）
    context_builder = ContextBuilder(memory)
    context = await context_builder.build(
        user_input=message,
        user=user,
        session_id=session_id,
    )

    # 意图引擎（LLM）
    intent_engine = IntentEngine(llm_client)
    intent_result = await intent_engine.analyze(
        user_input=message,
        context=context,
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

    # 1. 初始化会话
    memory = WorkingMemory(redis)
    session_id = body.session_id or str(uuid.uuid4())

    if not await memory.exists(session_id):
        await memory.init_session(session_id, user.id, user.usernumb)

    user_msg = Message(
        role="user",
        content=body.message,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
    )
    await memory.append_message(session_id, user_msg)

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
    if clarify_result.needs_clarify and clarify_result.question:
        reply_text = clarify_result.question
    else:
        exec_result = await execution_router.execute(
            intent_result=final_result,
            session_id=session_id,
        )
        reply_text = exec_result.reply

    # 4. 记录 assistant 消息
    assistant_msg = Message(
        role="assistant",
        content=reply_text,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
        intent_primary=final_result.intent.primary,
        route=final_result.route,
    )
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)

    # 5. 审计日志
    duration_ms = int((time.time() - start_time) * 1000)
    audit_logger.log_background(
        trace_id=trace_id,
        user_id=user.id,
        usernumb=user.usernumb,
        action="chat",
        route=final_result.route,
        input_text=body.message,
        duration_ms=duration_ms,
        metadata={
            "intent": final_result.intent.primary,
            "confidence": final_result.confidence,
            "complexity": final_result.complexity,
        },
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
            # 1. 初始化会话
            memory = WorkingMemory(redis)
            session_id = body.session_id or str(uuid.uuid4())

            if not await memory.exists(session_id):
                await memory.init_session(session_id, user.id, user.usernumb)

            user_msg = Message(
                role="user",
                content=body.message,
                timestamp=time.time(),
                message_id=str(uuid.uuid4()),
            )
            await memory.append_message(session_id, user_msg)

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

            # 5. 记录 assistant 消息
            reply_text = "".join(reply_chunks)
            assistant_msg = Message(
                role="assistant",
                content=reply_text,
                timestamp=time.time(),
                message_id=str(uuid.uuid4()),
                intent_primary=final_result.intent.primary,
                route=final_result.route,
            )
            await memory.append_message(session_id, assistant_msg)
            await memory.increment_turn(session_id)

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
