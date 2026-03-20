"""
MCP Client — 基于官方 mcp SDK 的 Streamable HTTP 客户端

封装 mcp.ClientSession，对外提供 list_tools() 和 call_tool() 接口。
上游调用方（connectors.py、registry.py）只依赖返回的 dict 格式，不感知 SDK 细节。
"""

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import get_settings

log = structlog.get_logger()
_settings = get_settings()


class MCPError(Exception):
    """MCP Server 错误（SDK 异常的统一包装）"""

    def __init__(self, message: str):
        super().__init__(f"MCP 错误: {message}")


def _convert_tool(tool) -> dict:
    """将 SDK Tool 对象转为 dict（保持与上游一致的格式）"""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
    }


def _convert_content(content) -> dict:
    """将 SDK Content 对象转为 dict"""
    return {
        "type": getattr(content, "type", "text"),
        "text": getattr(content, "text", str(content)),
    }


class MCPClient:
    """MCP Client — 通过官方 SDK 连接 MCP Server（Streamable HTTP）"""

    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url

    async def list_tools(self) -> list[dict]:
        """获取工具列表

        Returns:
            [{"name": "...", "description": "...", "inputSchema": {...}}, ...]
        """
        try:
            async with streamablehttp_client(url=self.mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    tools = [_convert_tool(t) for t in result.tools]
                    log.debug("MCP tools/list 完成", url=self.mcp_url, count=len(tools))
                    return tools
        except Exception as e:
            log.error("MCP tools/list 失败", url=self.mcp_url, error=str(e))
            raise MCPError(str(e)) from e

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用工具

        Args:
            tool_name: 工具名称（MCP Server 原始名称，不含前缀）
            arguments: 工具入参

        Returns:
            {"content": [{"type": "text", "text": "..."}]}
        """
        try:
            async with streamablehttp_client(url=self.mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return {
                        "content": [_convert_content(c) for c in result.content],
                    }
        except Exception as e:
            log.error("MCP tools/call 失败", url=self.mcp_url, tool=tool_name, error=str(e))
            raise MCPError(str(e)) from e
