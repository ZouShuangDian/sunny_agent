"""
执行路由器：根据 IntentResult.route 分发到对应的执行路径

两种执行模式：
- standard_l1: L1 标准执行，Bounded Loop + 固定工具集 + PromptService 检索
- deep_l3: L3 深度推理，模块化 ReAct 循环（Thinker/Actor/Observer）

关键设计（Q1 裁决）：L1 和 L3 共享同一个 ToolRegistry 实例，
通过 get_all_schemas() / get_schemas_by_tier() 分别获取各自需要的工具集。
"""

import time
from collections.abc import AsyncIterator
from pathlib import Path

import structlog

from app.execution.l1.fast_track import L1FastTrack
from app.execution.l3.react_engine import L3ReActEngine
from app.execution.schemas import ExecutionResult
from app.guardrails.schemas import IntentResult
from app.llm.client import LLMClient
from app.skills.registry import SkillRegistry
from app.subagents.registry import SubAgentRegistry
from app.tools.builtin_tools import create_builtin_registry
from app.tools.builtin_tools.skill_call import SkillCallTool
from app.tools.builtin_tools.skill_exec import SkillExecTool
from app.tools.builtin_tools.subagent_call import SubAgentCallTool

# 内置 Skills 目录（项目自带）
_BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills" / "builtin_skills"
# 用户自定义 Skills 目录（~/.sunny-agent/skills/）
_USER_SKILLS_DIR = Path.home() / ".sunny-agent" / "skills"

# 内置 SubAgents 目录
_BUILTIN_AGENTS_DIR = Path(__file__).parent.parent / "subagents" / "builtin_agents"
# 用户自定义 SubAgents 目录（~/.sunny-agent/agents/）
_USER_AGENTS_DIR = Path.home() / ".sunny-agent" / "agents"

log = structlog.get_logger()


class ExecutionRouter:
    """执行层统一入口"""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        tool_registry = create_builtin_registry()

        # 多目录加载 MarkdownSkill：内置目录 → 用户目录（同名用户覆盖内置）
        # 脚本不注册为 Tool，由 SkillExecTool 按白名单执行
        skill_registry = SkillRegistry.from_directories(
            [_BUILTIN_SKILLS_DIR, _USER_SKILLS_DIR],
        )

        # 注册 skill_call 元工具（单一入口代理所有 Skill）
        tool_registry.register(SkillCallTool(skill_registry))

        # 注册 skill_exec 工具（Tier 3 脚本执行，白名单校验，须先调用 skill_call）
        tool_registry.register(SkillExecTool(skill_registry))

        # 多目录加载 SubAgent：内置目录 → 用户目录（同名用户覆盖内置）
        agent_registry = SubAgentRegistry.from_directories(
            [_BUILTIN_AGENTS_DIR, _USER_AGENTS_DIR],
        )

        # 注册 subagent_call 元工具（单一入口代理所有 SubAgent）
        tool_registry.register(SubAgentCallTool(agent_registry, tool_registry, llm))

        self.l1 = L1FastTrack(llm, tool_registry)
        self.l3 = L3ReActEngine(llm, tool_registry)

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
            result = await self.l3.execute(intent_result, session_id)
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
            async for event in self.l3.execute_stream(intent_result, session_id):
                yield event
        else:
            log.warning("未知路由，降级到 standard_l1 流式", route=route)
            async for event in self.l1.execute_stream(intent_result, session_id):
                yield event
