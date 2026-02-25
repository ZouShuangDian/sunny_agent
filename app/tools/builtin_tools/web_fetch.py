"""
网页抓取工具：获取指定 URL 的页面正文内容

典型场景：web_search 返回链接列表后，LLM 用 web_fetch 读取具体页面内容。
使用 httpx 抓取 + 简单 HTML → 纯文本转换，截断到 max_length 防止 token 爆炸。
"""

import re

import httpx
import structlog
from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# 默认最大返回字符数（防止页面内容过长撑爆 LLM 上下文）
_DEFAULT_MAX_LENGTH = 4000


class WebFetchParams(BaseModel):
    """web_fetch 工具参数"""

    url: str = Field(description="要抓取的网页 URL")
    max_length: int = Field(
        default=_DEFAULT_MAX_LENGTH,
        description="返回正文的最大字符数，默认 4000",
    )


def _html_to_text(html: str) -> str:
    """简单的 HTML → 纯文本转换（去标签 + 去多余空白）"""
    # 移除 script / style 标签及其内容
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # 移除所有 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # HTML 实体转义
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    # 压缩连续空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


class WebFetchTool(BaseTool):
    """抓取指定 URL 的网页正文内容，返回纯文本"""

    def __init__(self, timeout: int = 15):
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "抓取指定 URL 的网页内容，返回纯文本。"
            "适用于读取 web_search 返回的链接详情、获取文章全文等。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return WebFetchParams

    @property
    def timeout_ms(self) -> int:
        """Registry 兜底超时 20s > 内部 httpx timeout 15s（W2 规范）"""
        return 20_000

    async def execute(self, args: dict) -> ToolResult:
        url = args.get("url", "")
        max_length = args.get("max_length", _DEFAULT_MAX_LENGTH)

        if not url:
            return ToolResult.fail("缺少 url 参数")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AgentSunny/1.0)",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")

            # 非 HTML 内容直接返回前 N 个字符
            if "text/html" not in content_type:
                raw_text = resp.text[:max_length]
                return ToolResult.success(
                    url=url,
                    content=raw_text,
                    content_type=content_type,
                    truncated=len(resp.text) > max_length,
                )

            # HTML → 纯文本
            text = _html_to_text(resp.text)
            truncated = len(text) > max_length
            text = text[:max_length]

            return ToolResult.success(
                url=url,
                content=text,
                content_type="text/plain",
                truncated=truncated,
            )

        except httpx.TimeoutException:
            return ToolResult.fail(f"请求超时（{self._timeout}s）: {url}")
        except httpx.HTTPStatusError as e:
            return ToolResult.fail(f"HTTP {e.response.status_code}: {url}")
        except Exception as e:
            log.warning("网页抓取失败", url=url, error=str(e))
            return ToolResult.fail(f"抓取失败: {e}")
