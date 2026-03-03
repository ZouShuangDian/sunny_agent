"""
L3 深度推理引擎：编排 Thinker → Actor → Observer

编排器本身极其简洁，只负责三个组件的交互循环。
所有具体逻辑（LLM 调用、工具执行、状态追踪）委托给对应组件。

Todo 三层机制（opencode 对标）：
- Layer 1（宪法层）：build_l3_system_prompt() 末尾的 Todo 规范静态约束
- Layer 2（感知层）：todo_write / todo_read 工具每次返回完整快照（工具层实现）
- Layer 3（干预层）：_inject_todo_reminder() 在每次 Think 前动态注入最新 todo 状态

Context 压缩（双层漏斗）：
- Level 1（内存级剪枝）：每步无条件剪枝 + prompt_tokens 超阈值触发额外剪枝
- Level 2（摘要截断）：prompt_tokens 接近上限时调用 LLM 生成摘要，重建 messages 列表
"""

import json
import time
from collections.abc import AsyncIterator

import structlog

from app.config import get_settings
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
settings = get_settings()

# Level 2 摘要生成 prompt（结构化，含制造业业务实体要求）
COMPACTION_PROMPT = """请为以下对话历史生成结构化摘要，包含：
1. 任务目标
2. 已完成的操作步骤
3. 重要发现和结论
4. 操作过的文件、路径、数据
5. 涉及的业务实体（产品型号、工单号、设备编号、指标名称等）
6. 当前状态与下一步计划
要求：摘要需足够详细，让继续任务的 AI 能无缝衔接。"""


def _build_plugin_context_block(ctx: "PluginCommandContext") -> str:
    """
    将 PluginCommandContext 序列化为 system prompt 注入块。

    内容：
    1. 命令标识（plugin_name:command_name）
    2. COMMAND.md 完整工作流指引
    3. 插件内可用 Skill 列表（含容器内 SKILL.md 路径）
    4. 使用规范（禁止走全局 skill_call）
    """
    skills_section = (
        "\n".join(
            f"- **{s['name']}**: `{s['skill_md_path']}`"
            for s in ctx.plugin_skills
        )
        if ctx.plugin_skills
        else "（此插件无内置 Skill）"
    )
    return (
        f"\n\n---\n## Plugin 命令执行上下文\n\n"
        f"你正在执行用户触发的 Plugin 命令 `/{ctx.plugin_name}:{ctx.command_name}`。\n\n"
        f"### 工作流指引（COMMAND.md）\n\n"
        f"{ctx.command_md_content}\n\n"
        f"### 插件内可用 Skill\n\n"
        f"{skills_section}\n\n"
        f"使用插件 Skill 时：通过 `read_file` 读取对应 SKILL.md 路径，按指引操作。\n"
        f"**禁止**通过 `skill_call` 调用——插件 Skill 不在全局 skill catalog 中。"
    )


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
        # Level 2 摘要内容暂存（供 chat.py 回调时持久化为 genesis block）
        self.last_compaction_summary: str | None = None
        # 流式执行收集的 l3_steps（供 chat_stream 回调时持久化，对称非流式路径）
        self.last_l3_steps: list[dict] | None = None

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
        4. 循环：熔断检查 → Layer 3 Todo 注入 → Think → 压缩检查 → Act → 追加到上下文
        5. 返回最终结果（含推理轨迹 + l3_steps 原始消息）
        """
        # ① 设置 session_id ContextVar（Todo 三层机制 Layer 2/3 依赖此值）
        sid_token = set_session_id(session_id)
        self.last_compaction_summary = None
        try:
            observer = Observer(self.config)
            observer.start()

            # 提取用户目标（用于 Layer 3 Reminder 复读，防止 LLM 在长任务中遗忘原始约束）
            user_goal: str | None = intent_result.intent.user_goal or None

            # 组装初始 messages
            messages = self._build_initial_messages(intent_result)

            # 获取 L3 可用工具集（skill_call / todo_write / todo_read 均在此）
            tool_schemas = self.tool_registry.get_schemas_by_tier("L3")

            think_result: ThinkResult | None = None

            # 收集 L3 中间步骤消息（用于持久化到 l3_steps 表）
            collected_steps: list[dict] = []

            for step in range(self.config.max_iterations):
                # ── 熔断检查 ──
                should_stop, reason = observer.should_stop()
                if should_stop:
                    return self._build_result(
                        think_result, observer, collected_steps, degrade_reason=reason
                    )

                # ── Layer 3 干预：注入当前 Todo 状态到 system prompt ──
                messages = await self._inject_todo_reminder(messages, user_goal)

                # ── Think：LLM 决策 ──
                # 最后一步不带 tools，强制总结（与 L1 一致）
                use_tools = tool_schemas if step < self.config.max_iterations - 1 else None
                think_result = await self.thinker.think(messages, use_tools)
                observer.on_think(step, think_result)

                # ── Level 1 检查：prompt_tokens 超阈值时额外剪枝 ──
                prompt_tokens = (think_result.usage or {}).get("prompt_tokens", 0)
                if prompt_tokens > settings.CONTEXT_PRUNE_TRIGGER:
                    messages = self._compress_stale_tool_results(messages)

                # ── Level 2 摘要截断 ──
                if prompt_tokens > settings.CONTEXT_SUMMARIZE_TRIGGER:
                    messages = await self._compact_messages(messages)

                # ── 任务完成 ──
                if think_result.is_done:
                    break

                # ── Act：执行工具（含 skill_call / todo_write / todo_read 等）──
                act_result = await self.actor.act(think_result)
                observer.on_act(step, act_result)

                # 收集步骤消息（持久化用，收集原始消息）
                for msg in act_result.messages:
                    collected_steps.append(msg)

                # ── 追加消息到上下文，并无条件执行内存级剪枝（每步保洁） ──
                messages.extend(act_result.messages)
                messages = self._compress_stale_tool_results(messages)

            return self._build_result(think_result, observer, collected_steps)
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
        self.last_compaction_summary = None
        self.last_l3_steps = None
        # 收集 L3 中间步骤消息（与 execute() 对称，在 finally 中统一持久化）
        collected_steps: list[dict] = []
        try:
            observer = Observer(self.config)
            observer.start()

            # 提取用户目标（用于 Layer 3 Reminder 复读）
            user_goal: str | None = intent_result.intent.user_goal or None

            messages = self._build_initial_messages(intent_result)
            tool_schemas = self.tool_registry.get_schemas_by_tier("L3")

            for step in range(self.config.max_iterations):
                # ── 熔断检查 ──
                should_stop, reason = observer.should_stop()
                if should_stop:
                    degrade_result = self._build_result(None, observer, [], degrade_reason=reason)
                    yield {"event": "delta", "data": degrade_result.reply}
                    yield {"event": "finish", "data": {
                        "iterations": observer.trace.step_count,
                        "llm_calls": observer.budget.llm_call_count,
                        "is_degraded": True,
                        "degrade_reason": reason,
                    }}
                    return

                # ── Layer 3 干预：注入当前 Todo 状态到 system prompt ──
                messages = await self._inject_todo_reminder(messages, user_goal)

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

                # ── Level 1 检查 ──
                prompt_tokens = (think_result.usage or {}).get("prompt_tokens", 0)
                if prompt_tokens > settings.CONTEXT_PRUNE_TRIGGER:
                    messages = self._compress_stale_tool_results(messages)

                # ── Level 2 摘要截断 ──
                if prompt_tokens > settings.CONTEXT_SUMMARIZE_TRIGGER:
                    messages = await self._compact_messages(messages)

                # ── 任务完成 → 最终回答流式输出 ──
                if think_result.is_done:
                    if think_result.thought:
                        yield {"event": "delta", "data": think_result.thought}
                    yield {"event": "finish", "data": {
                        "iterations": observer.trace.step_count,
                        "llm_calls": observer.budget.llm_call_count,
                    }}
                    return

                # ── Act：执行工具（含 skill_call / todo_write / todo_read 等）──
                act_result = await self.actor.act(think_result)
                observer.on_act(step, act_result)

                # 收集步骤消息（与 execute() 对称，供 chat_stream 持久化到 l3_steps 表）
                collected_steps.extend(act_result.messages)

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

                # ── 追加消息到上下文，并无条件执行内存级剪枝 ──
                messages.extend(act_result.messages)
                messages = self._compress_stale_tool_results(messages)

            # 达到 max_iterations 仍未完成 → 最后一步的 thought 即回答
            if think_result and think_result.thought:
                yield {"event": "delta", "data": think_result.thought}
            yield {"event": "finish", "data": {
                "iterations": observer.trace.step_count,
                "llm_calls": observer.budget.llm_call_count,
            }}
        finally:
            # 所有退出路径（正常/熔断/异常）统一在此存储 l3_steps，供 chat_stream 回调持久化
            self.last_l3_steps = self._convert_steps(collected_steps)
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
                return self._build_result(think_result, observer, [], degrade_reason=reason)

            use_tools = tool_schemas if step < self.config.max_iterations - 1 else None
            think_result = await self.thinker.think(messages, use_tools)
            observer.on_think(step, think_result)

            if think_result.is_done:
                break

            act_result = await self.actor.act(think_result)
            observer.on_act(step, act_result)
            messages.extend(act_result.messages)
            messages = self._compress_stale_tool_results(messages)

        return self._build_result(think_result, observer, [])

    async def _inject_todo_reminder(
        self,
        messages: list[dict],
        user_goal: str | None = None,
    ) -> list[dict]:
        """
        Layer 3 干预层：在每次 Think 前，将最新 Todo 状态动态注入 system prompt 末尾。

        注入内容：
        1. user_goal 复读（防止 LLM 在长任务中遗忘原始约束）
        2. 当前 Todo 列表（JSON 完整快照）
        3. 收尾强提醒（最终回答前必须关闭所有 Todo）

        注入策略：
        - 有活跃 Todo（pending / in_progress）时：追加完整状态块到 messages[0] 末尾
        - 无活跃 Todo 时：剥离上次注入的状态块，还原干净的 system prompt
        - 幂等：按 TODO_REMINDER_MARKER 截断后重新追加

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

        # 有活跃 Todo：追加最新状态块（含用户目标复读 + 收尾强提醒）
        goal_line = f"当前任务目标：{user_goal}\n\n" if user_goal else ""
        block = (
            f"{TODO_REMINDER_MARKER}\n"
            f"{goal_line}"
            f"当前 Todo 列表（自动同步）：\n"
            f"```json\n{json.dumps(todos, ensure_ascii=False, indent=2)}\n```\n"
            f"⚠️ 重要：若准备给出最终回答，必须先调用 `todo_write` 将所有已完成任务标记为 `completed`，"
            f"不可在 in_progress 或 pending 状态下直接结束。\n"
            f"<!-- todo-reminder-end -->"
        )
        updated = list(messages)
        updated[0] = {"role": "system", "content": base + block}
        return updated

    @staticmethod
    def _estimate_tokens(content: str) -> int:
        """
        估算字符串的 token 数（task 5.1）。

        策略：chars // 2
        - 中文约 1.92 chars/token（实测）
        - 英文约 4 chars/token（有利于保护区宽松）
        - 混合文本 chars//2 为合理中间值，允许 ±20% 误差
        """
        return len(content) // 2

    def _compress_stale_tool_results(self, messages: list[dict]) -> list[dict]:
        """
        Level 1 内存级剪枝：将旧 tool result 内容替换为占位符（task 5.2）。

        策略（token 估算边界，替代旧的步骤计数）：
        1. 从 messages 尾部往前累加 tool result 的 _estimate_tokens(content)
        2. 累积超出 PRUNE_PROTECT_TOKENS 的 tool result 替换为占位符
        3. skill_call 的 tool result 始终保留（不被替换）

        优于旧方案（按 llm_call_count 决定保留步数）：
        - token 感知，自适应大文件/长输出
        - 与服务端精确值相关，允许 ±20% 偏差
        """
        prune_protect = settings.PRUNE_PROTECT_TOKENS
        result = list(messages)

        # 从尾部往前累加 tool result token 数，超出保护区的进行替换
        accumulated = 0
        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            if msg.get("role") != "tool":
                continue

            content = msg.get("content", "")
            token_est = self._estimate_tokens(content)

            if accumulated + token_est <= prune_protect:
                # 在保护区内，保留
                accumulated += token_est
            else:
                # 超出保护区，判断是否为 skill_call（始终保护）
                tool_call_id = msg.get("tool_call_id", "")
                tool_name = self._find_tool_name(result, tool_call_id)
                if tool_name == "skill_call":
                    accumulated += token_est
                    continue
                result[i] = {
                    **msg,
                    "content": (
                        f"[已处理] {tool_name or 'tool'} 输出已压缩（原始内容保留在历史记录中）"
                    ),
                }

        return result

    @staticmethod
    def _find_tool_name(messages: list[dict], tool_call_id: str) -> str | None:
        """从 messages 中反查 tool_call_id 对应的工具名称"""
        if not tool_call_id:
            return None
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if tc.get("id") == tool_call_id:
                    return tc.get("function", {}).get("name")
        return None

    async def _compact_messages(self, messages: list[dict]) -> list[dict]:
        """
        Level 2 摘要截断（task 7.1）：生成摘要并重建 messages 列表。

        流程：
        1. 识别保护区（从尾部按 PRUNE_PROTECT_TOKENS 划定）
        2. 提取保护区外的可压缩区内容
        3. 调用 LLM 生成结构化摘要（max_tokens=COMPACTION_MAX_TOKENS）
        4. 重建 messages：[system] → [user: 摘要] → [保护区消息]
        5. 暂存摘要内容到 self.last_compaction_summary，供 chat.py 持久化为 genesis block

        失败时降级：记录 warning，返回原 messages（接受超限风险）。
        """
        if not messages:
            return messages

        system_msg = messages[0] if messages[0].get("role") == "system" else None
        non_system = messages[1:] if system_msg else messages

        # 计算保护区（从尾部往前，累积到 PRUNE_PROTECT_TOKENS 为止）
        prune_protect = settings.PRUNE_PROTECT_TOKENS
        protected: list[dict] = []
        accumulated = 0
        for msg in reversed(non_system):
            content = msg.get("content") or ""
            if not content and msg.get("tool_calls"):
                content = json.dumps(msg["tool_calls"])
            token_est = self._estimate_tokens(content)
            if accumulated + token_est <= prune_protect:
                accumulated += token_est
                protected.insert(0, msg)
            else:
                break

        # 可压缩区（保护区之前的所有消息）
        compressible = non_system[: len(non_system) - len(protected)]
        if not compressible:
            log.warning("Level 2 摘要：无可压缩消息，跳过")
            return messages

        # 将可压缩区拼接为对话文本，供 LLM 摘要
        history_text = "\n\n".join(
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')}"
            for m in compressible
        )
        summary_messages = [
            {"role": "user", "content": f"{COMPACTION_PROMPT}\n\n对话历史：\n{history_text}"},
        ]

        try:
            summary_result = await self.llm.chat(
                messages=summary_messages,
                max_tokens=settings.COMPACTION_MAX_TOKENS,
            )
            summary_content = summary_result.content or ""
            if not summary_content:
                raise ValueError("摘要内容为空")
        except Exception as e:
            log.warning("Level 2 摘要生成失败，跳过压缩", error=str(e))
            return messages

        # 暂存摘要内容，供 chat.py 在执行完成后持久化为 genesis block
        self.last_compaction_summary = summary_content

        # 摘要注入 LLM 时使用 role=user（避免连续 assistant 消息违反 API 格式）
        summary_inject = (
            "【系统自动生成的历史摘要】\n"
            "以下内容由系统生成，帮助你了解之前的对话背景，请基于此继续执行任务。\n\n"
            f"{summary_content}\n\n"
            "---\n"
            "请继续基于以上历史背景执行当前任务。"
        )

        # 重建 messages：[system] → [user: 摘要] → [保护区消息]
        rebuilt: list[dict] = []
        if system_msg:
            rebuilt.append(system_msg)
        rebuilt.append({"role": "user", "content": summary_inject})
        rebuilt.extend(protected)

        log.info(
            "Level 2 摘要完成，messages 已重建",
            compressed_msgs=len(compressible),
            protected_msgs=len(protected),
            summary_tokens=self._estimate_tokens(summary_content),
        )

        return rebuilt

    def _build_initial_messages(self, intent_result: IntentResult) -> list[dict]:
        """
        组装 ReAct 循环的初始消息列表（task 8.2）。

        若当前请求是 Plugin 命令触发（plugin_context ContextVar 已设置），
        在 system prompt 末尾追加 COMMAND.md 工作流指引 + 插件 Skill 列表。

        历史消息注入：使用 token 动态边界（替代旧的 [-10:] 硬截断）。
        """
        user_goal = getattr(intent_result.intent, "user_goal", None)

        system_prompt = build_l3_system_prompt(
            user_input=intent_result.raw_input,
            user_goal=user_goal,
            max_iterations=self.config.max_iterations,
        )

        # Plugin 命令上下文注入（chat.py 在 execute() 前设置 ContextVar）
        from app.execution.plugin_context import PluginCommandContext, get_plugin_context
        plugin_ctx = get_plugin_context()
        if plugin_ctx:
            system_prompt += _build_plugin_context_block(plugin_ctx)

        # 历史消息：token 动态边界（从尾部往前累加，超出 PRUNE_PROTECT_TOKENS 停止）
        history_messages = intent_result.history_messages or []
        selected_history = self._select_history_by_token(history_messages)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            *selected_history,
            {"role": "user", "content": intent_result.raw_input},
        ]
        return messages

    def _select_history_by_token(self, history_messages: list[dict]) -> list[dict]:
        """
        从 history_messages 尾部往前按 token 估算累积，
        超出 PRUNE_PROTECT_TOKENS 的旧消息不注入（task 8.3）。
        """
        prune_protect = settings.PRUNE_PROTECT_TOKENS
        selected: list[dict] = []
        accumulated = 0
        for msg in reversed(history_messages):
            content = msg.get("content") or ""
            token_est = self._estimate_tokens(content)
            if accumulated + token_est <= prune_protect:
                accumulated += token_est
                selected.insert(0, msg)
            else:
                break
        return selected

    def _build_result(
        self,
        think_result: ThinkResult | None,
        observer: Observer,
        collected_steps: list[dict],
        degrade_reason: str | None = None,
    ) -> ExecutionResult:
        """从最终的 ThinkResult 和 Observer 构建 ExecutionResult"""
        elapsed = int(observer.elapsed_seconds * 1000)

        if degrade_reason:
            # 熔断降级
            summary = observer.trace.summarize_observations()
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
                reason=degrade_reason,
                iterations=observer.trace.step_count,
                llm_calls=observer.budget.llm_call_count,
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
                degrade_reason=degrade_reason,
                l3_steps=self._convert_steps(collected_steps),
            )

        return ExecutionResult(
            reply=think_result.thought if think_result else "",
            tool_calls=observer.trace.to_tool_call_records(),
            source="deep_l3",
            duration_ms=elapsed,
            reasoning_trace=observer.trace.to_dict(),
            iterations=observer.trace.step_count,
            token_usage=observer.budget.to_dict(),
            l3_steps=self._convert_steps(collected_steps),
        )

    @staticmethod
    def _convert_steps(raw_steps: list[dict]) -> list[dict] | None:
        """
        将 act_result.messages 格式转换为 l3_steps 存储格式（task 4.2）。

        LLM 格式：
        - assistant: {"role": "assistant", "content": "...", "tool_calls": [...]}
        - tool:      {"role": "tool", "content": "...", "tool_call_id": "..."}

        输出格式：每条记录含 step_index, role, content, tool_name, tool_call_id
        """
        if not raw_steps:
            return None

        steps: list[dict] = []
        for idx, msg in enumerate(raw_steps):
            role = msg.get("role", "")
            content = msg.get("content") or ""
            tool_name: str | None = None
            tool_call_id: str | None = None

            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                tool_name = msg.get("name")  # tool 消息携带的工具名（如有）
            elif role == "assistant" and msg.get("tool_calls"):
                # assistant tool_calls 消息：content 可能为空，序列化 tool_calls 作为内容
                if not content:
                    content = json.dumps(msg["tool_calls"], ensure_ascii=False)

            steps.append({
                "step_index": idx,
                "role": role,
                "content": content,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
            })

        return steps
