"""SubAgent 框架（M08-5 Week 11）"""

from app.subagents.loader import SubAgentConfig, load_agent_from_dir, scan_agents_dir
from app.subagents.registry import SubAgentRegistry

__all__ = [
    "SubAgentConfig",
    "SubAgentRegistry",
    "load_agent_from_dir",
    "scan_agents_dir",
]
