"""
ThinkStrategy — Think 策略抽象

封装 batch/stream 两种 LLM 调用方式，核心循环 run() 通过策略模式统一调用。

- BatchThinkStrategy：非流式 Think，直接调用 thinker.think()
- StreamThinkStrategy：流式 Think，调用 thinker.think_stream()，逐 token 推送 delta

注意：StreamThinkStrategy 同时负责「调用 LLM」和「推送 delta」，
因为 delta 必须在 chunk 到达时立即推送，无法延迟到 after_think。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.execution.l3.schemas import ThinkResult, ToolCallRequest, parse_tool_arguments
from app.streaming.events import SSEEvent

if TYPE_CHECKING:
    from app.execution.l3.loop_context import LoopContext
    from app.execution.l3.thinker import Thinker


class ThinkStrategy(Protocol):
    """Think 策略：封装 batch/stream 两种 LLM 调用方式"""

    async def think(
        self,
        ctx: LoopContext,
        thinker: Thinker,
        tool_schemas: list[dict] | None,
    ) -> ThinkResult:
        """执行一步 Think，返回 ThinkResult"""
        ...


class BatchThinkStrategy:
    """非流式 Think：直接调用 thinker.think()"""

    async def think(
        self,
        ctx: LoopContext,
        thinker: Thinker,
        tool_schemas: list[dict] | None,
    ) -> ThinkResult:
        return await thinker.think(ctx.messages, tool_schemas)


class StreamThinkStrategy:
    """流式 Think：调用 thinker.think_stream()，逐 token 推送 delta"""

    async def think(
        self,
        ctx: LoopContext,
        thinker: Thinker,
        tool_schemas: list[dict] | None,
    ) -> ThinkResult:
        collected_tool_calls: list[dict] = []
        content_tokens: list[str] = []
        final_usage: dict = {}

        async for chunk in thinker.think_stream(ctx.messages, tool_schemas):
            if chunk["type"] == "delta":
                content_tokens.append(chunk["content"])
                # 流式推送 delta
                if ctx.event_emitter:
                    await ctx.event_emitter.emit(SSEEvent.DELTA, {"content": chunk["content"]})
            elif chunk["type"] == "tool_call":
                collected_tool_calls.append(chunk)
            elif chunk["type"] == "finish":
                final_usage = chunk.get("usage", {})

        return ThinkResult(
            thought="".join(content_tokens),
            tool_calls=[
                ToolCallRequest(
                    id=c["id"],
                    name=c["name"],
                    arguments=parse_tool_arguments(c["arguments"]),
                )
                for c in collected_tool_calls
            ] or None,
            usage=final_usage,
            is_done=not collected_tool_calls,
        )
