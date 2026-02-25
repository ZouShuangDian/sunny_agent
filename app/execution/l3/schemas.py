"""
L3 引擎数据结构定义

所有组件间通过纯数据结构通信，不引入事件总线或发布订阅模式。
"""

from dataclasses import asdict, dataclass, field

from app.config import get_settings


# ── L3 运行时配置 ──


@dataclass
class L3Config:
    """L3 引擎运行时配置，从全局 Settings 加载（W1 反馈修正）"""

    max_iterations: int
    timeout_seconds: float
    max_llm_calls: int

    @classmethod
    def from_settings(cls) -> "L3Config":
        """从全局 Settings 加载，确保运维可通过环境变量 / .env 覆盖"""
        s = get_settings()
        return cls(
            max_iterations=s.L3_MAX_ITERATIONS,
            timeout_seconds=s.L3_TIMEOUT_SECONDS,
            max_llm_calls=s.L3_MAX_LLM_CALLS,
        )


# ── Thinker 输出 ──


@dataclass
class ToolCallRequest:
    """单个工具调用请求（从 LLM 响应解析而来）"""

    id: str           # tool_call_id（LLM 生成的唯一 ID）
    name: str         # 工具名称
    arguments: dict   # 已解析的参数 dict


@dataclass
class ThinkResult:
    """Thinker 单步输出"""

    thought: str                                     # LLM 的思考内容（response.content）
    tool_calls: list[ToolCallRequest] | None = None  # 要执行的工具（None = 任务完成）
    usage: dict = field(default_factory=dict)         # token 消耗
    is_done: bool = False                             # 是否为最终回答（无 tool_calls）


# ── Actor 输出 ──


@dataclass
class Observation:
    """单个工具调用的执行结果"""

    tool_name: str       # 工具名称
    tool_call_id: str    # 关联到 ToolCallRequest.id
    arguments: dict      # 调用参数
    result: str          # ToolResult JSON 字符串
    duration_ms: int     # 执行耗时
    is_sub_step: bool = False  # W3：标识 Skill 内部子步骤


@dataclass
class ActResult:
    """Actor 单步输出"""

    observations: list[Observation]  # 每个工具调用的结果
    messages: list[dict]             # 格式化后的 assistant + tool messages（追加到 LLM 上下文）


# ── ReasoningTrace（推理轨迹） ──


@dataclass
class ThinkActObserve:
    """单步推理记录"""

    step: int
    thought: str                                      # LLM 的思考内容
    actions: list[dict] | None = None                 # [{"tool": "web_search", "args": {...}}]
    observations: list[dict] | None = None            # [{"tool": "web_search", "result": "..."}]
    tokens_used: int = 0                              # 本步 token 消耗
    duration_ms: int = 0                              # 本步耗时


class ReasoningTrace:
    """
    完整推理轨迹

    记录 L3 ReAct 循环中每步的 Thought / Action / Observation，
    用于审计、调试和前端展示 Agent 思考过程。
    """

    def __init__(self) -> None:
        self.steps: list[ThinkActObserve] = []

    def add_thought(self, step: int, thought: str, tokens_used: int = 0) -> None:
        """记录一步的思考内容（在 LLM 调用后立即调用）"""
        # 查找现有步骤或创建新步骤
        existing = next((s for s in self.steps if s.step == step), None)
        if existing:
            existing.thought = thought
            existing.tokens_used = tokens_used
        else:
            self.steps.append(ThinkActObserve(
                step=step,
                thought=thought,
                tokens_used=tokens_used,
            ))

    def add_action(
        self, step: int, tool_name: str, args: dict, result: str
    ) -> None:
        """记录一次工具调用及其结果"""
        existing = next((s for s in self.steps if s.step == step), None)
        if not existing:
            existing = ThinkActObserve(step=step, thought="")
            self.steps.append(existing)

        if existing.actions is None:
            existing.actions = []
        existing.actions.append({"tool": tool_name, "args": args})

        if existing.observations is None:
            existing.observations = []
        existing.observations.append({"tool": tool_name, "result": result})

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def summarize_observations(self) -> str:
        """拼接所有 Observation 的结果文本（优雅降级用，不做额外 LLM 调用）"""
        parts: list[str] = []
        for s in self.steps:
            if s.observations:
                for obs in s.observations:
                    parts.append(f"- {obs['tool']}: {obs['result'][:500]}")
        return "\n".join(parts)

    def to_dict(self) -> list[dict]:
        """序列化为 JSON（写入 PG JSONB / 返回前端）"""
        return [asdict(s) for s in self.steps]

    def to_tool_call_records(self) -> list:
        """
        提取所有工具调用记录，转为 ToolCall 对象列表。
        用于填充 ExecutionResult.tool_calls。
        """
        from app.memory.schemas import ToolCall

        records: list[ToolCall] = []
        for s in self.steps:
            if not s.actions or not s.observations:
                continue
            for action, obs in zip(s.actions, s.observations):
                records.append(ToolCall(
                    tool_call_id=f"l3_step{s.step}_{action['tool']}",
                    tool_name=action["tool"],
                    arguments=action["args"],
                    result=obs["result"],
                    status="success",
                    duration_ms=s.duration_ms,
                ))
        return records
