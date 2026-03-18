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

import asyncio
import time
import uuid as _uuid
from collections.abc import AsyncIterator

import structlog

from app.cache.redis_client import redis_client
from app.chat_ops import agent_scope, set_session_running, finalize_execution, cleanup_session
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
from app.streaming.events import SSEEvent
from app.feishu.project_manager import get_or_create_feishu_project, increment_project_file_count

log = structlog.get_logger()
settings = get_settings()

# 模块级单例（与 chat.py 中各自独立实例，共享同一 DB/Redis 连接池）
_llm_client = LLMClient()
_execution_router = ExecutionRouter(_llm_client)
_chat_persistence = ChatPersistence(async_session)

# source → sub_intent 映射表
_SOURCE_SUB_INTENT_MAP = {
    "chat": "chat",
    "cron": "cron_execution",
    "async_task": "async_task_execution",
}


# 流式事件类型
class PipelineStreamEvent:
    """Pipeline 流式事件类型"""
    STEP_COMPLETE = "step_complete"  # 步骤完成
    DELTA = "delta"                   # 答案片段
    FINISH = "finish"                 # 执行完成
    ERROR = "error"                   # 执行错误


# 工具展示配置: tool_name -> (图标, 格式化函数)
_TOOL_DISPLAY_CONFIG = {
    "web_search": ("🔍", lambda args: f"搜索: {args.get('query', '')}"),
    "web_fetch": ("🌐", lambda args: f"获取网页: {args.get('url', '')[:50]}..."),
    "bash_tool": ("⚡", lambda args: "执行命令"),
    "read_file": ("📄", lambda args: f"读取: {args.get('path', '').split('/')[-1]}"),
    "write_file": ("✏️", lambda args: f"写入: {args.get('path', '').split('/')[-1]}"),
    "str_replace_file": ("📝", lambda args: f"编辑: {args.get('path', '').split('/')[-1]}"),
    "ask_user": ("❓", lambda args: "询问用户"),
    "todo_read": ("✅", lambda args: "查看待办"),
    "todo_write": ("✅", lambda args: "更新待办"),
    "cron_create": ("⏰", lambda args: "创建定时任务"),
    "cron_manage": ("⏰", lambda args: "管理定时任务"),
    "create_task": ("📋", lambda args: "创建异步任务"),
    "present_files": ("📁", lambda args: "展示文件"),
    "skill_call": ("🛠️", lambda args: f"调用技能: {args.get('skill_name', 'unknown')}"),
    "subagent_call": ("🤖", lambda args: f"调用子代理: {args.get('agent_name', 'unknown')}"),
}


def _format_step_info(tool_name: str, args: dict) -> str:
    """格式化步骤信息，只显示操作描述，不显示结果"""
    config = _TOOL_DISPLAY_CONFIG.get(tool_name, ("🔧", lambda a: tool_name))
    icon, formatter = config
    return f"{icon} {formatter(args)}"


async def run_agent_pipeline(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "chat",
    sub_intent: str | None = None,
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
        source: 会话来源（'chat' | 'cron' | 'async_task'）
        sub_intent: 可选，为空时按 source 自动映射

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

        actual_sub_intent = sub_intent or _SOURCE_SUB_INTENT_MAP.get(source, source)
        intent_result = IntentResult(
            intent=IntentDetail(
                primary="general",
                sub_intent=actual_sub_intent,
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
    feishu_chat_id: str | None = None,
    feishu_open_id: str | None = None,
    feishu_chat_type: str | None = None,
    feishu_app_id: str | None = None,
) -> AsyncIterator[dict]:
    """完整的 Agent 执行管线（流式版本）
    
    与 run_agent_pipeline 业务逻辑完全一致，但改为流式输出。
    每完成一个步骤会推送步骤信息，最后流式输出答案。
    
    区别：
    - 返回 AsyncIterator[dict]，调用方需使用 async for 消费
    - 支持 media_paths 参数（媒体文件提示）
    - 支持飞书会话映射（feishu_chat_id, feishu_open_id）
    - 每个步骤完成后推送 PipelineStreamEvent.STEP_COMPLETE 事件
    - 答案内容通过 PipelineStreamEvent.DELTA 事件流式推送
    
    事件格式：
    - {"event": "step_complete", "data": {"step": 1, "info": "🔍 搜索: xxx", "total_steps": 1}}
    - {"event": "delta", "data": {"content": "文本片段"}}
    - {"event": "finish", "data": {"reply": "完整答案", "steps": [...], "finish_meta": {...}}}
    
    Args:
        usernumb: 用户工号
        user_id: 用户 UUID（对应 users.id）
        input_text: 投喂给 Agent 的用户消息
        session_id: 可选，为空则每次新建
        trace_id: 可选，用于日志追踪
        source: 会话来源（'chat' | 'cron' | 'feishu'）
        media_paths: 媒体文件路径列表（可选）
        feishu_chat_id: 飞书会话 ID（可选，用于飞书会话映射）
        feishu_open_id: 飞书用户 ID（可选，用于飞书会话映射）
        feishu_chat_type: 飞书会话类型（可选，'p2p' | 'group'）
    
    Yields:
        流式事件字典
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())
    
    # ← 新增：如果是飞书来源，创建/更新会话映射
    if source == "feishu" and feishu_chat_id and feishu_open_id:
        try:
            await _create_or_update_feishu_session_mapping(
                chat_id=feishu_chat_id,
                open_id=feishu_open_id,
                session_id=sid,
                user_id=user_id,
                chat_type=feishu_chat_type or "p2p",
                app_id=feishu_app_id,
            )
        except Exception as e:
            log.warning("Failed to create Feishu session mapping",
                       chat_id=feishu_chat_id,
                       open_id=feishu_open_id,
                       error=str(e))
    
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
    
    # 状态追踪
    current_step: dict | None = None
    steps_history: list[dict] = []
    reply_chunks: list[str] = []
    
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
        
        # -- Step 4: agent_scope + 流式执行 --
        async with agent_scope(sid, _chat_persistence) as scope:
            # 使用 execute_stream 替代 execute
            async for event in _execution_router.execute_stream(intent_result, sid):
                evt_type = event["event"]
                evt_data = event["data"]
                
                # ── 步骤追踪 ──
                if evt_type == SSEEvent.TOOL_CALL:
                    current_step = {
                        "step": evt_data["step"],
                        "tool_name": evt_data["name"],
                        "args": evt_data["args"],
                    }
                
                elif evt_type == SSEEvent.TOOL_RESULT and current_step:
                    # 步骤完成，生成描述（不包含结果）
                    step_info = _format_step_info(
                        current_step["tool_name"], 
                        current_step["args"]
                    )
                    
                    steps_history.append({
                        "step": current_step["step"],
                        "info": step_info,
                    })
                    
                    yield {
                        "event": PipelineStreamEvent.STEP_COMPLETE,
                        "data": {
                            "step": current_step["step"],
                            "info": step_info,
                            "total_steps": len(steps_history),
                        }
                    }
                    current_step = None
                
                # ── 答案流式输出 ──
                elif evt_type == SSEEvent.DELTA:
                    content = evt_data.get("content", "")
                    reply_chunks.append(content)
                    yield {
                        "event": PipelineStreamEvent.DELTA,
                        "data": {"content": content}
                    }
                
                # ── 执行完成 ──
                elif evt_type == SSEEvent.FINISH:
                    yield {
                        "event": PipelineStreamEvent.FINISH,
                        "data": {
                            "reply": "".join(reply_chunks),
                            "steps": steps_history,
                            "finish_meta": evt_data,
                        }
                    }
            
            # -- Step 5: 记录 assistant 消息（流式执行完成后） --
            reply_text = "".join(reply_chunks)
            assistant_msg_id = str(_uuid.uuid4())
            assistant_msg = Message(
                role="assistant",
                content=reply_text,
                timestamp=time.time(),
                message_id=assistant_msg_id,
                intent_primary="general",
                route="deep_l3",
                tool_calls=None,  # 从 steps_history 提取（如有需要）
            )
            await memory.append_message(sid, assistant_msg)
            await memory.increment_turn(sid)
            
            scope.set_result(
                message_id=assistant_msg_id,
                msg=assistant_msg,
                reasoning_trace=None,
                l3_steps=[s["info"] for s in steps_history],
            )
        
    except Exception as e:
        log.error("Pipeline streaming error", error=str(e), exc_info=True)
        yield {
            "event": PipelineStreamEvent.ERROR,
            "data": {"error": str(e), "session_id": sid}
        }
        raise
    finally:
        reset_session_id(session_token)
        reset_user_id(user_token)


async def _create_or_update_feishu_session_mapping(
    *,
    chat_id: str,
    open_id: str,
    session_id: str,
    user_id: str,
    chat_type: str = "p2p",
    app_id: str | None = None,
):
    """
    创建或更新飞书会话与系统会话的映射关系
    
    Args:
        chat_id: 飞书会话 ID
        open_id: 飞书用户 ID
        session_id: 系统会话 ID
        user_id: 系统用户 UUID（字符串）
        chat_type: 飞书会话类型（'p2p' | 'group'）
    """
    from datetime import datetime
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from uuid import UUID
    
    from app.db.models.feishu import FeishuChatSessionMapping
    
    async with async_session() as db:
        # 尝试使用 PostgreSQL 的 ON CONFLICT 语法进行 upsert
        try:
            # 构建 INSERT 语句
            stmt = pg_insert(FeishuChatSessionMapping).values(
                chat_id=chat_id,
                open_id=open_id,
                session_id=session_id,
                chat_type=chat_type,
                user_id=UUID(user_id) if user_id else None,
                message_count=1,
                last_active_at=datetime.utcnow(),
                is_active=True,
            )
            
            # 如果冲突（chat_id + open_id 已存在），则更新
            stmt = stmt.on_conflict_do_update(
                index_elements=["chat_id", "open_id"],  # 唯一索引字段
                set_={
                    "session_id": session_id,
                    "last_active_at": datetime.utcnow(),
                    "message_count": FeishuChatSessionMapping.message_count + 1,
                    "is_active": True,
                },
            )
            
            await db.execute(stmt)
            await db.commit()
            
            # 如果是私聊且有 app_id，创建/获取对应的 Project
            if chat_type == "p2p" and app_id and user_id:
                try:
                    from uuid import UUID
                    from app.feishu.project_manager import get_or_create_feishu_project
                    
                    # 创建或获取项目
                    project = await get_or_create_feishu_project(
                        db=db,
                        app_id=app_id,
                        user_id=UUID(user_id),
                        company=None,  # 暂时不设置 company
                    )
                    
                    log.debug("Created/retrieved Feishu project",
                             project_id=str(project.id),
                             project_name=project.name,
                             chat_id=chat_id,
                             user_id=user_id)
                except Exception as proj_err:
                    log.warning("Failed to create Feishu project",
                               chat_id=chat_id,
                               user_id=user_id,
                               app_id=app_id,
                               error=str(proj_err))
                    # Project 创建失败不影响会话映射的创建
            
            log.debug("Feishu session mapping created/updated",
                     chat_id=chat_id,
                     open_id=open_id,
                     session_id=session_id,
                     chat_type=chat_type)
        except Exception as e:
            log.error("Failed to create Feishu session mapping",
                     chat_id=chat_id,
                     open_id=open_id,
                     error=str(e))
            # 回滚事务
            await db.rollback()
            raise


async def run_agent_pipeline_feishu(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "chat",
    media_paths: list[str] | None = None,
    feishu_chat_id: str | None = None,
    feishu_open_id: str | None = None,
    feishu_chat_type: str | None = None,
) -> tuple[str, str]:
    """完整的 Agent 执行管线（流式版本）
    
    与 run_agent_pipeline 业务逻辑完全一致，支持媒体文件处理。
    
    区别：
    - 支持 media_paths 参数（媒体文件提示）
    - 支持飞书会话映射（feishu_chat_id, feishu_open_id）
    - 可用于飞书等需要媒体处理的场景
    
    Args:
        usernumb: 用户工号
        user_id: 用户 UUID（对应 users.id）
        input_text: 投喂给 Agent 的用户消息
        session_id: 可选，为空则每次新建
        trace_id: 可选，用于日志追踪
        source: 会话来源（'chat' | 'cron' | 'feishu'）
        media_paths: 媒体文件路径列表（可选）
        feishu_chat_id: 飞书会话 ID（可选，用于飞书会话映射）
        feishu_open_id: 飞书用户 ID（可选，用于飞书会话映射）
        feishu_chat_type: 飞书会话类型（可选，'p2p' | 'group'）
    
    Returns:
        (reply_text, session_id) 二元组
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())
    
    # ← 新增：如果是飞书来源，创建/更新会话映射
    if source == "feishu" and feishu_chat_id and feishu_open_id:
        try:
            await _create_or_update_feishu_session_mapping(
                chat_id=feishu_chat_id,
                open_id=feishu_open_id,
                session_id=sid,
                user_id=user_id,
                chat_type=feishu_chat_type or "p2p",
            )
        except Exception as e:
            log.warning("Failed to create Feishu session mapping",
                       chat_id=feishu_chat_id,
                       open_id=feishu_open_id,
                       error=str(e))
    
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
        
        log.info("Constructing IntentResult",
                history_messages=history_messages,
                media_paths=media_paths)

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



async def run_agent_pipeline_stream(
    *,
    usernumb: str,
    user_id: str,
    input_text: str,
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "chat",
    sub_intent: str | None = None,
    feishu_chat_id: str | None = None,
    feishu_open_id: str | None = None,
    feishu_chat_type: str | None = None,
) -> AsyncIterator[dict]:
    """完整的 Agent 执行管线（流式）

    与 run_agent_pipeline 共享 Step 1-3（会话初始化、IntentResult 构造、用户消息记录），
    Step 4 改为 execute_stream 流式执行，yield SSE 事件。

    调用方自行处理 yield 出来的事件（如写入 Redis、推送 SSE 等）。

    事件格式（与 chat.py chat_stream 一致）：
        {"event": "delta",        "data": {"content": "文本片段"}}
        {"event": "tool_call",    "data": {"name": "...", "args": {...}}}
        {"event": "tool_result",  "data": {"name": "...", "result": "..."}}
        {"event": "context_usage","data": {...}}
        {"event": "finish",       "data": {"iterations": N, ...}}

    最后 yield 一个 done 事件（管线层追加）：
        {"event": "done", "data": {"session_id": "...", "reply": "...", "message_id": "..."}}

    Args:
        usernumb: 用户工号
        user_id: 用户 UUID
        input_text: 用户消息
        session_id: 可选，为空则新建
        trace_id: 可选
        source: 会话来源
        sub_intent: 可选

    Yields:
        dict — SSE 事件
    """
    sid = session_id or str(_uuid.uuid4())
    tid = trace_id or str(_uuid.uuid4())

    # ← 新增：如果是飞书来源，创建/更新会话映射
    if source == "feishu" and feishu_chat_id and feishu_open_id:
        try:
            await _create_or_update_feishu_session_mapping(
                chat_id=feishu_chat_id,
                open_id=feishu_open_id,
                session_id=sid,
                user_id=user_id,
                chat_type=feishu_chat_type or "p2p",
            )
        except Exception as e:
            log.warning("Failed to create Feishu session mapping",
                       chat_id=feishu_chat_id,
                       open_id=feishu_open_id,
                       error=str(e))

    user_token = set_user_id(usernumb)
    session_token = set_session_id(sid)

    try:
        memory = WorkingMemory(redis_client)

        # -- Step 1: 初始化会话 --
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

        actual_sub_intent = sub_intent or _SOURCE_SUB_INTENT_MAP.get(source, source)
        intent_result = IntentResult(
            intent=IntentDetail(
                primary="general",
                sub_intent=actual_sub_intent,
                user_goal=input_text,
            ),
            raw_input=input_text,
            session_id=sid,
            trace_id=tid,
            history_messages=history_messages,
        )

        # -- Step 3: 记录用户消息 --
        user_msg = Message(
            role="user",
            content=input_text,
            timestamp=time.time(),
            message_id=str(_uuid.uuid4()),
        )
        await memory.append_message(sid, user_msg)
        if settings.CHAT_PERSIST_ENABLED:
            await _chat_persistence.save_message(sid, user_msg)

        # -- Step 4: 流式执行 --
        await set_session_running(sid)
        reply_chunks: list[str] = []
        finish_meta: dict = {}
        stream_completed = False

        try:
            async for event in _execution_router.execute_stream(intent_result, sid):
                evt_type = event["event"]
                evt_data = event["data"]

                if evt_type == SSEEvent.DELTA:
                    reply_chunks.append(evt_data.get("content", ""))
                elif evt_type == SSEEvent.FINISH:
                    finish_meta = evt_data if isinstance(evt_data, dict) else {}

                # 所有事件都 yield 出去，由调用方决定怎么处理
                yield event

            stream_completed = True

        finally:
            reply_text = "".join(reply_chunks)

            if reply_text:
                # -- Step 5: 记录 assistant 消息 --
                _intent = actual_sub_intent or "general"
                assistant_msg = Message(
                    role="assistant",
                    content=reply_text,
                    timestamp=time.time(),
                    message_id=str(_uuid.uuid4()),
                    intent_primary=_intent,
                    route="deep_l3",
                    model=settings.LLM_DEFAULT_MODEL,
                )
                await memory.append_message(sid, assistant_msg)
                await memory.increment_turn(sid)

                if stream_completed:
                    # 正常完成：顺序持久化
                    asyncio.create_task(finalize_execution(
                        sid, _chat_persistence,
                        message_id=assistant_msg.message_id,
                        msg=assistant_msg,
                        reasoning_trace=None,
                        l3_steps=finish_meta.get("l3_steps"),
                    ))
                else:
                    # 中断：fire-and-forget 保底
                    _chat_persistence.save_message_background(sid, assistant_msg)

            if not stream_completed:
                asyncio.create_task(cleanup_session(sid))

        # yield done 事件（管线层追加，包含 session_id 和最终回复）
        if stream_completed and reply_text:
            yield {
                "event": "done",
                "data": {
                    "session_id": sid,
                    "reply": reply_text[:200],
                    "message_id": assistant_msg.message_id,
                },
            }

    finally:
        reset_session_id(session_token)
        reset_user_id(user_token)