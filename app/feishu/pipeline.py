"""
Feishu Agent Pipeline 扩展
支持流式回调的Agent执行
"""

import asyncio
import time
import uuid as _uuid
from typing import AsyncIterator, Callable, List, Optional

import structlog

from app.cache.redis_client import redis_client
from app.chat_ops import agent_scope
from app.config import get_settings
from app.db.engine import async_session
from app.execution.pipeline import (
    _chat_persistence,
    _execution_router,
    _llm_client,
)
from app.execution.session_context import reset_session_id, set_session_id
from app.execution.user_context import reset_user_id, set_user_id
from app.guardrails.schemas import IntentDetail, IntentResult
from app.intent.context_builder import ContextBuilder
from app.memory.schemas import Message
from app.memory.working_memory import WorkingMemory

log = structlog.get_logger()
settings = get_settings()


async def run_agent_pipeline_streaming(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "feishu",
    media_paths: List[str] = None,
    on_token: Callable[[str], None] = None,
    on_complete: Callable[[str], None] = None,
) -> str:
    """
    流式Agent执行管线
    
    支持实时token回调，用于BlockStreaming流式卡片更新
    
    Args:
        usernumb: 用户工号
        user_id: 用户UUID
        input_text: 用户输入文本
        session_id: 可选会话ID
        trace_id: 可选追踪ID
        source: 来源（默认feishu）
        media_paths: 媒体文件路径列表
        on_token: token回调函数，每个生成的token都会调用
        on_complete: 完成回调函数，生成结束时调用
        
    Returns:
        完整的回复文本
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())
    
    # ContextVar 设置
    user_token = set_user_id(usernumb)
    session_token = set_session_id(sid)
    
    # 构建带媒体提示的输入
    full_input = input_text
    if media_paths:
        media_hint = f"[用户上传了{len(media_paths)}个媒体文件: {', '.join(media_paths)}]"
        if full_input:
            full_input = f"{media_hint}\n\n{full_input}"
        else:
            full_input = media_hint
    
    try:
        memory = WorkingMemory(redis_client)
        
        # -- Step 1: 初始化会话 --
        if not await memory.exists(sid):
            if settings.CHAT_PERSIST_ENABLED:
                from app.memory.chat_persistence import ChatPersistence
                persistence = ChatPersistence(async_session)
                history = await persistence.load_history(sid)
                if history and history.messages:
                    await memory.init_session(sid, user_id, usernumb)
                    for msg in history.messages:
                        await memory.append_message(sid, msg)
                else:
                    await memory.init_session(sid, user_id, usernumb)
            else:
                await memory.init_session(sid, user_id, usernumb)
        
        if settings.CHAT_PERSIST_ENABLED:
            from app.memory.chat_persistence import ChatPersistence
            persistence = ChatPersistence(async_session)
            await persistence.ensure_session(sid, user_id, full_input, source=source)
        
        # -- Step 2: 构造 IntentResult --
        context_builder = ContextBuilder(memory)
        history_messages = await context_builder.load_history_messages(sid)
        
        intent_result = IntentResult(
            intent=IntentDetail(
                primary="general",
                sub_intent="feishu_chat",
                user_goal=full_input,
            ),
            raw_input=full_input,
            session_id=sid,
            trace_id=tid,
            history_messages=history_messages,
        )
        
        # -- Step 3: 记录用户消息 --
        user_msg_id = str(_uuid.uuid4())
        user_msg = Message(
            role="user",
            content=full_input,
            timestamp=time.time(),
            message_id=user_msg_id,
        )
        await memory.append_message(sid, user_msg)
        if settings.CHAT_PERSIST_ENABLED:
            from app.memory.chat_persistence import ChatPersistence
            persistence = ChatPersistence(async_session)
            await persistence.save_message(sid, user_msg)
        
        # -- Step 4: 执行并收集流式输出 --
        reply_chunks = []
        
        async with agent_scope(sid, _chat_persistence) as scope:
            # TODO: 当execution_router支持流式输出时，使用流式执行
            # 目前先使用非流式执行，然后模拟流式效果
            exec_result = await _execution_router.execute(intent_result, sid)
            
            reply_text = exec_result.reply
            
            # 模拟流式输出效果（按字符分割）
            # 实际生产环境应该使用真正的流式LLM调用
            chunk_size = 5  # 每5个字符发送一次
            for i in range(0, len(reply_text), chunk_size):
                chunk = reply_text[i:i+chunk_size]
                reply_chunks.append(chunk)
                
                # 调用token回调
                if on_token:
                    on_token(chunk)
                
                # 小延迟模拟打字效果
                await asyncio.sleep(0.05)
            
            # -- Step 5: 记录 assistant 消息 --
            assistant_msg_id = str(_uuid.uuid4())
            assistant_msg = Message(
                role="assistant",
                content=reply_text,
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
        
        # 调用完成回调
        if on_complete:
            on_complete(reply_text)
        
        return reply_text
        
    finally:
        reset_session_id(session_token)
        reset_user_id(user_token)


# 保持与原pipeline兼容的接口
async def run_agent_pipeline_with_streaming(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "feishu",
    media_paths: List[str] = None,
) -> AsyncIterator[str]:
    """
    异步生成器版本的流式执行
    
    使用示例:
        async for token in run_agent_pipeline_with_streaming(...):
            await update_streaming_card(token)
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())
    
    # ContextVar 设置
    user_token = set_user_id(usernumb)
    session_token = set_session_id(sid)
    
    # 构建带媒体提示的输入
    full_input = input_text
    if media_paths:
        media_hint = f"[用户上传了{len(media_paths)}个媒体文件: {', '.join(media_paths)}]"
        if full_input:
            full_input = f"{media_hint}\n\n{full_input}"
        else:
            full_input = media_hint
    
    try:
        memory = WorkingMemory(redis_client)
        
        # 初始化会话
        if not await memory.exists(sid):
            await memory.init_session(sid, user_id, usernumb)
        
        # 构造 IntentResult
        context_builder = ContextBuilder(memory)
        history_messages = await context_builder.load_history_messages(sid)
        
        intent_result = IntentResult(
            intent=IntentDetail(
                primary="general",
                sub_intent="feishu_chat",
                user_goal=full_input,
            ),
            raw_input=full_input,
            session_id=sid,
            trace_id=tid,
            history_messages=history_messages,
        )
        
        # 记录用户消息
        user_msg_id = str(_uuid.uuid4())
        user_msg = Message(
            role="user",
            content=full_input,
            timestamp=time.time(),
            message_id=user_msg_id,
        )
        await memory.append_message(sid, user_msg)
        
        # 执行并流式生成
        async with agent_scope(sid, _chat_persistence) as scope:
            exec_result = await _execution_router.execute(intent_result, sid)
            
            reply_text = exec_result.reply
            
            # 按字符流式输出
            for char in reply_text:
                yield char
                await asyncio.sleep(0.01)  # 10ms延迟模拟打字效果
            
            # 记录 assistant 消息
            assistant_msg_id = str(_uuid.uuid4())
            assistant_msg = Message(
                role="assistant",
                content=reply_text,
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
        
    finally:
        reset_session_id(session_token)
        reset_user_id(user_token)
