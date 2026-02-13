"""
已迁移到 app/services/prompt_service.py — 此文件仅保留兼容性重导出。

重构原因：PromptService 同时被 IntentEngine（意图层）和 PromptRetriever（执行层）使用，
放在 execution/l1 下会导致意图层依赖执行层（层级倒置），迁移到 services/ 公共层解耦。
"""

# 兼容性重导出，避免外部残留引用报错
from app.services.prompt_service import PromptService as PromptCache  # noqa: F401
from app.services.prompt_service import prompt_service as prompt_cache  # noqa: F401
