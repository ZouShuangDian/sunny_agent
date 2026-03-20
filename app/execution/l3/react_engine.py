"""
L3 深度推理引擎：编排 Thinker → Actor → Observer

中间件重构后的架构（v1.2）：
- run()：唯一公开执行入口，所有场景最终都调用此方法
- execute()：主 Agent 非流式场景预设（ContextVar + 默认中间件）
- execute_stream()：主 Agent 流式场景预设（ContextVar + 流式中间件 + Queue 并发）
- execute_raw() 已消除：SubAgent 直接调用 run(LoopContext.from_messages(...))

关注点通过中间件可插拔组合：
- TodoMiddleware：Layer 3 干预（before_think）
- ContextUsageMiddleware：上下文用量追踪（after_think）
- CompactionMiddleware：Level 2 摘要压缩（after_think）
- StepCollectorMiddleware：L3 步骤收集（after_act）
- SSEToolEventMiddleware：SSE 工具事件推送（after_act）

Context 压缩（保留在引擎中）：
- Level 1（内存级剪枝）：每步 Act 后无条件剪枝（token 估算边界）
- Level 2（摘要截断）：通过 CompactionMiddleware 触发
"""

import asyncio
import json
from collections.abc import AsyncIterator

import structlog

from app.config import get_settings
from app.execution.l3.actor import Actor
from app.execution.l3.event_emitter import EventEmitter, QueueEventEmitter
from app.execution.l3.loop_context import LoopContext
from app.execution.l3.middleware import (
    CompactionMiddleware,
    ContextUsageMiddleware,
    ReActMiddleware,
    SSEToolEventMiddleware,
    StepCollectorMiddleware,
    TodoMiddleware,
)
from app.execution.l3.observer import Observer
from app.execution.l3.prompts import build_l3_system_prompt
from app.execution.l3.schemas import L3Config, ThinkResult
from app.execution.l3.think_strategy import BatchThinkStrategy, StreamThinkStrategy, ThinkStrategy
from app.execution.l3.thinker import Thinker
from app.execution.schemas import ExecutionResult
from app.execution.session_context import get_session_id, reset_session_id, set_session_id
from app.execution.user_context import get_user_id
from app.guardrails.schemas import IntentResult
from app.llm.client import LLMClient
from app.cache.redis_client import redis_client
from app.execution.l3.live_steps_writer import LiveStepsWriter
from app.execution.mode_context import get_mode_context
from app.execution.plugin_context import PluginCommandContext, get_plugin_context
from app.execution.skill_directive_context import SkillDirectiveContext, get_skill_directive_context
from app.streaming.events import SSEEvent
from app.tools.registry import ToolRegistry

log = structlog.get_logger()
settings = get_settings()

# Level 2 摘要生成 prompt（结构化，含制造业业务实体要求）
COMPACTION_PROMPT = """请为以下对话历史生成结构化摘要（1500字以内），包含：
1. 任务目标
2. 已完成的操作步骤
3. 重要发现和结论
4. 操作过的文件、路径、数据
5. 涉及的业务实体（产品型号、工单号、设备编号、指标名称等）
6. 当前状态与下一步计划
要求：摘要需足够详细，让继续任务的 AI 能无缝衔接；同时控制篇幅，避免冗余。"""


def _build_plugin_context_block(ctx: PluginCommandContext) -> str:
    """
    将 PluginCommandContext 序列化为 system prompt 注入块。

    内容：
    1. 命令标识（plugin_name:command_name）
    2. COMMAND.md 完整工作流指引
    3. 插件内可用 Skill 列表（含容器内 SKILL.md 路径）
    4. 使用规范（禁止走全局 skill_call）
    """
    # 构建 Skill 列表，同时计算每个 Skill 的工作目录（scripts/ 的父目录）
    skill_lines = []
    skill_work_dirs = []
    for s in ctx.plugin_skills:
        # skill_md_path 形如 /mnt/.../skills/quality-complaints/SKILL.md
        # 工作目录 = SKILL.md 的父目录
        skill_dir = s["skill_md_path"].rsplit("/", 1)[0]
        skill_lines.append(
            f"- **{s['name']}**\n"
            f"  - SKILL.md: `{s['skill_md_path']}`\n"
            f"  - 工作目录: `{skill_dir}/`"
        )
        skill_work_dirs.append(skill_dir)

    skills_section = "\n".join(skill_lines) if skill_lines else "（此插件无内置 Skill）"

    # 如果只有一个 Skill，直接告知工作目录（最常见场景）
    work_dir_hint = ""
    if len(skill_work_dirs) == 1:
        work_dir_hint = (
            f"\n\n**⚠️ 路径重要提示：** 工作流指引中的相对路径（如 `scripts/xxx.py`、`references/xxx.md`）"
            f"必须基于 Skill 工作目录执行：\n"
            f"```\ncd {skill_work_dirs[0]} && python3 scripts/xxx.py\n```\n"
            f"或使用绝对路径：`python3 {skill_work_dirs[0]}/scripts/xxx.py`"
        )
    elif len(skill_work_dirs) > 1:
        work_dir_hint = (
            f"\n\n**⚠️ 路径重要提示：** 工作流指引中的相对路径必须基于对应 Skill 的工作目录执行，"
            f"不要直接在 `/workspace/` 下运行。"
        )

    return (
        f"\n\n---\n## Plugin 命令执行上下文\n\n"
        f"你正在执行用户触发的 Plugin 命令 `/{ctx.plugin_name}:{ctx.command_name}`。\n\n"
        f"**⚠️ 重要：下方已提供完整的工作流指引，直接按指引执行即可。"
        f"禁止用 `ls`、`find` 等命令探索插件目录结构——所有你需要的信息都在下方。**\n"
        f"{work_dir_hint}\n\n"
        f"### 工作流指引（COMMAND.md）\n\n"
        f"{ctx.command_md_content}\n\n"
        f"### 插件内可用 Skill\n\n"
        f"{skills_section}\n\n"
        f"使用插件 Skill 时：通过 `read_file` 读取对应 SKILL.md 路径，按指引操作。\n"
        f"**禁止**通过 `skill_call` 调用——插件 Skill 不在全局 skill catalog 中。\n"
    )


def _build_skill_directive_block(ctx: SkillDirectiveContext) -> str:
    """
    将 SkillDirectiveContext 序列化为 system prompt 注入块。

    用户通过 /skill:skillname 显式指定 Skill 时，
    将 SKILL.md 完整内容注入 system prompt，跳过 skill_call 间接调用。
    """
    return (
        f"\n\n---\n## Skill 指令执行上下文\n\n"
        f"用户指定使用 Skill `{ctx.skill_name}`，请严格按照下方指引执行。\n\n"
        f"**⚠️ 重要：SKILL.md 指引已直接提供，无需调用 `skill_call` 工具。"
        f"直接按指引使用 `read_file` 和 `bash_tool` 执行即可。**\n\n"
        f"### Skill 工作目录\n\n"
        f"- Skill 根目录: `{ctx.skill_dir}/`\n"
        f"- Scripts 目录: `{ctx.scripts_dir}/`\n\n"
        f"工作流指引中的相对路径（如 `scripts/xxx.py`）必须基于 Skill 根目录执行：\n"
        f"```\ncd {ctx.skill_dir} && python3 scripts/xxx.py\n```\n"
        f"或使用绝对路径：`python3 {ctx.scripts_dir}/xxx.py`\n\n"
        f"### SKILL.md 指引\n\n"
        f"{ctx.skill_md_content}\n"
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

    # ────────────────────────────────────────────────────────────────
    #  核心循环 — 唯一公开执行入口
    # ────────────────────────────────────────────────────────────────

    async def run(
        self,
        ctx: LoopContext,
        middlewares: list[ReActMiddleware] | None = None,
        think_strategy: ThinkStrategy | None = None,
    ) -> ExecutionResult:
        """
        ReAct 核心循环 — 唯一公开执行入口。

        调用方（execute / execute_stream / SubAgent / Task）通过构建不同的
        LoopContext + 中间件组合来控制行为差异。

        Args:
            ctx: 循环上下文（含 messages、observer、config、tool_schemas）
            middlewares: 中间件列表（None = 零中间件）
            think_strategy: Think 策略（None = BatchThinkStrategy）

        骨架：熔断检查 → before_think → Think → after_think → 完成检查 → Act → after_act → 压缩
        """
        middlewares = middlewares or []
        think_strategy = think_strategy or BatchThinkStrategy()
        think_result: ThinkResult | None = None

        # ── Langfuse: react_loop span ──
        from app.observability.context import langfuse_trace_var
        trace = langfuse_trace_var.get()
        react_span = None
        if trace:
            react_span = trace.start_span(name="react_loop", metadata={"max_iterations": ctx.config.max_iterations})

        for step in range(ctx.config.max_iterations):
            ctx.step = step

            # ── 熔断检查 ──
            should_stop, reason = ctx.observer.should_stop()
            if should_stop:
                return self._build_degraded_result(ctx, reason)

            # ── before_think（如 Todo 注入）──
            for mw in middlewares:
                await mw.before_think(ctx)

            # ── Think（batch 或 stream）──
            tool_schemas_for_step = (
                ctx.tool_schemas if step < ctx.config.max_iterations - 1 else None
            )
            think_result = await think_strategy.think(ctx, self.thinker, tool_schemas_for_step)
            # 注意：on_think 在 after_think（compaction）之前调用。
            # 与重构前 execute_stream 的顺序不同（重构前是 compaction 后再 on_think），
            # 但 on_think 仅做记录和 budget 累计，不依赖 compaction 结果，功能等价。
            ctx.observer.on_think(step, think_result)

            # ── after_think（如 context_usage、Level 2 压缩）──
            for mw in middlewares:
                await mw.after_think(ctx, think_result)

            # ── 完成检查 ──
            if think_result.is_done:
                break

            # ── Act ──
            act_result = await self.actor.act(think_result)
            ctx.observer.on_act(step, act_result)

            # ── after_act（如 Step 收集、SSE 推送）──
            for mw in middlewares:
                await mw.after_act(ctx, act_result)

            # ── 追加消息 + Level 1 剪枝（核心循环固有逻辑）──
            ctx.messages.extend(act_result.messages)
            ctx.messages = self._compress_stale_tool_results(ctx.messages)

        if react_span:
            react_span.end()

        return self._build_loop_result(ctx, think_result)

    # ────────────────────────────────────────────────────────────────
    #  场景预设：execute() — 主 Agent 非流式
    # ────────────────────────────────────────────────────────────────

    async def execute(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> ExecutionResult:
        """
        主 Agent 非流式场景预设：ContextVar 管理 + 默认中间件组合。

        中间件顺序有语义约束（见 middleware.py 文档）：
        TodoMiddleware → ContextUsageMiddleware → CompactionMiddleware → StepCollectorMiddleware
        """
        sid_token = set_session_id(session_id)
        try:
            ctx = await self._build_context(intent_result)
            writer = LiveStepsWriter(redis=redis_client, session_id=session_id)
            return await self.run(ctx, middlewares=[
                TodoMiddleware(),
                ContextUsageMiddleware(),
                CompactionMiddleware(self),
                StepCollectorMiddleware(live_steps_writer=writer),
            ])
        finally:
            reset_session_id(sid_token)

    # ────────────────────────────────────────────────────────────────
    #  场景预设：execute_stream() — 主 Agent 流式
    # ────────────────────────────────────────────────────────────────

    async def execute_stream(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """
        主 Agent 流式场景预设：ContextVar + 默认中间件 + StreamThinkStrategy + Queue 并发。

        SSE 事件格式：
        - {"event": "tool_call",     "data": {"step": 0, "name": "...", "args": {...}}}
        - {"event": "tool_result",   "data": {"step": 0, "name": "...", "result": "..."}}
        - {"event": "delta",         "data": {"content": "文本片段（逐 token）"}}
        - {"event": "context_usage", "data": {"prompt_tokens": ..., ...}}
        - {"event": "finish",        "data": {"iterations": 2, ...}}
        """
        sid_token = set_session_id(session_id)
        emitter = QueueEventEmitter()
        loop_task: asyncio.Task | None = None
        try:
            ctx = await self._build_context(intent_result, event_emitter=emitter)
            writer = LiveStepsWriter(redis=redis_client, session_id=session_id)
            middlewares: list[ReActMiddleware] = [
                TodoMiddleware(),
                ContextUsageMiddleware(),
                CompactionMiddleware(self),
                StepCollectorMiddleware(live_steps_writer=writer),
                SSEToolEventMiddleware(),
            ]

            loop_task = asyncio.create_task(
                self._run_and_finish(ctx, middlewares, StreamThinkStrategy(), emitter)
            )

            # 从 Queue 消费事件并 yield
            while True:
                event = await emitter.queue.get()
                if event is None:  # 哨兵值 = 循环结束
                    break
                yield event

            # 等待循环任务完成（获取可能的异常）
            await loop_task
        finally:
            # P1-2 修复：客户端断开时取消后台任务，防止资源泄漏
            if loop_task and not loop_task.done():
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass  # 预期行为：任务被取消
            reset_session_id(sid_token)

    async def _run_and_finish(
        self,
        ctx: LoopContext,
        middlewares: list[ReActMiddleware],
        think_strategy: ThinkStrategy,
        emitter: QueueEventEmitter,
    ) -> None:
        """执行循环 + 推送 finish + 关闭 emitter"""
        try:
            result = await self.run(ctx, middlewares, think_strategy)

            # P0 修复：熔断降级时，先推送降级文本 DELTA，再推 FINISH
            if result.is_degraded and result.reply:
                await emitter.emit(SSEEvent.DELTA, {"content": result.reply})

            await emitter.emit(SSEEvent.FINISH, self._build_finish_data(
                ctx.observer, ctx.collected_steps, ctx.last_compaction_summary,
                is_degraded=result.is_degraded,
            ))
        except Exception as e:
            log.error("ReAct 循环异常", error=str(e), exc_info=True)
            # P1-1 修复：异常路径推送完整 FINISH 结构
            await emitter.emit(SSEEvent.FINISH, {
                "error": str(e),
                "iterations": ctx.observer.trace.step_count,
                "llm_calls": ctx.observer.budget.llm_call_count,
                "is_degraded": True,
                "l3_steps": L3ReActEngine._convert_steps(ctx.collected_steps),
                "compaction_summary": ctx.last_compaction_summary,
                "token_usage": ctx.observer.budget.to_dict(),
            })
        finally:
            await emitter.close()

    # ────────────────────────────────────────────────────────────────
    #  内部辅助方法
    # ────────────────────────────────────────────────────────────────

    async def _build_context(
        self,
        intent_result: IntentResult,
        event_emitter: EventEmitter | None = None,
    ) -> LoopContext:
        """
        从 IntentResult 构建 LoopContext（execute / execute_stream 共用）。

        职责：
        1. 构建初始 messages（_build_initial_messages）
        2. 获取工具 schema
        3. 创建 Observer 并 start()
        4. 提取 user_goal
        """
        observer = Observer(self.config)
        observer.start()

        user_goal = getattr(intent_result.intent, "user_goal", None) or None

        # 工具过滤：mode 配置了 allowed_tools 则仅暴露白名单（不加载 MCP 工具）
        mode_ctx = get_mode_context()
        if mode_ctx and mode_ctx.allowed_tools is not None:
            tool_schemas = self.tool_registry.get_schemas(mode_ctx.allowed_tools)
        else:
            tool_schemas = self.tool_registry.get_all_schemas(
                include_mode_only=mode_ctx is not None,
            )
            # 普通对话：追加用户已启用的 MCP 连接器工具
            from app.execution.user_context import get_user_id
            from app.connector.tool_loader import load_mcp_tool_schemas
            mcp_schemas = await load_mcp_tool_schemas(get_user_id() or "")
            if mcp_schemas:
                tool_schemas = tool_schemas + mcp_schemas

        return LoopContext(
            messages=self._build_initial_messages(intent_result),
            observer=observer,
            config=self.config,
            tool_schemas=tool_schemas,
            user_goal=user_goal,
            event_emitter=event_emitter,
        )

    def _build_degraded_result(self, ctx: LoopContext, reason: str) -> ExecutionResult:
        """熔断降级时构建 ExecutionResult（从 LoopContext 提取所有参数）"""
        return self._build_result(
            think_result=None,
            observer=ctx.observer,
            collected_steps=ctx.collected_steps,
            degrade_reason=reason,
            context_usage=ctx.last_context_usage,
            compaction_summary=ctx.last_compaction_summary,
        )

    def _build_loop_result(
        self,
        ctx: LoopContext,
        think_result: ThinkResult | None,
    ) -> ExecutionResult:
        """正常结束 / 达到 max_iterations 时构建 ExecutionResult"""
        # P2-B 修复：循环耗尽（think_result 为 None 或未完成）= 防御性降级
        is_exhausted = think_result is None or not think_result.is_done
        return self._build_result(
            think_result=think_result,
            observer=ctx.observer,
            collected_steps=ctx.collected_steps,
            degrade_reason="max_iterations" if is_exhausted else None,
            context_usage=ctx.last_context_usage,
            compaction_summary=ctx.last_compaction_summary,
        )

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
        """
        prune_protect = settings.PRUNE_PROTECT_TOKENS
        result = list(messages)

        accumulated = 0
        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            if msg.get("role") != "tool":
                continue

            content = msg.get("content", "")
            token_est = self._estimate_tokens(content)

            if accumulated + token_est <= prune_protect:
                accumulated += token_est
            else:
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

    async def _compact_messages(self, messages: list[dict]) -> tuple[list[dict], str | None]:
        """
        Level 2 摘要截断（task 7.1）：生成摘要并重建 messages 列表。

        流程：
        1. 识别保护区（从尾部按 PRUNE_PROTECT_TOKENS 划定）
        2. 提取保护区外的可压缩区内容
        3. 调用 LLM 生成结构化摘要（max_tokens=COMPACTION_MAX_TOKENS）
        4. 重建 messages：[system] → [user: 摘要] → [保护区消息]

        返回值：(重建后的 messages, 摘要内容)。摘要内容供调用方传递给 chat.py 持久化为 genesis block。
        失败时降级：记录 warning，返回 (原 messages, None)。
        """
        if not messages:
            return messages, None

        system_msg = messages[0] if messages[0].get("role") == "system" else None
        non_system = messages[1:] if system_msg else messages

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

        compressible = non_system[: len(non_system) - len(protected)]
        if not compressible:
            log.warning("Level 2 摘要：无可压缩消息，跳过")
            return messages, None

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
            return messages, None

        summary_inject = (
            "【系统自动生成的历史摘要】\n"
            "以下内容由系统生成，帮助你了解之前的对话背景，请基于此继续执行任务。\n\n"
            f"{summary_content}\n\n"
            "---\n"
            "请继续基于以上历史背景执行当前任务。"
        )

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

        return rebuilt, summary_content

    def _build_initial_messages(self, intent_result: IntentResult) -> list[dict]:
        """
        组装 ReAct 循环的初始消息列表（task 8.2）。

        若当前请求是 Plugin 命令触发（plugin_context ContextVar 已设置），
        在 system prompt 末尾追加 COMMAND.md 工作流指引 + 插件 Skill 列表。

        历史消息注入：使用 token 动态边界（替代旧的 [-10:] 硬截断）。
        """
        user_goal = getattr(intent_result.intent, "user_goal", None)
        mode_ctx = get_mode_context()

        # 模式可选择完全替换 L3 基础 prompt（如深度研究只需 ask_user + create_task，不需要 L3 工具链指引）
        if mode_ctx and mode_ctx.override_system_prompt:
            system_prompt = mode_ctx.system_prompt_block
        else:
            system_prompt = build_l3_system_prompt(
                user_input=intent_result.raw_input,
                user_goal=user_goal,
                max_iterations=self.config.max_iterations,
                user_id=get_user_id() or "",
                session_id=get_session_id() or "",
            )

            plugin_ctx = get_plugin_context()
            if plugin_ctx:
                system_prompt += _build_plugin_context_block(plugin_ctx)
                log.info(
                    "Plugin 上下文已注入 system prompt",
                    plugin=plugin_ctx.plugin_name,
                    command=plugin_ctx.command_name,
                    content_len=len(plugin_ctx.command_md_content),
                    skills_count=len(plugin_ctx.plugin_skills),
                )

            skill_directive_ctx = get_skill_directive_context()
            if skill_directive_ctx:
                system_prompt += _build_skill_directive_block(skill_directive_ctx)
                log.info(
                    "Skill 指令上下文已注入 system prompt",
                    skill=skill_directive_ctx.skill_name,
                    content_len=len(skill_directive_ctx.skill_md_content),
                )

            if mode_ctx:
                system_prompt += mode_ctx.system_prompt_block

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
        超出 HISTORY_TOKEN_BUDGET 的旧消息不注入。
        """
        budget = settings.HISTORY_TOKEN_BUDGET
        selected: list[dict] = []
        accumulated = 0
        for msg in reversed(history_messages):
            content = msg.get("content") or ""
            token_est = self._estimate_tokens(content)
            if accumulated + token_est <= budget:
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
        context_usage: dict | None = None,
        compaction_summary: str | None = None,
    ) -> ExecutionResult:
        """从最终的 ThinkResult 和 Observer 构建 ExecutionResult"""
        elapsed = int(observer.elapsed_seconds * 1000)

        if degrade_reason:
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
                context_usage=context_usage,
                compaction_summary=compaction_summary,
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
            context_usage=context_usage,
            compaction_summary=compaction_summary,
        )

    @staticmethod
    def _build_finish_data(
        observer: Observer,
        collected_steps: list[dict],
        last_compaction_summary: str | None,
        *,
        is_degraded: bool = False,
    ) -> dict:
        """统一构建 finish 事件 data，确保数据一致。"""
        return {
            "iterations": observer.trace.step_count,
            "llm_calls": observer.budget.llm_call_count,
            "is_degraded": is_degraded,
            "l3_steps": L3ReActEngine._convert_steps(collected_steps),
            "compaction_summary": last_compaction_summary,
            "token_usage": observer.budget.to_dict(),
        }

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

            tool_args: dict | None = None

            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                tool_name = msg.get("name")
            elif role == "assistant" and msg.get("tool_calls"):
                # 提取工具入参：{tool_name: args_dict, ...}
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

            steps.append({
                "step_index": idx,
                "role": role,
                "content": content,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "tool_args": tool_args,
            })

        return steps
