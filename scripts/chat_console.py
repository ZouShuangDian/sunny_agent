"""
控制台交互测试脚本：跳过 JWT 鉴权，直接运行完整对话链路

运行方式：
    poetry run python scripts/chat_console.py

支持命令：
    /new     — 开启新会话
    /debug   — 切换调试信息显示
    /quit    — 退出

[DB存储] 说明：
    凡是带有 # [DB存储] 注释的行，都是写入 PG 数据库的代码。
    如果不需要持久化，把这些行注释掉即可（对应的 import 和实例也一起注释）。
    Redis 写入（WorkingMemory）不带此标记，始终保留，否则多轮对话无法工作。
"""

import asyncio
import sys
import time
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession
from sqlalchemy import select

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.observability.logging_config import setup_logging
setup_logging()  # 初始化 structlog，避免日志乱码

from app.config import get_settings
settings = get_settings()
from app.cache.redis_client import redis_client
from app.db.engine import async_session                          # [DB存储] PG session 工厂
from app.db.models.user import User                              # [DB存储] 用于查询真实用户
from app.execution.plugin_context import PluginCommandContext, reset_plugin_context, set_plugin_context
from app.execution.router import ExecutionRouter
from app.execution.user_context import reset_user_id, set_user_id
from app.guardrails.schemas import IntentDetail, IntentResult
from app.intent.context_builder import ContextBuilder
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence          # [DB存储] PG 冷存储
from app.memory.schemas import L3Step, Message
from app.memory.working_memory import WorkingMemory
from app.plugins.service import plugin_service
from app.security.auth import AuthenticatedUser

# ── 组件初始化 ──
llm_client = LLMClient()
execution_router = ExecutionRouter(llm_client)
chat_persistence = ChatPersistence(async_session)                # [DB存储] 如不需要写 PG，注释此行


async def _load_real_user() -> AuthenticatedUser:               # [DB存储] 从 DB 查询真实用户
    """从 users 表加载第一个活跃用户作为测试用户（保证 chat_sessions FK 合法）"""
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.is_active == True).limit(1)
        )
        user = result.scalar_one_or_none()

    if user is None:
        raise RuntimeError(
            "users 表中没有活跃用户，无法写入 DB。\n"
            "请先通过 API /auth/register 注册一个账号，或把 [DB存储] 相关行注释掉跳过持久化。"
        )

    role_name = user.role.name if user.role else "admin"
    data_scope = user.data_scope if isinstance(user.data_scope, str) else "全部"

    print(f"\033[90m  使用真实用户: {user.usernumb} / {user.username} (role={role_name})\033[0m")
    return AuthenticatedUser(
        id=str(user.id),
        usernumb=user.usernumb,
        username=user.username,
        role=role_name,
        department=user.department,
        data_scope=data_scope,
    )


def _is_plugin_command(message: str) -> bool:
    """判断是否为 Plugin 命令格式（/{plugin}:{command} 开头）"""
    if not message.startswith("/"):
        return False
    return ":" in message.split()[0]


async def _handle_plugin_command(
    message: str,
    session_id: str,
    memory: WorkingMemory,
    mock_user: AuthenticatedUser,
    show_debug: bool,
) -> str:
    """Plugin 命令快速路径（对标 chat.py 的 _build_plugin_intent）"""
    start = time.time()
    trace_id = str(uuid.uuid4())[:8]

    cmd_part, _, user_context = message[1:].partition(" ")
    plugin_name, _, command_name = cmd_part.partition(":")

    # 记录用户消息
    user_msg = Message(role="user", content=message, timestamp=time.time(), message_id=str(uuid.uuid4()))
    await memory.append_message(session_id, user_msg)
    chat_persistence.save_message_background(session_id, user_msg)  # [DB存储]

    # 查询 Plugin 命令
    info = await plugin_service.get_user_command(plugin_name, command_name, mock_user.usernumb)
    if info is None:
        # Plugin 未找到：构造错误 IntentResult 走 L3，与生产 chat.py 行为对齐
        print(f"\033[93m  Plugin 命令未找到，走 L3 返回错误提示\033[0m")
        intent_result = IntentResult(
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
        uid_token = set_user_id(mock_user.usernumb)
        try:
            exec_result = await execution_router.execute(intent_result=intent_result, session_id=session_id)
            reply = exec_result.reply
        finally:
            reset_user_id(uid_token)
    else:
        command_md = plugin_service.read_command_content(info)
        plugin_skills = plugin_service.scan_plugin_skills(info)

        # 统一使用 to_llm_messages（含 compaction 节点包装）
        context_builder = ContextBuilder(memory)
        history_messages = await context_builder.load_history_messages(session_id)

        plugin_ctx = PluginCommandContext(
            plugin_name=plugin_name,
            command_name=command_name,
            command_md_content=command_md,
            plugin_skills=plugin_skills,
        )
        plugin_token = set_plugin_context(plugin_ctx)
        uid_token = set_user_id(mock_user.usernumb)

        try:
            intent_result = IntentResult(
                intent=IntentDetail(
                    primary="plugin_command",
                    sub_intent=f"{plugin_name}:{command_name}",
                    user_goal=f"执行 Plugin 命令 /{plugin_name}:{command_name}",
                ),
                raw_input=user_context or f"执行 {plugin_name}:{command_name}",
                session_id=session_id,
                trace_id=trace_id,
                history_messages=history_messages,
            )
            exec_result = await execution_router.execute(intent_result=intent_result, session_id=session_id)
            reply = exec_result.reply
        finally:
            reset_user_id(uid_token)
            reset_plugin_context(plugin_token)

        if show_debug:
            duration = int((time.time() - start) * 1000)
            print(f"\033[90m  ── Plugin Fast Path | /{plugin_name}:{command_name} | {duration}ms ──\033[0m")

    assistant_msg = Message(role="assistant", content=reply, timestamp=time.time(), message_id=str(uuid.uuid4()))
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)
    chat_persistence.save_message_background(session_id, assistant_msg)  # [DB存储]

    return reply


def _extract_ask_user_questions(exec_result) -> list[dict] | None:
    """从 exec_result.tool_calls 中提取 ask_user 工具的问题列表。"""
    if not exec_result or not exec_result.tool_calls:
        return None
    for tc in exec_result.tool_calls:
        name = tc.tool_name if hasattr(tc, "tool_name") else tc.get("tool_name")
        args = tc.arguments if hasattr(tc, "arguments") else tc.get("arguments")
        if name == "ask_user" and args:
            return args.get("questions")
    return None


async def chat_once(
    message: str,
    session_id: str,
    memory: WorkingMemory,
    mock_user: AuthenticatedUser,
    show_debug: bool = True,
) -> tuple[str, list[dict] | None]:
    """
    执行一轮完整对话。

    返回: (回复文本, ask_user 问题列表 或 None)
    """
    # Plugin 命令快速路径（绕过意图分析）
    if _is_plugin_command(message):
        reply = await _handle_plugin_command(message, session_id, memory, mock_user, show_debug)
        return reply, None

    start = time.time()
    trace_id = str(uuid.uuid4())[:8]

    # 1. 记录用户消息
    user_msg = Message(
        role="user",
        content=message,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
    )
    await memory.append_message(session_id, user_msg)
    chat_persistence.save_message_background(session_id, user_msg)   # [DB存储] 用户消息写 PG

    # 2. 加载历史 + 直接构造 IntentResult
    context_builder = ContextBuilder(memory)
    history_messages = await context_builder.load_history_messages(session_id)

    intent_result = IntentResult(
        intent=IntentDetail(primary="general", user_goal=message),
        raw_input=message,
        session_id=session_id,
        trace_id=trace_id,
        history_messages=history_messages,
    )

    # 3. 执行层
    uid_token = set_user_id(mock_user.usernumb)
    try:
        exec_result = await execution_router.execute(
            intent_result=intent_result,
            session_id=session_id,
        )
    finally:
        reset_user_id(uid_token)
    reply_text = exec_result.reply

    # 4. 记录 assistant 消息
    assistant_msg = Message(
        role="assistant",
        content=reply_text,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
        intent_primary=intent_result.intent.primary,
        route=intent_result.route,
        tool_calls=exec_result.tool_calls if exec_result and exec_result.tool_calls else None,
    )
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)

    # [DB存储] assistant 消息写 PG（含 reasoning_trace）
    trace_data = exec_result.reasoning_trace if exec_result and exec_result.reasoning_trace else None
    chat_persistence.save_message_background(                        # [DB存储]
        session_id, assistant_msg, reasoning_trace=trace_data,       # [DB存储]
    )                                                                # [DB存储]

    # [DB存储] L3 中间步骤写 PG
    if settings.CHAT_PERSIST_ENABLED and exec_result and exec_result.l3_steps:
        l3_step_objs = [L3Step(**s) for s in exec_result.l3_steps]
        chat_persistence.save_l3_steps_background(
            session_id, assistant_msg.message_id, l3_step_objs,
        )

    duration = int((time.time() - start) * 1000)

    # 调试信息
    if show_debug:
        print(f"\033[90m  ── route={intent_result.route} | intent={intent_result.intent.primary} "
              f"| {duration}ms ──\033[0m")
        if exec_result and exec_result.iterations > 0:
            print(f"\033[90m  ── L3: {exec_result.iterations} 步 | tokens={exec_result.token_usage} ──\033[0m")

    # 提取 ask_user 问题（若有）
    ask_questions = _extract_ask_user_questions(exec_result)
    return reply_text, ask_questions


async def main():
    """交互式对话主循环"""
    print("=" * 60)
    print("  Agent Sunny 控制台测试")
    print("  输入消息开始对话，支持多轮上下文")
    print("  命令: /new (新会话) | /debug (切换调试) | /quit (退出)")
    print("=" * 60)

    # [DB存储] 从 DB 加载真实用户（保证 FK 合法）
    mock_user = await _load_real_user()                              # [DB存储]

    redis = redis_client
    memory = WorkingMemory(redis)
    session_id = str(uuid.uuid4())
    show_debug = True
    pt_session = PromptSession()

    # 初始化会话
    await memory.init_session(session_id, mock_user.id, mock_user.usernumb)
    chat_persistence.ensure_session_background(                      # [DB存储] 会话写 PG
        session_id, mock_user.id, "控制台测试会话",                  # [DB存储]
    )                                                                # [DB存储]
    print(f"\033[90m  session: {session_id[:8]}...\033[0m\n")

    while True:
        try:
            user_input = (await pt_session.prompt_async("你: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        # 控制台命令
        if user_input == "/quit":
            print("再见！")
            break
        elif user_input == "/new":
            session_id = str(uuid.uuid4())
            await memory.init_session(session_id, mock_user.id, mock_user.usernumb)
            chat_persistence.ensure_session_background(              # [DB存储] 新会话写 PG
                session_id, mock_user.id, "控制台测试会话",          # [DB存储]
            )                                                        # [DB存储]
            print(f"\033[90m  新会话: {session_id[:8]}...\033[0m\n")
            continue
        elif user_input == "/debug":
            show_debug = not show_debug
            print(f"\033[90m  调试信息: {'开启' if show_debug else '关闭'}\033[0m\n")
            continue

        # 执行对话
        try:
            reply, ask_questions = await chat_once(user_input, session_id, memory, mock_user, show_debug)
            print(f"\n\033[36mSunny: \033[0m{reply}\n")

            # ask_user 交互式选择（3 个选项 + "其他"）
            if ask_questions:
                answers: list[dict] = []
                for qi, q in enumerate(ask_questions, 1):
                    print(f"\033[33m  问题 {qi}: {q['question']}\033[0m")
                    for oi, opt in enumerate(q["options"], 1):
                        print(f"    {oi}. {opt}")
                    print(f"    {len(q['options']) + 1}. 其他（自定义输入）")

                    while True:
                        try:
                            total = len(q["options"]) + 1  # 含"其他"
                            raw = (await pt_session.prompt_async(f"  选择(1-{total}): ")).strip()
                            idx = int(raw) - 1
                            if 0 <= idx < len(q["options"]):
                                selected = q["options"][idx]
                                break
                            if idx == len(q["options"]):
                                # 用户选择"其他"，提示输入
                                custom = (await pt_session.prompt_async("  请输入: ")).strip()
                                selected = custom if custom else q["options"][0]
                                break
                            print(f"\033[91m    请输入 1-{total} 之间的数字\033[0m")
                        except ValueError:
                            # 非数字输入直接视为自定义回答
                            selected = raw
                            break

                    answers.append({"question": q["question"], "answer": selected})
                    print(f"\033[90m    -> {selected}\033[0m")

                # 打包为结构化消息，自动发送下一轮对话
                answer_text = "\n".join(f"Q: {a['question']} A: {a['answer']}" for a in answers)
                print(f"\n\033[90m  ── 提交选择，继续对话 ──\033[0m")
                reply2, ask2 = await chat_once(answer_text, session_id, memory, mock_user, show_debug)
                print(f"\n\033[36mSunny: \033[0m{reply2}\n")
                # 如果连续反问（极少见），提示用户手动继续
                if ask2:
                    print("\033[93m  [提示] Agent 继续追问，请根据上方回复手动输入回答\033[0m\n")
        except Exception as e:
            print(f"\n\033[31m错误: {e}\033[0m\n")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
