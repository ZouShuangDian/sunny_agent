"""
L3 深度推理引擎：编排 Thinker → Actor → Observer

编排器本身极其简洁，只负责三个组件的交互循环。
所有具体逻辑（LLM 调用、工具执行、状态追踪）委托给对应组件。

Todo 三层机制（opencode 对标）：
- Layer 1（宪法层）：build_l3_system_prompt() 末尾的 Todo 规范静态约束
- Layer 2（感知层）：todo_write / todo_read 工具每次返回完整快照（工具层实现）
- Layer 3（干预层）：_inject_todo_reminder() 在每次 Think 前动态注入最新 todo 状态
"""

import json
import time
from collections.abc import AsyncIterator

import structlog

from app.execution.l3.actor import Actor
from app.execution.l3.observer import Observer
from app.execution.l3.prompts import build_l3_system_prompt
from app.execution.l3.schemas import L3Config, ThinkResult
from app.execution.l3.thinker import Thinker
from app.execution.schemas import ExecutionResult
from app.execution.session_context import get_session_id, reset_session_id, set_session_id
from app.guardrails.schemas import IntentResult
from app.llm.client import LLMClient
from app.prompts.markers import TODO_REMINDER_MARKER
from app.todo.store import TodoStore
from app.tools.registry import ToolRegistry

log = structlog.get_logger()


class L3ReActEngine:
    """L3 深度推理引擎：编排 Thinker → Actor → Observer"""

    def __init__(
        self,
        llm: LLMClient,
        tool_registry: ToolRegistry,
        config: L3Config | None = None,
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.config = config or L3Config.from_settings()
        self.thinker = Thinker(llm)
        self.actor = Actor(tool_registry)

    async def execute(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> ExecutionResult:
        """
        非流式 ReAct 执行。

        流程：
        1. 设置 session_id ContextVar（供 TodoWriteTool / TodoReadTool 读取）
        2. 组装初始 messages（System Prompt + 对话历史 + 用户输入）
        3. 获取 L3 可用工具集
        4. 循环：熔断检查 → Layer 3 Todo 注入 → Think → Act → 追加到上下文
        5. 返回最终结果（含推理轨迹）
        """
        # ① 设置 session_id ContextVar（Todo 三层机制 Layer 2/3 依赖此值）
        sid_token = set_session_id(session_id)
        try:
            observer = Observer(self.config)
            observer.start()

            # 组装初始 messages
            messages = self._build_initial_messages(intent_result)

            # 获取 L3 可用工具集（skill_call / todo_write / todo_read 均在此）
            tool_schemas = self.tool_registry.get_schemas_by_tier("L3")

            think_result: ThinkResult | None = None

            for step in range(self.config.max_iterations):
                # ── 熔断检查 ──
                should_stop, reason = observer.should_stop()
                if should_stop:
                    return self._graceful_degrade(observer, reason)

                # ── Layer 3 干预：注入当前 Todo 状态到 system prompt ──
                messages = await self._inject_todo_reminder(messages)

                # ── Think：LLM 决策 ──
                # 最后一步不带 tools，强制总结（与 L1 一致）
                use_tools = tool_schemas if step < self.config.max_iterations - 1 else None
                think_result = await self.thinker.think(messages, use_tools)
                observer.on_think(step, think_result)

                # ── 任务完成 ──
                if think_result.is_done:
                    break

                # ── Act：执行工具（含 skill_call / todo_write / todo_read 等）──
                act_result = await self.actor.act(think_result)
                observer.on_act(step, act_result)

                # ── 追加消息到上下文 ──
                messages.extend(act_result.messages)

            return self._build_result(think_result, observer)
        finally:
            # 精确还原 session_id，即使异常也安全
            reset_session_id(sid_token)

    async def execute_stream(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """
        流式 ReAct 执行。

        SSE 事件格式：
        - {"event": "thought", "data": {"step": 0, "content": "..."}}
        - {"event": "tool_call", "data": {"step": 0, "name": "...", "args": {...}}}
        - {"event": "tool_result", "data": {"step": 0, "name": "...", "result": "..."}}
        - {"event": "delta", "data": "最终回答的文本片段"}
        - {"event": "finish", "data": {"iterations": 2, "tokens_used": 3500}}

        中间步骤用非流式（thought + tool_call + tool_result），
        最终回答用流式（delta）。
        """
        # 设置 session_id ContextVar（与 execute() 保持一致）
        sid_token = set_session_id(session_id)
        try:
            observer = Observer(self.config)
            observer.start()

            messages = self._build_initial_messages(intent_result)
            tool_schemas = self.tool_registry.get_schemas_by_tier("L3")

            for step in range(self.config.max_iterations):
                # ── 熔断检查 ──
                should_stop, reason = observer.should_stop()
                if should_stop:
                    degrade_result = self._graceful_degrade(observer, reason)
                    yield {"event": "delta", "data": degrade_result.reply}
                    yield {"event": "finish", "data": {
                        "iterations": observer.trace.step_count,
                        "tokens_used": observer.budget.to_dict()["total_tokens"],
                        "is_degraded": True,
                        "degrade_reason": reason,
                    }}
                    return

                # ── Layer 3 干预：注入当前 Todo 状态到 system prompt ──
                messages = await self._inject_todo_reminder(messages)

                # ── Think ──
                use_tools = tool_schemas if step < self.config.max_iterations - 1 else None
                think_result = await self.thinker.think(messages, use_tools)
                observer.on_think(step, think_result)

                # 推送 thought 事件（让前端展示推理过程）
                if think_result.thought:
                    yield {"event": "thought", "data": {
                        "step": step,
                        "content": think_result.thought,
                    }}

                # ── 任务完成 → 最终回答流式输出 ──
                if think_result.is_done:
                    if think_result.thought:
                        yield {"event": "delta", "data": think_result.thought}
                    yield {"event": "finish", "data": {
                        "iterations": observer.trace.step_count,
                        "tokens_used": observer.budget.to_dict()["total_tokens"],
                    }}
                    return

                # ── Act：执行工具（含 skill_call / todo_write / todo_read 等）──
                act_result = await self.actor.act(think_result)
                observer.on_act(step, act_result)

                # 推送 tool_call / tool_result 事件
                for obs in act_result.observations:
                    yield {"event": "tool_call", "data": {
                        "step": step,
                        "name": obs.tool_name,
                        "args": obs.arguments,
                    }}
                    yield {"event": "tool_result", "data": {
                        "step": step,
                        "name": obs.tool_name,
                        "result": obs.result,
                    }}

                messages.extend(act_result.messages)

            # 达到 max_iterations 仍未完成 → 最后一步的 thought 即回答
            if think_result and think_result.thought:
                yield {"event": "delta", "data": think_result.thought}
            yield {"event": "finish", "data": {
                "iterations": observer.trace.step_count,
                "tokens_used": observer.budget.to_dict()["total_tokens"],
            }}
        finally:
            reset_session_id(sid_token)

    async def execute_raw(self, messages: list[dict]) -> ExecutionResult:
        """
        直接接受已组装好的 messages 执行 ReAct 循环（SubAgent 专用）。

        与 execute() 的区别：跳过 _build_initial_messages()，
        由调用方（SubAgentCallTool）传入隔离好的子 Agent 上下文。

        Args:
            messages: 已组装完毕的消息列表
                      [{"role": "system", "content": agent_system_prompt},
                       {"role": "user",   "content": task_description}]
        """
        observer = Observer(self.config)
        observer.start()

        tool_schemas = self.tool_registry.get_schemas_by_tier("L3")
        think_result: ThinkResult | None = None

        for step in range(self.config.max_iterations):
            should_stop, reason = observer.should_stop()
            if should_stop:
                return self._graceful_degrade(observer, reason)

            use_tools = tool_schemas if step < self.config.max_iterations - 1 else None
            think_result = await self.thinker.think(messages, use_tools)
            observer.on_think(step, think_result)

            if think_result.is_done:
                break

            act_result = await self.actor.act(think_result)
            observer.on_act(step, act_result)
            messages.extend(act_result.messages)

        return self._build_result(think_result, observer)

    async def _inject_todo_reminder(self, messages: list[dict]) -> list[dict]:
        """
        Layer 3 干预层：在每次 Think 前，将最新 Todo 状态动态注入 system prompt 末尾。

        注入策略：
        - 有活跃 Todo（pending / in_progress）时：追加完整状态块到 messages[0] 末尾
        - 无活跃 Todo 时：剥离上次注入的状态块，还原干净的 system prompt
        - 幂等：按 TODO_REMINDER_MARKER（来自 app.prompts.markers）截断后重新追加

        注意：只修改 system message（messages[0]），不追加新消息，
        规避 Anthropic API 连续角色限制。
        """
        session_id = get_session_id()
        if not session_id or not messages or messages[0].get("role") != "system":
            return messages

        todos = await TodoStore.get(session_id)
        active = [t for t in todos if t.get("status") in ("pending", "in_progress")]

        # 剥离上次注入的状态块（幂等处理）
        base: str = messages[0]["content"]
        if TODO_REMINDER_MARKER in base:
            base = base[: base.index(TODO_REMINDER_MARKER)]

        # 无活跃 Todo：还原干净 system prompt
        if not active:
            if messages[0]["content"] == base:
                return messages  # 未变化，直接返回原列表
            updated = list(messages)
            updated[0] = {"role": "system", "content": base}
            return updated

        # 有活跃 Todo：追加最新状态块
        block = (
            f"{TODO_REMINDER_MARKER}\n"
            f"当前 Todo 列表（自动同步，请检查进度并继续执行）：\n"
            f"```json\n{json.dumps(todos, ensure_ascii=False, indent=2)}\n```\n"
            f"<!-- todo-reminder-end -->"
        )
        updated = list(messages)
        updated[0] = {"role": "system", "content": base + block}
        return updated

    def _build_initial_messages(self, intent_result: IntentResult) -> list[dict]:
        """组装 ReAct 循环的初始消息列表"""
        # 提取用户目标（如果 IntentResult 有此字段）
        user_goal = getattr(intent_result.intent, "user_goal", None)

        system_prompt = build_l3_system_prompt(
            user_input=intent_result.raw_input,
            user_goal=user_goal,
            max_iterations=self.config.max_iterations,
        )

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            *intent_result.history_messages[-10:],
            {"role": "user", "content": intent_result.raw_input},
        ]
        return messages

    def _build_result(
        self,
        think_result: ThinkResult | None,
        observer: Observer,
    ) -> ExecutionResult:
        """从最终的 ThinkResult 和 Observer 构建 ExecutionResult"""
        elapsed = int(observer.elapsed_seconds * 1000)

        return ExecutionResult(
            reply=think_result.thought if think_result else "",
            tool_calls=observer.trace.to_tool_call_records(),
            source="deep_l3",
            duration_ms=elapsed,
            reasoning_trace=observer.trace.to_dict(),
            iterations=observer.trace.step_count,
            token_usage=observer.budget.to_dict(),
        )

    def _graceful_degrade(
        self,
        observer: Observer,
        reason: str,
    ) -> ExecutionResult:
        """
        熔断时的优雅降级。

        用已收集的工具结果拼接摘要，不做额外 LLM 调用（Q2 裁决）。
        """
        summary = observer.trace.summarize_observations()
        elapsed = int(observer.elapsed_seconds * 1000)

        if summary:
            reply = (
                f"由于处理时间/资源限制，我基于已收集到的信息为您总结：\n\n"
                f"{summary}\n\n"
                f"如需更详细的分析，建议您缩小问题范围后重新提问。"
            )
        else:
            reply = "抱歉，该问题的分析超出了当前处理能力限制。建议您简化问题或拆分为多个小问题后重试。"

        log.warning(
            "L3 优雅降级",
            reason=reason,
            iterations=observer.trace.step_count,
            tokens=observer.budget.to_dict()["total_tokens"],
            elapsed_ms=elapsed,
        )

        return ExecutionResult(
            reply=reply,
            source="deep_l3",
            tool_calls=observer.trace.to_tool_call_records(),
            duration_ms=elapsed,
            reasoning_trace=observer.trace.to_dict(),
            iterations=observer.trace.step_count,
            token_usage=observer.budget.to_dict(),
            is_degraded=True,
            degrade_reason=reason,
        )
