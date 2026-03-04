"""
M03-4 意图引擎：调用 LLM 做意图识别 + 复杂度分级

输出 JSON 结构，解析失败时最多重试 1 次，仍然失败返回默认结果。
JSON 解析统一委托给 M04 的 JsonRepairer，避免重复造轮子。
"""

from dataclasses import dataclass, field

import structlog

from app.guardrails.json_repairer import JsonRepairer
from app.intent.context_builder import AssembledContext
from app.llm.client import LLMClient

log = structlog.get_logger()


@dataclass
class IntentEngineResult:
    """意图引擎原始输出"""

    intent_primary: str  # 主意图
    sub_intent: str | None = None  # 子意图
    user_goal: str = ""  # 用户目标描述
    route: str = "deep_l3"  # 路由（统一为 deep_l3）
    complexity: str = "simple"  # 复杂度
    confidence: float = 0.5  # 置信度
    entity_hints: dict = field(default_factory=dict)  # 实体线索（弱类型）
    needs_clarify: bool = False  # 是否需要追问
    clarify_question: str | None = None  # 追问话术
    raw_json: str = ""  # LLM 原始 JSON 输出（调试用）


# ── 意图分析 Prompt 模板（{intent_categories} 编译时注入）──

_INTENT_PROMPT_TEMPLATE = """你是意图分析引擎。根据用户输入和上下文，输出结构化的意图分析结果。

## 意图类别（intent.primary）

以下是当前系统支持的意图类别，intent.primary **必须** 是其中之一：
{intent_categories}
如果用户的意图不属于以上任何类别，请使用 `general_qa`。

## 复杂度分级
- simple: 单一意图，直接处理
- moderate: 多个实体或需要简单计算
- complex: 需要多步推理、跨数据源对比、因果分析

## 输出格式
严格按以下 JSON 格式输出，不要添加任何额外文字：
{{
  "intent": {{
    "primary": "writing",
    "sub_intent": null,
    "user_goal": "帮用户撰写一份本周的工作周报，属于写作辅助任务"
  }},
  "complexity": "simple",
  "confidence": 0.95,
  "entity_hints": {{}},
  "needs_clarify": false,
  "clarify_question": null
}}

字段说明：
- intent.primary: 上述意图类别之一
- intent.user_goal: 用一句完整的中文描述用户的核心需求（15-50字）
- complexity: simple / moderate / complex
- confidence: 0.0 ~ 1.0
- entity_hints: 从用户输入中识别到的关键实体线索（如 product, metric, period 等），只放有把握的
- needs_clarify: 信息不足时设为 true，并在 clarify_question 中给出追问"""

# 意图分类常量（原 seed_prompts.py 中的 4 个分类）
_INTENT_CATEGORIES = """- general_qa: 通用知识问答（默认）
- writing: 写作辅助（周报、邮件、文案、翻译、润色等）
- summarize: 内容总结（文档摘要、会议纪要、要点提取等）
- translate: 翻译任务（中英互译、多语言翻译等）"""


def build_intent_prompt() -> str:
    """构建意图分析 Prompt（意图分类硬编码，不再依赖 PG）"""
    return _INTENT_PROMPT_TEMPLATE.format(intent_categories=_INTENT_CATEGORIES)


# 默认结果：当 LLM 完全无法解析时使用
DEFAULT_RESULT = IntentEngineResult(
    intent_primary="general_qa",
    user_goal="无法识别用户意图",
    route="deep_l3",
    complexity="simple",
    confidence=0.0,
)


class IntentEngine:
    """LLM 驱动的意图识别引擎"""

    MAX_RETRIES = 1  # JSON 解析失败时最多重试次数

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self._repairer = JsonRepairer()

    async def analyze(
        self,
        user_input: str,
        context: AssembledContext,
    ) -> IntentEngineResult:
        """调用 LLM 分析意图，返回结构化结果"""
        intent_prompt = build_intent_prompt()

        messages = [
            {"role": "system", "content": context.system_prompt + "\n\n" + intent_prompt},
            # 加入对话历史（如果有）
            *context.history.to_llm_messages()[-6:],  # 最近 3 轮
            {"role": "user", "content": user_input},
        ]

        for attempt in range(1 + self.MAX_RETRIES):
            try:
                response = await self.llm.chat(
                    messages=messages,
                    temperature=0.0,
                    max_tokens=1024,
                )
                result = self._parse_response(response.content)
                result.raw_json = response.content
                return result

            except (ValueError, KeyError) as e:
                log.warning(
                    "意图引擎 JSON 解析失败",
                    attempt=attempt + 1,
                    error=str(e),
                )
                if attempt < self.MAX_RETRIES:
                    # 重试时在 messages 末尾加一条提示
                    messages.append(
                        {"role": "user", "content": "请严格按 JSON 格式输出，不要添加任何额外文字。"}
                    )
                    continue

            except Exception as e:
                log.error("意图引擎调用异常", error=str(e), exc_info=True)
                break

        # 全部失败 → 返回默认结果
        log.warning("意图引擎降级为默认结果")
        return DEFAULT_RESULT

    def _parse_response(self, raw: str) -> IntentEngineResult:
        """解析 LLM 输出的 JSON，委托 JsonRepairer 处理畸形输出"""
        data = self._repairer.repair(raw)
        intent = data.get("intent", {})

        return IntentEngineResult(
            intent_primary=intent.get("primary", "general_qa"),
            sub_intent=intent.get("sub_intent"),
            user_goal=intent.get("user_goal", ""),
            route="deep_l3",  # 统一路由，不再从 LLM 输出中读取
            complexity=data.get("complexity", "simple"),
            confidence=float(data.get("confidence", 0.5)),
            entity_hints=data.get("entity_hints", {}),
            needs_clarify=data.get("needs_clarify", False),
            clarify_question=data.get("clarify_question"),
        )
