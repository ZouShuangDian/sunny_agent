"""
深度研究执行器 — Mock 模式（开发调试用）

直接回放录制的标准化事件（stage + detail），零 token 消耗。
所有 executor 输出统一标准格式，task_executor 只管 push。

切换方式：task_executor.py 中将 import 改为
    from app.tasks.deep_research_mock import DeepResearchExecutor
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

ProgressCallback = Callable[[dict], Awaitable[None]]

_EVENTS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "deep_research_events_20260318_111929.json"
)


@dataclass
class DeepResearchResult:
    """深度研究执行结果"""
    content: str
    interaction_id: str | None = None
    sources: list[dict] = field(default_factory=list)


class DeepResearchExecutor:
    """Mock 深度研究执行器 — 直接回放标准格式事件。"""

    def __init__(self, **kwargs):
        pass

    async def execute(
        self,
        query: str,
        on_progress: ProgressCallback | None = None,
    ) -> DeepResearchResult:
        log.info("Mock Deep Research 开始", query=query[:50])

        if not os.path.exists(_EVENTS_FILE):
            raise FileNotFoundError(f"Mock 事件文件不存在: {_EVENTS_FILE}")

        with open(_EVENTS_FILE, "r", encoding="utf-8") as f:
            recorded = json.load(f)

        events = recorded.get("events", [])
        log.info("Mock Deep Research 加载事件", total=len(events))

        text_chunks: list[str] = []
        writing_count = 0

        for event in events:
            stage = event.get("stage", "")
            detail = event.get("detail", {})

            if stage == "done":
                # done 由 task_executor 推，跳过
                continue

            if stage == "writing":
                content = detail.get("content", "")
                if content:
                    text_chunks.append(content)
                writing_count += 1
                # 每 10 个 writing 推送一次
                if writing_count % 10 == 0:
                    if on_progress:
                        await on_progress({"stage": "writing", "detail": {"content": content}})
                    await asyncio.sleep(0.05)
            else:
                # 非 writing 事件直接推送
                if on_progress:
                    await on_progress({"stage": stage, "detail": detail})
                if stage in ("started", "searching", "analyzing"):
                    await asyncio.sleep(1.0)

        final_text = "".join(text_chunks)
        log.info("Mock Deep Research 完成", content_len=len(final_text))

        return DeepResearchResult(
            content=final_text,
            interaction_id="mock-interaction-id",
            sources=[
                {"url": "https://mock-source-1.com", "title": "Mock Source 1"},
                {"url": "https://mock-source-2.com", "title": "Mock Source 2"},
            ],
        )
