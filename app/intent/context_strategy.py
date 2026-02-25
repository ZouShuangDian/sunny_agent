"""
上下文加载策略（策略模式）

根据意图类型按需加载扩展上下文，减少不必要的延迟和资源消耗。

W4 约束：所有 Strategy 必须是无状态单例（Stateless Singleton），
仅持有 Service 引用，不持有请求级数据。应用启动时实例化一次，所有请求共享。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass
class ExtendedContext:
    """按需加载的扩展上下文"""

    codebook_mappings: dict[str, str] = field(default_factory=dict)  # 码表映射
    knowledge_snippets: list[str] = field(default_factory=list)  # 知识库片段
    similar_histories: list[dict] = field(default_factory=list)  # 相似历史问答
    metadata: dict[str, Any] = field(default_factory=dict)  # 策略附加信息


class ContextStrategy(ABC):
    """
    上下文加载策略（策略模式）。

    W4 约束：Strategy 必须是无状态单例（Stateless Singleton）。
    - 仅持有 Service 引用（codebook_service、knowledge_service 等）
    - 不持有任何请求级数据（session_id、user_input 等通过方法参数传入）
    - 应用启动时实例化一次，所有请求共享
    """

    @abstractmethod
    async def load(self, user_input: str, session_id: str) -> ExtendedContext:
        """按策略加载扩展上下文（请求级数据通过参数传入，不存在实例上）"""
        ...


class MinimalStrategy(ContextStrategy):
    """最小策略：greeting / general_qa / writing — 不加载额外上下文"""

    async def load(self, user_input: str, session_id: str) -> ExtendedContext:
        return ExtendedContext()


class QueryStrategy(ContextStrategy):
    """
    查询策略：query — 加载码表映射 + 知识库

    Week 9 桩实现：codebook_service / knowledge_service 传 None 时返回空。
    Phase 3 接入 Milvus + CodebookService 后，只需替换 __init__ 注入的实例。
    """

    def __init__(self, codebook_service=None, knowledge_service=None):
        self.codebook_service = codebook_service
        self.knowledge_service = knowledge_service

    async def load(self, user_input: str, session_id: str) -> ExtendedContext:
        tasks = []

        if self.codebook_service:
            tasks.append(self.codebook_service.resolve_entities(user_input))
        if self.knowledge_service:
            tasks.append(self.knowledge_service.search(user_input))

        if not tasks:
            return ExtendedContext()

        results = await asyncio.gather(*tasks, return_exceptions=True)

        codebook_mappings = {}
        knowledge_snippets = []
        idx = 0

        if self.codebook_service:
            codebook_mappings = results[idx] if not isinstance(results[idx], Exception) else {}
            idx += 1
        if self.knowledge_service:
            knowledge_snippets = results[idx] if not isinstance(results[idx], Exception) else []

        return ExtendedContext(
            codebook_mappings=codebook_mappings,
            knowledge_snippets=knowledge_snippets,
        )


class AnalysisStrategy(ContextStrategy):
    """
    分析策略：analysis — 加载码表 + 知识库 + 相似历史

    Week 9 桩实现：所有 Service 传 None 时返回空。
    Phase 3 接入完整数据源后，只需替换 __init__ 注入的实例。
    """

    def __init__(self, codebook_service=None, knowledge_service=None, history_service=None):
        self.codebook_service = codebook_service
        self.knowledge_service = knowledge_service
        self.history_service = history_service

    async def load(self, user_input: str, session_id: str) -> ExtendedContext:
        tasks = []
        task_labels = []

        if self.codebook_service:
            tasks.append(self.codebook_service.resolve_entities(user_input))
            task_labels.append("codebook")
        if self.knowledge_service:
            tasks.append(self.knowledge_service.search(user_input))
            task_labels.append("knowledge")
        if self.history_service:
            tasks.append(self.history_service.find_similar(user_input))
            task_labels.append("history")

        if not tasks:
            return ExtendedContext()

        results = await asyncio.gather(*tasks, return_exceptions=True)

        codebook_mappings = {}
        knowledge_snippets = []
        similar_histories = []

        for label, result in zip(task_labels, results):
            if isinstance(result, Exception):
                log.warning("扩展上下文加载失败", source=label, error=str(result))
                continue
            if label == "codebook":
                codebook_mappings = result
            elif label == "knowledge":
                knowledge_snippets = result
            elif label == "history":
                similar_histories = result

        return ExtendedContext(
            codebook_mappings=codebook_mappings,
            knowledge_snippets=knowledge_snippets,
            similar_histories=similar_histories,
        )


# ── 策略映射表 ──

# 意图类型 → 策略实例（W4：应用启动时在 chat.py 中实例化）
INTENT_STRATEGY_MAP: dict[str, type[ContextStrategy]] = {
    "greeting": MinimalStrategy,
    "general_qa": MinimalStrategy,
    "writing": MinimalStrategy,
    "query": QueryStrategy,
    "analysis": AnalysisStrategy,
}
