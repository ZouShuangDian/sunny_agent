"""
内置模式注册表

每个模式一个独立文件（prompt + 工具配置），在此汇总为 BUILTIN_MODES。
新增模式只需：1) 在本目录新建 xxx.py  2) 在此注册。
"""

from app.execution.mode_context import ModeConfig
from app.modes.deep_research import CONFIG as deep_research_config

# 模式名称 → ModeConfig 映射
# chat.py 通过 /mode:{name} 检索，name 必须与此处 key 完全匹配
BUILTIN_MODES: dict[str, ModeConfig] = {
    "deep-research": deep_research_config,
}
