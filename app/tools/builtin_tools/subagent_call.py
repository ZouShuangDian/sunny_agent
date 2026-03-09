"""
SubAgentCallTool — SubAgent 元工具（M08-5 Week 11）

设计意图（对标 opencode TaskTool）：
- 将所有 SubAgent 收敛到一个工具入口，避免 N Agent → N function schema
- LLM 调用 subagent_call(agent_name, task) → 启动独立 L3 ReAct 循环
- 子 Agent 拥有隔离上下文（只有专属 system prompt + task），不看主 Agent 历史
- 通过 RestrictedToolRegistry 实现工具物理白名单（非仅隐藏 schema）
- 通过 ContextVar 管理深度，防递归爆炸

与 SkillCallTool 的区别：
  skill_call   → 注入操作手册（Tier 2 body），当前 Agent 自己执行
  subagent_call → 启动独立 Agent 实例，等待汇总报告（任务委派）
"""

from __future__ import annotations

import asyncio

import structlog
from pydantic import BaseModel

from app.execution.agent_context import get_agent_depth, reset_agent_depth, set_agent_depth
from app.execution.l3.schemas import L3Config
from app.execution.session_context import reset_session_id, set_session_id
from app.subagents.registry import SubAgentRegistry
from app.tools.base import BaseTool, ToolResult
from app.tools.registry import RestrictedToolRegistry, ToolRegistry

log = structlog.get_logger()


class _SubAgentParams(BaseModel):
    """占位参数模型（实际 schema 由 schema() 覆盖）"""
    model_config = {"extra": "allow"}


class SubAgentCallTool(BaseTool):
    """
    SubAgent 元工具：单一入口代理所有 SubAgent 调用。

    tier = ["L3"]：只在 L3 深度引擎中可用。
    """

    def __init__(
        self,
        agent_registry: SubAgentRegistry,
        tool_registry: ToolRegistry,
        llm: "LLMClient",  # type: ignore[name-defined]
    ) -> None:
        self._registry = agent_registry
        self._tool_registry = tool_registry
        self._llm = llm

    # ── 抽象属性实现 ──

    @property
    def name(self) -> str:
        return "subagent_call"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        """动态生成：列出所有可用 SubAgent 的名称和描述"""
        catalog = self._registry.get_catalog()
        lines = [
            "将复杂任务委派给专业领域子 Agent 独立执行。"
            "子 Agent 拥有独立推理循环和隔离上下文，完成后返回汇总报告。\n",
            "可用 SubAgent（格式：name: 描述）：",
        ]
        if catalog:
            for agent_name, agent_desc in catalog:
                lines.append(f"  - {agent_name}: {agent_desc}")
        else:
            lines.append("  （暂无可用 SubAgent）")
        return "\n".join(lines)

    @property
    def params_model(self) -> type[BaseModel]:
        return _SubAgentParams

    # ── 覆盖 schema()：动态 enum ──

    def schema(self) -> dict:
        catalog = self._registry.get_catalog()
        agent_names = [n for n, _ in catalog]

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "要调用的 SubAgent 名称",
                            "enum": agent_names if agent_names else ["__no_agent__"],
                        },
                        "task": {
                            "type": "string",
                            "description": (
                                "委派给 SubAgent 的任务描述，尽量详细清晰，"
                                "包含必要的背景信息和期望输出格式。"
                            ),
                        },
                    },
                    "required": ["agent_name", "task"],
                },
            },
        }

    # ── 执行：按 type 路由到对应后端 ──

    async def execute(self, args: dict) -> ToolResult:
        """
        启动子 Agent 执行。

        当前仅支持 local_l3 类型（独立 L3 ReAct 循环）。
        非 local_l3 类型直接返回错误（v3 简化，local_code / http 已移除）。
        """
        agent_name = args.get("agent_name", "")
        task = args.get("task", "")

        if not agent_name or not task:
            return ToolResult.fail("agent_name 和 task 参数不能为空")

        config = self._registry.get(agent_name)
        if not config:
            return ToolResult.fail(
                f"未知 SubAgent: {agent_name}，"
                f"可用列表: {self._registry.agent_names}"
            )

        # 深度熔断（所有类型通用）
        current_depth = get_agent_depth()
        if current_depth >= config.max_depth:
            log.warning(
                "SubAgent 深度超限，拒绝执行",
                agent=agent_name,
                current_depth=current_depth,
                max_depth=config.max_depth,
            )
            return ToolResult.fail(
                f"SubAgent '{agent_name}' 嵌套深度已达上限 {config.max_depth}，"
                "请在当前 Agent 中直接处理此任务"
            )

        log.info("SubAgent 启动", agent=agent_name, task_preview=task[:100])

        if config.type != "local_l3":
            return ToolResult.fail(
                f"SubAgent 仅支持 local_l3 类型，当前: {config.type}"
            )

        return await self._execute_local_l3(config, task, current_depth)

    async def _execute_local_l3(self, config, task: str, current_depth: int) -> ToolResult:
        """local_l3：独立 L3 ReAct 循环（含超时控制）"""
        # 延迟导入，避免循环依赖（SubAgentCallTool ← react_engine ← router ← SubAgentCallTool）
        from app.execution.l3.loop_context import LoopContext
        from app.execution.l3.react_engine import L3ReActEngine

        messages = [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": task},
        ]

        if config.tool_filter is not None:
            sub_tool_registry = RestrictedToolRegistry(self._tool_registry, config.tool_filter)
        else:
            sub_tool_registry = self._tool_registry

        timeout_s = config.timeout_ms / 1000

        sub_l3_config = L3Config(
            max_iterations=config.max_iterations,
            timeout_seconds=timeout_s,
            max_llm_calls=config.max_iterations * 2,
        )

        sub_engine = L3ReActEngine(self._llm, sub_tool_registry, sub_l3_config)

        # 构建 LoopContext（零中间件 + BatchThinkStrategy，SubAgent 无需 Todo/压缩等）
        ctx = LoopContext.from_messages(
            messages=messages,
            config=sub_l3_config,
            tool_schemas=sub_tool_registry.get_all_schemas(),
        )

        # ContextVar 管理：depth + session_id
        depth_token = set_agent_depth(current_depth + 1)
        sid_token = set_session_id("")
        sub_result = None
        error_type = None  # "timeout" | "error" | None
        try:
            # 显式超时控制（防止单个慢工具导致 SubAgent 无限挂起）
            sub_result = await asyncio.wait_for(
                sub_engine.run(ctx),  # 零中间件 + BatchThinkStrategy（默认值）
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            error_type = "timeout"
            log.warning("SubAgent 执行超时", agent=config.name, timeout_s=timeout_s)
        except Exception as e:
            error_type = "error"
            log.error("SubAgent 执行异常", agent=config.name, error=str(e), exc_info=True)
        finally:
            reset_agent_depth(depth_token)
            reset_session_id(sid_token)

        # 超时/异常时返回降级结果
        if error_type == "timeout":
            return ToolResult.fail(
                f"SubAgent '{config.name}' 执行超时（{timeout_s:.0f}s）"
            )
        if error_type == "error":
            return ToolResult.fail(f"SubAgent '{config.name}' 执行异常")

        llm_calls = sub_result.token_usage.get("llm_calls", 0) if sub_result.token_usage else 0
        log.info(
            "SubAgent 完成",
            agent=config.name,
            iterations=sub_result.iterations,
            llm_calls=llm_calls,
        )
        return ToolResult.success(
            agent=config.name,
            report=sub_result.reply,
            iterations=sub_result.iterations,
            llm_calls=llm_calls,
            is_degraded=sub_result.is_degraded,
        )
