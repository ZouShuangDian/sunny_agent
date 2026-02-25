"""
SubAgent 加载器：从 agent.md 解析 SubAgentConfig

agent.md 支持三种类型（type 字段）：

1. local_l3（默认）：系统提示词 + 工具白名单，走标准 L3 ReAct 循环
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

2. local_code：指向 Python 实现类，适用于无法用 system prompt + tools 满足的复杂场景
    ---
    name: supply_chain_agent
    type: local_code
    description: 供应链深度分析，内含多阶段数据处理流程
    entry: app.subagents.builtin_agents.supply_chain.executor::SupplyChainExecutor
    timeout_ms: 120000
    max_depth: 2
    ---
    （body 可留空，实际逻辑在 executor 类中）

3. http：调用外部 Agent HTTP 接口，对方内部实现无关紧要
    ---
    name: external_quality_agent
    type: http
    description: 外部质量分析服务（由质量团队维护）
    endpoint: https://internal.company.com/agents/quality
    timeout_ms: 60000
    max_depth: 2
    ---
    （body 可留空）
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

    type 决定执行后端：
      - "local_l3"（默认）：L3 ReAct 循环，system_prompt + tool_filter 生效
      - "local_code"：Python 实现类，entry 字段指定，system_prompt / tool_filter 无效
      - "http"：外部 HTTP 接口，endpoint 字段指定，system_prompt / tool_filter 无效

    tool_filter：仅 local_l3 有效；None=继承全部，有值=物理白名单（RestrictedToolRegistry）
    entry：仅 local_code 有效；格式 "module.path::ClassName"
    endpoint：仅 http 有效；外部 Agent HTTP POST 地址
    """
    name: str
    description: str
    agent_dir: Path
    type: str = "local_l3"               # "local_l3" | "local_code" | "http"
    system_prompt: str = ""              # local_l3 专用，agent.md body
    tool_filter: list[str] | None = None # local_l3 专用，None=继承全部
    entry: str = ""                      # local_code 专用，"module.path::ClassName"
    endpoint: str = ""                   # http 专用，外部接口地址
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
    if agent_type not in ("local_l3", "local_code", "http"):
        log.error(
            "agent.md type 字段无效，支持：local_l3 / local_code / http",
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

    # local_code 专用字段
    entry = str(fm.get("entry", ""))
    if agent_type == "local_code" and not entry:
        log.error("local_code 类型必须指定 entry 字段", path=str(agent_md_path))
        return None

    # http 专用字段
    endpoint = str(fm.get("endpoint", ""))
    if agent_type == "http" and not endpoint:
        log.error("http 类型必须指定 endpoint 字段", path=str(agent_md_path))
        return None

    config = SubAgentConfig(
        name=str(name),
        description=str(description),
        agent_dir=agent_dir,
        type=agent_type,
        system_prompt=body,
        tool_filter=tool_filter,
        entry=entry,
        endpoint=endpoint,
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
