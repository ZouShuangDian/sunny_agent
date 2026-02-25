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

from app.cache.redis_client import redis_client
from app.db.engine import async_session                          # [DB存储] PG session 工厂
from app.db.models.user import User                              # [DB存储] 用于查询真实用户
from app.execution.router import ExecutionRouter
from app.guardrails.validator import GuardrailsValidator
from app.intent.clarify_handler import ClarifyHandler
from app.intent.context_builder import ContextBuilder
from app.intent.context_strategy import AnalysisStrategy, MinimalStrategy, QueryStrategy
from app.intent.intent_engine import IntentEngine
from app.intent.output_assembler import OutputAssembler
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence          # [DB存储] PG 冷存储
from app.memory.schemas import LastIntent, Message
from app.memory.working_memory import WorkingMemory
from app.security.auth import AuthenticatedUser

# ── 组件初始化 ──
llm_client = LLMClient()
clarify_handler = ClarifyHandler()
output_assembler = OutputAssembler()
guardrails = GuardrailsValidator()
execution_router = ExecutionRouter(llm_client)
chat_persistence = ChatPersistence(async_session)                # [DB存储] 如不需要写 PG，注释此行

# W4：Context Strategy 无状态单例（与 chat.py 保持一致）
_context_strategies = {
    "greeting": MinimalStrategy(),
    "general_qa": MinimalStrategy(),
    "writing": MinimalStrategy(),
    "query": QueryStrategy(),
    "analysis": AnalysisStrategy(),
}


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


async def chat_once(
    message: str,
    session_id: str,
    memory: WorkingMemory,
    mock_user: AuthenticatedUser,
    show_debug: bool = True,
) -> str:
    """执行一轮完整对话，返回回复文本"""
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

    # 2. 阶段 1：基础上下文（一次 Redis 读取）
    context_builder = ContextBuilder(memory, strategies=_context_strategies)
    basic_context = await context_builder.build(
        user_input=message,
        user=mock_user,
        session_id=session_id,
    )

    # 3. 意图引擎
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

    # 4. 追问检查
    clarify_result = clarify_handler.check_and_clarify(intent_result)

    # 5. 输出组装
    assembled = output_assembler.assemble(
        user_input=message,
        intent_result=intent_result,
        context=context,
        clarify=clarify_result,
        user=mock_user,
        session_id=session_id,
        trace_id=trace_id,
    )

    # 6. 护栏校验
    guardrails_output = guardrails.validate(
        raw_json=intent_result.raw_json,
        raw_input=message,
        session_id=session_id,
        trace_id=trace_id,
    )
    final_result = assembled if not guardrails_output.fell_back else guardrails_output.result

    # 7. 保存意图快照
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

    # 8. 执行层
    exec_result = None
    if clarify_result.needs_clarify and clarify_result.question:
        reply_text = clarify_result.question
    else:
        exec_result = await execution_router.execute(
            intent_result=final_result,
            session_id=session_id,
        )
        reply_text = exec_result.reply

    # 9. 记录 assistant 消息
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

    # [DB存储] assistant 消息写 PG（含 reasoning_trace）
    trace_data = exec_result.reasoning_trace if exec_result and exec_result.reasoning_trace else None
    chat_persistence.save_message_background(                        # [DB存储]
        session_id, assistant_msg, reasoning_trace=trace_data,       # [DB存储]
    )                                                                # [DB存储]

    duration = int((time.time() - start) * 1000)

    # 调试信息
    if show_debug:
        print(f"\033[90m  ── route={final_result.route} | intent={final_result.intent.primary} "
              f"| confidence={final_result.confidence:.2f} | complexity={final_result.complexity} "
              f"| {duration}ms ──\033[0m")
        if guardrails_output.fell_back:
            print(f"\033[93m  ⚠ 护栏降级触发\033[0m")
        if clarify_result.needs_clarify:
            print(f"\033[93m  ? 追问模式\033[0m")
        if exec_result and exec_result.iterations > 0:
            print(f"\033[90m  ── L3: {exec_result.iterations} 步 | tokens={exec_result.token_usage} ──\033[0m")

    return reply_text


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
            reply = await chat_once(user_input, session_id, memory, mock_user, show_debug)
            print(f"\n\033[36mSunny: \033[0m{reply}\n")
        except Exception as e:
            print(f"\n\033[31m错误: {e}\033[0m\n")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
