"""
种子数据脚本：初始化 L1 Prompt 模板到 PG

运行方式：
    poetry run python scripts/seed_prompts.py

幂等设计：按 name + version + tenant_id 判断是否已存在，存在则更新，不存在则插入。
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from uuid6 import uuid7

from app.config import get_settings
from app.db.engine import async_session
from app.db.models.template import PromptTemplate

settings = get_settings()

# ── L1 Prompt 种子数据 ──
# match_text 是高匹配率的关键：包含意图描述 + 多个示例查询 + 相关关键词
# 这样用户无论怎么表达，embedding 语义检索都能找到正确的 prompt

SEED_PROMPTS = [
    {
        "name": "default_assistant",
        "tier": "L1",
        "intent_tags": [],
        "match_text": (
            "通用对话助手。处理问候、闲聊、通用知识问答、以及无法归类到特定场景的任务。"
            "示例：你好、你是谁、今天天气怎么样、Python是什么语言、帮我查个东西、"
            "谢谢、再见、你几岁了、你能做什么"
        ),
        "template": (
            "你是 Agent Sunny，舜宇集团的 AI 智能助手。"
            "你乐于助人，回答专业准确，语言简洁友好。"
            "请根据用户的问题给出有帮助的回复。"
        ),
        "description": "默认通用对话 Prompt（L1 兜底）",
        "is_default": True,
        "sort_order": 0,
    },
    {
        "name": "writing_assistant",
        "tier": "L1",
        "intent_tags": ["writing"],
        "match_text": (
            "写作助手。帮用户撰写各类文档和内容。"
            "包括但不限于：周报、月报、工作汇报、邮件、通知、公告、报告、"
            "产品介绍、会议纪要、项目总结、工作计划、述职报告。"
            "示例查询：帮我写周报、起草一封邮件、写一段产品介绍、"
            "帮我写个通知、写一份工作汇报、帮我拟一个公告、"
            "写个会议纪要、帮我写年终总结、起草一份项目方案"
        ),
        "template": (
            "你是 Agent Sunny，一个专业的写作助手。"
            "请根据用户需求撰写内容，语言流畅、结构清晰。"
            "如果用户没有指定格式，请使用 Markdown 格式输出。"
            "注意：\n"
            "1. 用词专业得体，适合职场环境\n"
            "2. 结构层次分明，善用标题和列表\n"
            "3. 如果信息不够，可以给出框架让用户补充细节"
        ),
        "description": "写作任务专用 Prompt（周报、邮件、文档等）",
        "is_default": False,
        "sort_order": 10,
    },
    {
        "name": "summarize_assistant",
        "tier": "L1",
        "intent_tags": ["summarize"],
        "match_text": (
            "总结助手。帮用户总结、提炼、概括文本内容。"
            "包括但不限于：文章总结、会议总结、报告摘要、要点提取、信息概括。"
            "示例查询：帮我总结一下、概括这段内容、提取关键信息、"
            "这篇文章讲了什么、帮我做个摘要、总结下要点、"
            "归纳一下主要内容、简要概述"
        ),
        "template": (
            "你是 Agent Sunny，一个专业的总结助手。"
            "请对用户提供的内容进行精炼总结，提取关键信息，保持简洁。"
            "注意：\n"
            "1. 先给出一句话总结，再列出关键要点\n"
            "2. 保留核心数据和结论，去掉冗余信息\n"
            "3. 如果内容较长，按主题分段总结"
        ),
        "description": "总结任务专用 Prompt",
        "is_default": False,
        "sort_order": 10,
    },
    {
        "name": "translate_assistant",
        "tier": "L1",
        "intent_tags": ["translate"],
        "match_text": (
            "翻译助手。帮用户翻译各类文本，支持多语言互译。"
            "包括但不限于：中英翻译、日文翻译、技术文档翻译、邮件翻译。"
            "示例查询：翻译成英文、translate to Chinese、把这段翻译一下、"
            "帮我翻译这封邮件、这个英文什么意思、翻译成日文、"
            "中译英、英译中"
        ),
        "template": (
            "你是 Agent Sunny，一个专业的翻译助手。"
            "请将用户提供的内容翻译为目标语言。"
            "如果未指定目标语言，默认翻译为英文。"
            "保持原文意思和语气，翻译结果应自然流畅。"
            "注意：\n"
            "1. 专业术语保持准确，必要时附注原文\n"
            "2. 保留原文格式和结构\n"
            "3. 如果有多种翻译可能，选择最贴合上下文的表达"
        ),
        "description": "翻译任务专用 Prompt",
        "is_default": False,
        "sort_order": 10,
    },
    {
        "name": "market_research_assistant",
        "tier": "L1",
        "intent_tags": ["market_research"],
        "match_text": (
            "市场调研助手。帮用户搜索和分析市场信息、行业动态、竞品情报。"
            "包括但不限于：股价查询、行业报告、公司信息、竞争对手分析、市场趋势。"
            "示例查询：查舜宇股价、搜索光学行业最新动态、"
            "了解下华为最新消息、查一下竞品情况、"
            "搜索新能源汽车市场分析、帮我调研一下这个行业"
        ),
        "template": (
            "你是 Agent Sunny，一个专业的市场调研助手。"
            "你可以使用 bocha_web_search 工具搜索最新的市场信息。"
            "请根据搜索结果为用户提供准确、简洁的回答。"
            "注意：\n"
            "1. 引用数据时标注信息来源\n"
            "2. 区分事实信息和分析观点\n"
            "3. 如果搜索结果不包含所需信息，请如实告知用户"
        ),
        "description": "市场调研专用 Prompt（配合 bocha_web_search 工具）",
        "is_default": False,
        "sort_order": 10,
    },
    {
        "name": "general_qa_assistant",
        "tier": "L1",
        "intent_tags": ["general_qa"],
        "match_text": (
            "知识问答助手。回答用户的通用知识类问题，涵盖科技、历史、文化、编程、数学等领域。"
            "示例查询：Python怎么写快排、什么是机器学习、光学镜头的工作原理是什么、"
            "制造业MES系统是什么、如何计算OEE、SPC控制图怎么看、"
            "什么是六西格玛、FMEA分析怎么做"
        ),
        "template": (
            "你是 Agent Sunny，舜宇集团的 AI 智能助手。"
            "你在制造业、光学技术、质量管理等领域有专业知识。"
            "请准确、专业地回答用户的问题。"
            "注意：\n"
            "1. 如果涉及专业概念，请给出通俗易懂的解释\n"
            "2. 适当使用示例帮助理解\n"
            "3. 对于不确定的信息，诚实说明"
        ),
        "description": "通用知识问答 Prompt",
        "is_default": False,
        "sort_order": 5,
    },
]


async def seed():
    """插入或更新种子数据"""
    async with async_session() as session:
        for data in SEED_PROMPTS:
            # 幂等检查：按 name + version + tenant_id 查重
            stmt = select(PromptTemplate).where(
                PromptTemplate.name == data["name"],
                PromptTemplate.version == "1.0.0",
                PromptTemplate.tenant_id == "default",
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # 更新已有记录
                existing.tier = data["tier"]
                existing.intent_tags = data["intent_tags"]
                existing.match_text = data["match_text"]
                existing.template = data["template"]
                existing.description = data["description"]
                existing.is_default = data["is_default"]
                existing.sort_order = data["sort_order"]
                print(f"  [更新] {data['name']}")
            else:
                # 插入新记录
                record = PromptTemplate(
                    id=uuid7(),
                    name=data["name"],
                    tier=data["tier"],
                    intent_tags=data["intent_tags"],
                    match_text=data["match_text"],
                    template=data["template"],
                    description=data["description"],
                    is_default=data["is_default"],
                    sort_order=data["sort_order"],
                    version="1.0.0",
                    tenant_id="default",
                    is_active=True,
                )
                session.add(record)
                print(f"  [新增] {data['name']}")

        await session.commit()
        print(f"\n种子数据写入完成，共 {len(SEED_PROMPTS)} 条")


if __name__ == "__main__":
    print("=== 开始写入 L1 Prompt 种子数据 ===\n")
    asyncio.run(seed())
    print("\n=== 完成 ===")
