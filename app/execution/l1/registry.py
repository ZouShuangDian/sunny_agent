"""
L1 配置注册表：维护 intent_primary → L1TemplateConfig 的映射

Week 5：硬编码字典 (Code-as-Configuration)，快速验证。
Week 6+：迁移至数据库表，支持热更新。
"""

from app.execution.l1.schemas import L1TemplateConfig

# ── 硬编码配置表 ──

_REGISTRY: dict[str, L1TemplateConfig] = {
    "writing": L1TemplateConfig(
        key="writing",
        prompt_template_key="writing",
        allowed_tools=[],
        max_loop_steps=1,
        temperature=0.7,
        max_tokens=4096,
        description="写作任务（周报、邮件、文档）",
    ),
    "summarize": L1TemplateConfig(
        key="summarize",
        prompt_template_key="summarize",
        allowed_tools=[],
        max_loop_steps=1,
        temperature=0.3,
        max_tokens=2048,
        description="总结任务",
    ),
    "translate": L1TemplateConfig(
        key="translate",
        prompt_template_key="translate",
        allowed_tools=[],
        max_loop_steps=1,
        temperature=0.1,
        max_tokens=4096,
        description="翻译任务",
    ),
    "market_research": L1TemplateConfig(
        key="market_research",
        prompt_template_key="market_research",
        allowed_tools=["bocha_web_search"],
        max_loop_steps=3,
        temperature=0.5,
        max_tokens=2048,
        description="市场调研（绑定博查搜索工具）",
    ),
}

# ── Prompt 模板（硬编码，Week 6+ 迁移到 PG） ──

_PROMPTS: dict[str, str] = {
    "writing": (
        "你是 Agent Sunny，一个专业的写作助手。"
        "请根据用户需求撰写内容，语言流畅、结构清晰。"
        "如果用户没有指定格式，请使用 Markdown 格式输出。"
    ),
    "summarize": (
        "你是 Agent Sunny，一个专业的总结助手。"
        "请对用户提供的内容进行精炼总结，提取关键信息，保持简洁。"
    ),
    "translate": (
        "你是 Agent Sunny，一个专业的翻译助手。"
        "请将用户提供的内容翻译为目标语言。如果未指定目标语言，默认翻译为英文。"
        "保持原文意思和语气，翻译结果应自然流畅。"
    ),
    "market_research": (
        "你是 Agent Sunny，一个专业的市场调研助手。"
        "你可以使用 bocha_web_search 工具搜索最新的市场信息。"
        "请根据搜索结果为用户提供准确、简洁的回答。"
        "如果搜索结果不包含所需信息，请如实告知用户。"
    ),
}


def get_l1_config(intent_primary: str) -> L1TemplateConfig | None:
    """根据 intent_primary 获取 L1 执行配置，未注册则返回 None"""
    return _REGISTRY.get(intent_primary)


def get_prompt(prompt_key: str) -> str | None:
    """获取 Prompt 模板内容"""
    return _PROMPTS.get(prompt_key)


def list_registered_intents() -> list[str]:
    """列出所有注册的 intent_primary"""
    return list(_REGISTRY.keys())
