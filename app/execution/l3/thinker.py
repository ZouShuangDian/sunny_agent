"""
Thinker（决策者）：负责 Prompt 构建 + LLM 调用 + 响应解析

在 Function Calling 型 ReAct 中，Think 和 Act 的"决策"发生在同一次 LLM 调用内：
- content = Thought（LLM 的思考过程）
- tool_calls = Action 决策（LLM 选择的工具）

Thinker 拥有 LLM 调用权，返回结构化的 ThinkResult。
"""

import json

import structlog

from app.execution.l3.schemas import ThinkResult, ToolCallRequest
from app.llm.client import LLMClient

log = structlog.get_logger()


def _safe_json_loads(s: str) -> dict:
    """安全解析 JSON，失败时返回空 dict"""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


class Thinker:
    """决策者：负责 Prompt 构建 + LLM 调用 + 响应解析"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def think(
        self,
        messages: list[dict],
        tool_schemas: list[dict] | None,
    ) -> ThinkResult:
        """
        单步思考：调用 LLM，返回结构化决策。

        Args:
            messages: 当前完整的消息上下文
            tool_schemas: 可用工具 schema 列表。None 时为最后一步（强制总结）。

        Returns:
            ThinkResult: 包含 thought、tool_calls、usage、is_done
        """
        if tool_schemas:
            response = await self.llm.chat_with_tools(
                messages=messages,
                tools=tool_schemas,
                temperature=0.7,
                max_tokens=4096,
            )
            raw_tool_calls = getattr(response, "tool_calls_raw", None)
        else:
            # 最后一步不带 tools，强制 LLM 总结
            response = await self.llm.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
            )
            raw_tool_calls = None

        thought = response.content or ""
        parsed_calls = self._parse_tool_calls(raw_tool_calls)
        is_done = parsed_calls is None or len(parsed_calls) == 0

        return ThinkResult(
            thought=thought,
            tool_calls=parsed_calls if parsed_calls else None,
            usage=response.usage,
            is_done=is_done,
        )

    @staticmethod
    def _parse_tool_calls(raw_tool_calls) -> list[ToolCallRequest] | None:
        """
        将 LLM 返回的原始 tool_calls 解析为结构化的 ToolCallRequest 列表。

        raw_tool_calls 是 litellm 返回的对象列表，每个对象有：
        - id: str
        - function.name: str
        - function.arguments: str (JSON)
        """
        if not raw_tool_calls:
            return None

        result: list[ToolCallRequest] = []
        for tc in raw_tool_calls:
            args = _safe_json_loads(tc.function.arguments)
            result.append(ToolCallRequest(
                id=tc.id,
                name=tc.function.name,
                arguments=args,
            ))

        return result if result else None
