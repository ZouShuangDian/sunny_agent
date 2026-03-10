"""
LoopContext — ReAct 循环的共享上下文（纯数据载体）

- 中间件通过 ctx.messages 修改消息列表（如 Todo 注入）
- 中间件通过 ctx.collected_steps 等字段写入收集数据
- event_emitter 只在流式模式注入，中间件据此判断是否推送 SSE
- from_messages() 工厂方法供 SubAgent / Task 等外部调用方使用
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.execution.l3.event_emitter import EventEmitter
from app.execution.l3.observer import Observer
from app.execution.l3.schemas import L3Config


@dataclass
class LoopContext:
    """ReAct 循环的共享上下文，中间件通过此对象读写状态"""

    messages: list[dict]             # 当前消息列表（中间件可修改）
    observer: Observer               # 熔断 + 轨迹记录
    config: L3Config                 # 运行时配置
    tool_schemas: list[dict]         # 可用工具 schema
    step: int = 0                    # 当前步骤号
    user_goal: str | None = None     # 用户原始目标（Todo reminder 用）

    # ── 中间件可写入的共享状态 ──
    collected_steps: list[dict] = field(default_factory=list)
    last_context_usage: dict | None = None
    last_compaction_summary: str | None = None

    # ── 事件发射器（流式模式由外部注入，非流式为 None） ──
    event_emitter: EventEmitter | None = None

    @classmethod
    def from_messages(
        cls,
        messages: list[dict],
        config: L3Config,
        tool_schemas: list[dict],
    ) -> LoopContext:
        """
        从裸 messages 构建 LoopContext（SubAgent / Task 场景的便利工厂）。

        封装 Observer 创建 + start()，调用方无需关心内部细节。
        """
        observer = Observer(config)
        observer.start()
        return cls(
            messages=messages,
            observer=observer,
            config=config,
            tool_schemas=tool_schemas,
        )
