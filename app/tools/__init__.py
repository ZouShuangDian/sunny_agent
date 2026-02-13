"""
工具系统：BaseTool 抽象基类 + ToolRegistry 注册中心 + 内置工具集

公共模块，供 L1/L3 等执行层共用。
"""

from app.tools.base import BaseTool, ToolResult
from app.tools.registry import ToolRegistry

__all__ = ["BaseTool", "ToolResult", "ToolRegistry"]
