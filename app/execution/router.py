"""
执行路由器：根据 IntentResult.route 分发到对应的执行路径

两种执行模式：
- standard_l1: L1 标准执行，Bounded Loop + 固定工具集 + PromptService 检索
- deep_l3: L3 深度推理（Week 7+），当前用通用 LLM 兜底
"""

import time
from collections.abc import AsyncIterator

import structlog

from app.execution.l1.fast_track import L1FastTrack
from app.execution.schemas import ExecutionResult
from app.guardrails.schemas import IntentResult
from app.llm.client import LLMClient
from app.tools.builtin_tools import create_builtin_registry

log = structlog.get_logger()

# L3 兜底基座人设
_L3_BASE_PROMPT = (
    "你是 Agent Sunny，舜宇集团的 AI 智能助手。"
    "你乐于助人，回答专业准确，语言简洁友好。"
    "请根据用户的问题给出有帮助的回复。"
)


class ExecutionRouter:
    """执行层统一入口"""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.l1 = L1FastTrack(llm, create_builtin_registry())

    async def execute(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> ExecutionResult:
        """非流式执行，根据 route 分发"""
        route = intent_result.route
        start = time.time()

        if route == "standard_l1":
            result = await self.l1.execute(intent_result, session_id)
        elif route == "deep_l3":
            result = await self._deep_l3(intent_result)
        else:
            log.warning("未知路由，降级到 standard_l1", route=route)
            result = await self.l1.execute(intent_result, session_id)

        result.duration_ms = int((time.time() - start) * 1000)
        return result

    async def execute_stream(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """流式执行，根据 route 分发"""
        route = intent_result.route

        if route == "standard_l1":
            async for event in self.l1.execute_stream(intent_result, session_id):
                yield event
        elif route == "deep_l3":
            async for event in self._deep_l3_stream(intent_result):
                yield event
        else:
            log.warning("未知路由，降级到 standard_l1 流式", route=route)
            async for event in self.l1.execute_stream(intent_result, session_id):
                yield event

    # ── deep_l3: 通用 LLM 兜底（Week 7+ 实现完整多步推理） ──

    async def _deep_l3(self, intent_result: IntentResult) -> ExecutionResult:
        """L3 深度推理：当前用通用 LLM 兜底"""
        messages = [
            {"role": "system", "content": _L3_BASE_PROMPT},
            *intent_result.history_messages[-10:],
            {"role": "user", "content": intent_result.raw_input},
        ]
        response = await self.llm.chat(
            messages=messages,
            temperature=0.7,
            max_tokens=4096,
        )
        return ExecutionResult(
            reply=response.content,
            source="deep_l3",
        )

    async def _deep_l3_stream(self, intent_result: IntentResult) -> AsyncIterator[dict]:
        """L3 深度推理流式输出"""
        messages = [
            {"role": "system", "content": _L3_BASE_PROMPT},
            *intent_result.history_messages[-10:],
            {"role": "user", "content": intent_result.raw_input},
        ]
        async for chunk in self.llm.chat_stream(
            messages=messages,
            temperature=0.7,
            max_tokens=4096,
        ):
            if chunk["type"] == "delta":
                yield {"event": "delta", "data": chunk["content"]}
            elif chunk["type"] == "finish":
                yield {"event": "finish", "data": {}}
