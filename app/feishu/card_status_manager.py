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
    CardStatus.THINKING: "⏳ 思考中...",
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
    open_id: str = ""
    chat_id: str = ""
    receive_id: str = ""
    receive_id_type: str = "open_id"
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
    
    async def start_session(
        self,
        open_id: str,
        chat_id: str,
        receive_id: str,
        receive_id_type: str = "open_id",
        title: str = None,
    ) -> CardSession:
        """
        开始卡片会话，创建并发送初始卡片
        
        Args:
            open_id: 用户 open_id
            chat_id: 会话 chat_id
            receive_id: 接收者 ID
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
                # 关闭流式卡片
                await self.block_streaming_manager.close_streaming(
                    open_id=self.session.open_id,
                    chat_id=self.session.chat_id,
                    final_text=STATUS_TEXTS[CardStatus.COMPLETED],
                )
                
                # 发送最终答案作为普通消息
                if send_as_message and final_answer:
                    await self.block_streaming_manager.send_message(
                        receive_id=self.session.receive_id,
                        content=final_answer,
                        msg_type="text",
                        receive_id_type=self.session.receive_id_type,
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
        return self.block_streaming_manager._get_or_create_state(
            self.session.open_id,
            self.session.chat_id,
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


# 全局管理器实例
_card_status_managers: dict[str, CardStatusManager] = {}


async def get_card_status_manager(
    open_id: str,
    chat_id: str,
    feishu_client: FeishuClient = None,
    block_streaming_manager: BlockStreamingManager = None,
) -> CardStatusManager:
    """
    获取卡片状态管理器
    
    Args:
        open_id: 用户 open_id
        chat_id: 会话 chat_id
        feishu_client: 飞书客户端（可选）
        block_streaming_manager: 流式管理器（可选）
    
    Returns:
        CardStatusManager 实例
    """
    key = f"{open_id}:{chat_id}"
    
    if key not in _card_status_managers:
        # 获取依赖
        if not feishu_client:
            feishu_client = await get_feishu_client()
        
        if not block_streaming_manager:
            block_streaming_manager = await get_block_streaming_manager(feishu_client)
        
        _card_status_managers[key] = CardStatusManager(
            block_streaming_manager=block_streaming_manager,
            feishu_client=feishu_client,
        )
    
    return _card_status_managers[key]


async def cleanup_card_status_manager(open_id: str, chat_id: str):
    """清理卡片状态管理器"""
    key = f"{open_id}:{chat_id}"
    if key in _card_status_managers:
        await _card_status_managers[key].cleanup()
        del _card_status_managers[key]


async def cleanup_all_card_status_managers():
    """清理所有卡片状态管理器"""
    for key, manager in list(_card_status_managers.items()):
        await manager.cleanup()
    _card_status_managers.clear()
