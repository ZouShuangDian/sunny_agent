"""
工具注册中心：统一管理所有工具的注册、Schema 获取和执行分发

功能：
- get_all_schemas：获取全量工具 schema
- execute 增加 asyncio.wait_for 超时保护（见 W2 超时嵌套规范）
"""

import asyncio
import json
import time

import structlog

from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# 工具入参/出参日志截断长度（避免刷屏）
_LOG_TRUNCATE = 300


def _truncate(s: str) -> str:
    """截断长字符串，仅用于日志输出"""
    s = str(s)
    return s if len(s) <= _LOG_TRUNCATE else s[:_LOG_TRUNCATE] + f"…[共{len(s)}字]"


class ToolRegistry:
    """工具注册中心"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例"""
        self._tools[tool.name] = tool
        log.debug("工具已注册", tool=tool.name, tier=tool.tier, timeout_ms=tool.timeout_ms)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_all_schemas(self, *, include_mode_only: bool = False) -> list[dict]:
        """获取已注册工具的 OpenAI function calling schema

        Args:
            include_mode_only: 是否包含 mode_only=True 的工具。
                普通对话传 False（默认），/mode:xxx 路径传 True。
        """
        return [
            tool.schema() for tool in self._tools.values()
            if include_mode_only or not tool.mode_only
        ]

    def get_schemas(self, allowed_tools: list[str]) -> list[dict]:
        """获取指定工具的 schema（按需过滤）"""
        return [
            self._tools[name].schema()
            for name in allowed_tools
            if name in self._tools
        ]

    async def execute(self, name: str, arguments: dict) -> str:
        """
        执行工具，返回 JSON 字符串结果。

        始终返回标准化的 ToolResult JSON：
        - 成功: {"status": "success", ...data}
        - 失败: {"status": "error", "error": "..."}

        超时保护（W2 规范）：
        - Registry 层使用 BaseTool.timeout_ms 作为兜底超时
        - 工具内部应自行管理更短的超时，内部超时触发时返回具体错误信息
        - 正常情况下 Registry 层超时不应被触发
        """
        # MCP 连接器工具：name 包含 __ 前缀（如 gx_four__query_status）
        if "__" in name:
            return await self._execute_mcp_tool(name, arguments)

        tool = self._tools.get(name)
        if not tool:
            return ToolResult.fail(f"未知工具: {name}").to_json()

        log.debug(
            "工具调用",
            tool=name,
            args=_truncate(json.dumps(arguments, ensure_ascii=False)),
        )

        _start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                tool.execute(arguments),
                timeout=tool.timeout_ms / 1000,
            )
            result_json = result.to_json()
            log.debug(
                "工具返回",
                tool=name,
                duration_ms=int((time.monotonic() - _start) * 1000),
                result=_truncate(result_json),
            )
            return result_json
        except asyncio.TimeoutError:
            log.warning("工具执行超时（Registry 兜底）", tool=name, timeout_ms=tool.timeout_ms)
            return ToolResult.fail(f"工具 {name} 执行超时（{tool.timeout_ms}ms）").to_json()
        except asyncio.CancelledError:
            # 系统级中断信号，必须向上传播，不可吞掉
            log.warning("工具执行被取消", tool=name)
            raise
        except Exception as e:
            log.error("工具执行异常", tool=name, error=str(e), exc_info=True)
            return ToolResult.fail(f"工具执行异常: {e}").to_json()

    async def _execute_mcp_tool(self, full_name: str, arguments: dict) -> str:
        """执行 MCP 连接器工具：拆分前缀 → 查 DB 找 mcp_url → MCPClient.call_tool"""
        from app.db.engine import async_session
        from app.db.models.connector import UserConnector
        from app.execution.user_context import get_user_id
        from app.connector.client import MCPClient
        from sqlalchemy import select

        prefix, _, tool_name = full_name.partition("__")

        usernumb = get_user_id()
        if not usernumb:
            return ToolResult.fail("无法获取用户信息").to_json()

        # 查 DB 找连接器的 mcp_url
        async with async_session() as db:
            result = await db.execute(
                select(UserConnector.mcp_url).where(
                    UserConnector.usernumb == usernumb,
                    UserConnector.tool_prefix == prefix,
                    UserConnector.is_enabled == True,  # noqa: E712
                )
            )
            row = result.first()

        if not row:
            return ToolResult.fail(f"连接器 {prefix} 未找到或已关闭").to_json()

        mcp_url = row[0]
        client = MCPClient(mcp_url=mcp_url)

        _start = time.monotonic()
        try:
            mcp_result = await asyncio.wait_for(
                client.call_tool(tool_name, arguments),
                timeout=60,
            )
            # MCP 返回格式：{"content": [{"type": "text", "text": "..."}]}
            content = mcp_result.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            result_text = "\n".join(texts) if texts else json.dumps(mcp_result, ensure_ascii=False)

            duration_ms = int((time.monotonic() - _start) * 1000)
            log.info("MCP 工具执行完成", tool=full_name, duration_ms=duration_ms)

            return ToolResult.success(result=result_text).to_json()

        except asyncio.TimeoutError:
            log.warning("MCP 工具超时", tool=full_name)
            return ToolResult.fail(f"MCP 工具 {tool_name} 执行超时").to_json()
        except Exception as e:
            log.error("MCP 工具异常", tool=full_name, error=str(e))
            return ToolResult.fail(f"MCP 工具执行异常: {e}").to_json()

    @property
    def tool_names(self) -> list[str]:
        """获取所有已注册工具名称"""
        return list(self._tools.keys())

    @property
    def tool_count(self) -> int:
        """已注册工具总数"""
        return len(self._tools)


class RestrictedToolRegistry(ToolRegistry):
    """
    受限工具注册中心：在 execute() 层做物理白名单拦截。

    用于 SubAgent 的工具隔离：不仅隐藏 schema，执行时也拒绝白名单外的调用。
    即使 LLM 猜出工具名，也会在 execute() 处被拦截，返回 PermissionError。

    注意：直接引用父级 _tools dict（不复制），schema 过滤和执行拦截保持一致。
    """

    def __init__(self, parent: ToolRegistry, allowed_tools: list[str]) -> None:
        super().__init__()
        # 只把白名单内的工具注册进来，schema 层和执行层同时受限
        for name in allowed_tools:
            if parent.has_tool(name):
                self._tools[name] = parent._tools[name]
            else:
                log.warning("SubAgent 工具白名单中包含未知工具，忽略", tool=name)

    async def execute(self, name: str, arguments: dict) -> str:
        """物理拦截：白名单外的工具名直接返回 PermissionError，不调用父类。"""
        if name not in self._tools:
            log.warning("SubAgent 工具调用被拦截（不在白名单）", tool=name)
            return ToolResult.fail(
                f"PermissionError: 工具 '{name}' 不在此 SubAgent 的授权工具列表中"
            ).to_json()
        return await super().execute(name, arguments)
