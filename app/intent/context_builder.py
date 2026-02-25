"""
M03-3 Context Builder：按需组装 LLM 调用所需的上下文

W2 修正：拆分为 build() + enrich() 两个方法。
- build()：基础上下文（一次 Redis 读取），用于意图分析
- enrich()：增量加载扩展上下文（零次 Redis 读取），复用 build() 已加载的数据

码表实体解析已下沉至 ContextStrategy 按需执行。
"""

from dataclasses import dataclass, field

import structlog

from app.intent.context_strategy import ContextStrategy, ExtendedContext
from app.memory.schemas import ConversationHistory, LastIntent
from app.memory.working_memory import WorkingMemory
from app.security.auth import AuthenticatedUser

log = structlog.get_logger()


@dataclass
class AssembledContext:
    """组装完成的 LLM 上下文"""

    system_prompt: str  # 系统角色描述
    last_intent: LastIntent | None = None  # 上一轮意图
    history: ConversationHistory = field(default_factory=ConversationHistory)  # 对话历史
    user_profile: dict = field(default_factory=dict)  # 用户信息
    metadata: dict = field(default_factory=dict)  # 扩展信息


# ── 系统 Prompt 模板 ──

SYSTEM_PROMPT_TEMPLATE = """你是 Agent Sunny，一个制造业智能助手。你的任务是理解用户意图，提取关键信息，并给出结构化的分析结果。

当前用户: {usernumb}，姓名: {username}，角色: {role}，部门: {department}
数据权限范围: {data_scope}

{history_section}
{knowledge_section}
请分析用户的意图，严格按照指定的 JSON 格式输出结果。"""


class ContextBuilder:
    """按需组装 LLM 上下文（支持策略选择）"""

    def __init__(
        self,
        memory: WorkingMemory,
        strategies: dict[str, ContextStrategy] | None = None,
    ):
        self.memory = memory
        # W4：strategies 在应用启动时注入，所有请求共享（无状态单例）
        self._strategies = strategies or {}

    async def build(
        self,
        user_input: str,
        user: AuthenticatedUser,
        session_id: str,
    ) -> AssembledContext:
        """阶段 1：基础上下文（history + user_profile），用于意图分析。仅一次 Redis 读取。"""
        # 1. 加载工作记忆
        history = await self.memory.get_history(session_id)
        last_intent = await self.memory.get_last_intent(session_id)

        # 2. 构造用户信息
        user_profile = {
            "usernumb": user.usernumb,
            "username": user.username,
            "role": user.role,
            "department": user.department,
            "data_scope": user.data_scope,
        }

        # 3. 组装系统 Prompt（阶段 1 无扩展知识）
        history_section = self._format_history(history)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            usernumb=user.usernumb,
            username=user.username,
            role=user.role,
            department=user.department or "未指定",
            data_scope=user.data_scope or "全部",
            history_section=history_section,
            knowledge_section="",
        )

        return AssembledContext(
            system_prompt=system_prompt,
            last_intent=last_intent,
            history=history,
            user_profile=user_profile,
        )

    async def enrich(
        self,
        base_context: AssembledContext,
        user_input: str,
        session_id: str,
        intent_hint: str,
    ) -> AssembledContext:
        """
        阶段 2：在基础上下文上追加扩展信息（W2 修正：增量加载，不重复读 Redis）。
        复用阶段 1 已加载的 history / last_intent / user_profile，仅加载 ExtendedContext。
        """
        extended = ExtendedContext()
        if intent_hint in self._strategies:
            try:
                extended = await self._strategies[intent_hint].load(user_input, session_id)
            except Exception as e:
                log.warning(
                    "扩展上下文加载失败，降级为基础上下文",
                    intent_hint=intent_hint,
                    error=str(e),
                )

        # 无扩展内容 → 直接返回原上下文
        if (
            not extended.codebook_mappings
            and not extended.knowledge_snippets
            and not extended.similar_histories
        ):
            return base_context

        # 重建 system_prompt（追加 knowledge_section）
        history_section = self._format_history(base_context.history)
        knowledge_section = self._format_knowledge(extended)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            usernumb=base_context.user_profile.get("usernumb", ""),
            username=base_context.user_profile.get("username", ""),
            role=base_context.user_profile.get("role", ""),
            department=base_context.user_profile.get("department", "未指定"),
            data_scope=base_context.user_profile.get("data_scope", "全部"),
            history_section=history_section,
            knowledge_section=knowledge_section,
        )

        return AssembledContext(
            system_prompt=system_prompt,
            last_intent=base_context.last_intent,
            history=base_context.history,  # 复用
            user_profile=base_context.user_profile,  # 复用
            metadata={
                "extended_context": extended,
                "intent_hint": intent_hint,
            },
        )

    def _format_history(self, history: ConversationHistory) -> str:
        """格式化对话历史（只取最近几轮）"""
        recent = [
            m for m in history.messages
            if m.role in ("user", "assistant")
        ][-10:]  # 最多展示最近 5 轮（10 条消息）

        if not recent:
            return ""

        lines = ["对话历史:"]
        for m in recent:
            role_label = "用户" if m.role == "user" else "助手"
            lines.append(f"  {role_label}: {m.content}")

        return "\n".join(lines)

    def _format_knowledge(self, extended: ExtendedContext) -> str:
        """格式化扩展上下文（知识库 + 码表映射）"""
        sections = []

        if extended.codebook_mappings:
            mappings_text = "\n".join(
                f"  {k} → {v}" for k, v in extended.codebook_mappings.items()
            )
            sections.append(f"术语标准化映射:\n{mappings_text}")

        if extended.knowledge_snippets:
            snippets_text = "\n---\n".join(extended.knowledge_snippets[:5])
            sections.append(f"相关知识:\n{snippets_text}")

        if extended.similar_histories:
            history_text = "\n".join(
                f"  Q: {h.get('question', '')}\n  A: {h.get('answer', '')[:200]}"
                for h in extended.similar_histories[:3]
            )
            sections.append(f"相似历史问答:\n{history_text}")

        return "\n\n".join(sections) if sections else ""
