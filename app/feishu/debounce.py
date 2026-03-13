"""
消息防抖模块 (Debounce)
实现双阶段防抖策略：Time Debounce + No-Text Debounce
"""

import asyncio
import importlib.util
import json
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import structlog

from app.cache.redis_client import FeishuRedisKeys, redis_client
from app.config import get_settings

settings = get_settings()
logger = structlog.get_logger()



# 默认配置
DEFAULT_DEBOUNCE_WAIT_SECONDS = 2.0
DEFAULT_NO_TEXT_ENABLED = True
DEFAULT_NO_TEXT_MAX_WAIT_SECONDS = 3.0
DEFAULT_MAX_BATCH_SIZE = 10
DEFAULT_MAX_BUFFER_SIZE = 100


class DebounceManager:
    """消息防抖管理器"""
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.debounce_wait_seconds = self.config.get(
            "debounce_wait_seconds", DEFAULT_DEBOUNCE_WAIT_SECONDS
        )
        self.no_text_config = self.config.get("no_text_debounce", {})
        self.no_text_enabled = self.no_text_config.get(
            "enabled", DEFAULT_NO_TEXT_ENABLED
        )
        self.no_text_max_wait_seconds = self.no_text_config.get(
            "max_wait_seconds", DEFAULT_NO_TEXT_MAX_WAIT_SECONDS
        )
        self.max_batch_size = self.config.get(
            "max_batch_size", DEFAULT_MAX_BATCH_SIZE
        )
        self.max_buffer_size = self.config.get(
            "max_buffer_size", DEFAULT_MAX_BUFFER_SIZE
        )
        self.should_debounce_hook = self.config.get("should_debounce_hook")
    
    def _get_session_key(self, open_id: str, chat_id: str) -> str:
        """生成session key"""
        return f"{open_id}:{chat_id}"
    
    def _get_buffer_key(self, session_key: str) -> str:
        """获取缓冲队列Redis key"""
        open_id, chat_id = session_key.split(":", 1)
        return FeishuRedisKeys.debounce_buffer(open_id, chat_id)
    
    def _get_state_key(self, session_key: str) -> str:
        """获取状态Redis key"""
        open_id, chat_id = session_key.split(":", 1)
        return FeishuRedisKeys.debounce_state(open_id, chat_id)
    
    def _get_timer_key(self, session_key: str) -> str:
        """获取定时器Redis key"""
        open_id, chat_id = session_key.split(":", 1)
        return FeishuRedisKeys.debounce_timer(open_id, chat_id)
    
    def _get_no_text_key(self, session_key: str) -> str:
        """获取无文本检测Redis key"""
        open_id, chat_id = session_key.split(":", 1)
        return FeishuRedisKeys.debounce_no_text(open_id, chat_id)
    
    def _get_lock_key(self, session_key: str) -> str:
        """获取分布式锁Redis key"""
        open_id, chat_id = session_key.split(":", 1)
        return FeishuRedisKeys.debounce_lock(open_id, chat_id)
    
    def _content_has_text(self, content: dict) -> bool:
        """检查消息内容是否包含文本"""
        text = content.get("text", "")
        if isinstance(text, str):
            return len(text.strip()) > 0
        return False
    
    def should_debounce(self, message: dict) -> bool:
        """
        判断是否应该防抖
        
        默认逻辑:
        - 系统命令 (以/开头) -> 不防抖
        - [URGENT] 标记 -> 不防抖
        - 其他消息 -> 防抖
        """
        content = message.get("content", {})
        text = content.get("text", "")
        
        if isinstance(text, str):
            # 系统命令不防抖
            if text.strip().startswith("/"):
                return False
            # URGENT标记不防抖
            if "[URGENT]" in text.upper():
                return False
        
        return True
    
    async def _load_custom_hook(self) -> Optional[Callable]:
        """加载自定义防抖钩子"""
        if not self.should_debounce_hook:
            return None
        
        try:
            # 动态导入模块
            module_path, func_name = self.should_debounce_hook.rsplit(":", 1)
            spec = importlib.util.spec_from_file_location("custom_hook", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return getattr(module, func_name)
        except Exception as e:
            logger.error("Failed to load custom debounce hook",
                        hook=self.should_debounce_hook,
                        error=str(e))
            return None
    
    async def check_should_debounce(self, message: dict) -> bool:
        """检查是否应该防抖（支持自定义钩子）"""
        # 先使用默认逻辑
        default_result = self.should_debounce(message)
        
        # 如果有自定义钩子，尝试使用
        hook = await self._load_custom_hook()
        if hook:
            try:
                hook_result = hook(message)
                if hook_result is not None:
                    return hook_result
            except Exception as e:
                logger.error("Custom debounce hook failed",
                            error=str(e),
                            fallback="using default logic")
        
        return default_result
    
    async def add_message(
        self,
        open_id: str,
        chat_id: str,
        message: dict,
    ) -> tuple[str, List[dict]]:
        """
        添加消息到缓冲
        
        Returns:
            (操作结果, 待处理消息列表)
            操作结果: "buffered" | "flushed" | "processing"
        """
        logger.info("Add message to debounce buffer",
                   open_id=open_id,
                   chat_id=chat_id)
        
        session_key = self._get_session_key(open_id, chat_id)
        buffer_key = self._get_buffer_key(session_key)
        state_key = self._get_state_key(session_key)
        timer_key = self._get_timer_key(session_key)
        lock_key = self._get_lock_key(session_key)
        
        # 获取分布式锁
        lock_acquired = await redis_client.set(
            lock_key, "1", nx=True, ex=10
        )
        if not lock_acquired:
            logger.warning("Could not acquire debounce lock",
                          session_key=session_key)
            return "processing", []
        
        try:
            # 检查当前状态
            current_state = await redis_client.get(state_key)
            current_state_str = current_state.decode() if isinstance(current_state, bytes) else current_state
            
            if current_state_str == "processing":
                # 正在处理中，直接返回
                return "processing", []
            
            # 将消息添加到缓冲队列
            message_json = json.dumps(message)
            await redis_client.lpush(buffer_key, message_json)
            
            # 限制缓冲队列大小
            buffer_len = await redis_client.llen(buffer_key)
            if buffer_len > self.max_buffer_size:
                # 移除最旧的消息
                await redis_client.ltrim(buffer_key, 0, self.max_buffer_size - 1)
                logger.warning("Buffer overflow, trimmed old messages",
                              session_key=session_key,
                              max_size=self.max_buffer_size)
            
            # 检查是否需要启动无文本防抖
            has_text = self._content_has_text(message.get("content", {}))
            if not has_text and self.no_text_enabled:
                no_text_key = self._get_no_text_key(session_key)
                await redis_client.setex(
                    no_text_key,
                    int(self.no_text_max_wait_seconds),
                    "1"
                )
                logger.debug("No-text debounce started",
                            session_key=session_key,
                            max_wait=self.no_text_max_wait_seconds)
            
            # 检查是否是第一条消息
            if not current_state or current_state_str == "idle":
                # 设置为缓冲状态
                await redis_client.setex(
                    state_key,
                    int(self.debounce_wait_seconds + self.no_text_max_wait_seconds + 10),
                    "buffering"
                )
                
                # 设置定时器
                await redis_client.setex(
                    timer_key,
                    int(self.debounce_wait_seconds),
                    "1"
                )
                
                logger.debug("Debounce started",
                            session_key=session_key,
                            wait_seconds=self.debounce_wait_seconds)
                
                return "buffered", []
            
            # 已经有消息在缓冲中，重置定时器
            await redis_client.setex(
                timer_key,
                int(self.debounce_wait_seconds),
                "1"
            )
            
            logger.debug("Debounce timer reset",
                        session_key=session_key)
            
            return "buffered", []
            
        finally:
            # 释放锁
            await redis_client.delete(lock_key)
    
    async def should_flush(self, open_id: str, chat_id: str) -> tuple[bool, List[dict]]:
        """
        检查是否应该刷新缓冲
        
        Returns:
            (是否应该刷新, 消息列表)
        """
        session_key = self._get_session_key(open_id, chat_id)
        buffer_key = self._get_buffer_key(session_key)
        state_key = self._get_state_key(session_key)
        timer_key = self._get_timer_key(session_key)
        lock_key = self._get_lock_key(session_key)
        no_text_key = self._get_no_text_key(session_key)
        
        # 获取锁
        lock_acquired = await redis_client.set(
            lock_key, "1", nx=True, ex=10
        )
        if not lock_acquired:
            return False, []
        
        try:
            # 检查状态
            current_state = await redis_client.get(state_key)
            current_state_str = current_state.decode() if isinstance(current_state, bytes) else current_state
            if current_state_str != "buffering":
                return False, []
            
            # 检查缓冲队列
            buffer_len = await redis_client.llen(buffer_key)
            if buffer_len == 0:
                return False, []
            
            # 检查定时器是否过期
            timer_exists = await redis_client.exists(timer_key)
            
            # 检查无文本防抖
            no_text_waiting = await redis_client.exists(no_text_key)
            
            should_flush_now = False
            
            if not timer_exists:
                # 时间防抖已过期
                if not no_text_waiting or not self.no_text_enabled:
                    # 无文本防抖也过期或未启用
                    should_flush_now = True
                else:
                    # 还在等无文本防抖
                    logger.debug("Waiting for no-text debounce",
                               session_key=session_key)
            
            if should_flush_now:
                # 获取所有缓冲的消息
                messages_raw = await redis_client.lrange(buffer_key, 0, -1)
                messages = [json.loads(m) for m in messages_raw]
                
                # 合并消息
                merged = self._merge_messages(messages)
                
                # 设置为处理状态
                await redis_client.setex(
                    state_key,
                    3600,  # 1小时过期
                    "processing"
                )
                
                # 清空缓冲队列
                await redis_client.delete(buffer_key)
                await redis_client.delete(no_text_key)
                
                logger.info("Messages flushed",
                           session_key=session_key,
                           message_count=len(messages))
                
                return True, [merged]
            
            return False, []
            
        finally:
            await redis_client.delete(lock_key)
    
    def _merge_messages(self, messages: List[dict]) -> dict:
        """合并多条消息"""
        if not messages:
            return {}
        
        if len(messages) == 1:
            return messages[0]
        
        # 按时间排序
        messages.sort(key=lambda m: m.get("create_time", 0))
        
        # 合并文本内容
        texts = []
        media_placeholders = []
        
        for msg in messages:
            content = msg.get("content", {})
            text = content.get("text", "")
            msg_type = msg.get("msg_type", "")
            
            if text and isinstance(text, str):
                texts.append(text.strip())
            
            # 为媒体文件添加占位符
            if msg_type in ["image", "file", "audio", "media", "sticker"]:
                file_name = content.get("file_name", "媒体文件")
                media_placeholders.append(f"[{msg_type.upper()}: {file_name}]")
        
        # 合并后的消息
        merged_text = "\n\n".join(texts)
        
        # 如果有媒体占位符，添加到文本前面
        if media_placeholders:
            media_text = "\n".join(media_placeholders)
            if merged_text:
                merged_text = f"{media_text}\n\n{merged_text}"
            else:
                merged_text = media_text
        
        # 使用最后一条消息作为基础
        merged = messages[-1].copy()
        merged["content"] = merged.get("content", {}).copy()
        merged["content"]["text"] = merged_text
        merged["merged_count"] = len(messages)
        merged["merged_from"] = [m.get("message_id") for m in messages]
        
        return merged
    
    async def complete_processing(self, open_id: str, chat_id: str):
        """完成处理，重置状态"""
        session_key = self._get_session_key(open_id, chat_id)
        state_key = self._get_state_key(session_key)
        
        await redis_client.setex(state_key, 3600, "idle")
        logger.debug("Processing completed", session_key=session_key)
    
    async def batch_consume(
        self,
        open_id: str,
        chat_id: str,
    ) -> List[dict]:
        """
        批量消费同一session的消息
        
        Returns:
            消息列表（最多max_batch_size条）
        """
        session_key = self._get_session_key(open_id, chat_id)
        buffer_key = self._get_buffer_key(session_key)
        
        # 批量获取消息
        messages_raw = await redis_client.lrange(
            buffer_key, 0, self.max_batch_size - 1
        )
        
        if not messages_raw:
            return []
        
        # 解析消息
        messages = [json.loads(m) for m in messages_raw]
        
        # 从队列中移除
        await redis_client.ltrim(buffer_key, len(messages_raw), -1)
        
        logger.debug("Batch consumed messages",
                    session_key=session_key,
                    count=len(messages))
        
        return messages


class DebounceScanner:
    """防抖扫描器 - 主动扫描过期session"""
    
    def __init__(self, debounce_manager: DebounceManager = None):
        self.debounce_manager = debounce_manager or DebounceManager()
        self.scan_interval = 5  # 5秒扫描一次
        self.running = False
    
    async def _enqueue_messages(self, messages: list):
        """将 flush 后的消息重新入队到 ARQ 队列"""
        from arq import create_pool
        from arq.connections import RedisSettings
        from app.config import get_settings
        
        settings = get_settings()
        
        try:
            # 解析 Redis URL
            redis_url = settings.REDIS_URL
            logger.debug(f"Connecting to Redis: {redis_url}")
            
            # 创建 ARQ Redis 配置
            # 简化的 Redis 配置
            redis_settings = RedisSettings.from_dsn(redis_url)
            
            # 创建 ARQ 连接池
            logger.debug("Creating ARQ pool...")
            arq_pool = await create_pool(redis_settings)
            logger.debug("ARQ pool created")
            
            # 将所有消息重新入队
            queue_name = "arq:feishu:queue"
            for i, message in enumerate(messages):
                logger.debug(f"Enqueuing message {i+1}/{len(messages)}")
                job = await arq_pool.enqueue_job(
                    "process_feishu_message",
                    message,
                    _queue_name=queue_name,
                )
                logger.info("Re-enqueued flushed message",
                           message_id=message.get("message_id"),
                           event_id=message.get("event_id"),
                           job_id=str(job) if job else None)
            
            await arq_pool.close()
            logger.info(f"Successfully re-enqueued {len(messages)} flushed messages")
            
        except Exception as e:
            logger.error("Failed to enqueue flushed messages", 
                        error=str(e), 
                        error_type=type(e).__name__,
                        message_count=len(messages))
            import traceback
            logger.error(f"Enqueue traceback: {traceback.format_exc()}")
            raise
    
    async def start(self):
        """启动扫描器"""
        self.running = True
        logger.info("DebounceScanner started")
        
        while self.running:
            try:
                await self._scan_once()
            except Exception as e:
                logger.error("DebounceScanner error", error=str(e))
            
            await asyncio.sleep(self.scan_interval)
    
    async def _scan_once(self):
        """执行一次扫描"""
        # 扫描所有处于buffering状态的session
        pattern = "feishu:state:*"
        cursor = 0
        expired_sessions = []
        total_buffering = 0
        
        logger.debug(f"Starting scan with pattern: {pattern}")
        
        while True:
            cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
            logger.debug(f"Scan returned {len(keys)} keys, cursor={cursor}")
            
            for key in keys:
                # 处理 key - 可能是 bytes 或 str
                key_str = key.decode() if isinstance(key, bytes) else key
                state = await redis_client.get(key)
                logger.debug(f"Checking key: {key_str}, state: {state}")
                
                # 处理 state - 可能是 bytes 或 str
                state_str = state.decode() if isinstance(state, bytes) else state
                logger.debug(f"Checking key: {key_str}, state: {state_str}")
                
                if state_str == "buffering":
                    total_buffering += 1
                    # 检查定时器
                    session_key = key_str.replace("feishu:state:", "")
                    timer_key = f"feishu:timer:{session_key}"
                    
                    timer_exists = await redis_client.exists(timer_key)
                    timer_ttl = await redis_client.ttl(timer_key) if timer_exists else -2
                    
                    logger.debug(f"Session {session_key}: timer_exists={timer_exists}, timer_ttl={timer_ttl}")
                    
                    if not timer_exists:
                        # 定时器已过期，需要flush
                        logger.debug(f"Session {session_key} timer expired, adding to flush list")
                        expired_sessions.append(session_key)
            
            if cursor == 0:
                break
        
        # 处理过期的session
        logger.info(f"Scan complete: found {total_buffering} buffering sessions, {len(expired_sessions)} expired to flush")
        for session_key in expired_sessions:
            try:
                logger.info(f"Processing expired session: {session_key}")
                open_id, chat_id = session_key.split(":", 1)
                logger.info(f"Split result - open_id: '{open_id}', chat_id: '{chat_id}'")
                
                should_flush, messages = await self.debounce_manager.should_flush(
                    open_id, chat_id
                )
                
                logger.info(f"should_flush result: {should_flush}, messages count: {len(messages)}")
                
                if should_flush and messages:
                    logger.info("Scanner flushed expired session",
                               session_key=session_key,
                               message_count=len(messages))
                    
                    # 将 flush 后的消息重新入队到 ARQ
                    try:
                        await self._enqueue_messages(messages)
                        logger.info(f"Successfully enqueued {len(messages)} messages for session {session_key}")
                    except Exception as enqueue_error:
                        logger.error(f"Failed to enqueue messages for session {session_key}",
                                   error=str(enqueue_error),
                                   error_type=type(enqueue_error).__name__)
                        # 不要因为入队失败而停止处理其他 session
                        
            except Exception as e:
                logger.error("Failed to flush expired session",
                            session_key=session_key,
                            error=str(e),
                            error_type=type(e).__name__)
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
    
    async def stop(self):
        """停止扫描器"""
        self.running = False
        logger.info("DebounceScanner stopped")


# 全局防抖管理器实例
_debounce_manager: DebounceManager | None = None
_debounce_scanner: DebounceScanner | None = None


def get_debounce_manager(config: dict = None) -> DebounceManager:
    """获取DebounceManager单例"""
    global _debounce_manager
    if _debounce_manager is None:
        _debounce_manager = DebounceManager(config)
    return _debounce_manager


def get_debounce_scanner() -> DebounceScanner:
    """获取DebounceScanner单例"""
    global _debounce_scanner
    if _debounce_scanner is None:
        _debounce_scanner = DebounceScanner()
    return _debounce_scanner
