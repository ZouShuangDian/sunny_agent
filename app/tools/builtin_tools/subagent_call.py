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

        按 config.type 路由到不同后端：
        - local_l3：独立 L3 ReAct 循环（默认）
        - local_code：Python 实现类（任意复杂逻辑）
        - http：外部 Agent HTTP 接口
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

        log.info("SubAgent 启动", agent=agent_name, type=config.type, task_preview=task[:100])

        if config.type == "local_l3":
            return await self._execute_local_l3(config, task, current_depth)
        elif config.type == "local_code":
            return await self._execute_local_code(config, task, current_depth)
        elif config.type == "http":
            return await self._execute_http(config, task, current_depth)
        else:
            return ToolResult.fail(f"未知 SubAgent 类型: {config.type}")

    async def _execute_local_l3(self, config, task: str, current_depth: int) -> ToolResult:
        """local_l3：独立 L3 ReAct 循环"""
        # 延迟导入，避免循环依赖（SubAgentCallTool ← react_engine ← router ← SubAgentCallTool）
        from app.execution.l3.react_engine import L3ReActEngine

        messages = [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": task},
        ]

        if config.tool_filter is not None:
            sub_tool_registry = RestrictedToolRegistry(self._tool_registry, config.tool_filter)
        else:
            sub_tool_registry = self._tool_registry

        sub_l3_config = L3Config(
            max_iterations=config.max_iterations,
            timeout_seconds=config.timeout_ms / 1000,
            max_llm_calls=config.max_iterations * 2,
        )

        sub_engine = L3ReActEngine(self._llm, sub_tool_registry, sub_l3_config)

        # ContextVar 在 await 链路中会被子协程继承，必须显式清空 session_id
        depth_token = set_agent_depth(current_depth + 1)
        sid_token = set_session_id("")
        try:
            sub_result = await sub_engine.execute_raw(messages)
        finally:
            reset_agent_depth(depth_token)
            reset_session_id(sid_token)

        log.info(
            "SubAgent(local_l3) 完成",
            agent=config.name,
            iterations=sub_result.iterations,
            tokens=sub_result.token_usage.get("total_tokens", 0) if sub_result.token_usage else 0,
        )
        return ToolResult.success(
            agent=config.name,
            report=sub_result.reply,
            iterations=sub_result.iterations,
            tokens_used=(
                sub_result.token_usage.get("total_tokens", 0)
                if sub_result.token_usage else 0
            ),
            is_degraded=sub_result.is_degraded,
        )

    async def _execute_local_code(self, config, task: str, current_depth: int) -> ToolResult:
        """
        local_code：动态加载 Python 实现类并执行。

        entry 格式：module.path::ClassName
        实现类须继承 LocalAgentExecutor 并实现 execute(task: str) -> str。
        """
        import importlib
        from app.subagents.executor import LocalAgentExecutor

        try:
            module_path, class_name = config.entry.rsplit("::", 1)
        except ValueError:
            return ToolResult.fail(
                f"entry 格式错误（期望 'module.path::ClassName'）：{config.entry}"
            )

        try:
            module = importlib.import_module(module_path)
            executor_class = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            return ToolResult.fail(f"无法加载 entry '{config.entry}'：{e}")

        if not (isinstance(executor_class, type) and issubclass(executor_class, LocalAgentExecutor)):
            return ToolResult.fail(f"{config.entry} 必须是 LocalAgentExecutor 的子类")

        depth_token = set_agent_depth(current_depth + 1)
        sid_token = set_session_id("")
        try:
            executor = executor_class()
            report = await executor.execute(task)
        except Exception as e:
            log.error("SubAgent(local_code) 执行异常", agent=config.name, error=str(e), exc_info=True)
            return ToolResult.fail(f"SubAgent '{config.name}' 执行失败：{e}")
        finally:
            reset_agent_depth(depth_token)
            reset_session_id(sid_token)

        log.info("SubAgent(local_code) 完成", agent=config.name)
        return ToolResult.success(agent=config.name, report=report)

    async def _execute_http(self, config, task: str, current_depth: int) -> ToolResult:
        """
        http：调用外部 Agent HTTP 接口。

        约定请求格式：POST {endpoint}  Body: {"task": "..."}
        约定响应格式：{"reply": "..."}  或 {"result": "..."}（兼容两种 key）
        """
        import httpx

        depth_token = set_agent_depth(current_depth + 1)
        sid_token = set_session_id("")
        try:
            async with httpx.AsyncClient(timeout=config.timeout_ms / 1000) as client:
                resp = await client.post(config.endpoint, json={"task": task})
                resp.raise_for_status()
                data = resp.json()
                report = data.get("reply") or data.get("result") or str(data)
        except httpx.TimeoutException:
            return ToolResult.fail(
                f"外部 Agent '{config.name}' 请求超时（{config.timeout_ms / 1000:.0f}s）"
            )
        except httpx.HTTPStatusError as e:
            return ToolResult.fail(
                f"外部 Agent '{config.name}' 返回错误状态码 {e.response.status_code}"
            )
        except Exception as e:
            log.error("SubAgent(http) 请求异常", agent=config.name, error=str(e), exc_info=True)
            return ToolResult.fail(f"外部 Agent '{config.name}' 请求失败：{e}")
        finally:
            reset_agent_depth(depth_token)
            reset_session_id(sid_token)

        log.info("SubAgent(http) 完成", agent=config.name, endpoint=config.endpoint)
        return ToolResult.success(agent=config.name, report=report)
