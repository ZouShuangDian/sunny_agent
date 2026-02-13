"""
Prompt 服务层：从 PG 加载模板 → 内存缓存 → 提供 intent→prompt 查询 + 动态分类列表

公共服务，同时供 IntentEngine（意图层）和 PromptRetriever（执行层）使用，
避免上游模块依赖下游模块导致的层级倒置。

设计：
- 启动时（或首次调用时）从 PG 加载所有 L1 活跃模板
- 构建 intent_tag → prompt_content 映射
- 提供 get_intent_categories() 供 IntentEngine 动态拼接提示词
- 内存缓存 + TTL 刷新（避免每次请求都查 PG）
"""

import time

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.engine import async_session
from app.db.models.template import PromptTemplate

log = structlog.get_logger()
settings = get_settings()

# 缓存刷新间隔（秒），默认 5 分钟
_CACHE_TTL = 300

# 硬编码兜底 Prompt（PG 不可用时使用）
_FALLBACK_PROMPT = (
    "你是 Agent Sunny，舜宇集团的 AI 智能助手。"
    "你乐于助人，回答专业准确，语言简洁友好。"
    "请根据用户的问题给出有帮助的回复。"
)


class PromptService:
    """Prompt 模板缓存服务（单例使用）"""

    def __init__(self):
        # intent_tag → prompt_content 映射
        self._tag_to_prompt: dict[str, str] = {}
        # intent_tag → description 映射（供 INTENT_PROMPT 使用）
        self._tag_to_desc: dict[str, str] = {}
        # 默认 prompt（is_default=True 的模板）
        self._default_prompt: str = _FALLBACK_PROMPT
        # 缓存时间戳
        self._loaded_at: float = 0
        self._loading: bool = False

    async def _load_from_pg(self):
        """从 PG 加载 L1 活跃模板"""
        if self._loading:
            return
        self._loading = True

        try:
            async with async_session() as session:
                stmt = (
                    select(PromptTemplate)
                    .where(
                        PromptTemplate.tier == "L1",
                        PromptTemplate.is_active.is_(True),
                    )
                    .order_by(PromptTemplate.sort_order.desc())
                )
                result = await session.execute(stmt)
                templates = result.scalars().all()

            tag_to_prompt: dict[str, str] = {}
            tag_to_desc: dict[str, str] = {}
            default_prompt = _FALLBACK_PROMPT

            for tpl in templates:
                # 记录默认 prompt
                if tpl.is_default:
                    default_prompt = tpl.template

                # 建立 intent_tag → prompt 映射
                for tag in (tpl.intent_tags or []):
                    tag_to_prompt[tag] = tpl.template
                    tag_to_desc[tag] = tpl.description or tpl.name

            self._tag_to_prompt = tag_to_prompt
            self._tag_to_desc = tag_to_desc
            self._default_prompt = default_prompt
            self._loaded_at = time.time()

            log.info(
                "Prompt 缓存已加载",
                intent_count=len(tag_to_prompt),
                intents=list(tag_to_prompt.keys()),
            )

        except Exception as e:
            log.warning("Prompt 缓存加载失败，使用兜底配置", error=str(e))
        finally:
            self._loading = False

    async def _ensure_loaded(self):
        """确保缓存已加载且未过期"""
        if time.time() - self._loaded_at > _CACHE_TTL:
            await self._load_from_pg()

    async def get_prompt(self, intent_primary: str) -> str:
        """
        根据 intent_primary 获取对应的 Prompt 模板。

        查找策略：
        1. intent_primary 在 tag 映射中 → 返回对应 prompt
        2. 不在 → 返回 default prompt
        """
        await self._ensure_loaded()

        prompt = self._tag_to_prompt.get(intent_primary)
        if prompt:
            log.debug("Prompt 标签命中", intent=intent_primary)
            return prompt

        log.debug("Prompt 标签未命中，使用默认", intent=intent_primary)
        return self._default_prompt

    async def get_intent_categories(self) -> list[dict[str, str]]:
        """
        获取所有 L1 意图分类列表，供 IntentEngine 动态拼接到 INTENT_PROMPT。

        返回格式: [{"tag": "writing", "description": "写作任务专用 Prompt"}]
        """
        await self._ensure_loaded()

        return [
            {"tag": tag, "description": desc}
            for tag, desc in self._tag_to_desc.items()
        ]

    async def get_valid_tags(self) -> set[str]:
        """获取所有有效的 intent_tag 集合"""
        await self._ensure_loaded()
        return set(self._tag_to_prompt.keys())


# 模块级单例
prompt_service = PromptService()
