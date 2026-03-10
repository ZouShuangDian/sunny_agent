"""
事件发射器：SSE 事件推送抽象

- EventEmitter Protocol：中间件通过此协议推送事件，不关心底层传输
- QueueEventEmitter：基于 asyncio.Queue 的实现（流式模式使用）
- 非流式模式：event_emitter = None，中间件内部检查后跳过
"""

from __future__ import annotations

import asyncio
from typing import Protocol


class EventEmitter(Protocol):
    """SSE 事件发射协议"""

    async def emit(self, event: str, data: dict) -> None:
        """发射一个 SSE 事件"""
        ...


class QueueEventEmitter:
    """基于 asyncio.Queue 的事件发射器（流式模式）"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def emit(self, event: str, data: dict) -> None:
        await self.queue.put({"event": event, "data": data})

    async def close(self) -> None:
        """推送哨兵值 None，通知消费者结束"""
        await self.queue.put(None)
