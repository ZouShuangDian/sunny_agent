"""
聊天记录持久化服务（PG 冷存储）

职责：
- 写入：每条 user/assistant 消息异步写入 PG（write-behind，不阻塞主流程）
- 读取：Redis miss 时从 PG 回源加载会话历史
- L3 中间步骤：save_l3_steps / load_l3_steps（支持 Level 1 剪枝标记）
- compaction 节点：load_history 倒序扫描，遇到 is_compaction=True 即停止

设计要点：
- tool_calls（W7）：从 msg.tool_calls 直接读取，挂载在 Message 模型上
- reasoning_trace（W6）：独立参数传入，不经过 Message 模型
- 写入失败静默降级，不影响用户对话
"""

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from app.config import get_settings
from app.db.models.chat import ChatMessage, ChatSession, L3Step as L3StepModel
from app.memory.schemas import ConversationHistory, L3Step, Message, ToolCall

log = structlog.get_logger()
settings = get_settings()


class ChatPersistence:
    """聊天记录持久化服务（PG 冷存储）"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # ── 写入 ──

    async def ensure_session(
        self,
        session_id: str,
        user_id: str,
        first_message: str | None = None,
        project_id: uuid.UUID | None = None,
        *,
        source: str = "chat",
    ) -> None:
        """确保 PG 中存在会话记录（幂等）

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            first_message: 第一条消息（用于生成标题）
            project_id: 项目 ID（可选，用于关联项目）
            source: 会话来源（'chat' | 'cron'），新建时写入
        """
        async with self._session_factory() as db:
            existing = await db.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            )
            existing_session = existing.scalar_one_or_none()
            if existing_session:
                # 如果会话已存在且提供了 project_id，更新项目关联
                if project_id and existing_session.project_id != project_id:
                    old_project_id = existing_session.project_id
                    existing_session.project_id = project_id
                    await db.commit()
                    
                    # 同步更新旧项目和新项目的 session_count
                    from app.db.models.project import Project
                    
                    # 旧项目计数 -1
                    if old_project_id:
                        await db.execute(
                            update(Project)
                            .where(Project.id == old_project_id)
                            .values(session_count=Project.session_count - 1)
                        )
                        log.info(
                            "减少旧项目会话计数",
                            session_id=session_id,
                            old_project_id=str(old_project_id),
                        )
                    
                    # 新项目计数 +1
                    await db.execute(
                        update(Project)
                        .where(Project.id == project_id)
                        .values(session_count=Project.session_count + 1)
                    )
                    await db.commit()
                    
                    log.info(
                        "更新会话项目关联",
                        session_id=session_id,
                        old_project_id=str(old_project_id) if old_project_id else None,
                        new_project_id=str(project_id),
                    )
                return
            
            title = (
                first_message[:50] + "..."
                if first_message and len(first_message) > 50
                else first_message
            )
            db.add(ChatSession(
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
                title=title,
                source=source,
            ))
            await db.commit()
            
            # 如果关联了项目，更新项目计数器
            if project_id:
                from app.db.models.project import Project
                await db.execute(
                    update(Project)
                    .where(Project.id == project_id)
                    .values(session_count=Project.session_count + 1)
                )
                await db.commit()

    def ensure_session_background(
        self,
        session_id: str,
        user_id: str,
        first_message: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> None:
        """异步创建 PG 会话（发后即忘）
        
        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            first_message: 第一条消息（用于生成标题）
            project_id: 项目 ID（可选，用于关联项目）
        """
        asyncio.create_task(
            self._safe_ensure_session(session_id, user_id, first_message, project_id)
        )

    async def _safe_ensure_session(
        self,
        session_id: str,
        user_id: str,
        first_message: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> None:
        try:
            await self.ensure_session(session_id, user_id, first_message, project_id)
        except Exception as e:
            log.warning("会话创建持久化失败", session_id=session_id, error=str(e))

    async def save_message(
        self,
        session_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
    ) -> None:
        """
        保存单条消息到 PG。

        Args:
            session_id: 会话 ID
            msg: Message 对象（含 tool_calls，W7 从 Message 直接读取）
            reasoning_trace: L3 推理轨迹（W6，从 ExecutionResult 提取，不走 Message）
        """
        async with self._session_factory() as db:
            db.add(ChatMessage(
                session_id=session_id,
                message_id=msg.message_id,
                role=msg.role,
                content=msg.content,
                intent_primary=msg.intent_primary,
                route=msg.route,
                model=msg.model,
                # W7：tool_calls 从 Message 模型直接读取（L1 + L3 通用）
                tool_calls=(
                    [tc.model_dump() for tc in msg.tool_calls]
                    if msg.tool_calls
                    else None
                ),
                # W6：reasoning_trace 独立参数（仅 L3 assistant）
                reasoning_trace=reasoning_trace,
                # compaction 节点标记（Level 2 摘要 genesis block）
                is_compaction=msg.is_compaction,
                created_at=datetime.fromtimestamp(msg.timestamp, tz=timezone.utc),
            ))
            # 更新会话活跃时间；assistant 消息时轮次 +1
            update_values = {"last_active_at": func.now()}
            if msg.role == "assistant":
                update_values["turn_count"] = ChatSession.turn_count + 1
            await db.execute(
                update(ChatSession)
                .where(ChatSession.session_id == session_id)
                .values(**update_values)
            )
            await db.commit()

    def save_message_background(
        self,
        session_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
    ) -> None:
        """发后即忘（与 audit_logger.log_background 同模式）"""
        asyncio.create_task(self._safe_save(session_id, msg, reasoning_trace))

    async def _safe_save(
        self,
        session_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
    ) -> None:
        """PG 写入失败静默降级，不影响主流程"""
        try:
            await self.save_message(session_id, msg, reasoning_trace)
        except Exception as e:
            log.warning("聊天记录持久化失败", session_id=session_id, error=str(e))

    # ── L3 中间步骤 ──

    async def save_l3_steps(
        self,
        session_id: str,
        message_id: str,
        steps: list[L3Step],
    ) -> None:
        """
        写入 L3 中间步骤到 l3_steps 表。

        Args:
            session_id: 所属会话 ID
            message_id: 关联的 assistant 最终消息 ID
            steps: L3Step 列表（从 ExecutionResult.l3_steps 提取）
        """
        if not steps:
            return
        async with self._session_factory() as db:
            for step in steps:
                db.add(L3StepModel(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    message_id=message_id,
                    step_index=step.step_index,
                    role=step.role,
                    content=step.content,
                    tool_name=step.tool_name,
                    tool_call_id=step.tool_call_id,
                    tool_args=step.tool_args,
                    compacted=step.compacted,
                ))
            await db.commit()

    def save_l3_steps_background(
        self,
        session_id: str,
        message_id: str,
        steps: list[L3Step],
    ) -> None:
        """异步写入 L3 中间步骤（发后即忘）"""
        asyncio.create_task(
            self._safe_save_l3_steps(session_id, message_id, steps)
        )

    async def _safe_save_l3_steps(
        self,
        session_id: str,
        message_id: str,
        steps: list[L3Step],
    ) -> None:
        try:
            await self.save_l3_steps(session_id, message_id, steps)
        except Exception as e:
            log.warning(
                "L3 步骤持久化失败",
                session_id=session_id,
                message_id=message_id,
                error=str(e),
            )

    async def load_l3_steps(
        self,
        session_id: str,
        after_created_at: datetime | None = None,
    ) -> list[L3StepModel]:
        """
        加载指定时间之后的 l3_steps 记录。

        Args:
            session_id: 会话 ID
            after_created_at: 只加载此时间点之后的步骤（None 表示全部）
        """
        async with self._session_factory() as db:
            stmt = (
                select(L3StepModel)
                .where(L3StepModel.session_id == session_id)
            )
            if after_created_at is not None:
                stmt = stmt.where(L3StepModel.created_at >= after_created_at)
            stmt = stmt.order_by(L3StepModel.created_at.asc(), L3StepModel.step_index.asc())
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def mark_steps_compacted(self, step_ids: list[uuid.UUID]) -> None:
        """
        批量将 l3_steps 标记为 compacted=True（Level 1 DB 剪枝）。

        Args:
            step_ids: 需要标记的步骤 UUID 列表
        """
        if not step_ids:
            return
        async with self._session_factory() as db:
            await db.execute(
                update(L3StepModel)
                .where(L3StepModel.id.in_(step_ids))
                .values(compacted=True)
            )
            await db.commit()

    def mark_steps_compacted_background(self, step_ids: list[uuid.UUID]) -> None:
        """异步标记（发后即忘）"""
        asyncio.create_task(self._safe_mark_compacted(step_ids))

    async def _safe_mark_compacted(self, step_ids: list[uuid.UUID]) -> None:
        try:
            await self.mark_steps_compacted(step_ids)
        except Exception as e:
            log.warning("L3 步骤 compacted 标记失败", count=len(step_ids), error=str(e))

    # ── 读取（Redis miss 时回源） ──

    async def load_history(self, session_id: str) -> ConversationHistory | None:
        """
        从 PG 加载会话历史。

        新行为：
        1. 倒序扫描 chat_messages，遇到 is_compaction=True 即停止（genesis block）
        2. 只加载 compaction 节点及之后的消息
        3. 若无 l3_steps 数据（老会话），降级为原有逻辑（兼容）
        """
        async with self._session_factory() as db:
            # 正序加载全部消息
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
            )
            rows = result.scalars().all()
            if not rows:
                return None

            # 倒序扫描，寻找 compaction 节点（genesis block）
            start_idx = 0
            for i in range(len(rows) - 1, -1, -1):
                if rows[i].is_compaction:
                    start_idx = i  # compaction 节点本身也要加载
                    break

            # 只保留 compaction 节点及之后的记录
            effective_rows = rows[start_idx:]

            # 记录 compaction 节点之后第一条消息的时间（用于加载 l3_steps）
            compaction_created_at: datetime | None = None
            if start_idx > 0:
                # 有 compaction 节点，加载其创建时间作为 l3_steps 的时间下界
                compaction_created_at = rows[start_idx].created_at

            history = ConversationHistory(max_turns=settings.WORKING_MEMORY_MAX_TURNS)

            # 检查是否有 l3_steps 数据
            steps_check = await db.execute(
                select(L3StepModel.id)
                .where(L3StepModel.session_id == session_id)
                .limit(1)
            )
            has_l3_steps = steps_check.scalar_one_or_none() is not None

            if has_l3_steps:
                # 新会话：加载 l3_steps 并按 message_id 分组
                l3_steps_rows = await self.load_l3_steps(
                    session_id,
                    after_created_at=compaction_created_at,
                )
                # 按 message_id 分组，保留步骤顺序
                steps_by_msg: dict[str, list[L3StepModel]] = {}
                for step in l3_steps_rows:
                    if step.message_id:
                        steps_by_msg.setdefault(step.message_id, []).append(step)

                # 组装历史：每条 chat_message 之后插入对应 l3_steps
                for row in effective_rows:
                    history.append(Message(
                        role=row.role,
                        content=row.content,
                        timestamp=row.created_at.timestamp(),
                        message_id=row.message_id,
                        intent_primary=row.intent_primary,
                        route=row.route,
                        model=row.model,
                        is_compaction=row.is_compaction,
                        tool_calls=(
                            [ToolCall(**tc) for tc in row.tool_calls]
                            if row.tool_calls
                            else None
                        ),
                    ))
                    # assistant 消息后插入其 l3_steps（如有）
                    if row.role == "assistant" and row.message_id in steps_by_msg:
                        for step in steps_by_msg[row.message_id]:
                            placeholder = f"[已处理] {step.tool_name or 'tool'} 输出已压缩（原始内容保留在历史记录中）"
                            history.append(Message(
                                role=step.role,
                                content=placeholder if step.compacted else step.content,
                                timestamp=row.created_at.timestamp(),
                                tool_name=step.tool_name,
                                tool_call_id=step.tool_call_id,
                            ))
            else:
                # 老会话（无 l3_steps）：降级为原有逻辑
                for row in effective_rows:
                    history.append(Message(
                        role=row.role,
                        content=row.content,
                        timestamp=row.created_at.timestamp(),
                        message_id=row.message_id,
                        intent_primary=row.intent_primary,
                        route=row.route,
                        model=row.model,
                        is_compaction=row.is_compaction,
                        tool_calls=(
                            [ToolCall(**tc) for tc in row.tool_calls]
                            if row.tool_calls
                            else None
                        ),
                    ))

            return history
