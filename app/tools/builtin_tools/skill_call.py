"""
SkillCallTool — Skill 元工具

设计意图：
- 将所有 Skill 统一收敛到一个工具入口，避免 N Skill → N 函数 schema 撑大 context
- description 和 skill_name enum 从请求级 skill_context ContextVar 动态读取（DB 驱动）
- 始终采用 pull 模式：返回 SKILL.md 在容器内的路径元数据，LLM 通过 read_file 读取后自主执行

执行流程：
  ExecutionRouter 查询 DB → 设置 skill_context ContextVar
  LLM 感知：skill_call description 列出当前用户可用 Skill
  LLM 决策：调用 skill_call(skill_name="github")
  返回路径：返回 SKILL.md 容器路径 + scripts/ 目录路径
  LLM 读取：调用 read_file(path="/mnt/skills/github/SKILL.md") 获取完整指令
  LLM 执行：按指令调用 bash_tool 执行 scripts/ 下的脚本
"""

from pydantic import BaseModel

from app.execution.skill_context import get_skill_catalog
from app.tools.base import BaseTool, ToolResult


class _PlaceholderParams(BaseModel):
    """占位 Pydantic Model，满足 BaseTool 抽象约束（实际 schema 由 schema() 覆盖）"""
    model_config = {"extra": "allow"}


class SkillCallTool(BaseTool):
    """
    Skill 元工具：单一入口代理所有 Skill 调用。

    tier = ["L3"]：只在 L3 深度推理循环中可用（Skill 是高阶工作流，不适合 L1 快速路径）。
    构造时无需传入 SkillRegistry，运行时从 skill_context ContextVar 动态读取。
    """

    def __init__(self) -> None:
        pass

    # ── 抽象属性实现 ──

    @property
    def name(self) -> str:
        return "skill_call"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        """动态生成：列出当前请求上下文中用户可用的 Skill"""
        catalog = get_skill_catalog()
        lines = [
            "执行高阶 Skill 工作流。调用后返回 Skill 文件在沙箱容器内的路径，"
            "你需先用 read_file 读取完整指令，再按指令调用 bash_tool 执行脚本。\n",
            "可用 Skill（格式：name: 描述）：",
        ]
        if catalog:
            for skill in catalog:
                lines.append(f"  - {skill.name}: {skill.description}")
        else:
            lines.append("  （暂无可用 Skill）")
        return "\n".join(lines)

    @property
    def params_model(self) -> type[BaseModel]:
        # schema() 已完整覆盖，此属性仅为满足抽象约束
        return _PlaceholderParams

    # ── 覆盖 schema()：仅 skill_name 一个参数 ──

    def schema(self) -> dict:
        """覆盖父类 schema()，动态构建 skill_name enum"""
        catalog = get_skill_catalog()
        skill_names = [s.name for s in catalog]

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

    # ── 执行：始终 pull 模式 ──

    async def execute(self, args: dict) -> ToolResult:
        """
        返回 Skill 文件在沙箱容器内的路径元数据（pull 模式）。

        LLM 接收到路径后，依次：
        1. 调用 read_file(path=instructions_path) 读取 SKILL.md 完整指令
        2. 按指令调用 bash_tool 执行 scripts/ 下的脚本
        """
        skill_name = args.get("skill_name", "")
        catalog = get_skill_catalog()

        skill = next((s for s in catalog if s.name == skill_name), None)
        if skill is None:
            return ToolResult.fail(f"未知 Skill: {skill_name}，请检查 skill_name 参数")

        instructions_path = skill.get_container_skill_path()
        scripts_dir = skill.get_container_scripts_path()

        hint = f"请先调用 read_file(path='{instructions_path}') 获取完整执行指令，再按指令使用 bash_tool 执行脚本。"

        return ToolResult.success(
            skill=skill_name,
            instructions_path=instructions_path,
            summary=skill.description,
            scripts_dir=scripts_dir,
            hint=hint,
        )
