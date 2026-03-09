"""
SubAgent 加载器：从 agent.md 解析 SubAgentConfig

agent.md 格式（v3 简化：仅支持 local_l3 类型）：

    ---
    name: quality_expert
    type: local_l3           # 可省略，默认值
    description: 制造业产品质量分析专家...
    tools:                   # 可选，不填则继承主 Agent 全部工具
      - web_search
      - web_fetch
    max_iterations: 15
    timeout_ms: 180000
    max_depth: 2
    ---
    # 系统提示词正文
    你是一位专注于...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()


@dataclass
class SubAgentConfig:
    """
    从 agent.md 解析出的 SubAgent 完整配置。

    v3 简化：仅支持 local_l3 类型（L3 ReAct 循环）。
    tool_filter：None=继承全部，有值=物理白名单（RestrictedToolRegistry）。
    """
    name: str
    description: str
    agent_dir: Path
    type: str = "local_l3"
    system_prompt: str = ""              # agent.md body
    tool_filter: list[str] | None = None # None=继承全部
    max_iterations: int = 15
    timeout_ms: int = 180_000
    max_depth: int = 10


def load_agent_from_dir(agent_dir: Path) -> SubAgentConfig | None:
    """
    从单个 Agent 目录加载 SubAgentConfig。

    目录结构：
        agent_dir/
        ├── agent.md    ← 必须存在（frontmatter + system prompt body）
        └── docs/       ← 可选参考文档

    Returns:
        SubAgentConfig 或 None（目录不合法时）
    """
    agent_md_path = agent_dir / "agent.md"
    if not agent_md_path.exists():
        log.debug("跳过非 Agent 目录（缺少 agent.md）", dir=str(agent_dir))
        return None

    raw = agent_md_path.read_text(encoding="utf-8")

    # 分割 frontmatter 和 body
    if not raw.startswith("---"):
        log.warning("agent.md 缺少 YAML frontmatter（---）", path=str(agent_md_path))
        return None

    parts = raw.split("---", 2)
    if len(parts) < 3:
        log.warning("agent.md frontmatter 格式错误，缺少结束 ---", path=str(agent_md_path))
        return None

    frontmatter_raw = parts[1]
    body = parts[2].strip()

    try:
        fm = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as e:
        log.error("agent.md frontmatter YAML 解析失败", path=str(agent_md_path), error=str(e))
        return None

    name = fm.get("name")
    description = fm.get("description")
    if not name or not description:
        log.error(
            "agent.md 缺少必填字段 name/description",
            path=str(agent_md_path),
            found_fields=list(fm.keys()),
        )
        return None

    if not body:
        log.warning("agent.md body（系统提示词）为空", path=str(agent_md_path))

    agent_type = str(fm.get("type", "local_l3"))
    # v3 简化：仅支持 local_l3，local_code / http 已从 subagent_call 移除
    if agent_type != "local_l3":
        log.error(
            "agent.md type 字段无效，当前仅支持 local_l3",
            path=str(agent_md_path),
            type=agent_type,
        )
        return None

    # local_l3 专用字段
    tools_raw = fm.get("tools")
    tool_filter: list[str] | None = (
        [str(t) for t in tools_raw]
        if tools_raw and isinstance(tools_raw, list)
        else None
    )

    config = SubAgentConfig(
        name=str(name),
        description=str(description),
        agent_dir=agent_dir,
        type=agent_type,
        system_prompt=body,
        tool_filter=tool_filter,
        max_iterations=int(fm.get("max_iterations", 15)),
        timeout_ms=int(fm.get("timeout_ms", 180_000)),
        max_depth=int(fm.get("max_depth", 2)),
    )

    log.info(
        "SubAgent 已加载",
        name=name,
        type=agent_type,
        max_depth=config.max_depth,
    )
    return config


def scan_agents_dir(agents_root: Path) -> list[SubAgentConfig]:
    """
    扫描 builtin_agents 目录，加载所有合法的 SubAgentConfig。

    每个包含 agent.md 的子目录都被视为一个 SubAgent。
    """
    if not agents_root.exists():
        log.debug("SubAgents 根目录不存在，跳过", path=str(agents_root))
        return []

    agents: list[SubAgentConfig] = []
    for entry in sorted(agents_root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("_"):
            agent = load_agent_from_dir(entry)
            if agent:
                agents.append(agent)

    log.info("SubAgents 扫描完成", count=len(agents), root=str(agents_root))
    return agents
