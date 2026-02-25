"""
工具注册中心：统一管理所有工具的注册、Schema 获取和执行分发

Week 7 增强：
- get_schemas_by_tier：按层级过滤工具 schema（L1/L3）
- execute 增加 asyncio.wait_for 超时保护（见 W2 超时嵌套规范）
"""

import asyncio
import json

import structlog

from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


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

    def get_all_schemas(self) -> list[dict]:
        """获取所有已注册工具的 OpenAI function calling schema"""
        return [tool.schema() for tool in self._tools.values()]

    def get_schemas(self, allowed_tools: list[str]) -> list[dict]:
        """获取指定工具的 schema（按需过滤）"""
        return [
            self._tools[name].schema()
            for name in allowed_tools
            if name in self._tools
        ]

    def get_schemas_by_tier(self, tier: str) -> list[dict]:
        """按层级过滤工具 schema（L1 / L3）"""
        return [
            tool.schema()
            for tool in self._tools.values()
            if tier in tool.tier
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
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.fail(f"未知工具: {name}").to_json()

        try:
            result = await asyncio.wait_for(
                tool.execute(arguments),
                timeout=tool.timeout_ms / 1000,
            )
            return result.to_json()
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
