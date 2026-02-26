"""
网页抓取工具：获取指定 URL 的页面正文内容

典型场景：web_search 返回链接列表后，LLM 用 web_fetch 读取具体页面内容。
使用 httpx 抓取 + HTML → 纯文本转换，截断到 max_length 防止 token 爆炸。

HTML 提取策略（三步降级）：
1. 移除噪声节点（nav/header/footer/aside/script/style/noscript）及其全部内容
2. 尝试提取语义主体（<article> 或 <main>）作为正文候选
3. 若无语义主体，使用已清洗的全文作为 fallback

编码处理：
- 优先使用 HTTP 响应头 charset
- 次选 HTML <meta> 标签声明的 charset（处理 GBK/GB2312 等中文财经网站）
- 最终 fallback 到 UTF-8（错误字符用 ? 替换，不抛异常）
"""

import re

import httpx
import structlog
from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# 默认最大返回字符数（防止页面内容过长撑爆 LLM 上下文）
_DEFAULT_MAX_LENGTH = 4000

# 噪声节点：连同其全部子内容一起剔除（导航、页眉、页脚、广告侧栏等）
_NOISE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript", "menu")

# 语义主体标签：按优先级依次尝试提取
_CONTENT_TAGS = ("article", "main")


def _decode_html(raw_bytes: bytes, header_charset: str | None = None) -> str:
    """
    正确解码 HTML 字节流，处理 GBK/GB2312 等非 UTF-8 中文页面。

    解码优先级：
    1. HTTP 响应头中的 charset（由调用方传入）
    2. HTML <meta> 标签声明的 charset
    3. UTF-8（fallback，错误字符用 ? 替换）
    """
    # 用 latin-1 安全解码前 2048 字节，搜索 meta charset 声明
    # latin-1 是单字节映射，任意字节序列都不会报错
    head = raw_bytes[:2048].decode("latin-1")
    meta_charset: str | None = None
    m = re.search(
        r'<meta[^>]+(?:charset=["\']?|content=["\'][^"\']*charset=)([a-zA-Z0-9_-]+)',
        head,
        flags=re.IGNORECASE,
    )
    if m:
        meta_charset = m.group(1)

    # 按优先级逐一尝试解码
    for charset in filter(None, [header_charset, meta_charset, "utf-8"]):
        try:
            return raw_bytes.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue

    # 最终兜底：UTF-8 + 替换不可解码字符
    return raw_bytes.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    """
    HTML → 纯文本：优先提取语义主体，过滤导航/页眉/页脚噪声。

    策略：
    1. 移除噪声节点（含内部内容）
    2. 尝试提取 <article> 或 <main> 作为正文候选
    3. 若无语义主体，使用噪声已剔除的全文
    4. 去除剩余 HTML 标签、解码常见实体、压缩空白
    """
    # 1. 移除噪声节点（含其全部子内容）
    for tag in _NOISE_TAGS:
        html = re.sub(
            rf"<{tag}(?:\s[^>]*)?>.*?</{tag}>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

    # 2. 尝试提取语义主体（按优先级）
    body = html
    for tag in _CONTENT_TAGS:
        m = re.search(
            rf"<{tag}(?:\s[^>]*)?>(.+?)</{tag}>",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if m:
            body = m.group(1)
            break

    # 3. 去除所有剩余 HTML 标签
    text = re.sub(r"<[^>]+>", " ", body)

    # 4. 常见 HTML 实体解码
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )

    # 5. 压缩连续空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


class WebFetchParams(BaseModel):
    """web_fetch 工具参数"""

    url: str = Field(description="要抓取的网页 URL")
    max_length: int = Field(
        default=_DEFAULT_MAX_LENGTH,
        description="返回正文的最大字符数，默认 4000",
    )


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

            # 从响应头提取 charset（供 _decode_html 优先使用）
            header_charset: str | None = None
            m = re.search(r"charset=([a-zA-Z0-9_-]+)", content_type, re.IGNORECASE)
            if m:
                header_charset = m.group(1)

            # 非 HTML 内容直接返回前 N 个字符
            if "text/html" not in content_type:
                decoded = _decode_html(resp.content, header_charset)
                return ToolResult.success(
                    url=url,
                    content=decoded[:max_length],
                    content_type=content_type,
                    truncated=len(decoded) > max_length,
                )

            # 正确解码（含 GBK/GB2312 等中文编码）再提取正文
            html_content = _decode_html(resp.content, header_charset)
            text = _html_to_text(html_content)
            truncated = len(text) > max_length
            text = text[:max_length]

            # 内容过短（< 200 字）通常意味着页面为 JS 动态渲染，静态抓取无法获取正文
            if len(text) < 200:
                log.warning("web_fetch 正文内容过短，疑似 JS 渲染页面", url=url, length=len(text))
                return ToolResult.success(
                    url=url,
                    content=text,
                    content_type="text/plain",
                    truncated=truncated,
                    warning="页面内容极少，可能是 JS 动态渲染页面，静态抓取无法获取完整正文",
                )

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
