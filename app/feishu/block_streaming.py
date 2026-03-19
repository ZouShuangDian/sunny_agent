"""
BlockStreaming 模块
实现流式累积、分块发送、段落感知刷新

支持两种模式:
1. Block模式(默认): 累积文本后批量刷新 (min_chars/max_chars/idle_ms)
2. 打字机模式: 50ms/次，每次2字符，逐字显示打字机效果

使用示例:
    # Block模式（默认）
    config = {"min_chars": 800, "max_chars": 1200}
    manager = BlockStreamingManager(client, config)
    
    # 打字机模式
    config = {
        "typewriter_mode": True,
        "typewriter_interval_ms": 50,
        "typewriter_chars_per_update": 2
    }
    manager = BlockStreamingManager(client, config)
"""

import asyncio
import re
from typing import Callable, List, Optional

import structlog

from app.feishu.client import FeishuClient
from app.feishu.markdown_chunker import ChunkerConfig, MarkdownAwareChunker
from app.feishu.markdown_sanitizer import normalize_markdown_headings

logger = structlog.get_logger()

# 默认配置
DEFAULT_MIN_CHARS = 1500
DEFAULT_MAX_CHARS = 2400
DEFAULT_IDLE_MS = 1000
DEFAULT_CHUNK_SIZE = 2000

# 打字机模式配置
DEFAULT_TYPEWRITER_INTERVAL_MS = 50  # 50ms/次
DEFAULT_TYPEWRITER_CHARS_PER_UPDATE = 2  # 每次2字符


class BlockStreamingState:
    """BlockStreaming 状态管理"""
    
    def __init__(
        self,
        min_chars: int = DEFAULT_MIN_CHARS,
        max_chars: int = DEFAULT_MAX_CHARS,
        idle_ms: int = DEFAULT_IDLE_MS,
        flush_on_enqueue: bool = True,
        paragraph_aware: bool = True,
        typewriter_mode: bool = False,
        typewriter_interval_ms: int = DEFAULT_TYPEWRITER_INTERVAL_MS,
        typewriter_chars_per_update: int = DEFAULT_TYPEWRITER_CHARS_PER_UPDATE,
    ):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.idle_ms = idle_ms
        self.flush_on_enqueue = flush_on_enqueue
        self.paragraph_aware = paragraph_aware
        self.typewriter_mode = typewriter_mode
        self.typewriter_interval_ms = typewriter_interval_ms
        self.typewriter_chars_per_update = typewriter_chars_per_update
        
        self.buffer = ""
        self.last_update_time = 0
        self.is_idle_timer_running = False
        self.card_id: Optional[str] = None
        self.message_id: Optional[str] = None
        self.element_id = "streaming_content"
        self.chunks_sent = 0
        self.total_text = ""
        self.sequence = 0  # 用于流式更新的序列号
        
        # 打字机模式专用
        self.displayed_text = ""  # 已显示的文本
        self.pending_text = ""    # 待显示的文本缓冲区
        self.typewriter_task: Optional[asyncio.Task] = None  # 打字机更新任务
        self.chunker = MarkdownAwareChunker(
            ChunkerConfig(
                min_chars=min_chars,
                max_chars=max_chars,
                idle_ms=idle_ms,
            )
        )
        
    def add_text(self, text: str) -> list[str]:
        """Append text and return ready-to-send chunks."""
        normalized_text = normalize_markdown_headings(text, max_level=4)
        self.total_text += normalized_text
        now_ms = int(asyncio.get_event_loop().time() * 1000)
        self.last_update_time = now_ms / 1000
        chunks = self.chunker.append(normalized_text, now_ms=now_ms)
        self.buffer = self.chunker.buffer
        self.chunks_sent += len(chunks)
        return chunks

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
    
    def flush_idle(self) -> list[str]:
        """Flush one chunk after the buffer stays idle long enough."""
        now_ms = int(asyncio.get_event_loop().time() * 1000)
        chunks = self.chunker.flush_idle(now_ms=now_ms)
        self.buffer = self.chunker.buffer
        self.chunks_sent += len(chunks)
        return chunks

    def flush_final(self) -> list[str]:
        """Flush all remaining buffered chunks."""
        chunks = self.chunker.flush_final()
        self.buffer = self.chunker.buffer
        self.chunks_sent += len(chunks)
        return chunks


class BlockStreamingManager:
    """BlockStreaming 管理器
    
    支持两种模式:
    1. Block模式(默认): 累积文本后批量刷新 (min_chars/max_chars)
    2. 打字机模式: 50ms/次，每次2字符，逐字显示
    """
    
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
        
        # 打字机模式配置
        self.typewriter_mode = self.config.get("typewriter_mode", False)
        self.typewriter_interval_ms = self.config.get(
            "typewriter_interval_ms", DEFAULT_TYPEWRITER_INTERVAL_MS
        )
        self.typewriter_chars_per_update = self.config.get(
            "typewriter_chars_per_update", DEFAULT_TYPEWRITER_CHARS_PER_UPDATE
        )
        
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
                typewriter_mode=self.typewriter_mode,
                typewriter_interval_ms=self.typewriter_interval_ms,
                typewriter_chars_per_update=self.typewriter_chars_per_update,
            )
        
        return self._streaming_states[key]
    
    async def start_streaming(
        self,
        receive_id: str,
        receive_id_type: str = "open_id",
        title: str = None,
    ) -> tuple[str, str]:
        """
        开始流式回复，创建并发送卡片
        
        Flow:
        1. createStreamingCard() -> POST /cardkit/v1/cards
        2. sendStreamingCard() -> POST /im/v1/messages
        
        Returns:
            (card_id, message_id)
        """
        try:
            # Step 1: 创建卡片实体
            create_response = await self.feishu_client.create_streaming_card(
                title=title,
                initial_content="⏳ 思考中...",
            )
            card_id = create_response.get("data", {}).get("card_id")
            
            if not card_id:
                raise ValueError("Failed to get card_id from create_streaming_card response")
            
            # Step 2: 发送卡片消息
            send_response = await self.feishu_client.send_streaming_card(
                receive_id=receive_id,
                card_id=card_id,
                receive_id_type=receive_id_type,
            )
            message_id = send_response.get("data", {}).get("message_id")
            
            logger.info("Streaming card created and sent", 
                       receive_id=receive_id,
                       card_id=card_id,
                       message_id=message_id)
            
            return card_id, message_id
            
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
        
        支持两种模式:
        - Block模式: 累积文本后批量刷新
        - 打字机模式: 50ms/次，每次2字符
        
        Returns:
            (是否应该发送, 要发送的文本)
        """
        state = self._get_or_create_state(open_id, chat_id)
        
        # 如果没有卡片ID，先创建
        if not state.card_id:
            try:
                card_id, message_id = await self.start_streaming(receive_id, receive_id_type)
                state.card_id = card_id
                state.message_id = message_id
                
                # 打字机模式: 启动打字机更新任务
                if state.typewriter_mode:
                    state.typewriter_task = asyncio.create_task(
                        self._typewriter_update_loop(state)
                    )
                    logger.info("Typewriter mode started",
                               open_id=open_id,
                               chat_id=chat_id,
                               interval_ms=state.typewriter_interval_ms,
                               chars_per_update=state.typewriter_chars_per_update)
            except Exception as e:
                # 创建失败，回退到普通消息
                logger.error("Failed to start streaming, falling back",
                            error=str(e))
                return True, text
        
        # 打字机模式: 追加到待显示缓冲区
        if state.typewriter_mode:
            state.total_text += text
            state.pending_text += text
            return False, ""
        
        # Block模式: 原有逻辑
        chunks = state.add_text(text)
        
        # 启动空闲定时器
        if not state.is_idle_timer_running:
            state.is_idle_timer_running = True
            key = self._get_state_key(open_id, chat_id)
            self._idle_timers[key] = asyncio.create_task(
                self._idle_timer_task(open_id, chat_id, receive_id, receive_id_type)
            )
        
        if chunks:
            sent_text = ""
            for chunk in chunks:
                await self.update_card_content(state, chunk)
                sent_text = chunk
            return True, sent_text

        return False, ""

    async def _typewriter_update_loop(self, state: BlockStreamingState):
        """
        打字机模式更新循环
        
        每50ms更新一次，每次显示2个字符
        """
        interval = state.typewriter_interval_ms / 1000.0  # 转换为秒
        chars_per_update = state.typewriter_chars_per_update
        
        logger.debug(f"Starting typewriter loop: interval={interval}s, chars={chars_per_update}")
        
        while True:
            try:
                await asyncio.sleep(interval)
                
                # 检查是否有待显示的文本
                if not state.pending_text:
                    # 如果没有待显示文本且总文本已显示完毕，退出循环
                    if len(state.displayed_text) >= len(state.total_text):
                        break
                    continue
                
                # 取出指定数量的字符
                chars_to_display = state.pending_text[:chars_per_update]
                state.pending_text = state.pending_text[chars_per_update:]
                
                # 更新已显示文本
                state.displayed_text += chars_to_display
                state.sequence += 1
                
                # 更新卡片
                await self.update_card_content(state, state.displayed_text)
                
            except asyncio.CancelledError:
                logger.debug("Typewriter loop cancelled")
                break
            except Exception as e:
                logger.warning(f"Typewriter update failed: {e}")
                continue
    
    async def update_card_content(self, state: BlockStreamingState, content: str):
        """更新流式卡片内容"""
        if not state.card_id:
            logger.error("Cannot update card: no card_id")
            return
        
        try:
            # 递增序列号
            state.sequence += 1
            
            await self.feishu_client.update_streaming_card(
                card_id=state.card_id,
                element_id=state.element_id,
                content=content,
                sequence=state.sequence,
            )
            logger.debug("Streaming card updated", 
                        card_id=state.card_id, 
                        sequence=state.sequence,
                        content_length=len(content))
        except Exception as e:
            logger.error("Failed to update streaming card", 
                        card_id=state.card_id, 
                        error=str(e))
    
    async def _idle_timer_task(self, open_id: str, chat_id: str, receive_id: str, receive_id_type: str = "open_id"):
        """空闲定时器任务"""
        state = self._get_or_create_state(open_id, chat_id)
        
        while True:
            await asyncio.sleep(self.idle_ms / 1000)
            
            idle_chunks = state.flush_idle()
            if idle_chunks:
                for chunk in idle_chunks:
                    await self.update_card_content(state, chunk)
                logger.debug("Idle flush triggered",
                           open_id=open_id,
                           chat_id=chat_id,
                           chunks=len(idle_chunks))
                break

            if not state.chunker.buffer:
                break
        
        state.is_idle_timer_running = False
    
    async def close_streaming(
        self,
        open_id: str,
        chat_id: str,
        final_text: str = None,
    ):
        """
        关闭流式回复，发送剩余内容并关闭流式模式
        
        Flow:
        1. 发送剩余内容（如果有）
        2. closeStreamingMode() -> PATCH /cardkit/v1/cards/{id}/settings
        """
        key = self._get_state_key(open_id, chat_id)
        state = self._streaming_states.get(key)
        
        if not state or not state.card_id:
            # 清理状态
            if key in self._streaming_states:
                del self._streaming_states[key]
            if key in self._idle_timers:
                self._idle_timers[key].cancel()
                del self._idle_timers[key]
            return
        
        try:
            # Step 1: 发送剩余内容
            final_content = final_text or state.total_text
            
            # 打字机模式: 等待打字机任务完成或强制刷新
            if state.typewriter_mode:
                if state.typewriter_task and not state.typewriter_task.done():
                    # 快速刷新剩余内容
                    if state.pending_text:
                        state.displayed_text += state.pending_text
                        state.pending_text = ""
                        state.sequence += 1
                        await self.update_card_content(state, state.displayed_text)
                    
                    # 取消打字机任务
                    state.typewriter_task.cancel()
                    try:
                        await state.typewriter_task
                    except asyncio.CancelledError:
                        pass
            else:
                for chunk in state.flush_final():
                    await self.update_card_content(state, chunk)
            
            # Step 2: 关闭流式模式
            # 生成 summary（截断文本用于聊天预览）
            summary = self._truncate_for_summary(final_content)
            state.sequence += 1
            
            await self.feishu_client.close_streaming_card(
                card_id=state.card_id,
                sequence=state.sequence,
                final_summary=summary,
            )
            
            logger.info("Streaming session closed",
                       open_id=open_id,
                       chat_id=chat_id,
                       card_id=state.card_id,
                       typewriter_mode=state.typewriter_mode)
            
        except Exception as e:
            logger.error("Failed to close streaming session",
                        open_id=open_id,
                        chat_id=chat_id,
                        error=str(e))
        finally:
            # 清理状态
            if key in self._streaming_states:
                del self._streaming_states[key]
            if key in self._idle_timers:
                self._idle_timers[key].cancel()
                del self._idle_timers[key]
    
    def _truncate_for_summary(self, text: str, max_length: int = 50) -> str:
        """截断文本用于聊天预览 summary"""
        if not text:
            return ""
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[:max_length - 3] + "..."
    
    def chunk_text(self, text: str) -> List[str]:
        """Split long text on Markdown-aware safe boundaries."""
        normalized_text = normalize_markdown_headings(text, max_level=4)
        if len(normalized_text) <= self.chunk_size:
            return [normalized_text]

        chunker = MarkdownAwareChunker(
            ChunkerConfig(
                min_chars=min(self.min_chars, self.chunk_size),
                max_chars=self.chunk_size,
                idle_ms=self.idle_ms,
                hard_limit_chars=max(self.chunk_size, self.max_chars),
            )
        )
        chunks = chunker.append(normalized_text, now_ms=0)
        chunks.extend(chunker.flush_final())
        return [chunk for chunk in chunks if chunk]

    async def send_message(
        self,
        receive_id: str,
        content: str,
        msg_type: str = "text",
        receive_id_type: str = "open_id",
    ) -> dict:
        """
        发送普通消息（非流式）
        
        Flow:
        sendMessageFeishu() -> POST /im/v1/messages
        
        Args:
            receive_id: 接收者ID
            content: 消息内容
            msg_type: 消息类型 ("text" | "post")
            receive_id_type: 接收者ID类型
        
        Returns:
            API响应
        """
        try:
            if msg_type == "text":
                return await self.feishu_client.send_text_message(
                    receive_id=receive_id,
                    text=content,
                    receive_id_type=receive_id_type,
                )
            elif msg_type == "post":
                # ?????
                post_content = {
                    "zh_cn": {
                        "title": "",
                        "content": [[{"tag": "text", "text": content}]]
                    }
                }
                import json
                return await self.feishu_client._request(
                    "POST",
                    "/im/v1/messages",
                    params={"receive_id_type": receive_id_type},
                    json_data={
                        "receive_id": receive_id,
                        "msg_type": "post",
                        "content": json.dumps(post_content),
                    }
                )
            elif msg_type == "interactive_markdown":
                return await self.feishu_client.send_markdown_card_message(
                    receive_id=receive_id,
                    markdown_content=content,
                    receive_id_type=receive_id_type,
                )
            else:
                raise ValueError(f"Unsupported msg_type: {msg_type}")
        except Exception as e:
            logger.error("Failed to send message",
                        receive_id=receive_id,
                        msg_type=msg_type,
                        error=str(e))
            raise
    
    async def send_chunks(
        self,
        receive_id: str,
        text: str,
        card_id: str,
        receive_id_type: str = "open_id",
    ):
        """Send chunked final reply messages as independent markdown cards."""
        chunks = self.chunk_text(text)

        if not chunks:
            return

        for i, chunk in enumerate(chunks, start=1):
            try:
                await self.send_message(
                    receive_id=receive_id,
                    content=chunk,
                    msg_type="interactive_markdown",
                    receive_id_type=receive_id_type,
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("Failed to send chunk",
                            chunk_index=i,
                            error=str(e))
                raise

# 全局BlockStreaming管理器实例
_block_streaming_managers: dict[str, BlockStreamingManager] = {}


async def get_block_streaming_manager(
    feishu_client: FeishuClient = None,
    config: dict = None,
    app_id: str | None = None,
) -> BlockStreamingManager:
    """Get a BlockStreamingManager instance isolated by app_id."""
    manager_app_id = app_id
    if manager_app_id is None and feishu_client is not None:
        manager_app_id = feishu_client.app_id
    if not manager_app_id:
        manager_app_id = "__default__"

    if manager_app_id not in _block_streaming_managers:
        from app.feishu.client import get_feishu_client
        client = feishu_client or await get_feishu_client(
            None if manager_app_id == "__default__" else manager_app_id
        )
        _block_streaming_managers[manager_app_id] = BlockStreamingManager(client, config)
    return _block_streaming_managers[manager_app_id]
