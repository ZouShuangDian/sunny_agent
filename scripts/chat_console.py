"""
控制台交互测试脚本：跳过 JWT 鉴权，直接运行完整对话链路

运行方式：
    poetry run python scripts/chat_console.py

支持命令：
    /new     — 开启新会话
    /debug   — 切换调试信息显示
    /quit    — 退出
"""

import asyncio
import sys
import time
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache.redis_client import redis_client
from app.execution.router import ExecutionRouter
from app.guardrails.validator import GuardrailsValidator
from app.intent.clarify_handler import ClarifyHandler
from app.intent.context_builder import ContextBuilder
from app.intent.intent_engine import IntentEngine
from app.intent.output_assembler import OutputAssembler
from app.llm.client import LLMClient
from app.memory.schemas import LastIntent, Message
from app.memory.working_memory import WorkingMemory
from app.security.auth import AuthenticatedUser

# ── 组件初始化 ──
llm_client = LLMClient()
clarify_handler = ClarifyHandler()
output_assembler = OutputAssembler()
guardrails = GuardrailsValidator()
execution_router = ExecutionRouter(llm_client)

# 模拟用户（跳过 JWT）
MOCK_USER = AuthenticatedUser(
    id="test-user-001",
    usernumb="TEST001",
    username="测试用户",
    role="admin",
    department="技术部",
)


async def chat_once(
    message: str,
    session_id: str,
    memory: WorkingMemory,
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

    # 2. 上下文组装
    context_builder = ContextBuilder(memory)
    context = await context_builder.build(
        user_input=message,
        user=MOCK_USER,
        session_id=session_id,
    )

    # 3. 意图引擎
    intent_engine = IntentEngine(llm_client)
    intent_result = await intent_engine.analyze(
        user_input=message,
        context=context,
    )

    # 4. 追问检查
    clarify_result = clarify_handler.check_and_clarify(intent_result)

    # 5. 输出组装
    assembled = output_assembler.assemble(
        user_input=message,
        intent_result=intent_result,
        context=context,
        clarify=clarify_result,
        user=MOCK_USER,
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
    )
    await memory.append_message(session_id, assistant_msg)
    await memory.increment_turn(session_id)

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

    return reply_text


async def main():
    """交互式对话主循环"""
    print("=" * 60)
    print("  Agent Sunny 控制台测试")
    print("  输入消息开始对话，支持多轮上下文")
    print("  命令: /new (新会话) | /debug (切换调试) | /quit (退出)")
    print("=" * 60)

    redis = redis_client
    memory = WorkingMemory(redis)
    session_id = str(uuid.uuid4())
    show_debug = True
    pt_session = PromptSession()

    # 初始化会话
    await memory.init_session(session_id, MOCK_USER.id, MOCK_USER.usernumb)
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
            await memory.init_session(session_id, MOCK_USER.id, MOCK_USER.usernumb)
            print(f"\033[90m  新会话: {session_id[:8]}...\033[0m\n")
            continue
        elif user_input == "/debug":
            show_debug = not show_debug
            print(f"\033[90m  调试信息: {'开启' if show_debug else '关闭'}\033[0m\n")
            continue

        # 执行对话
        try:
            reply = await chat_once(user_input, session_id, memory, show_debug)
            print(f"\n\033[36mSunny: \033[0m{reply}\n")
        except Exception as e:
            print(f"\n\033[31m错误: {e}\033[0m\n")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
