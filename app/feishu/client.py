"""
Feishu API 客户端
处理Token管理、消息发送、流式卡片等
"""

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import structlog

from app.cache.redis_client import FeishuRedisKeys, redis_client
from app.config import get_settings

settings = get_settings()
logger = structlog.get_logger()

# Token TTL配置
FEISHU_TOKEN_TTL = 7000  # 飞书token有效期7200秒，预留200秒缓冲


class FeishuError(Exception):
    """飞书API错误"""
    
    def __init__(self, message: str, code: int = None, response: dict = None):
        self.message = message
        self.code = code
        self.response = response
        super().__init__(message)


class FeishuRateLimitError(FeishuError):
    """飞书限流错误"""
    pass


class FeishuClient:
    """飞书API客户端"""
    
    BASE_URL = "https://open.feishu.cn/open-apis"
    
    def __init__(self, app_id: str = None, app_secret: str = None):
        """
        初始化 FeishuClient
        
        注意：app_id 必须提供，app_secret 必须从数据库获取
        建议使用 create() 工厂方法创建实例
        """
        if not app_id:
            raise FeishuError("app_id 不能为空，请提供飞书应用ID")
        
        self.app_id = app_id
        self._app_secret = app_secret  # 可能为 None，延迟加载
        self._initialized = app_secret is not None
        
        self.http_client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
        )
        
        self._access_token = None
        self._token_expires_at = 0
    
    async def _ensure_initialized(self):
        """确保客户端已初始化（异步加载 app_secret）"""
        if not self._initialized:
            # 从数据库获取配置（唯一配置来源）
            app_secret = None
            try:
                from sqlalchemy import select
                from app.db.models.feishu import FeishuAccessConfig
                from app.db.engine import async_session
                
                async with async_session() as db:
                    stmt = select(FeishuAccessConfig).where(
                        FeishuAccessConfig.app_id == self.app_id,
                        FeishuAccessConfig.is_active == True
                    )
                    result = await db.execute(stmt)
                    config = result.scalar_one_or_none()
                    if config:
                        app_secret = config.app_secret
                        logger.info("Got app_secret from database", 
                                  app_id=self.app_id)
            except Exception as e:
                logger.error("Failed to get app_secret from database",
                             app_id=self.app_id, 
                             error=str(e))
            
            if not app_secret:
                raise FeishuError(
                    f"未找到 app_id {self.app_id} 对应的 app_secret，"
                    f"请在数据库 feishu_access_config 表中添加配置"
                )
            
            self._app_secret = app_secret
            self._initialized = True
    
    @property
    def app_secret(self) -> str:
        """获取 app_secret（确保已初始化）"""
        if not self._initialized:
            raise FeishuError(f"Client not initialized. Call _ensure_initialized() first or use create() factory method.")
        return self._app_secret
    
    @classmethod
    async def create(cls, app_id: str = None, app_secret: str = None) -> "FeishuClient":
        """
        工厂方法：异步创建 FeishuClient 实例
        
        用法：
            client = await FeishuClient.create(app_id="cli_xxx")
        """
        client = cls(app_id=app_id, app_secret=app_secret)
        await client._ensure_initialized()
        return client
    
    async def _get_access_token(self) -> str:
        """获取access_token，优先从Redis缓存读取"""
        # 确保已初始化（获取 app_secret）
        await self._ensure_initialized()
        
        cache_key = FeishuRedisKeys.token(self.app_id)
        
        # 尝试从Redis读取
        cached_token = await redis_client.get(cache_key)
        if cached_token:
            logger.debug("Using cached Feishu token", app_id=self.app_id)
            return cached_token
        
        # 调用API获取新token
        logger.info("Fetching new Feishu token", app_id=self.app_id)
        response = await self.http_client.post(
            "/auth/v3/app_access_token/internal",
            json={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            }
        )
        
        if response.status_code != 200:
            raise FeishuError(f"获取token失败: HTTP {response.status_code}")
        
        data = response.json()
        if data.get("code") != 0:
            raise FeishuError(
                f"获取token失败: {data.get('msg')}",
                code=data.get("code")
            )
        
        token = data.get("app_access_token")
        expire = data.get("expire", 7200)
        
        # 缓存到Redis
        await redis_client.setex(
            cache_key,
            min(expire - 200, FEISHU_TOKEN_TTL),
            token
        )
        
        return token
    
    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict = None,
        params: dict = None,
        headers: dict = None,
        retry_count: int = 0,
    ) -> dict:
        """发送API请求"""
        token = await self._get_access_token()
        
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if headers:
            request_headers.update(headers)
        
        try:
            response = await self.http_client.request(
                method=method,
                url=path,
                json=json_data,
                params=params,
                headers=request_headers,
            )
        except httpx.TimeoutException:
            if retry_count < 3:
                await asyncio.sleep(2 ** retry_count)
                return await self._request(method, path, json_data, params, headers, retry_count + 1)
            raise FeishuError("请求超时")
        except httpx.NetworkError:
            if retry_count < 3:
                await asyncio.sleep(2 ** retry_count)
                return await self._request(method, path, json_data, params, headers, retry_count + 1)
            raise FeishuError("网络错误")
        
        # 处理限流
        if response.status_code == 429:
            if retry_count < 3:
                retry_after = int(response.headers.get("Retry-After", 2 ** retry_count))
                logger.warning("Feishu API rate limited", retry_after=retry_after)
                await asyncio.sleep(retry_after)
                return await self._request(method, path, json_data, params, headers, retry_count + 1)
            raise FeishuRateLimitError("飞书API限流")
        
        if response.status_code >= 500:
            if retry_count < 3:
                await asyncio.sleep(2 ** retry_count)
                return await self._request(method, path, json_data, params, headers, retry_count + 1)
            raise FeishuError(f"飞书服务器错误: HTTP {response.status_code}")
        
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise FeishuError(f"解析响应失败: {response.text}")
        
        # 处理业务错误
        if data.get("code") != 0:
            error_code = data.get("code")
            error_msg = data.get("msg", "未知错误")
            
            # 特定的错误码处理
            if error_code == 99991663:  # token过期
                await redis_client.delete(FeishuRedisKeys.token(self.app_id))
                if retry_count < 1:
                    return await self._request(method, path, json_data, params, headers, retry_count + 1)
            
            raise FeishuError(f"API错误: {error_msg}", code=error_code, response=data)
        
        return data
    
    async def get_user_by_open_id(self, open_id: str) -> dict | None:
        """通过open_id获取用户信息（使用 GET 请求）"""
        try:
            response = await self._request(
                "GET",
                "/contact/v3/users/batch",
                params={
                    "user_ids": open_id,  # 逗号分隔的字符串
                    "user_id_type": "open_id",
                }
            )
            
            # batch 接口返回的是 items 列表
            items = response.get("data", {}).get("items", [])
            if items:
                user_info = items[0]
                # 转换为统一格式
                return {
                    "open_id": user_info.get("open_id"),
                    "union_id": user_info.get("union_id"),
                    "employee_no": user_info.get("employee_no"),
                    "name": user_info.get("name"),
                    "en_name": user_info.get("en_name"),
                    "email": user_info.get("email"),
                    "mobile": user_info.get("mobile"),
                }
            return None
        except FeishuError as e:
            logger.error("Failed to get user info", error=e.message)
            return None
    
    async def send_text_message(
        self, 
        receive_id: str, 
        text: str, 
        receive_id_type: str = "open_id"
    ) -> dict:
        """发送文本消息"""
        content = json.dumps({"text": text})
        
        return await self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json_data={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": content,
            }
        )
    
    async def send_post_message(
        self,
        receive_id: str,
        title: str,
        content: list,
        receive_id_type: str = "open_id",
    ) -> dict:
        """发送富文本消息"""
        post_content = {
            "zh_cn": {
                "title": title,
                "content": content,
            }
        }
        
        return await self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json_data={
                "receive_id": receive_id,
                "msg_type": "post",
                "content": json.dumps(post_content),
            }
        )
    
    async def create_streaming_card(
        self,
        receive_id: str,
        initial_content: str = "思考中...",
        receive_id_type: str = "open_id",
    ) -> dict:
        """创建流式卡片"""
        card_data = {
            "schema": "2.0",
            "config": {"streaming_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "AI助手"},
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "plain_text",
                            "content": initial_content,
                        },
                    }
                ]
            },
        }
        
        response = await self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json_data={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card_data),
            }
        )
        
        return response
    
    async def update_streaming_card(
        self,
        card_id: str,
        element_id: str,
        content: str,
    ) -> dict:
        """更新流式卡片内容"""
        return await self._request(
            "PUT",
            f"/cardkit/v1/cards/{card_id}/elements/{element_id}/content",
            json_data={
                "content": {
                    "tag": "plain_text",
                    "content": content,
                }
            }
        )
    
    async def close_streaming_card(
        self,
        card_id: str,
    ) -> dict:
        """关闭流式卡片更新"""
        return await self._request(
            "PATCH",
            f"/cardkit/v1/cards/{card_id}/settings",
            json_data={"streaming_mode": False}
        )
    
    async def download_media(
        self,
        file_key: str,
        message_id: str,
    ) -> bytes:
        """下载媒体文件"""
        token = await self._get_access_token()
        
        response = await self.http_client.get(
            f"/im/v1/messages/{message_id}/resources/{file_key}",
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "file"},
        )
        
        if response.status_code != 200:
            raise FeishuError(f"下载媒体文件失败: HTTP {response.status_code}")
        
        return response.content
    
    async def close(self):
        """关闭HTTP客户端"""
        await self.http_client.aclose()


# 全局客户端实例缓存（支持多应用）
# 格式: {app_id: FeishuClient}
_feishu_clients: dict[str, FeishuClient] = {}


async def get_feishu_client(app_id: str = None, db=None) -> FeishuClient:
    """获取FeishuClient
    
    支持多应用：根据 app_id 返回对应的客户端实例
    app_secret 必须从数据库 feishu_access_config 表中获取
    
    Args:
        app_id: 飞书应用ID，不传则报错
        db: 数据库会话，用于查询应用配置（优先使用，避免创建新session）
        
    Returns:
        FeishuClient 实例（按 app_id 缓存）
    """
    global _feishu_clients
    
    # app_id 必须提供
    if not app_id:
        raise FeishuError(
            "app_id 不能为空。请确保：\n"
            "1. 在数据库 feishu_access_config 表中添加了应用配置\n"
            "2. Webhook 消息中包含了 app_id\n"
            "3. 调用 API 时传入了正确的 app_id"
        )
    
    # 检查缓存
    if app_id not in _feishu_clients:
        logger.info("Creating new FeishuClient", app_id=app_id)
        
        # 如果提供了 db session，先尝试从中获取 app_secret
        app_secret = None
        if db:
            try:
                from sqlalchemy import select
                from app.db.models.feishu import FeishuAccessConfig
                
                stmt = select(FeishuAccessConfig).where(
                    FeishuAccessConfig.app_id == app_id,
                    FeishuAccessConfig.is_active == True
                )
                result = await db.execute(stmt)
                config = result.scalar_one_or_none()
                if config:
                    app_secret = config.app_secret
                    logger.info("Got app_secret from provided db session", app_id=app_id)
            except Exception as e:
                logger.warning("Failed to get app_secret from provided db", 
                             app_id=app_id, error=str(e))
        
        # 使用工厂方法创建并初始化客户端
        # 如果已获取 app_secret，直接传入；否则 create 会从数据库获取
        client = await FeishuClient.create(app_id=app_id, app_secret=app_secret)
        _feishu_clients[app_id] = client
    
    return _feishu_clients[app_id]


async def close_all_feishu_clients():
    """关闭所有 FeishuClient 实例"""
    global _feishu_clients
    for app_id, client in _feishu_clients.items():
        await client.close()
        logger.info("Closed FeishuClient", app_id=app_id)
    _feishu_clients.clear()
