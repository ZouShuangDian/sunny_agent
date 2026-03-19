"""
飞书卡片状态管理器
管理卡片状态流转：思考中 → 校验中 → 生成答案中 → 完成

状态说明:
- THINKING: ⏳ 思考中... (收到消息后立即显示)
- VALIDATING: 🔍 校验中... (校验用户输入和上下文)
- GENERATING: 🤖 生成答案中... (调用大模型)
- COMPLETED: 完成 (关闭流式模式，发送最终答案)
"""

import asyncio
from enum import Enum
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
import time

import structlog

from app.feishu.block_streaming import BlockStreamingManager, get_block_streaming_manager
from app.feishu.client import FeishuClient, get_feishu_client
from app.feishu.markdown_sanitizer import normalize_markdown_headings

logger = structlog.get_logger()


class CardStatus(Enum):
    """卡片状态枚举"""
    THINKING = "thinking"
    VALIDATING = "validating"
    GENERATING = "generating"
    COMPLETED = "completed"
    ERROR = "error"


# 状态对应的显示文本
STATUS_TEXTS = {
    CardStatus.THINKING: "⏳ 处理中...",
    CardStatus.VALIDATING: "🔍 校验中...",
    CardStatus.GENERATING: "🤖 生成答案中...",
    CardStatus.COMPLETED: "✅ 已完成",
    CardStatus.ERROR: "❌ 出错了",
}


@dataclass
class CardSession:
    """卡片会话状态"""
    card_id: str
    message_id: str
    status: CardStatus = CardStatus.THINKING
    app_id: str = ""  # 支持多机器人
    open_id: str = ""
    chat_id: str = ""
    receive_id: str = ""
    receive_id_type: str = "open_id"
    element_id: str = "streaming_content"  # 流式元素 ID
    accumulated_content: str = ""  # ← 新增：累积的内容（用于流式更新）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    sequence: int = 0
    error_message: Optional[str] = None
    
    def update_status(self, status: CardStatus):
        """更新状态"""
        self.status = status
        self.updated_at = time.time()
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "card_id": self.card_id,
            "message_id": self.message_id,
            "status": self.status.value,
            "open_id": self.open_id,
            "chat_id": self.chat_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# 防抖配置常量
MIN_CONTENT_DIFF = 20          # 内容长度变化阈值：至少变化20字符才调用API
MIN_API_INTERVAL = 0.2         # API调用间隔阈值：至少间隔200ms才调用API


class CardStatusManager:
    """
    卡片状态管理器
    
    管理单个卡片的状态流转，提供状态更新接口
    
    使用示例:
        manager = CardStatusManager(block_streaming_manager)
        
        # 开始会话
        await manager.start_session(
            open_id="ou_xxx",
            chat_id="oc_xxx",
            receive_id="ou_xxx",
        )
        
        # 更新状态
        await manager.update_status(CardStatus.VALIDATING)
        await asyncio.sleep(1)
        
        await manager.update_status(CardStatus.GENERATING)
        answer = await call_llm()
        
        # 完成并发送答案
        await manager.complete(answer)
    """
    
    def __init__(
        self,
        block_streaming_manager: BlockStreamingManager,
        feishu_client: FeishuClient = None,
    ):
        self.block_streaming_manager = block_streaming_manager
        self.feishu_client = feishu_client
        self.session: Optional[CardSession] = None
        self._lock = asyncio.Lock()
        
        # 防抖状态追踪
        self._last_sent_content: str = ""      # 上次发送到API的内容
        self._last_api_call_time: float = 0    # 上次调用API的时间戳
    
    async def start_session(
        self,
        open_id: str,
        chat_id: str,
        receive_id: str,
        app_id: str = "",
        receive_id_type: str = "open_id",
        title: str = None,
    ) -> CardSession:
        """
        开始卡片会话，创建并发送初始卡片
        
        Args:
            open_id: 用户 open_id
            chat_id: 会话 chat_id
            receive_id: 接收者 ID
            app_id: 机器人应用 ID（支持多机器人）
            receive_id_type: 接收者 ID 类型
            title: 卡片标题（可选）
        
        Returns:
            CardSession 对象
        """
        async with self._lock:
            # 创建流式卡片
            card_id, message_id = await self.block_streaming_manager.start_streaming(
                receive_id=receive_id,
                receive_id_type=receive_id_type,
                title=title,
            )
            
            self.session = CardSession(
                card_id=card_id,
                message_id=message_id,
                status=CardStatus.THINKING,
                app_id=app_id,
                open_id=open_id,
                chat_id=chat_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            
            logger.info("Card session started",
                       card_id=card_id,
                       message_id=message_id,
                       open_id=open_id,
                       chat_id=chat_id)
            
            return self.session
    
    async def update_status(
        self,
        status: CardStatus,
        custom_text: str = None,
    ) -> bool:
        """
        更新卡片状态
        
        Args:
            status: 目标状态
            custom_text: 自定义显示文本（如果不传则使用默认文本）
        
        Returns:
            是否更新成功
        """
        async with self._lock:
            if not self.session:
                logger.error("Cannot update status: no active session")
                return False
            
            if self.session.status == CardStatus.COMPLETED:
                logger.warning("Cannot update status: session already completed")
                return False
            
            # 更新状态
            self.session.update_status(status)
            
            # 获取显示文本
            display_text = custom_text or STATUS_TEXTS.get(status, status.value)
            
            # 更新卡片内容
            try:
                await self.block_streaming_manager.update_card_content(
                    self._get_state(),
                    display_text,
                )
                
                logger.info("Card status updated",
                           card_id=self.session.card_id,
                           status=status.value,
                           display_text=display_text)
                return True
                
            except Exception as e:
                logger.error("Failed to update card status",
                            card_id=self.session.card_id,
                            status=status.value,
                            error=str(e))
                return False
    
    async def complete(
        self,
        final_answer: str,
        send_as_message: bool = True,
    ) -> bool:
        """
        完成卡片会话，发送最终答案
        
        Args:
            final_answer: 最终答案内容
            send_as_message: 是否作为普通消息发送（True）还是更新卡片（False）
        
        Returns:
            是否完成成功
        """
        async with self._lock:
            if not self.session:
                logger.error("Cannot complete: no active session")
                return False
            
            try:
                # 先发送最终答案，再关闭状态卡，避免出现“卡片已完成但答案尚未送达”的窗口
                if send_as_message and final_answer:
                    normalized_answer = normalize_markdown_headings(final_answer, max_level=4)
                    chunks = self.block_streaming_manager.chunk_text(normalized_answer)
                    for chunk_index, chunk in enumerate(chunks, start=1):
                        try:
                            await self.block_streaming_manager.send_message(
                                receive_id=self.session.receive_id,
                                content=chunk,
                                msg_type="interactive_markdown",
                                receive_id_type=self.session.receive_id_type,
                            )
                        except Exception as send_err:
                            logger.warning(
                                "Failed to send markdown final reply chunk, falling back to text",
                                card_id=self.session.card_id,
                                chunk_index=chunk_index,
                                error=str(send_err),
                            )
                            await self.block_streaming_manager.send_message(
                                receive_id=self.session.receive_id,
                                content=chunk,
                                msg_type="text",
                                receive_id_type=self.session.receive_id_type,
                            )
                        await asyncio.sleep(0.3)

                # 关闭流式卡片
                await self.block_streaming_manager.close_streaming(
                    open_id=self.session.open_id,
                    chat_id=self.session.chat_id,
                    final_text=STATUS_TEXTS[CardStatus.COMPLETED],
                )
                self.session.update_status(CardStatus.COMPLETED)
                
                logger.info("Card session completed",
                           card_id=self.session.card_id,
                           answer_length=len(final_answer) if final_answer else 0)
                return True
                
            except Exception as e:
                logger.error("Failed to complete card session",
                            card_id=self.session.card_id,
                            error=str(e))
                self.session.error_message = str(e)
                return False
    
    async def set_error(self, error_message: str) -> bool:
        """
        设置错误状态
        
        Args:
            error_message: 错误信息
        
        Returns:
            是否设置成功
        """
        async with self._lock:
            if not self.session:
                return False
            
            self.session.update_status(CardStatus.ERROR)
            self.session.error_message = error_message
            
            try:
                await self.block_streaming_manager.update_card_content(
                    self._get_state(),
                    f"❌ 出错了：{error_message}",
                )
                
                # 关闭流式模式
                await self.block_streaming_manager.close_streaming(
                    open_id=self.session.open_id,
                    chat_id=self.session.chat_id,
                    final_text=f"错误：{error_message}",
                )
                
                logger.warning("Card session error",
                              card_id=self.session.card_id,
                              error=error_message)
                return True
                
            except Exception as e:
                logger.error("Failed to set error status",
                            card_id=self.session.card_id,
                            error=str(e))
                return False
    
    def _get_state(self):
        """获取 BlockStreamingState"""
        state = self.block_streaming_manager._get_or_create_state(
            self.session.open_id,
            self.session.chat_id,
        )
        # ← 关键修复：确保 state 中的 card_id 与 session 一致
        # 这样 update_card_content 时就能正确更新卡片
        if self.session and self.session.card_id:
            state.card_id = self.session.card_id
            state.element_id = self.session.element_id
        return state
    
    async def update_card_content(self, content: str):
        """
        更新卡片内容（用于显示 AI 生成的文本）
        
        采用累积模式：每次更新时将新内容追加到已有内容后面
        
        Args:
            content: 要显示的内容（新内容片段）
        """
        if not self.session:
            logger.warning("Cannot update card content: no active session")
            return
        
        if not self.session.card_id:
            logger.warning("Cannot update card content: no card_id in session")
            return
        
        # ← 累积内容：将新内容追加到已有内容后面
        self.session.accumulated_content += content
        
        # 使用累积的全部内容更新卡片
        state = self._get_state()
        # 确保 state 中的 card_id 与 session 一致
        state.card_id = self.session.card_id
        state.element_id = self.session.element_id
        
        logger.debug("Updating card with accumulated content",
                    card_id=self.session.card_id,
                    accumulated_length=len(self.session.accumulated_content),
                    new_content_length=len(content))
        
        await self.block_streaming_manager.update_card_content(
            state, 
            self.session.accumulated_content  # ← 使用累积的全部内容
        )
    
    async def set_card_content(self, content: str, force: bool = False):
        """
        直接设置卡片内容（非累积模式，带防抖）
        
        与 update_card_content 的区别：
        - update_card_content: 将新内容追加到已有内容后面（累积模式）
        - set_card_content: 直接替换全部内容（设置模式）
        
        防抖逻辑（force=False时生效）：
        - 内容长度变化 < MIN_CONTENT_DIFF (20字符) → 跳过
        - 距离上次调用 < MIN_API_INTERVAL (200ms) → 跳过
        
        Args:
            content: 要显示的完整内容（直接替换，不追加）
            force: 是否强制刷新（绕过防抖），用于 FINISH 时
        """
        if not self.session:
            logger.warning("Cannot set card content: no active session")
            return
        
        if not self.session.card_id:
            logger.warning("Cannot set card content: no card_id in session")
            return
        
        current_time = time.time()
        
        # 防抖检查（非强制模式下）
        if not force:
            # 检查内容变化量
            content_diff = len(content) - len(self._last_sent_content)
            if abs(content_diff) < MIN_CONTENT_DIFF:
                # logger.debug("Debounced: content change too small",
                #             card_id=self.session.card_id,
                #             content_diff=content_diff,
                #             min_diff=MIN_CONTENT_DIFF)
                return
            
            # 检查时间间隔
            time_since_last_call = current_time - self._last_api_call_time
            if time_since_last_call < MIN_API_INTERVAL:
                logger.debug("Debounced: API call interval too short",
                            card_id=self.session.card_id,
                            interval_ms=round(time_since_last_call * 1000, 1),
                            min_interval_ms=MIN_API_INTERVAL * 1000)
                return
        
        # ← 直接赋值，不是追加
        self.session.accumulated_content = content
        
        state = self._get_state()
        state.card_id = self.session.card_id
        state.element_id = self.session.element_id
        
        # 更新防抖状态
        self._last_sent_content = content
        self._last_api_call_time = current_time
        
        logger.debug("Setting card content",
                    card_id=self.session.card_id,
                    content_length=len(content),
                    force=force,
                    skipped_count=getattr(self, '_debounce_skip_count', 0))
        
        await self.block_streaming_manager.update_card_content(
            state, 
            self.session.accumulated_content
        )
    
    def get_session(self) -> Optional[CardSession]:
        """获取当前会话"""
        return self.session
    
    async def cleanup(self):
        """清理会话"""
        async with self._lock:
            if self.session:
                logger.info("Card session cleaned up",
                           card_id=self.session.card_id)
                self.session = None


# 全局管理器实例（支持多机器人，key 包含 app_id）
_card_status_managers: dict[str, CardStatusManager] = {}


async def get_card_status_manager(
    open_id: str,
    chat_id: str,
    app_id: str = "",
    feishu_client: FeishuClient = None,
    block_streaming_manager: BlockStreamingManager = None,
) -> CardStatusManager:
    """
    获取卡片状态管理器
    
    Args:
        open_id: 用户 open_id
        chat_id: 会话 chat_id
        app_id: 机器人应用 ID（支持多机器人）
        feishu_client: 飞书客户端（可选）
        block_streaming_manager: 流式管理器（可选）
    
    Returns:
        CardStatusManager 实例
    """
    # Key 包含 app_id，支持多机器人隔离
    key = f"{app_id}:{open_id}:{chat_id}"
    
    if key not in _card_status_managers:
        # 获取依赖
        if not feishu_client:
            feishu_client = await get_feishu_client(app_id) if app_id else await get_feishu_client()
        
        if not block_streaming_manager:
            block_streaming_manager = await get_block_streaming_manager(
                feishu_client,
                app_id=app_id,
            )
        
        _card_status_managers[key] = CardStatusManager(
            block_streaming_manager=block_streaming_manager,
            feishu_client=feishu_client,
        )
    
    return _card_status_managers[key]


async def cleanup_card_status_manager(open_id: str, chat_id: str, app_id: str = ""):
    """清理卡片状态管理器"""
    key = f"{app_id}:{open_id}:{chat_id}"
    if key in _card_status_managers:
        await _card_status_managers[key].cleanup()
        del _card_status_managers[key]


async def cleanup_all_card_status_managers():
    """清理所有卡片状态管理器"""
    for key, manager in list(_card_status_managers.items()):
        await manager.cleanup()
    _card_status_managers.clear()


# 导出 CardStatus 和 STATUS_TEXTS 供外部使用
__all__ = [
    "CardStatus",
    "STATUS_TEXTS",
    "CardStatusManager",
    "get_card_status_manager",
    "cleanup_card_status_manager",
    "cleanup_all_card_status_managers",
]
