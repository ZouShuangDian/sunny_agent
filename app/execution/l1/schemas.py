"""
L1 执行配置 Schema

定义每个意图对应的执行配置：使用哪个 Prompt、允许哪些工具、最大循环步数等。
"""

from pydantic import BaseModel, Field


class L1TemplateConfig(BaseModel):
    """L1 执行配置"""

    key: str = Field(..., description="配置唯一标识，如 market_research")
    prompt_template_key: str = Field(..., description="Prompt 模板标识")
    allowed_tools: list[str] = Field(default_factory=list, description="白名单工具列表")
    max_loop_steps: int = 3  # 最大执行步数（Bounded Loop），防止死循环
    temperature: float = 0.7
    max_tokens: int = 2048
    description: str = ""
