"""
SkillExecTool — Skill 脚本执行工具（Tier 3 执行层）

设计意图：
- 脚本不作为全局 Tool 注册，避免 context 污染和流程管控失效
- 只有 LLM 读取完 Skill body（Tier 2）之后，才能通过此工具执行对应脚本
- 白名单校验：skill_name + script_name 必须是已注册 Skill 的合法脚本，防止路径注入

执行流程：
  skill_call("github", {...})           ← LLM 调用 Skill
    → 返回 Tier 2 body（含脚本调用指令）
    → LLM 读取指令后决定执行哪个脚本
  skill_exec(skill_name="github",       ← LLM 按指令调用本工具
             script="search_repos",
             args={"query": "ai"})
    → SkillExecTool 校验白名单
    → subprocess 执行，stdin 传 args JSON，stdout 接结果 JSON

脚本通信协议（与 SkillRegistry 原 _ScriptTool 保持一致）：
  - 输入：stdin 接收 JSON 格式参数
  - 输出：stdout 输出 JSON（{"status": "success", ...} 或 {"status": "error", "error": "..."}）
  - 退出码：0=成功，非0=失败
"""

import asyncio
import json
import sys
from pathlib import Path
from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolResult

import structlog

log = structlog.get_logger()


class _Params(BaseModel):
    skill_name: str = Field(description="Skill 名称，必须与调用过的 skill_call 名称一致")
    script: str = Field(description="要执行的脚本名称（不含 .py 后缀），例：search_repos")
    args: dict = Field(default_factory=dict, description="传递给脚本的参数对象（JSON 格式，通过 stdin 传入）")


class SkillExecTool(BaseTool):
    """
    Skill 脚本执行工具。

    白名单约束：只允许执行已注册 Skill 目录下的脚本。
    调用方（LLM）必须先通过 skill_call 获取 Skill 执行指令，再调用此工具执行具体脚本。

    tier = ["L3"]：仅在 L3 深度推理循环中可用。
    """

    def __init__(self, skill_registry: "SkillRegistry") -> None:  # type: ignore[name-defined]
        self._registry = skill_registry

    @property
    def name(self) -> str:
        return "skill_exec"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        return (
            "执行 Skill 内部脚本。只能在已调用 skill_call 并读取执行指令后使用。\n"
            "skill_name 必须与本轮调用的 Skill 名称一致，script 为脚本名（不含 .py），"
            "args 为脚本所需的参数对象。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return _Params

    async def execute(self, args: dict) -> ToolResult:
        skill_name = args.get("skill_name", "").strip()
        script_name = args.get("script", "").strip()
        script_args = args.get("args", {})

        if not skill_name or not script_name:
            return ToolResult.fail("skill_name 和 script 均为必填参数")

        # 白名单校验：从 SkillRegistry 获取脚本路径
        script_path = self._registry.get_script_path(skill_name, script_name)
        if script_path is None:
            allowed = self._registry.get_script_names(skill_name)
            if allowed is None:
                return ToolResult.fail(f"未知 Skill: {skill_name}")
            return ToolResult.fail(
                f"Skill '{skill_name}' 中不存在脚本 '{script_name}'，"
                f"可用脚本：{allowed or '（无）'}"
            )

        # 获取脚本超时配置
        timeout_s = self._registry.get_skill_timeout_s(skill_name)

        return await self._run_script(script_path, script_args, timeout_s)

    async def _run_script(
        self,
        script_path: Path,
        script_args: dict,
        timeout_s: float,
    ) -> ToolResult:
        """通过 subprocess 执行脚本，stdin 传参，stdout 接收结果"""
        args_json = json.dumps(script_args, ensure_ascii=False)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=args_json.encode()),
                timeout=timeout_s,
            )

            if proc.returncode != 0:
                err_msg = stderr.decode(errors="replace").strip()
                log.error(
                    "Skill 脚本执行失败",
                    script=str(script_path),
                    stderr=err_msg,
                )
                return ToolResult.fail(f"脚本执行失败: {err_msg}")

            output = stdout.decode(errors="replace").strip()
            try:
                result_data = json.loads(output)
                if result_data.get("status") == "error":
                    return ToolResult.fail(result_data.get("error", "脚本返回错误"))
                data = {k: v for k, v in result_data.items() if k != "status"}
                return ToolResult.success(**data)
            except json.JSONDecodeError:
                log.error(
                    "Skill 脚本输出非 JSON，请检查脚本实现",
                    script=str(script_path),
                    output_preview=output[:200],
                    tip="脚本通过 stdout 输出 JSON，调试日志走 stderr",
                )
                return ToolResult.fail(
                    f"脚本输出格式错误（非 JSON），预览：{output[:100]!r}"
                )

        except TimeoutError:
            return ToolResult.fail(f"脚本执行超时（{timeout_s:.0f}s）")
        except Exception as e:
            log.error("Skill 脚本执行异常", script=str(script_path), error=str(e), exc_info=True)
            return ToolResult.fail(f"脚本执行异常: {e}")
