"""
BlockStreaming 模块
实现流式累积、分块发送、段落感知刷新
"""

import asyncio
import re
from typing import Callable, List, Optional

import structlog

from app.feishu.client import FeishuClient

logger = structlog.get_logger()

# 默认配置
DEFAULT_MIN_CHARS = 800
DEFAULT_MAX_CHARS = 1200
DEFAULT_IDLE_MS = 1000
DEFAULT_CHUNK_SIZE = 2000


class BlockStreamingState:
    """BlockStreaming 状态管理"""
    
    def __init__(
        self,
        min_chars: int = DEFAULT_MIN_CHARS,
        max_chars: int = DEFAULT_MAX_CHARS,
        idle_ms: int = DEFAULT_IDLE_MS,
        flush_on_enqueue: bool = True,
        paragraph_aware: bool = True,
    ):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.idle_ms = idle_ms
        self.flush_on_enqueue = flush_on_enqueue
        self.paragraph_aware = paragraph_aware
        
        self.buffer = ""
        self.last_update_time = 0
        self.is_idle_timer_running = False
        self.card_id: Optional[str] = None
        self.element_id = "content"
        self.chunks_sent = 0
        self.total_text = ""
        
    def add_text(self, text: str) -> bool:
        """
        添加文本到缓冲
        
        Returns:
            是否应该立即flush
        """
        self.buffer += text
        self.total_text += text
        self.last_update_time = asyncio.get_event_loop().time()
        
        # 检查是否达到最大字符数
        if len(self.buffer) >= self.max_chars:
            return True
        
        # 检查是否达到最小字符数
        if len(self.buffer) >= self.min_chars:
            # 检查段落边界
            if self.flush_on_enqueue and self.paragraph_aware:
                if self._is_paragraph_boundary(self.buffer):
                    return True
        
        return False
    
    def _is_paragraph_boundary(self, text: str) -> bool:
        """检测是否在段落边界"""
        # 段落边界模式
        boundary_patterns = [
            r'\n\n',           # 双换行
            r'\n[-*]\s',      # 列表项
            r'\n\d+\.\s',     # 数字列表
            r'```\n',         # 代码块结束
            r'[.!?]\n',       # 句子结束
        ]
        
        # 检查文本末尾是否匹配边界模式
        for pattern in boundary_patterns:
            if re.search(pattern + r'$', text):
                return True
        
        return False
    
    def should_flush_idle(self) -> bool:
        """检查是否应该因空闲而flush"""
        if not self.buffer:
            return False
        
        current_time = asyncio.get_event_loop().time()
        elapsed_ms = (current_time - self.last_update_time) * 1000
        
        if elapsed_ms >= self.idle_ms and len(self.buffer) >= self.min_chars:
            return True
        
        return False
    
    def get_buffer(self) -> str:
        """获取当前缓冲内容"""
        return self.buffer
    
    def clear_buffer(self):
        """清空缓冲"""
        self.buffer = ""
        self.chunks_sent += 1


class BlockStreamingManager:
    """BlockStreaming 管理器"""
    
    def __init__(
        self,
        feishu_client: FeishuClient,
        config: dict = None,
    ):
        self.feishu_client = feishu_client
        self.config = config or {}
        
        self.min_chars = self.config.get("min_chars", DEFAULT_MIN_CHARS)
        self.max_chars = self.config.get("max_chars", DEFAULT_MAX_CHARS)
        self.idle_ms = self.config.get("idle_ms", DEFAULT_IDLE_MS)
        self.flush_on_enqueue = self.config.get("flush_on_enqueue", True)
        self.paragraph_aware = self.config.get("paragraph_aware", True)
        self.chunk_size = self.config.get("chunk_size", DEFAULT_CHUNK_SIZE)
        
        self._streaming_states: dict[str, BlockStreamingState] = {}
        self._idle_timers: dict[str, asyncio.Task] = {}
    
    def _get_state_key(self, open_id: str, chat_id: str) -> str:
        """生成状态key"""
        return f"{open_id}:{chat_id}"
    
    def _get_or_create_state(
        self,
        open_id: str,
        chat_id: str,
    ) -> BlockStreamingState:
        """获取或创建状态"""
        key = self._get_state_key(open_id, chat_id)
        
        if key not in self._streaming_states:
            self._streaming_states[key] = BlockStreamingState(
                min_chars=self.min_chars,
                max_chars=self.max_chars,
                idle_ms=self.idle_ms,
                flush_on_enqueue=self.flush_on_enqueue,
                paragraph_aware=self.paragraph_aware,
            )
        
        return self._streaming_states[key]
    
    async def start_streaming(
        self,
        receive_id: str,
        receive_id_type: str = "open_id",
    ) -> str:
        """
        开始流式回复，创建卡片
        
        Returns:
            卡片ID
        """
        try:
            response = await self.feishu_client.create_streaming_card(
                receive_id=receive_id,
                initial_content="思考中...",
                receive_id_type=receive_id_type,
            )
            
            # 从响应中提取卡片ID
            # 注意：实际实现需要根据飞书API响应结构调整
            card_id = response.get("data", {}).get("message_id", "")
            
            logger.info("Streaming card created", 
                       receive_id=receive_id,
                       card_id=card_id)
            
            return card_id
            
        except Exception as e:
            logger.error("Failed to create streaming card",
                        error=str(e))
            raise
    
    async def update_streaming(
        self,
        open_id: str,
        chat_id: str,
        text: str,
        receive_id: str,
        receive_id_type: str = "open_id",
    ) -> tuple[bool, str]:
        """
        更新流式回复
        
        Returns:
            (是否应该发送, 要发送的文本)
        """
        state = self._get_or_create_state(open_id, chat_id)
        
        # 如果没有卡片ID，先创建
        if not state.card_id:
            try:
                state.card_id = await self.start_streaming(receive_id, receive_id_type)
            except Exception as e:
                # 创建失败，回退到普通消息
                logger.error("Failed to start streaming, falling back",
                            error=str(e))
                return True, text
        
        # 添加文本到缓冲
        should_flush = state.add_text(text)
        
        # 启动空闲定时器
        if not state.is_idle_timer_running:
            state.is_idle_timer_running = True
            key = self._get_state_key(open_id, chat_id)
            self._idle_timers[key] = asyncio.create_task(
                self._idle_timer_task(open_id, chat_id, receive_id, receive_id_type)
            )
        
        if should_flush:
            buffer_text = state.get_buffer()
            state.clear_buffer()
            # 立即更新卡片
            await self.update_card_content(state.card_id, buffer_text)
            return True, buffer_text
        
        return False, ""
    
    async def update_card_content(self, card_id: str, content: str):
        """更新流式卡片内容"""
        try:
            await self.feishu_client.update_streaming_card(
                card_id=card_id,
                element_id="content",
                content=content,
            )
            logger.debug("Streaming card updated", card_id=card_id, content_length=len(content))
        except Exception as e:
            logger.error("Failed to update streaming card", card_id=card_id, error=str(e))
    
    async def _idle_timer_task(self, open_id: str, chat_id: str, receive_id: str, receive_id_type: str = "open_id"):
        """空闲定时器任务"""
        state = self._get_or_create_state(open_id, chat_id)
        
        while True:
            await asyncio.sleep(self.idle_ms / 1000)
            
            if state.should_flush_idle():
                buffer_text = state.get_buffer()
                if buffer_text:
                    # 发送剩余内容
                    await self.update_card_content(state.card_id, buffer_text)
                    state.clear_buffer()
                    logger.debug("Idle flush triggered",
                               open_id=open_id,
                               chat_id=chat_id)
                break
            
            # 检查是否还有内容
            if not state.buffer:
                break
        
        state.is_idle_timer_running = False
    
    async def close_streaming(
        self,
        open_id: str,
        chat_id: str,
    ):
        """关闭流式回复，发送剩余内容"""
        key = self._get_state_key(open_id, chat_id)
        state = self._streaming_states.get(key)
        
        if state:
            # 发送剩余内容
            if state.buffer and state.card_id:
                try:
                    await self.update_card_content(state.card_id, state.buffer)
                    state.clear_buffer()
                except Exception as e:
                    logger.error("Failed to send final content", error=str(e))
            
            # 关闭卡片
            if state.card_id:
                try:
                    await self.feishu_client.close_streaming_card(state.card_id)
                    logger.info("Streaming card closed",
                               open_id=open_id,
                               chat_id=chat_id)
                except Exception as e:
                    logger.error("Failed to close streaming card",
                                error=str(e))
            
            # 关闭卡片
            if state.card_id:
                try:
                    await self.feishu_client.close_streaming_card(state.card_id)
                    logger.info("Streaming card closed",
                               open_id=open_id,
                               chat_id=chat_id)
                except Exception as e:
                    logger.error("Failed to close streaming card",
                                error=str(e))
        
        # 清理状态
        if key in self._streaming_states:
            del self._streaming_states[key]
        
        if key in self._idle_timers:
            self._idle_timers[key].cancel()
            del self._idle_timers[key]
    
    def chunk_text(self, text: str) -> List[str]:
        """
        将长文本分块
        
        策略:
        - 按段落边界分割
        - 每块不超过chunk_size
        - 第一块用于流式卡片，后续块用普通消息
        """
        if len(text) <= self.chunk_size:
            return [text]
        
        chunks = []
        current_chunk = ""
        
        # 按段落分割
        paragraphs = text.split('\n\n')
        
        for paragraph in paragraphs:
            # 如果当前段落加上已有内容超过限制，先保存当前块
            if len(current_chunk) + len(paragraph) + 2 > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph
            else:
                if current_chunk:
                    current_chunk += '\n\n' + paragraph
                else:
                    current_chunk = paragraph
        
        # 添加最后一块
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    async def send_chunks(
        self,
        receive_id: str,
        text: str,
        card_id: str,
        receive_id_type: str = "open_id",
    ):
        """
        发送分块文本
        
        第一块更新到流式卡片，后续块用普通消息
        """
        chunks = self.chunk_text(text)
        
        if not chunks:
            return
        
        # 第一块更新到流式卡片
        first_chunk = chunks[0]
        try:
            await self.feishu_client.update_streaming_card(
                card_id=card_id,
                element_id="content",
                content=first_chunk,
            )
        except Exception as e:
            logger.error("Failed to update streaming card",
                        card_id=card_id,
                        error=str(e))
            # 失败时发送普通消息
            await self.feishu_client.send_text_message(
                receive_id=receive_id,
                text=first_chunk,
                receive_id_type=receive_id_type,
            )
        
        # 后续块用普通消息
        for chunk in chunks[1:]:
            try:
                await self.feishu_client.send_text_message(
                    receive_id=receive_id,
                    text=chunk,
                    receive_id_type=receive_id_type,
                )
                # 小延迟避免发送过快
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("Failed to send chunk",
                            error=str(e))


# 全局BlockStreaming管理器实例
_block_streaming_manager: BlockStreamingManager | None = None


async def get_block_streaming_manager(
    feishu_client: FeishuClient = None,
    config: dict = None,
) -> BlockStreamingManager:
    """获取BlockStreamingManager单例"""
    global _block_streaming_manager
    if _block_streaming_manager is None:
        from app.feishu.client import get_feishu_client
        client = feishu_client or await get_feishu_client()
        _block_streaming_manager = BlockStreamingManager(client, config)
    return _block_streaming_manager
