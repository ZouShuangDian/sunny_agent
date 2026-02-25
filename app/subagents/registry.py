"""
SubAgent 注册中心

核心设计（对齐 M08-6 SkillRegistry 范式）：
- 单一 subagent_call 元工具代理所有 SubAgent 调用
- 只维护内存中的 SubAgent 索引（name → SubAgentConfig）
- get_catalog() 供 SubAgentCallTool 动态构建工具描述

多目录加载：
- from_directories([builtin_dir, user_dir]) 按顺序扫描
- 同名 SubAgent 后加载的覆盖先加载的（用户目录 > 内置目录）
"""

from __future__ import annotations

from pathlib import Path

import structlog

from app.subagents.loader import SubAgentConfig, scan_agents_dir

log = structlog.get_logger()


class SubAgentRegistry:
    """SubAgent 注册中心"""

    def __init__(self) -> None:
        self._agents: dict[str, SubAgentConfig] = {}

    @classmethod
    def from_directory(cls, agents_root: Path) -> "SubAgentRegistry":
        """工厂方法：从单个目录扫描创建 SubAgentRegistry（向后兼容）"""
        return cls.from_directories([agents_root])

    @classmethod
    def from_directories(cls, agent_dirs: list[Path]) -> "SubAgentRegistry":
        """
        工厂方法：从多个目录扫描创建 SubAgentRegistry。

        按顺序加载，同名 SubAgent 后加载的覆盖先加载的。
        典型用法：from_directories([builtin_dir, user_dir])
        - builtin_dir：项目内置 SubAgents
        - user_dir：~/.sunny-agent/agents/（用户自定义，优先级更高）
        """
        registry = cls()
        for agent_dir in agent_dirs:
            if not agent_dir.exists():
                log.debug("SubAgent 目录不存在，跳过", path=str(agent_dir))
                continue
            agents = scan_agents_dir(agent_dir)
            for agent in agents:
                registry.register(agent)
        return registry

    def register(self, config: SubAgentConfig) -> None:
        """
        注册单个 SubAgentConfig。

        同名 SubAgent 会被覆盖（后注册优先，支持用户 override 内置）。
        """
        if config.name in self._agents:
            log.info("SubAgent 同名覆盖", agent=config.name, tip="用户目录 SubAgent 覆盖内置")
        self._agents[config.name] = config
        log.debug("SubAgent 已注册", agent=config.name, max_depth=config.max_depth)

    def has_agent(self, name: str) -> bool:
        return name in self._agents

    def get(self, name: str) -> SubAgentConfig | None:
        return self._agents.get(name)

    def get_catalog(self) -> list[tuple[str, str]]:
        """
        返回所有已注册 SubAgent 的目录，每项为 (name, description) 二元组。
        供 SubAgentCallTool 动态构建工具描述。
        """
        return [
            (config.name, config.description)
            for config in self._agents.values()
        ]

    @property
    def agent_names(self) -> list[str]:
        return list(self._agents.keys())

    @property
    def agent_count(self) -> int:
        return len(self._agents)
