"""
执行路由器：统一通过 L3 ReAct 引擎执行

Skill 加载策略（DB 驱动）：
- 每次请求在 execute() / execute_stream() 入口处调用 SkillService 查询 DB，
  将结果设置到 skill_context ContextVar（try/finally 确保还原）
- SkillCallTool 运行时从 ContextVar 动态读取当前用户的 Skill 列表
"""

import time
from collections.abc import AsyncIterator
from pathlib import Path

import structlog

from app.execution.l3.react_engine import L3ReActEngine
from app.execution.schemas import ExecutionResult
from app.execution.skill_context import reset_skill_catalog, set_skill_catalog
from app.execution.user_context import get_user_id
from app.guardrails.schemas import IntentResult
from app.llm.client import LLMClient
from app.skills.service import skill_service
from app.subagents.registry import SubAgentRegistry
from app.tools.builtin_tools import create_builtin_registry
from app.tools.builtin_tools.skill_call import SkillCallTool
from app.tools.builtin_tools.subagent_call import SubAgentCallTool

# 内置 SubAgents 目录
_BUILTIN_AGENTS_DIR = Path(__file__).parent.parent / "subagents" / "builtin_agents"
# 用户自定义 SubAgents 目录（~/.sunny-agent/agents/）
_USER_AGENTS_DIR = Path.home() / ".sunny-agent" / "agents"

log = structlog.get_logger()


class ExecutionRouter:
    """执行层统一入口（统一 L3）"""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        tool_registry = create_builtin_registry()

        # 注册 skill_call 元工具（无构造参数，运行时从 skill_context ContextVar 读取）
        tool_registry.register(SkillCallTool())

        # 多目录加载 SubAgent：内置目录 → 用户目录（同名用户覆盖内置）
        agent_registry = SubAgentRegistry.from_directories(
            [_BUILTIN_AGENTS_DIR, _USER_AGENTS_DIR],
        )

        # 注册 subagent_call 元工具（单一入口代理所有 SubAgent）
        tool_registry.register(SubAgentCallTool(agent_registry, tool_registry, llm))

        self.l3 = L3ReActEngine(llm, tool_registry)

    async def execute(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> ExecutionResult:
        """非流式执行"""
        start = time.time()

        # 查询当前用户可用的 Skill 列表并设置到请求级 ContextVar
        usernumb = get_user_id()
        catalog = await skill_service.get_user_skills(usernumb)
        skill_token = set_skill_catalog(catalog)

        try:
            result = await self.l3.execute(intent_result, session_id)
        finally:
            reset_skill_catalog(skill_token)

        result.duration_ms = int((time.time() - start) * 1000)
        return result

    async def execute_stream(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """流式执行"""
        # 查询当前用户可用的 Skill 列表并设置到请求级 ContextVar
        usernumb = get_user_id()
        catalog = await skill_service.get_user_skills(usernumb)
        skill_token = set_skill_catalog(catalog)

        try:
            async for event in self.l3.execute_stream(intent_result, session_id):
                yield event
        finally:
            reset_skill_catalog(skill_token)
