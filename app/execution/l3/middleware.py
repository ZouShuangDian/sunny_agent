"""
ReAct 中间件：可插拔的关注点分离

- ReActMiddleware Protocol：三个 hook 点（before_think / after_think / after_act）
- TodoMiddleware：Layer 3 干预，每次 Think 前注入最新 Todo 状态
- ContextUsageMiddleware：Think 后计算上下文用量
- CompactionMiddleware：Think 后检测溢出，触发 Level 2 摘要压缩
- StepCollectorMiddleware：Act 后收集 L3 中间步骤
- SSEToolEventMiddleware：Act 后推送 tool_call/tool_result SSE 事件

中间件执行顺序有语义约束：
- ContextUsageMiddleware 必须在 CompactionMiddleware 之前
- StepCollectorMiddleware 必须在 SSEToolEventMiddleware 之前
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from app.config import get_settings
from app.execution.l3.live_steps_writer import LiveStepsWriter
from app.execution.l3.schemas import ActResult, ThinkResult
from app.execution.session_context import get_session_id
from app.prompts.markers import TODO_REMINDER_MARKER
from app.streaming.events import SSEEvent
from app.todo.store import TodoStore

if TYPE_CHECKING:
    from app.execution.l3.loop_context import LoopContext
    from app.execution.l3.react_engine import L3ReActEngine

settings = get_settings()


class ReActMiddleware(Protocol):
    """ReAct 循环中间件协议（三个 hook 点）"""

    async def before_think(self, ctx: LoopContext) -> None:
        """Think 前调用。可修改 ctx.messages（如 Todo 注入）"""
        ...

    async def after_think(self, ctx: LoopContext, think_result: ThinkResult) -> None:
        """Think 后调用。可做 context_usage 计算、Level 2 压缩"""
        ...

    async def after_act(self, ctx: LoopContext, act_result: ActResult) -> None:
        """Act 后调用。可收集 L3 steps、推送 SSE tool_call/tool_result 事件"""
        ...


# ─────────────────────────────────────────────
#  5.1 TodoMiddleware — Layer 3 干预
# ─────────────────────────────────────────────


class TodoMiddleware:
    """Layer 3 干预：每次 Think 前注入最新 Todo 状态到 system prompt"""

    async def before_think(self, ctx: LoopContext) -> None:
        """注入 Todo reminder 到 messages[0]（system prompt）"""
        session_id = get_session_id()
        if not session_id or not ctx.messages or ctx.messages[0].get("role") != "system":
            return

        todos = await TodoStore.get(session_id)
        active = [t for t in todos if t.get("status") in ("pending", "in_progress")]

        # 幂等：按 marker 截断后重新追加
        base: str = ctx.messages[0]["content"]
        if TODO_REMINDER_MARKER in base:
            base = base[: base.index(TODO_REMINDER_MARKER)]

        if not active:
            if ctx.messages[0]["content"] != base:
                ctx.messages[0] = {"role": "system", "content": base}
            return

        goal_line = f"当前任务目标：{ctx.user_goal}\n\n" if ctx.user_goal else ""
        block = (
            f"{TODO_REMINDER_MARKER}\n"
            f"{goal_line}"
            f"当前 Todo 列表（自动同步）：\n"
            f"```json\n{json.dumps(todos, ensure_ascii=False, indent=2)}\n```\n"
            f"⚠️ 严格要求：上方列表中仍有 pending 或 in_progress 的任务，"
            f"你必须继续逐步执行，禁止跳过未完成的任务直接给出最终回答。"
            f"只有当所有任务都实际完成并标记为 completed 后，才可输出最终回答。\n"
            f"<!-- todo-reminder-end -->"
        )
        ctx.messages[0] = {"role": "system", "content": base + block}

    async def after_think(self, ctx: LoopContext, think_result: ThinkResult) -> None:
        pass

    async def after_act(self, ctx: LoopContext, act_result: ActResult) -> None:
        pass


# ─────────────────────────────────────────────
#  5.2 ContextUsageMiddleware — 上下文用量追踪
# ─────────────────────────────────────────────


class ContextUsageMiddleware:
    """Think 后计算 context_usage，流式模式下推送 SSE"""

    async def before_think(self, ctx: LoopContext) -> None:
        pass

    async def after_think(self, ctx: LoopContext, think_result: ThinkResult) -> None:
        prompt_tokens = (think_result.usage or {}).get("prompt_tokens", 0)
        completion_tokens = (think_result.usage or {}).get("completion_tokens", 0)
        effective_limit = settings.MODEL_CONTEXT_LIMIT - settings.COMPACTION_BUFFER

        ctx.last_context_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "remaining": max(effective_limit - prompt_tokens, 0),
            "percent": round(prompt_tokens / effective_limit * 100, 1) if effective_limit > 0 else 0.0,
            "limit": effective_limit,
        }

        # 流式模式推送
        if ctx.event_emitter and prompt_tokens > 0:
            await ctx.event_emitter.emit(SSEEvent.CONTEXT_USAGE, ctx.last_context_usage)

    async def after_act(self, ctx: LoopContext, act_result: ActResult) -> None:
        pass


# ─────────────────────────────────────────────
#  5.3 CompactionMiddleware — Level 2 摘要压缩
# ─────────────────────────────────────────────


class CompactionMiddleware:
    """Think 后检测上下文溢出，触发 Level 2 摘要压缩"""

    def __init__(self, engine: L3ReActEngine):
        # 需要调用 engine._compact_messages()（依赖 LLM client）
        self._engine = engine

    async def before_think(self, ctx: LoopContext) -> None:
        pass

    async def after_think(self, ctx: LoopContext, think_result: ThinkResult) -> None:
        prompt_tokens = (think_result.usage or {}).get("prompt_tokens", 0)
        remaining = settings.MODEL_CONTEXT_LIMIT - prompt_tokens
        if remaining < settings.COMPACTION_BUFFER:
            ctx.messages, summary = await self._engine._compact_messages(ctx.messages)
            if summary:
                ctx.last_compaction_summary = summary

    async def after_act(self, ctx: LoopContext, act_result: ActResult) -> None:
        pass


# ─────────────────────────────────────────────
#  5.4 StepCollectorMiddleware — L3 步骤收集
# ─────────────────────────────────────────────


class StepCollectorMiddleware:
    """Act 后收集 L3 中间步骤消息（用于持久化到 l3_steps 表）+ 增量写 Redis"""

    def __init__(self, live_steps_writer: LiveStepsWriter | None = None):
        self._writer = live_steps_writer
        self._step_offset = 0  # 已推送的 step 数量，用于计算增量 step_index

    async def before_think(self, ctx: LoopContext) -> None:
        pass

    async def after_think(self, ctx: LoopContext, think_result: ThinkResult) -> None:
        pass

    async def after_act(self, ctx: LoopContext, act_result: ActResult) -> None:
        # 现有：收集到内存
        ctx.collected_steps.extend(act_result.messages)

        # 新增：增量写 Redis
        if self._writer and act_result.messages:
            new_steps = []
            for i, msg in enumerate(act_result.messages):
                role = msg.get("role", "")
                content = msg.get("content") or ""
                tool_name = None
                tool_call_id = None
                tool_args = None

                if role == "tool":
                    tool_call_id = msg.get("tool_call_id")
                    tool_name = msg.get("name")
                elif role == "assistant" and msg.get("tool_calls"):
                    tool_args = {}
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        name = fn.get("name", "unknown")
                        args_raw = fn.get("arguments", "{}")
                        try:
                            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except (json.JSONDecodeError, TypeError):
                            args = {"_raw": args_raw}
                        tool_args[name] = args

                new_steps.append({
                    "step_index": self._step_offset + i,
                    "role": role,
                    "content": content,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "tool_args": tool_args,
                })

            self._step_offset += len(act_result.messages)
            await self._writer.push(new_steps)


# ─────────────────────────────────────────────
#  5.5 SSEToolEventMiddleware — SSE 工具事件推送
# ─────────────────────────────────────────────


class SSEToolEventMiddleware:
    """Act 后推送 tool_call/tool_result SSE 事件（仅流式模式）"""

    async def before_think(self, ctx: LoopContext) -> None:
        pass

    async def after_think(self, ctx: LoopContext, think_result: ThinkResult) -> None:
        pass

    async def after_act(self, ctx: LoopContext, act_result: ActResult) -> None:
        if not ctx.event_emitter:
            return

        for obs in act_result.observations:
            await ctx.event_emitter.emit(SSEEvent.TOOL_CALL, {
                "step": ctx.step,
                "name": obs.tool_name,
                "args": obs.arguments,
            })
            try:
                parsed = json.loads(obs.result)
            except (json.JSONDecodeError, TypeError):
                parsed = obs.result
            await ctx.event_emitter.emit(SSEEvent.TOOL_RESULT, {
                "step": ctx.step,
                "name": obs.tool_name,
                "result": parsed,
            })
