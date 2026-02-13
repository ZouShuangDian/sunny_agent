"""
网页搜索工具：调用博查搜索 API，返回搜索结果摘要
"""

import httpx
import structlog
from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# 博查搜索 API 地址
_BOCHA_API_URL = "https://api.bochaai.com/v1/web-search"


class WebSearchParams(BaseModel):
    """web_search 工具参数"""

    query: str = Field(description="搜索关键词")
    count: int = Field(default=5, description="返回结果数量，默认 5")


class WebSearchTool(BaseTool):
    """搜索互联网信息，返回相关网页摘要和链接"""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "搜索互联网信息，返回相关网页摘要和链接。适用于查询实时信息、新闻、市场数据等。"

    @property
    def params_model(self) -> type[BaseModel]:
        return WebSearchParams

    async def execute(self, args: dict) -> ToolResult:
        query = args.get("query", "")
        count = args.get("count", 5)

        if not self._api_key:
            log.warning("BOCHA_API_KEY 未配置，使用 mock 数据")
            return self._mock_response(query)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _BOCHA_API_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "summary": True,
                        "count": count,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # 提取摘要和结果
            results = []
            web_pages = data.get("data", {}).get("webPages", {}).get("value", [])
            for item in web_pages[:count]:
                results.append({
                    "title": item.get("name", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", ""),
                })

            summary = data.get("data", {}).get("summary", "")

            return ToolResult.success(
                query=query,
                summary=summary,
                results=results,
            )

        except Exception as e:
            log.warning("搜索 API 调用失败，降级为 mock", error=str(e))
            return self._mock_response(query)

    @staticmethod
    def _mock_response(query: str) -> ToolResult:
        """搜索 mock 响应"""
        return ToolResult.success(
            query=query,
            summary=f"[Mock] 关于「{query}」的搜索结果暂不可用，请稍后重试。",
            results=[
                {
                    "title": f"[Mock] {query} 相关信息",
                    "url": "https://example.com",
                    "snippet": f"这是关于「{query}」的模拟搜索结果。真实搜索需要配置 BOCHA_API_KEY。",
                }
            ],
        )
