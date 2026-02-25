"""
M06 Output Validator 数据结构定义
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.memory.schemas import ToolCall


class ValidatorInput(BaseModel):
    """校验器输入"""
    execution_output: str                              # 执行层的最终回复文本
    tool_calls: list[ToolCall] = Field(default_factory=list)  # 工具调用记录（含 result 字段）
    reasoning_trace: list[dict] | None = None          # L3 推理轨迹（仅 L3 路由时有值）
    enable_hallucination: bool = True                  # 是否启用幻觉检测（额外 LLM 调用）
    enable_logic_check: bool = False                   # 是否启用逻辑自洽检查（默认关闭）


class ValidationIssue(BaseModel):
    """单个校验问题"""
    type: Literal["numeric_mismatch", "hallucination", "logic_inconsistency"]
    severity: Literal["critical", "warning", "info"]
    description: str                                   # 问题描述
    location: str | None = None                        # 在输出文本中的大致位置（可选）


class ValidatorOutput(BaseModel):
    """校验器输出"""
    validated_output: str                              # 校验后的输出（有严重问题时附加警告标注）
    confidence: float                                  # 整体置信度 0-1
    issues: list[ValidationIssue] = Field(default_factory=list)
    is_modified: bool = False                          # 是否修改了原始输出
