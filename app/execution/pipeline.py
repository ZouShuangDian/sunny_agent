"""
共享的 Agent 执行链路（非流式）。

chat.py 和 Worker 都可调用此函数，保证执行逻辑单一来源。
Phase 1 仅 Worker 使用；chat.py 保持现状，后续重构时再统一。

严格对照 chat.py 实际代码确认签名（v1.3 三轮架构评审）：
- scope.set_result(msg=Message 对象)
- init_session / ensure_session 需要 user_id（UUID）
- append_message 接收 Message 对象
- 必须 set_session_id() 设置 ContextVar
"""

import time
import uuid as _uuid

import structlog

from app.cache.redis_client import redis_client
from app.chat_ops import agent_scope
from app.config import get_settings
from app.db.engine import async_session
from app.execution.router import ExecutionRouter
from app.execution.session_context import reset_session_id, set_session_id
from app.execution.user_context import reset_user_id, set_user_id
from app.guardrails.schemas import IntentDetail, IntentResult
from app.intent.context_builder import ContextBuilder
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import Message
from app.memory.working_memory import WorkingMemory

log = structlog.get_logger()
settings = get_settings()

# 模块级单例（与 chat.py 中各自独立实例，共享同一 DB/Redis 连接池）
_llm_client = LLMClient()
_execution_router = ExecutionRouter(_llm_client)
_chat_persistence = ChatPersistence(async_session)


async def run_agent_pipeline(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "chat",
) -> tuple[str, str]:
    """完整的 Agent 执行管线（非流式）

    严格对照 chat.py 实际代码复刻链路：
    1. set_user_id + set_session_id ContextVar
    2. init_session（Redis WorkingMemory + PG ChatSession）
    3. 加载历史 -> 构造 IntentResult
    4. 记录用户消息（Message 对象 -> WorkingMemory + PG）
    5. agent_scope（status running -> active 生命周期管理）
    6. execution_router.execute（L3 ReAct）
    7. 记录 assistant 消息 -> scope.set_result 持久化

    Args:
        usernumb: 用户工号
        user_id: 用户 UUID（对应 users.id）
        input_text: 投喂给 Agent 的用户消息
        session_id: 可选，为空则每次新建
        trace_id: 可选，用于日志追踪
        source: 会话来源（'chat' | 'cron'）

    Returns:
        (reply_text, session_id) 二元组
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())

    # ContextVar 设置
    user_token = set_user_id(usernumb)
    session_token = set_session_id(sid)

    try:
        memory = WorkingMemory(redis_client)

        # -- Step 1: 初始化会话（对照 chat.py._init_session） --
        if not await memory.exists(sid):
            if settings.CHAT_PERSIST_ENABLED:
                history = await _chat_persistence.load_history(sid)
                if history and history.messages:
                    await memory.init_session(sid, user_id, usernumb)
                    for msg in history.messages:
                        await memory.append_message(sid, msg)
                else:
                    await memory.init_session(sid, user_id, usernumb)
            else:
                await memory.init_session(sid, user_id, usernumb)

        if settings.CHAT_PERSIST_ENABLED:
            await _chat_persistence.ensure_session(sid, user_id, input_text, source=source)

        # -- Step 2: 构造 IntentResult --
        context_builder = ContextBuilder(memory)
        history_messages = await context_builder.load_history_messages(sid)

        intent_result = IntentResult(
            intent=IntentDetail(
                primary="general",
                sub_intent="cron_execution",
                user_goal=input_text,
            ),
            raw_input=input_text,
            session_id=sid,
            trace_id=tid,
            history_messages=history_messages,
        )

        # -- Step 3: 记录用户消息（对照 chat.py._record_user_message） --
        user_msg_id = str(_uuid.uuid4())
        user_msg = Message(
            role="user",
            content=input_text,
            timestamp=time.time(),
            message_id=user_msg_id,
        )
        await memory.append_message(sid, user_msg)
        if settings.CHAT_PERSIST_ENABLED:
            await _chat_persistence.save_message(sid, user_msg)

        # -- Step 4: agent_scope + 执行 --
        async with agent_scope(sid, _chat_persistence) as scope:
            exec_result = await _execution_router.execute(intent_result, sid)

            # -- Step 5: 记录 assistant 消息（对照 chat.py._record_assistant_message） --
            assistant_msg_id = str(_uuid.uuid4())
            assistant_msg = Message(
                role="assistant",
                content=exec_result.reply,
                timestamp=time.time(),
                message_id=assistant_msg_id,
                intent_primary="general",
                route="deep_l3",
                tool_calls=exec_result.tool_calls if exec_result.tool_calls else None,
            )
            await memory.append_message(sid, assistant_msg)
            await memory.increment_turn(sid)

            scope.set_result(
                message_id=assistant_msg_id,
                msg=assistant_msg,
                reasoning_trace=exec_result.reasoning_trace,
                l3_steps=exec_result.l3_steps,
            )

        return exec_result.reply, sid

    finally:
        reset_session_id(session_token)
        reset_user_id(user_token)


async def run_agent_pipeline_streaming(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "chat",
    media_paths: list[str] | None = None,
) -> tuple[str, str]:
    """完整的 Agent 执行管线（流式版本）
    
    与 run_agent_pipeline 业务逻辑完全一致，支持媒体文件处理。
    
    区别：
    - 支持 media_paths 参数（媒体文件提示）
    - 可用于飞书等需要媒体处理的场景
    
    Args:
        usernumb: 用户工号
        user_id: 用户 UUID（对应 users.id）
        input_text: 投喂给 Agent 的用户消息
        session_id: 可选，为空则每次新建
        trace_id: 可选，用于日志追踪
        source: 会话来源（'chat' | 'cron' | 'feishu'）
        media_paths: 媒体文件路径列表（可选）
    
    Returns:
        (reply_text, session_id) 二元组
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())
    
    # ContextVar 设置
    user_token = set_user_id(usernumb)
    session_token = set_session_id(sid)
    
    # 构建带媒体提示的输入
    full_input = input_text
    if media_paths:
        media_hint = f"[用户上传了{len(media_paths)}个媒体文件：{', '.join(media_paths)}]"
        if full_input:
            full_input = f"{media_hint}\n\n{full_input}"
        else:
            full_input = media_hint
    
    try:
        memory = WorkingMemory(redis_client)
        
        # -- Step 1: 初始化会话（与 run_agent_pipeline 完全一致） --
        if not await memory.exists(sid):
            if settings.CHAT_PERSIST_ENABLED:
                history = await _chat_persistence.load_history(sid)
                if history and history.messages:
                    await memory.init_session(sid, user_id, usernumb)
                    for msg in history.messages:
                        await memory.append_message(sid, msg)
                else:
                    await memory.init_session(sid, user_id, usernumb)
            else:
                await memory.init_session(sid, user_id, usernumb)
        
        if settings.CHAT_PERSIST_ENABLED:
            await _chat_persistence.ensure_session(sid, user_id, full_input, source=source)
        
        # -- Step 2: 构造 IntentResult（与 run_agent_pipeline 完全一致） --
        context_builder = ContextBuilder(memory)
        history_messages = await context_builder.load_history_messages(sid)
        
        intent_result = IntentResult(
            intent=IntentDetail(
                primary="general",
                sub_intent="feishu_chat" if media_paths else "cron_execution",
                user_goal=full_input,
            ),
            raw_input=full_input,
            session_id=sid,
            trace_id=tid,
            history_messages=history_messages,
        )
        
        # -- Step 3: 记录用户消息（与 run_agent_pipeline 完全一致） --
        user_msg_id = str(_uuid.uuid4())
        user_msg = Message(
            role="user",
            content=full_input,
            timestamp=time.time(),
            message_id=user_msg_id,
        )
        await memory.append_message(sid, user_msg)
        if settings.CHAT_PERSIST_ENABLED:
            await _chat_persistence.save_message(sid, user_msg)
        
        # -- Step 4: agent_scope + 执行（与 run_agent_pipeline 完全一致） --
        async with agent_scope(sid, _chat_persistence) as scope:
            exec_result = await _execution_router.execute(intent_result, sid)
            
            # -- Step 5: 记录 assistant 消息（与 run_agent_pipeline 完全一致） --
            assistant_msg_id = str(_uuid.uuid4())
            assistant_msg = Message(
                role="assistant",
                content=exec_result.reply,
                timestamp=time.time(),
                message_id=assistant_msg_id,
                intent_primary="general",
                route="deep_l3",
                tool_calls=exec_result.tool_calls if exec_result.tool_calls else None,
            )
            await memory.append_message(sid, assistant_msg)
            await memory.increment_turn(sid)
            
            scope.set_result(
                message_id=assistant_msg_id,
                msg=assistant_msg,
                reasoning_trace=exec_result.reasoning_trace,
                l3_steps=exec_result.l3_steps,
            )
        
        return exec_result.reply, sid
        
    finally:
        reset_session_id(session_token)
        reset_user_id(user_token)

