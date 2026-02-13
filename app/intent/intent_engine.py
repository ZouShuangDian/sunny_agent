"""
M03-4 意图引擎：调用 LLM 做意图识别 + 复杂度分级 + 路由决策

输出 JSON 结构，解析失败时最多重试 1 次，仍然失败返回默认结果。
JSON 解析统一委托给 M04 的 JsonRepairer，避免重复造轮子。
"""

from dataclasses import dataclass, field

import structlog

from app.services.prompt_service import prompt_service
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
    route: str = "standard_l1"  # 路由
    complexity: str = "simple"  # 复杂度
    confidence: float = 0.5  # 置信度
    entity_hints: dict = field(default_factory=dict)  # 实体线索（弱类型）
    needs_clarify: bool = False  # 是否需要追问
    clarify_question: str | None = None  # 追问话术
    raw_json: str = ""  # LLM 原始 JSON 输出（调试用）


# ── 意图分析 Prompt 模板（{intent_categories} 运行时动态注入）──

_INTENT_PROMPT_TEMPLATE = """你是意图分析引擎。根据用户输入和上下文，输出结构化的意图分析结果。

## 路由决策（二选一）

根据任务的 **推理深度** 和 **步骤复杂度** 选择路由：

### standard_l1（标准执行）
- **判断标准**：任务可以在 1-3 步内完成，不需要制定计划、不需要因果分析、不需要跨多个数据源对比。
- **涵盖场景**：
  - 直接回答：问候、闲聊、通用知识问答、拒绝超出能力范围的请求
  - 内容生成：写作、翻译、总结、润色
  - 简单检索：查股价、查天气、搜索信息
- **简单规则**：如果你能一口气回答（可能用 1 个工具辅助），就选 `standard_l1`。

### deep_l3（深度推理）
- **判断标准**：任务需要先制定执行计划，需要多步推理、归因分析、或跨数据源综合判断才能得出结论。
- **涵盖场景**：
  - 归因分析："为什么本周良率下降了？"
  - 复杂对比："A100 和 B200 哪个更适合量产？"
  - 涉及内部业务数据的深度查询和分析
- **简单规则**：如果你需要"先...然后...最后..."地分步思考，就选 `deep_l3`。

**默认选 `standard_l1`**，只有明确需要多步推理时才选 `deep_l3`。

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
  "route": "standard_l1",
  "complexity": "simple",
  "confidence": 0.95,
  "entity_hints": {{}},
  "needs_clarify": false,
  "clarify_question": null
}}

字段说明：
- intent.primary: 上述意图类别之一
- intent.user_goal: 用一句完整的中文描述用户的核心需求（15-50字）
- route: standard_l1 / deep_l3
- complexity: simple / moderate / complex
- confidence: 0.0 ~ 1.0
- entity_hints: 从用户输入中识别到的关键实体线索（如 product, metric, period 等），只放有把握的
- needs_clarify: 信息不足时设为 true，并在 clarify_question 中给出追问"""

# 内置兜底分类（PromptCache 加载失败时使用）
_FALLBACK_CATEGORIES = "- general_qa: 通用知识问答（默认）"


async def build_intent_prompt() -> str:
    """动态构建意图分析 Prompt：从 PG 加载意图类别列表"""
    try:
        categories = await prompt_service.get_intent_categories()
        if categories:
            lines = [f"- {c['tag']}: {c['description']}" for c in categories]
            # 追加兜底分类提示
            tags = {c["tag"] for c in categories}
            if "general_qa" not in tags:
                lines.append("- general_qa: 通用知识问答（默认）")
            category_text = "\n".join(lines)
        else:
            category_text = _FALLBACK_CATEGORIES
    except Exception:
        category_text = _FALLBACK_CATEGORIES

    return _INTENT_PROMPT_TEMPLATE.format(intent_categories=category_text)

# 默认结果：当 LLM 完全无法解析时使用
DEFAULT_RESULT = IntentEngineResult(
    intent_primary="general_qa",
    user_goal="无法识别用户意图",
    route="standard_l1",
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
        # 动态加载意图类别（从 PG 缓存）
        intent_prompt = await build_intent_prompt()

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

                # 校验 route 合法性，非法值降级为 standard_l1
                valid_routes = {"standard_l1", "deep_l3"}
                if result.route not in valid_routes:
                    log.warning("意图引擎返回非法路由，降级处理", raw_route=result.route)
                    result.route = "standard_l1"

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
            route=data.get("route", "standard_l1"),
            complexity=data.get("complexity", "simple"),
            confidence=float(data.get("confidence", 0.5)),
            entity_hints=data.get("entity_hints", {}),
            needs_clarify=data.get("needs_clarify", False),
            clarify_question=data.get("clarify_question"),
        )
