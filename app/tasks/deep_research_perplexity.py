"""
深度研究执行器 — Perplexity Agent API（preset="deep-research"）

使用 Perplexity 新版 Agent API（/v1/agent），将原生事件转换为标准 stage 格式输出。

标准 stage：
- started       研究创建
- searching     正在搜索（含 thought、queries、round）
- search_done   搜索完成（含 results 详情、count、round）
- reading       正在阅读 URL（含 thought、urls）
- read_done     阅读完成（含 contents 详情、count）
- writing       正文增量
- error         研究失败

executor 负责原生事件 → 标准格式转换，task_executor 只管推送。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
import structlog

log = structlog.get_logger()

# 回调签名：async def callback(data: dict) -> None
ProgressCallback = Callable[[dict], Awaitable[None]]


@dataclass
class DeepResearchResult:
    """深度研究执行结果"""
    content: str
    interaction_id: str | None = None
    sources: list[dict] = field(default_factory=list)


class DeepResearchExecutor:
    """
    Perplexity Agent API 执行器（preset="deep-research"，流式模式）。

    内部完成 Perplexity 原生事件 → 标准 stage + detail 格式的转换，
    通过 on_progress 回调输出标准化事件。
    """

    def __init__(
        self,
        preset: str = "deep-research",
        max_steps: int = 10,
        max_output_tokens: int = 8192,
    ):
        self.preset = preset
        self.max_steps = max_steps
        self.max_output_tokens = max_output_tokens
        self.api_url = "https://api.perplexity.ai/v1/agent"

    async def execute(
        self,
        query: str,
        on_progress: ProgressCallback | None = None,
    ) -> DeepResearchResult:
        """执行深度研究，流式返回标准化事件。"""
        from app.config import get_settings
        api_key = get_settings().PERPLEXITY_API_KEY
        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY 未配置，请在 .env 中设置")

        async def _notify(data: dict) -> None:
            if on_progress is None:
                return
            try:
                await on_progress(data)
            except Exception as e:
                log.warning("进度回调异常", error=str(e))

        log.info("Perplexity Deep Research 开始", preset=self.preset)

        payload = {
            "input": query,
            "preset": self.preset,
            "stream": True,
            "max_steps": self.max_steps,
            "max_output_tokens": self.max_output_tokens,
            "language_preference": "zh",
            "instructions": (
                "你是一位专业的深度研究分析师。"
                "请对用户提出的主题进行全面、深入的研究分析，"
                "生成结构化的研究报告，包含数据、引用来源和专业见解。"
                "报告使用中文撰写，使用 Markdown 格式。"
            ),
        }

        text_chunks: list[str] = []
        sources: list[dict] = []
        response_id: str | None = None
        search_round = 0
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as http:
                    async with http.stream(
                        "POST",
                        self.api_url,
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    ) as resp:
                        if resp.status_code != 200:
                            body = await resp.aread()
                            raise RuntimeError(f"Perplexity API {resp.status_code}: {body.decode()[:500]}")

                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue

                            raw = line[6:]
                            if raw == "[DONE]":
                                break

                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            evt_type = event.get("type", "")

                            # ── Perplexity 原生事件 → 标准 stage + detail ──

                            if evt_type == "response.created":
                                response_id = event.get("response", {}).get("id")
                                model = event.get("response", {}).get("model")
                                await _notify({
                                    "stage": "started",
                                    "detail": {
                                        "message": "正在启动深度研究...",
                                        "response_id": response_id,
                                        "model": model,
                                    },
                                })

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

                            elif evt_type == "response.reasoning.search_results":
                                results = event.get("results", [])
                                result_items = [
                                    {
                                        "url": r.get("url", ""),
                                        "title": r.get("title", ""),
                                        "snippet": r.get("snippet", "")[:200],
                                    }
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

                            elif evt_type == "response.reasoning.fetch_url_queries":
                                await _notify({
                                    "stage": "reading",
                                    "detail": {
                                        "thought": event.get("thought", ""),
                                        "urls": event.get("urls", []),
                                    },
                                })

                            elif evt_type == "response.reasoning.fetch_url_results":
                                contents = event.get("contents", [])
                                content_items = [
                                    {
                                        "url": c.get("url", ""),
                                        "title": c.get("title", ""),
                                        "snippet": c.get("snippet", "")[:200],
                                    }
                                    for c in contents
                                ]
                                await _notify({
                                    "stage": "read_done",
                                    "detail": {
                                        "contents": content_items,
                                        "count": len(contents),
                                    },
                                })

                            elif evt_type == "response.output_text.delta":
                                delta = event.get("delta", "")
                                if delta:
                                    text_chunks.append(delta)
                                    await _notify({
                                        "stage": "writing",
                                        "detail": {"content": delta},
                                    })

                            elif evt_type == "response.failed":
                                error = event.get("error", {})
                                await _notify({
                                    "stage": "error",
                                    "detail": {"message": error.get("message", "研究失败")},
                                })
                                raise RuntimeError(
                                    f"Perplexity 研究失败: {error.get('message', '未知错误')}"
                                )

                            # response.in_progress / output_item.added / output_item.done
                            # / output_text.done / response.completed 等不需要推送给前端

                break  # stream 正常结束

            except Exception as e:
                is_connection_error = "RemoteProtocolError" in type(e).__name__ or "peer closed" in str(e)
                collected = "".join(text_chunks)

                if is_connection_error and attempt < max_retries:
                    if len(collected) > 1000:
                        log.warning(
                            "Perplexity stream 断连，使用已有结果",
                            attempt=attempt, collected_len=len(collected),
                        )
                        break

                    log.warning("Perplexity stream 断连，重试", attempt=attempt, error=str(e))
                    await asyncio.sleep(2 ** attempt)
                    text_chunks.clear()
                    sources.clear()
                    search_round = 0
                    continue

                log.error("Perplexity Deep Research 失败", error=str(e), exc_info=True)
                raise

        final_text = "".join(text_chunks)
        if not final_text:
            log.warning("Perplexity 返回空内容")
            final_text = "深度研究已完成，但未能获取到报告内容。"

        log.info("Perplexity Deep Research 完成",
                 content_len=len(final_text), sources=len(sources),
                 search_rounds=search_round)

        return DeepResearchResult(
            content=final_text,
            interaction_id=response_id,
            sources=sources,
        )
