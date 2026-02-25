"""
Actor（执行者）：安全执行工具调用

职责：
- 接收 ThinkResult 中的 tool_calls 决策
- 通过 ToolRegistry 执行调用（包括 skill_call 元工具）
- 并行执行多个工具调用（asyncio.gather）
- 格式化结果为 LLM 可消费的 tool messages

Skill 执行模型（M08-6 Prompt-Driven）：
- skill_call 已注册为普通 Tool，Actor 无需特殊处理
- LLM 调用 skill_call → ToolRegistry.execute("skill_call", {...})
  → SkillCallTool.execute() → SkillRegistry.execute() → Tier 2 body 注入
- LLM 读取 instructions 字段后，通过正常 ReAct 循环自主调用 web_search 等子工具
"""

import asyncio
import json
import time

import structlog

from app.execution.l3.schemas import ActResult, Observation, ThinkResult, ToolCallRequest
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

log = structlog.get_logger()


class Actor:
    """执行者：安全执行工具调用（含 skill_call 元工具）"""

    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry

    async def act(self, think_result: ThinkResult) -> ActResult:
        """
        执行 ThinkResult 中的所有工具调用，返回观察结果。

        skill_call 作为普通工具处理，无需特殊分支。

        Args:
            think_result: Thinker 的输出（含 tool_calls 列表）

        Returns:
            ActResult: 包含 observations 和格式化后的 messages
        """
        if not think_result.tool_calls:
            return ActResult(observations=[], messages=[])

        # 构建 assistant message（含 tool_calls，追加到 LLM 上下文）
        assistant_msg = self._build_assistant_message(think_result)

        # 并行执行所有工具调用
        async def _execute_one(tc: ToolCallRequest) -> tuple[str, int]:
            """执行单个工具调用，返回 (result_str, duration_ms)"""
            start = time.monotonic()
            result_str = await self.tool_registry.execute(tc.name, tc.arguments)
            duration = int((time.monotonic() - start) * 1000)
            return result_str, duration

        results = await asyncio.gather(
            *[_execute_one(tc) for tc in think_result.tool_calls],
            return_exceptions=True,
        )

        # 组装 observations 和 tool messages
        observations: list[Observation] = []
        tool_messages: list[dict] = []

        for tc, result in zip(think_result.tool_calls, results):
            if isinstance(result, Exception):
                result_str = ToolResult.fail(f"执行异常: {result}").to_json()
                duration = 0
            else:
                result_str, duration = result

            observations.append(Observation(
                tool_name=tc.name,
                tool_call_id=tc.id,
                arguments=tc.arguments,
                result=result_str,
                duration_ms=duration,
            ))

            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

            log.info(
                "L3 工具执行完成",
                tool=tc.name,
                duration_ms=duration,
            )

        return ActResult(
            observations=observations,
            messages=[assistant_msg, *tool_messages],
        )

    @staticmethod
    def _build_assistant_message(think_result: ThinkResult) -> dict:
        """构建包含 tool_calls 的 assistant 消息（OpenAI 格式）"""
        msg: dict = {
            "role": "assistant",
            "content": think_result.thought,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(
                            tc.arguments, ensure_ascii=False
                        ),
                    },
                }
                for tc in (think_result.tool_calls or [])
            ],
        }
        return msg
