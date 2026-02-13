"""
内置工具集：自动注册所有内置工具到 ToolRegistry

使用方式：
    from app.tools.builtin_tools import create_builtin_registry
    registry = create_builtin_registry()
"""

from app.config import get_settings
from app.tools.builtin_tools.web_search import WebSearchTool
from app.tools.builtin_tools.web_fetch import WebFetchTool
from app.tools.registry import ToolRegistry


def create_builtin_registry() -> ToolRegistry:
    """创建并注册所有内置工具的 Registry 实例"""
    settings = get_settings()
    registry = ToolRegistry()

    registry.register(WebSearchTool(api_key=settings.BOCHA_API_KEY))
    registry.register(WebFetchTool())

    return registry
