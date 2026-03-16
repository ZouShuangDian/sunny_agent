"""
ask_user 工具：向用户提出问题并等待回答

适用场景（不限于澄清）：
1. 用户请求模糊（缺关键参数、指代不明、多种解读）→ 主动反问
2. 任务本身需要用户输入（问卷、测评、确认、选择偏好等）→ 必须使用此工具
3. 需要用户做决策才能继续（如：选择方案 A 还是 B）→ 必须使用此工具

核心原则：**任何需要用户回答的问题，都必须通过此工具发起，禁止在文本回复中直接提问。**

设计要点：
- 不暂停 ReAct 循环：工具返回结构化结果，LLM 据此生成反问回复，当前对话轮结束
- 前端（或控制台）从 tool_calls 中检测 ask_user，进入交互式选择模式
- 用户完成所有选择后，打包为新消息发起下一轮对话（携带完整历史）
"""

from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolResult


class _Question(BaseModel):
    question: str = Field(..., description="问题文本，清晰描述需要用户确认的信息")
    options: list[str] = Field(
        ...,
        description="可选项列表（2-4个），覆盖最常见的选择。禁止包含'其他'选项，前端会自动追加",
        min_length=2,
        max_length=4,
    )


class AskUserParams(BaseModel):
    questions: list[_Question] = Field(
        ...,
        description="需要用户回答的问题列表（1-4个），每个问题带选项",
        min_length=1,
        max_length=4,
    )


class AskUserTool(BaseTool):

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "向用户提出问题并等待回答。适用于所有需要用户输入的场景："
            "澄清模糊请求、收集用户偏好、问卷测评、方案确认等。"
            "支持一次提出 1-4 个问题，每个问题提供 2-4 个选项（前端会自动追加'其他'选项，禁止自行添加'其他'类选项）。"
            "**重要**：任何需要用户回答的问题都必须通过此工具发起，禁止在文本回复中直接向用户提问。"
            "调用后请直接将问题转达给用户并结束当前回答，等待用户回答后继续。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return AskUserParams

    async def execute(self, args: dict) -> ToolResult:
        # 兼容 LLM 将 questions 传为 JSON 字符串的情况
        if isinstance(args.get("questions"), str):
            import json
            args["questions"] = json.loads(args["questions"])
        params = AskUserParams(**args)

        questions_data = [
            {"question": q.question, "options": q.options}
            for q in params.questions
        ]

        return ToolResult.success(
            type="ask_user",
            questions=questions_data,
            instruction="请将以上问题以友好的方式转达给用户，然后结束当前回答。等待用户选择后继续。",
        )
