"""
SkillCallTool — Skill 元工具（M08-6）

设计意图：
- 将所有 Skill 统一收敛到一个工具入口，避免 N Skill → N 函数 schema 撑大 context
- description 和 skill_name enum 动态生成，随 SkillRegistry 内容实时更新
- 调用后返回 Skill 的 Tier 2 body（执行指令），LLM 读取后自主 ReAct 执行后续步骤

执行流程：
  LLM 感知：skill_call description 列出所有可用 Skill
  LLM 决策：调用 skill_call(skill_name="github")
  Tier 2 注入：返回 skill.md body（操作手册）
  LLM 执行：按手册逐步调用 skill_exec / web_search 等工具，参数由 LLM 根据用户请求自行决定
"""

from pydantic import BaseModel

from app.tools.base import BaseTool, ToolResult


class _PlaceholderParams(BaseModel):
    """占位 Pydantic Model，满足 BaseTool 抽象约束（实际 schema 由 schema() 覆盖）"""
    model_config = {"extra": "allow"}


class SkillCallTool(BaseTool):
    """
    Skill 元工具：单一入口代理所有 MarkdownSkill 调用。

    tier = ["L3"]：只在 L3 深度推理循环中可用（Skill 是高阶工作流，不适合 L1 快速路径）。
    """

    def __init__(self, skill_registry: "SkillRegistry") -> None:  # type: ignore[name-defined]
        self._registry = skill_registry

    # ── 抽象属性实现 ──

    @property
    def name(self) -> str:
        return "skill_call"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        """动态生成：列出所有已加载 Skill 的名称和描述"""
        catalog = self._registry.get_catalog()
        lines = [
            "执行高阶 Skill 工作流。调用后系统返回该 Skill 的详细执行指令（操作手册），"
            "你需严格按照指令一步步调用后续工具完成任务。\n",
            "可用 Skill（格式：name: 描述）：",
        ]
        if catalog:
            for skill_name, skill_desc in catalog:
                lines.append(f"  - {skill_name}: {skill_desc}")
        else:
            lines.append("  （暂无可用 Skill）")
        return "\n".join(lines)

    @property
    def params_model(self) -> type[BaseModel]:
        # schema() 已完整覆盖，此方法仅为满足抽象约束
        return _PlaceholderParams

    # ── 覆盖 schema()：仅 skill_name 一个参数 ──

    def schema(self) -> dict:
        """覆盖父类 schema()，动态构建 skill_name enum"""
        catalog = self._registry.get_catalog()
        skill_names = [n for n, _ in catalog]

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "要执行的 Skill 名称",
                            "enum": skill_names if skill_names else ["__no_skill__"],
                        },
                    },
                    "required": ["skill_name"],
                },
            },
        }

    # ── 执行：Tier 2 注入 ──

    async def execute(self, args: dict) -> ToolResult:
        """
        触发 Skill Tier 2 加载：从 SkillRegistry 读取 skill.md body，
        以 instructions 字段返回给 LLM，LLM 读取后按操作手册自主 ReAct 执行。
"""
        skill_name = args.get("skill_name", "")

        if not self._registry.has_skill(skill_name):
            return ToolResult.fail(f"未知 Skill: {skill_name}，请检查 skill_name 参数")

        instructions = self._registry.execute(skill_name)
        return ToolResult.success(instructions=instructions)
