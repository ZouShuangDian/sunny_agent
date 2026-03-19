"""
公司 MCP 管理平台接口封装

调用公司平台获取当前用户可见的 MCP 连接器列表。
平台返回的连接器信息会追加自定义 MCP 服务（如测试用的 12306）。
"""

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger()
_settings = get_settings()

# 开发环境追加的测试 MCP 服务（生产环境不暴露）
_CUSTOM_MCP_SERVICES: list[dict] = [
    {
        "id": 99999,
        "code": "custom_12306",
        "name": "12306 火车票查询",
        "classify": "CUSTOM_12306",
        "classifyName": "12306",
        "txxy": "STREAMABLE_HTTP",
        "remark": "12306 火车票查询服务（Streamable HTTP，仅开发环境）",
        "scope": "PUBLIC",
        "urlList": [
            {
                "env": "2",
                "url": "https://mcp.api-inference.modelscope.net/1d4ddc316dde48/mcp",
                "authType": "NONE",
                "apiState": "1",
            }
        ],
    },
] if _settings.ENV == "development" else []


async def fetch_available_connectors(usernumb: str) -> list[dict]:
    """从公司 MCP 平台获取当前用户可用的连接器列表

    Args:
        usernumb: 用户工号

    Returns:
        连接器列表（平台返回 + 自定义追加）

    Raises:
        httpx.HTTPError: 平台接口请求失败
    """
    platform_url = _settings.MCP_PLATFORM_URL
    platform_token = _settings.MCP_PLATFORM_TOKEN

    infos: list[dict] = []

    # 调公司平台（如果配置了）
    if platform_url and platform_token:
        try:
            async with httpx.AsyncClient(timeout=_settings.MCP_PLATFORM_TIMEOUT) as client:
                resp = await client.post(
                    f"{platform_url}/apis-service/mcp/list-servies",
                    headers={
                        "mcp-token": platform_token,
                        "Content-Type": "application/json",
                    },
                    json={
                        "workCode": usernumb,
                        "envs": [1, 2],
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("success"):
                    infos = data.get("result", {}).get("infos", [])
                    log.info("MCP 平台返回连接器列表", count=len(infos), usernumb=usernumb)
                else:
                    log.warning("MCP 平台返回失败", message=data.get("message"), usernumb=usernumb)

        except Exception as e:
            log.error("MCP 平台请求失败", error=str(e), usernumb=usernumb)
            # 平台不可用时不阻断，继续返回自定义服务

    # 追加自定义 MCP 服务
    infos.extend(_CUSTOM_MCP_SERVICES)

    return infos


def generate_tool_prefix(classify: str | None, code: str | None, existing_prefixes: set[str]) -> str:
    """根据 classify 生成工具名前缀

    规则：
    1. classify（如 MCP_GX_FOUR）→ 去 MCP_ 前缀 → 转小写 → gx_four
    2. classify 为空则取 code 前 8 位转小写
    3. 与已有前缀冲突则追加数字后缀

    Args:
        classify: 连接器分类
        code: 连接器编码
        existing_prefixes: 当前用户已有的前缀集合

    Returns:
        生成的前缀
    """
    # 生成基础前缀
    if classify:
        prefix = classify.lower()
        if prefix.startswith("mcp_"):
            prefix = prefix[4:]
    elif code:
        prefix = code[:8].lower()
    else:
        prefix = "mcp"

    # 去重
    if prefix not in existing_prefixes:
        return prefix

    for i in range(2, 100):
        candidate = f"{prefix}_{i}"
        if candidate not in existing_prefixes:
            return candidate

    return f"{prefix}_{id(classify)}"  # 极端情况兜底
