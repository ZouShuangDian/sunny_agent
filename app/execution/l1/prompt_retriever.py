"""
L1 Prompt 检索服务：基于 intent_primary 标签直接匹配

检索流程：
1. intent_primary → PromptCache 标签查询（内存缓存，0ms）
2. 标签未命中 → 返回 default prompt
3. PG 不可用 → 硬编码兜底 prompt

Milvus 向量检索保留基础设施，待 L3/RAG 阶段使用。
"""

import structlog

from app.services.prompt_service import prompt_service

log = structlog.get_logger()


class PromptRetriever:
    """L1 Prompt 检索（标签直查）"""

    async def retrieve(self, intent_primary: str) -> str:
        """
        根据 intent_primary 获取最匹配的 L1 Prompt。

        Args:
            intent_primary: 意图引擎输出的主意图标签（如 "writing"）

        Returns:
            匹配到的 Prompt 正文，或默认 Prompt
        """
        return await prompt_service.get_prompt(intent_primary)
