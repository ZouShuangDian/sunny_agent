"""
MCP Client — 通过 Streamable HTTP 与 MCP Server 交互

职责：
- tools/list：获取 MCP Server 提供的工具列表
- tools/call：调用 MCP Server 的工具

使用模块级共享 httpx.AsyncClient 复用 TCP 连接。
应用关闭时调用 close_mcp_client() 清理。
"""

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger()
_settings = get_settings()

# 模块级共享 httpx 客户端（复用 TCP 连接，类似 redis_client 模式）
_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(_settings.MCP_SERVER_TIMEOUT),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


class MCPError(Exception):
    """MCP Server 返回的 JSON-RPC 错误"""

    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"MCP 错误 [{code}]: {message}")


def _check_jsonrpc_error(body: dict) -> None:
    """检查 JSON-RPC 错误响应，有错误则抛异常"""
    if "error" in body:
        error = body["error"]
        raise MCPError(
            code=error.get("code", -1),
            message=error.get("message", "未知错误"),
        )


class MCPClient:
    """MCP Client — 通过 Streamable HTTP 连接 MCP Server"""

    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url

    async def list_tools(self) -> list[dict]:
        """调用 tools/list 获取工具列表

        Returns:
            工具列表，每个元素包含 name、description、inputSchema
        """
        resp = await _http_client.post(self.mcp_url, json={
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })
        resp.raise_for_status()
        body = resp.json()
        _check_jsonrpc_error(body)
        return body.get("result", {}).get("tools", [])

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 tools/call 执行工具

        Args:
            tool_name: 工具名称（MCP Server 原始名称，不含前缀）
            arguments: 工具入参

        Returns:
            MCP 工具返回结果（含 content 数组）
        """
        resp = await _http_client.post(
            self.mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "id": 1,
                "params": {"name": tool_name, "arguments": arguments},
            },
            timeout=_settings.MCP_TOOL_CALL_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        _check_jsonrpc_error(body)
        return body.get("result", {})


async def close_mcp_client() -> None:
    """应用关闭时清理共享 httpx 连接"""
    await _http_client.aclose()
