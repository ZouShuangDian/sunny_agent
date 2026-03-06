"""
ask_user 工具：向用户提出澄清性问题

当用户请求模糊（缺关键参数、指代不明、存在多种合理解读）时，
LLM 调用此工具主动反问，而非盲猜执行。

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
        description="可选项列表（固定3个），覆盖最常见的选择；前端会自动追加\"其他\"选项供用户自由输入",
        min_length=3,
        max_length=3,
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
            "向用户提出澄清性问题，用于请求不清晰时主动确认关键信息。"
            "支持一次提出 1-4 个问题，每个问题固定提供 3 个选项（前端会自动追加'其他'选项供用户自由输入）。"
            "调用后请直接将问题转达给用户并结束当前回答，等待用户回答后继续。"
            "注意：仅在用户请求确实模糊且影响执行方向时使用，简单问题直接处理。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return AskUserParams

    async def execute(self, args: dict) -> ToolResult:
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
