"""
/chat 对话接口：串联意图理解 + 护栏校验 + 执行层全链路

端点：
- POST /chat        — 非流式 JSON 响应
- POST /chat/stream — SSE 流式响应

Plugin 命令快速路径：
- 消息以 "/{plugin}:{command}" 开头时，绕过意图分析直接路由到 L3
- COMMAND.md 内容通过 PluginCommandContext ContextVar 注入 system prompt

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
from app.guardrails.schemas import IntentDetail, IntentResult
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


# ── Plugin 命令处理（快速路径，绕过意图分析直接进 L3）──

def _is_plugin_command(message: str) -> bool:
    """判断消息是否为 Plugin 命令格式（/{plugin}:{command} 开头）"""
    if not message.startswith("/"):
        return False
    first_token = message.split()[0]  # 取第一个空格前的内容
    return ":" in first_token


async def _handle_plugin_command(
    body: "ChatRequest",
    user: AuthenticatedUser,
    redis: "aioredis.Redis",
) -> "ChatResponse":
    """
    Plugin 命令快速路径处理（非流式）。

    解析命令 → 查 DB → 读文件 → 设置 ContextVar → L3 执行 → 记录消息 → 返回。
    整个过程绕过意图理解管线，直接构造 synthetic IntentResult 进 L3。
    """
    trace_id = get_trace_id()
    start_time = time.time()

    # 1. 解析命令
    cmd_part, _, user_context = body.message[1:].partition(" ")
    plugin_name, _, command_name = cmd_part.partition(":")
    plugin_name = plugin_name.strip()
    command_name = command_name.strip()

    if not plugin_name or not command_name:
        return ChatResponse(
            session_id=body.session_id or str(uuid.uuid4()),
            reply="命令格式错误，请使用 `/{plugin-name}:{command-name}` 格式。",
        )

    # 2. 初始化 session（与正常路径完全相同）
    memory = WorkingMemory(redis)
    session_id = body.session_id or str(uuid.uuid4())
    await _init_session(session_id, user, memory, body.message)

    # 3. 查询 DB 获取命令信息
    info = await plugin_service.get_user_command(plugin_name, command_name, user.usernumb)
    if info is None:
        reply = (
            f"未找到 Plugin 命令 `/{plugin_name}:{command_name}`，"
            f"请确认 Plugin 已上传且命令名正确。"
        )
        # 记录一轮消息（即使未找到命令，也保留对话历史）
        user_msg = Message(
            role="user", content=body.message,
            timestamp=time.time(), message_id=str(uuid.uuid4()),
        )
        await memory.append_message(session_id, user_msg)
        assistant_msg = Message(
            role="assistant", content=reply,
            timestamp=time.time(), message_id=str(uuid.uuid4()),
        )
        await memory.append_message(session_id, assistant_msg)
        await memory.increment_turn(session_id)
        return ChatResponse(session_id=session_id, reply=reply)

    # 4. 读取 COMMAND.md + 扫描 Plugin Skills
    try:
        command_content = plugin_service.read_command_content(info)
    except FileNotFoundError:
        return ChatResponse(
            session_id=session_id,
            reply="Plugin 命令文件不存在，请重新上传 Plugin 包。",
        )

    plugin_skills = plugin_service.scan_plugin_skills(info)

    # 5. 加载历史消息（用于 IntentResult.history_messages）
    conv_history = await memory.get_history(session_id)
    history_messages = [
        {"role": m.role, "content": m.content}
        for m in conv_history.messages[-10:]
        if m.role in ("user", "assistant")
    ]

    # 6. 构建 PluginCommandContext + 设置 ContextVar
    plugin_ctx = PluginCommandContext(
        plugin_name=plugin_name,
        command_name=command_name,
        command_md_content=command_content,
        plugin_skills=plugin_skills,
    )
    plugin_token = set_plugin_context(plugin_ctx)

    # 7. 构建 synthetic IntentResult（精确对标 IntentResult/IntentDetail 字段）
    raw_input = user_context.strip() if user_context.strip() else f"执行 {plugin_name}:{command_name}"
    synthetic_intent = IntentResult(
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

    # 8. 执行 L3
    exec_result = None
    _uid_token = set_user_id(user.usernumb)
    try:
        exec_result = await execution_router.execute(
            intent_result=synthetic_intent,
            session_id=session_id,
        )
    finally:
        reset_user_id(_uid_token)
        reset_plugin_context(plugin_token)

    reply_text = exec_result.reply if exec_result else "Plugin 命令执行失败，请重试。"

    # 9. 记录消息（与正常路径完全相同）
    user_msg = Message(
        role="user", content=body.message,
        timestamp=time.time(), message_id=str(uuid.uuid4()),
    )
    await memory.append_message(session_id, user_msg)
    if settings.CHAT_PERSIST_ENABLED:
        chat_persistence.save_message_background(session_id, user_msg)

    assistant_msg = Message(
        role="assistant", content=reply_text,
        timestamp=time.time(), message_id=str(uuid.uuid4()),
        intent_primary="plugin_command",
        route="deep_l3",
        tool_calls=exec_result.tool_calls if exec_result and exec_result.tool_calls else None,
    )
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)
    if settings.CHAT_PERSIST_ENABLED:
        trace_data = exec_result.reasoning_trace if exec_result and exec_result.reasoning_trace else None
        chat_persistence.save_message_background(session_id, assistant_msg, reasoning_trace=trace_data)

    # 10. 审计日志
    duration_ms = int((time.time() - start_time) * 1000)
    audit_logger.log_background(
        trace_id=trace_id,
        user_id=user.id,
        usernumb=user.usernumb,
        action="plugin_command",
        route="deep_l3",
        input_text=body.message,
        duration_ms=duration_ms,
        metadata={
            "plugin_name": plugin_name,
            "command_name": command_name,
            "iterations": exec_result.iterations if exec_result else 0,
        },
    )

    return ChatResponse(session_id=session_id, reply=reply_text)


async def _handle_plugin_command_stream(
    body: "ChatRequest",
    user: AuthenticatedUser,
    redis: "aioredis.Redis",
):
    """
    Plugin 命令快速路径处理（流式 SSE 版本）。
    与 _handle_plugin_command 逻辑相同，但通过 execute_stream 推送事件。
    """
    trace_id = get_trace_id()
    start_time = time.time()

    cmd_part, _, user_context = body.message[1:].partition(" ")
    plugin_name, _, command_name = cmd_part.partition(":")
    plugin_name, command_name = plugin_name.strip(), command_name.strip()

    if not plugin_name or not command_name:
        yield _sse_event("error", {"message": "命令格式错误，请使用 /{plugin-name}:{command-name} 格式。"})
        yield _sse_event("done", {})
        return

    memory = WorkingMemory(redis)
    session_id = body.session_id or str(uuid.uuid4())
    await _init_session(session_id, user, memory, body.message)

    yield _sse_event("status", {"phase": "plugin_command", "session_id": session_id,
                                 "plugin": plugin_name, "command": command_name})

    info = await plugin_service.get_user_command(plugin_name, command_name, user.usernumb)
    if info is None:
        reply = f"未找到 Plugin 命令 `/{plugin_name}:{command_name}`，请确认 Plugin 已上传且命令名正确。"
        yield _sse_event("delta", reply)
        yield _sse_event("done", {"session_id": session_id})
        return

    try:
        command_content = plugin_service.read_command_content(info)
    except FileNotFoundError:
        yield _sse_event("error", {"message": "Plugin 命令文件不存在，请重新上传 Plugin 包。"})
        yield _sse_event("done", {"session_id": session_id})
        return

    plugin_skills = plugin_service.scan_plugin_skills(info)
    conv_history = await memory.get_history(session_id)
    history_messages = [
        {"role": m.role, "content": m.content}
        for m in conv_history.messages[-10:]
        if m.role in ("user", "assistant")
    ]

    plugin_ctx = PluginCommandContext(
        plugin_name=plugin_name,
        command_name=command_name,
        command_md_content=command_content,
        plugin_skills=plugin_skills,
    )
    plugin_token = set_plugin_context(plugin_ctx)

    raw_input = user_context.strip() if user_context.strip() else f"执行 {plugin_name}:{command_name}"
    synthetic_intent = IntentResult(
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

    _uid_token = set_user_id(user.usernumb)
    reply_chunks: list[str] = []
    try:
        async for event in execution_router.execute_stream(
            intent_result=synthetic_intent,
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
            elif evt_type == "thought":
                yield _sse_event("thought", evt_data)
    finally:
        reset_user_id(_uid_token)
        reset_plugin_context(plugin_token)

    reply_text = "".join(reply_chunks)

    user_msg = Message(role="user", content=body.message,
                       timestamp=time.time(), message_id=str(uuid.uuid4()))
    await memory.append_message(session_id, user_msg)
    if settings.CHAT_PERSIST_ENABLED:
        chat_persistence.save_message_background(session_id, user_msg)

    assistant_msg = Message(role="assistant", content=reply_text,
                            timestamp=time.time(), message_id=str(uuid.uuid4()),
                            intent_primary="plugin_command", route="deep_l3")
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)
    if settings.CHAT_PERSIST_ENABLED:
        chat_persistence.save_message_background(session_id, assistant_msg)

    duration_ms = int((time.time() - start_time) * 1000)
    audit_logger.log_background(
        trace_id=trace_id, user_id=user.id, usernumb=user.usernumb,
        action="plugin_command_stream", route="deep_l3",
        input_text=body.message, duration_ms=duration_ms,
        metadata={"plugin_name": plugin_name, "command_name": command_name},
    )
    yield _sse_event("done", {"session_id": session_id, "duration_ms": duration_ms})


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

    # ── Plugin 命令快速路径（绕过意图分析直接进 L3）──
    if _is_plugin_command(body.message):
        return await _handle_plugin_command(body, user, redis)

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
        # 设置 user_id ContextVar（供 bash_tool/read_file/write_file 读取，用于路径隔离）
        _uid_token = set_user_id(user.usernumb)
        try:
            exec_result = await execution_router.execute(
                intent_result=final_result,
                session_id=session_id,
            )
        finally:
            reset_user_id(_uid_token)

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
        # ── Plugin 命令快速路径（绕过意图分析直接进 L3）──
        if _is_plugin_command(body.message):
            async for evt in _handle_plugin_command_stream(body, user, redis):
                yield evt
            return

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

            # 设置 user_id ContextVar（供沙箱工具读取，用于路径隔离）
            _uid_token = set_user_id(user.usernumb)
            reply_chunks: list[str] = []
            try:
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
            finally:
                reset_user_id(_uid_token)

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
