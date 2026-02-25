"""
LLM 调用次数追踪器

追踪 L3 ReAct 循环的 LLM 调用次数，与 L3Config 配合判断是否需要熔断。

说明：
- 仅保留调用次数控制，防止 ReAct 死循环
- Token 计数已移除：公司内部模型无需按量计费，且 token 限制由 max_iterations 间接控制
"""

from app.execution.l3.schemas import L3Config


class TokenBudget:
    """LLM 调用次数追踪器"""

    def __init__(self, config: L3Config):
        self.config = config
        self.llm_call_count = 0

    def add_usage(self, usage: dict) -> None:
        """记录一次 LLM 调用（usage 参数保留兼容性，当前不使用）"""
        self.llm_call_count += 1

    def is_exhausted(self) -> bool:
        """是否已超出 LLM 调用次数上限"""
        return self.llm_call_count >= self.config.max_llm_calls

    def to_dict(self) -> dict:
        """序列化为 dict（写入审计日志 / 返回前端）"""
        return {
            "llm_calls": self.llm_call_count,
        }
