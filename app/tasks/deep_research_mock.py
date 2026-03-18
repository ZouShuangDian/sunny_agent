"""
深度研究执行器 — Mock 模式（开发调试用）

从录制的 Perplexity 原始事件文件中回放，经过与真实 executor 相同的映射逻辑
输出标准 stage + detail 格式。零 token 消耗，前端体验接近真实 API。

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

# 录制的 Perplexity 原始事件文件
_RAW_EVENTS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "perplexity_raw_events_20260318_202050.json"
)


@dataclass
class DeepResearchResult:
    """深度研究执行结果"""
    content: str
    interaction_id: str | None = None
    sources: list[dict] = field(default_factory=list)


class DeepResearchExecutor:
    """Mock 执行器：回放原始事件，经标准映射后输出。"""

    def __init__(self, **kwargs):
        pass

    async def execute(
        self,
        query: str,
        on_progress: ProgressCallback | None = None,
    ) -> DeepResearchResult:
        log.info("Mock Deep Research 开始", query=query[:50])

        if not os.path.exists(_RAW_EVENTS_FILE):
            raise FileNotFoundError(f"Mock 原始事件文件不存在: {_RAW_EVENTS_FILE}")

        with open(_RAW_EVENTS_FILE, "r", encoding="utf-8") as f:
            recorded = json.load(f)

        raw_events = recorded.get("events", [])
        log.info("Mock Deep Research 加载原始事件", total=len(raw_events))

        text_chunks: list[str] = []
        sources: list[dict] = []
        response_id: str | None = None
        search_round = 0
        writing_count = 0

        async def _notify(data: dict) -> None:
            if on_progress:
                await on_progress(data)

        for entry in raw_events:
            event = entry.get("raw_event", {})
            evt_type = event.get("type", "")

            # 与 deep_research_perplexity.py 完全相同的映射逻辑

            if evt_type == "response.created":
                response_id = event.get("response", {}).get("id")
                await _notify({
                    "stage": "started",
                    "detail": {
                        "message": "正在启动深度研究...",
                        "response_id": response_id,
                        "model": event.get("response", {}).get("model"),
                    },
                })
                await asyncio.sleep(0.5)

            elif evt_type == "response.reasoning.search_queries":
                search_round += 1
                await _notify({
                    "stage": "searching",
                    "detail": {
                        "thought": event.get("thought", ""),
                        "queries": event.get("queries", []),
                        "round": search_round,
                    },
                })
                await asyncio.sleep(0.3)

            elif evt_type == "response.reasoning.search_results":
                results = event.get("results", [])
                result_items = [
                    {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")[:200]}
                    for r in results
                ]
                sources.extend(result_items)
                await _notify({
                    "stage": "search_done",
                    "detail": {
                        "results": result_items,
                        "count": len(results),
                        "round": search_round,
                    },
                })
                await asyncio.sleep(0.3)

            elif evt_type == "response.reasoning.fetch_url_queries":
                await _notify({
                    "stage": "reading",
                    "detail": {
                        "thought": event.get("thought", ""),
                        "urls": event.get("urls", []),
                    },
                })
                await asyncio.sleep(0.3)

            elif evt_type == "response.reasoning.fetch_url_results":
                contents = event.get("contents", [])
                await _notify({
                    "stage": "read_done",
                    "detail": {
                        "contents": [
                            {"url": c.get("url", ""), "title": c.get("title", ""), "snippet": c.get("snippet", "")[:200]}
                            for c in contents
                        ],
                        "count": len(contents),
                    },
                })
                await asyncio.sleep(0.3)

            elif evt_type == "response.output_text.delta":
                delta = event.get("delta", "")
                if delta:
                    text_chunks.append(delta)
                    writing_count += 1
                    # 每 10 个 delta 推送一次
                    if writing_count % 10 == 0:
                        await _notify({
                            "stage": "writing",
                            "detail": {"content": delta},
                        })
                        await asyncio.sleep(0.02)

        final_text = "".join(text_chunks)
        log.info("Mock Deep Research 完成",
                 content_len=len(final_text), sources=len(sources), search_rounds=search_round)

        return DeepResearchResult(
            content=final_text,
            interaction_id=response_id or "mock-id",
            sources=sources,
        )
