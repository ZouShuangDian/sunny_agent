"""
聊天记录模型：ChatSession + ChatMessage

PG 冷存储，永久保存聊天历史。与 Redis WorkingMemory 互补：
- Redis：热存储，活跃会话，30min TTL
- PG：冷存储，全量历史，永久保存

设计说明：
- 只持久化 user + assistant 消息（Q6 裁决：tool/system 是执行中间态）
- ChatMessage 是 Message schema 的超集：额外包含 tool_calls JSONB + reasoning_trace JSONB
- tool_calls（W7）：L1+L3 通用，挂载在 Message 模型上，Redis + PG 双写
- reasoning_trace（W6）：L3 独有，不经过 Message，仅 PG 存储
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

settings = get_settings()
_schema = settings.DB_SCHEMA


class ChatSession(Base):
    """聊天会话记录"""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="会话唯一ID"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.users.id"),
        index=True,
        comment="关联用户",
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.projects.id"),
        nullable=True,
        comment="关联项目",
    )
    title: Mapped[str | None] = mapped_column(
        String(200), comment="会话标题（首条消息截取前50字）"
    )
    turn_count: Mapped[int] = mapped_column(default=0, comment="对话轮次")
    status: Mapped[str] = mapped_column(
        String(20), default="active", comment="状态: active / archived"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="最后活跃时间"
    )

    # 反向关联
    project: Mapped["Project"] = relationship("Project", back_populates="sessions")


class ChatMessage(Base):
    """聊天消息记录"""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{_schema}.chat_sessions.session_id"),
        index=True,
        comment="所属会话",
    )
    message_id: Mapped[str] = mapped_column(
        String(64), unique=True, comment="消息唯一ID（防重）"
    )
    role: Mapped[str] = mapped_column(
        String(20), comment="角色: user / assistant"
    )
    content: Mapped[str] = mapped_column(Text, comment="消息内容")

    # assistant 消息元数据
    intent_primary: Mapped[str | None] = mapped_column(
        String(50), comment="主意图"
    )
    route: Mapped[str | None] = mapped_column(
        String(30), comment="执行路由（统一为 deep_l3）"
    )
    model: Mapped[str | None] = mapped_column(
        String(100), comment="使用的 LLM 模型"
    )

    # 工具调用记录（L1 + L3 通用，W7 新增）
    # 存储 [ToolCall.model_dump()] 列表，含 arguments + result + status
    tool_calls: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="工具调用记录"
    )

    # L3 推理轨迹（仅 L3 assistant 消息有值，W6 新增）
    # 存储 ReasoningTrace.to_dict() 完整结果
    reasoning_trace: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="L3 推理轨迹"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # compaction 标记（Level 2 摘要 genesis block）
    is_compaction: Mapped[bool] = mapped_column(
        default=False, server_default="false",
        comment="是否为 Level 2 摘要节点，加载历史时遇到此标记即停止往前读取",
    )


class L3Step(Base):
    """
    L3 ReAct 中间步骤记录。

    每次 L3 执行产生 N 个步骤（assistant tool_calls + tool results），
    全部写入此表，支持：
    - Level 1 剪枝：按 token 估算标记旧步骤为 compacted
    - 跨轮 context 还原：下轮执行时加载上轮步骤
    """

    __tablename__ = "l3_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(
        String(64), index=True, comment="所属会话 ID"
    )
    message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="关联的 assistant 最终消息 ID（写入后回填）",
    )
    step_index: Mapped[int] = mapped_column(
        comment="步骤序号（0-based，同一 message_id 内）"
    )
    role: Mapped[str] = mapped_column(
        String(20), comment="角色: assistant | tool"
    )
    content: Mapped[str] = mapped_column(
        Text, server_default="", comment="消息内容"
    )
    tool_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="工具名称（tool 消息专用）"
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        comment="工具调用 ID（tool 消息专用，关联 assistant tool_calls）",
    )
    compacted: Mapped[bool] = mapped_column(
        default=False, server_default="false",
        comment="Level 1 剪枝标记：True 表示内容已被替换为占位符",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
