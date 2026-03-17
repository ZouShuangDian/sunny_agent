"""
深度研究执行器 — 封装 Google Deep Research API 调用。

职责：
- 创建流式 interaction（stream=True + thinking_summaries）
- 解析 Google API 事件（interaction.start / content.delta / interaction.complete）
- 通过回调函数推送进度（与 Redis 推送逻辑解耦）
- 拼接最终报告文本

可替换性：后续切自研 Agent 或其他 API（Perplexity 等），
只需实现相同的 execute() 接口签名，task_executor.py 无需改动。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# 进度回调类型：async def callback(event_type: str, data: dict) -> None
ProgressCallback = Callable[[str, dict], Awaitable[None]]


@dataclass
class DeepResearchResult:
    """深度研究执行结果"""
    content: str                        # 完整报告文本
    interaction_id: str | None = None   # Google interaction ID
    sources: list[dict] = field(default_factory=list)  # 预留：引用来源


class DeepResearchExecutor:
    """
    Google Deep Research API 执行器。

    使用方式：
        executor = DeepResearchExecutor()
        result = await executor.execute(query="...", on_progress=push_event)

    事件推送：
        on_progress("progress", {"stage": "正在思考", "delta_type": "thought_summary", ...})
        on_progress("progress", {"stage": "正在撰写", "delta_type": "text", ...})
        on_progress("progress", {"stage": "研究完成", "delta_type": "interaction.complete"})
    """

    def __init__(
        self,
        agent: str = "deep-research-pro-preview-12-2025",
        timeout: int = 3600,
    ):
        self.agent = agent
        self.timeout = timeout  # 最长等待时间（秒），Google 文档上限 60 分钟

    async def execute(
        self,
        query: str,
        on_progress: ProgressCallback | None = None,
    ) -> DeepResearchResult:
        """
        执行深度研究，返回结果。

        Args:
            query: 研究主题描述（LLM 加工后的完整描述）
            on_progress: 可选的进度回调函数，签名 async (event_type, data) -> None

        Returns:
            DeepResearchResult 包含完整报告文本和元信息

        Raises:
            TimeoutError: 超过 self.timeout 无新事件
            Exception: Google API 错误（认证失败、配额耗尽等）
        """
        from google import genai

        # Google genai SDK stream 是同步迭代器，用 Queue + executor 桥接到 async
        progress_queue: asyncio.Queue = asyncio.Queue()
        client = genai.Client()
        text_chunks: list[str] = []
        interaction_id: str | None = None

        async def _notify(event_type: str, data: dict) -> None:
            """安全调用回调，异常不影响主流程"""
            if on_progress is None:
                return
            try:
                await on_progress(event_type, data)
            except Exception as e:
                log.warning("进度回调异常", error=str(e), event_type=event_type)

        def _run_stream():
            """同步线程：创建流式 interaction，通过 Queue 实时回传事件"""
            stream = client.interactions.create(
                input=query,
                agent=self.agent,
                background=True,
                stream=True,
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": "auto",
                },
            )

            _interaction_id = None

            for chunk in stream:
                if chunk.event_type == "interaction.start":
                    _interaction_id = chunk.interaction.id
                    progress_queue.put_nowait({
                        "_type": "interaction.start",
                        "interaction_id": _interaction_id,
                    })

                elif chunk.event_type == "content.delta":
                    delta_type = getattr(chunk.delta, "type", None)

                    if delta_type == "text":
                        text = getattr(chunk.delta, "text", "") or ""
                        if text:
                            progress_queue.put_nowait({
                                "_type": "content.delta",
                                "delta_type": "text",
                                "content": text,
                            })

                    elif delta_type == "thought_summary":
                        thought_text = ""
                        if hasattr(chunk.delta, "content") and hasattr(chunk.delta.content, "text"):
                            thought_text = chunk.delta.content.text or ""
                        progress_queue.put_nowait({
                            "_type": "content.delta",
                            "delta_type": "thought_summary",
                            "content": thought_text,
                        })

                elif chunk.event_type == "interaction.complete":
                    progress_queue.put_nowait({"_type": "interaction.complete"})

            # 哨兵：stream 结束
            progress_queue.put_nowait(None)
            return _interaction_id

        # 并发：线程跑 Google API stream，协程消费 Queue 实时推送
        loop = asyncio.get_event_loop()
        stream_future = loop.run_in_executor(None, _run_stream)

        while True:
            item = await asyncio.wait_for(progress_queue.get(), timeout=self.timeout)
            if item is None:
                break

            event_type = item.get("_type", "")

            if event_type == "interaction.start":
                interaction_id = item.get("interaction_id")
                await _notify("progress", {
                    "stage": "研究已启动",
                    "delta_type": "interaction.start",
                    "interaction_id": interaction_id,
                })

            elif event_type == "content.delta":
                delta_type = item.get("delta_type", "text")
                content = item.get("content", "")

                if delta_type == "text":
                    text_chunks.append(content)
                    await _notify("progress", {
                        "stage": "正在撰写",
                        "delta_type": "text",
                        "content": content[:200],
                    })
                elif delta_type == "thought_summary":
                    await _notify("progress", {
                        "stage": "正在思考",
                        "delta_type": "thought_summary",
                        "content": content[:200],
                    })

            elif event_type == "interaction.complete":
                await _notify("progress", {
                    "stage": "研究完成",
                    "delta_type": "interaction.complete",
                })

        # 等待线程结束
        thread_result = await stream_future
        if interaction_id is None:
            interaction_id = thread_result

        # 拼接完整报告
        final_text = "".join(text_chunks)
        if not final_text:
            log.warning("流式未收集到正文", interaction_id=interaction_id)
            final_text = "深度研究完成，但未能获取到报告内容。"

        return DeepResearchResult(
            content=final_text,
            interaction_id=interaction_id,
        )
