"""
Observer（观察者）：跨步骤的状态追踪和监控

职责：
- 推理轨迹记录（每步 Thought + Action + Observation）
- Token 预算追踪（累计消耗，判断是否超限）
- 熔断检查（多维度：迭代/超时/预算）
- 指标采集（未来 Prometheus）
"""

import time

import structlog

from app.execution.l3.schemas import (
    ActResult,
    L3Config,
    ReasoningTrace,
    ThinkResult,
)
from app.execution.l3.token_budget import TokenBudget

log = structlog.get_logger()


class Observer:
    """观察者：状态追踪 + 熔断判断"""

    def __init__(self, config: L3Config):
        self.config = config
        self.trace = ReasoningTrace()
        self.budget = TokenBudget(config)
        self._start_time: float = 0

    def start(self) -> None:
        """启动计时（在 ReAct 循环开始前调用）"""
        self._start_time = time.time()

    @property
    def elapsed_seconds(self) -> float:
        """已耗时（秒）"""
        return time.time() - self._start_time if self._start_time else 0

    def on_think(self, step: int, result: ThinkResult) -> None:
        """记录思考结果 + 更新 token 预算"""
        tokens_used = result.usage.get("total_tokens", 0)
        self.trace.add_thought(step, result.thought, tokens_used=tokens_used)
        self.budget.add_usage(result.usage)

        log.debug(
            "L3 思考完成",
            step=step,
            is_done=result.is_done,
            tool_calls=len(result.tool_calls) if result.tool_calls else 0,
            tokens_used=tokens_used,
        )

    def on_act(self, step: int, result: ActResult) -> None:
        """记录执行结果"""
        for obs in result.observations:
            self.trace.add_action(step, obs.tool_name, obs.arguments, obs.result)

        log.debug(
            "L3 执行完成",
            step=step,
            tools=[obs.tool_name for obs in result.observations],
        )

    def should_stop(self) -> tuple[bool, str | None]:
        """
        熔断检查，返回 (是否停止, 原因)。

        检查维度：
        - timeout: 整体超时
        - budget: token 预算或 LLM 调用次数耗尽
        """
        elapsed = self.elapsed_seconds
        if elapsed > self.config.timeout_seconds:
            log.warning(
                "L3 熔断：超时",
                elapsed=f"{elapsed:.1f}s",
                limit=f"{self.config.timeout_seconds}s",
            )
            return True, "timeout"

        if self.budget.is_exhausted():
            log.warning(
                "L3 熔断：预算耗尽",
                total_tokens=self.budget.to_dict()["total_tokens"],
                llm_calls=self.budget.llm_call_count,
            )
            return True, "budget"

        return False, None
