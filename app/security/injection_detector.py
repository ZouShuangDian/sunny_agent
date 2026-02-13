"""
Prompt 注入检测器（Phase 1 占位）
Phase 2 填充：高危关键词规则引擎 + 启发式评分 + YAML 热加载
"""

from dataclasses import dataclass


@dataclass
class InjectionResult:
    """注入检测结果"""

    is_blocked: bool = False
    risk_score: float = 0.0
    matched_rule: str | None = None
    reason: str | None = None


class InjectionDetector:
    """
    Prompt 注入检测器

    TODO Phase 2:
    - 高危关键词规则引擎（BLOCK_PATTERNS / WARN_PATTERNS）
    - 启发式评分（输入长度异常、语言比例异常、代码特征等）
    - risk_score 阈值策略：> 0.8 拦截，0.5-0.8 告警，< 0.5 放行
    - 规则存储到 YAML 配置文件，支持热加载
    """

    def detect(self, user_input: str) -> InjectionResult:
        """Phase 1: 直接放行，不做检测"""
        return InjectionResult()


# 单例
injection_detector = InjectionDetector()


async def check_injection(user_input: str = "") -> InjectionResult:
    """FastAPI 依赖注入：注入检测（Phase 1 直接放行）"""
    return injection_detector.detect(user_input)
