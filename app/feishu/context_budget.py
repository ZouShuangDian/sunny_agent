from __future__ import annotations

from functools import lru_cache

from app.memory.schemas import Message
from app.config import get_settings

try:
    import tiktoken
except Exception:  # pragma: no cover
    tiktoken = None

settings = get_settings()


@lru_cache(maxsize=16)
def _get_encoding(model: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def _estimate_text_tokens(text: str, model: str) -> int:
    if not text:
        return 0
    encoding = _get_encoding(model)
    if encoding is not None:
        return len(encoding.encode(text))
    # Conservative heuristic for mixed Chinese/English text.
    return max(1, len(text) // 2)


def compute_available_input_budget(
    model_context_limit: int,
    reserved_output_tokens: int,
    reserved_tool_tokens: int,
) -> int:
    return max(model_context_limit - reserved_output_tokens - reserved_tool_tokens, 0)


def should_trigger_compaction(
    current_tokens: int,
    available_budget: int,
    trigger_ratio: float,
) -> bool:
    return current_tokens >= int(available_budget * trigger_ratio)


def should_hard_stop(
    current_tokens: int,
    available_budget: int,
    hard_stop_ratio: float,
) -> bool:
    return current_tokens >= int(available_budget * hard_stop_ratio)


async def estimate_history_tokens(messages: list[Message] | list[dict], model: str) -> int:
    total = 0
    for message in messages:
        if isinstance(message, Message):
            content = message.content
            role = message.role
        else:
            content = message.get("content", "")
            role = message.get("role", "")
        total += _estimate_text_tokens(f"{role}: {content}", model)
    return total


def build_budget_snapshot(history_tokens: int) -> dict:
    available_budget = compute_available_input_budget(
        settings.MODEL_CONTEXT_LIMIT,
        settings.COMPACTION_BUFFER,
        settings.PRUNE_PROTECT_TOKENS,
    )
    trigger_ratio = settings.HISTORY_TOKEN_BUDGET / max(available_budget, 1)
    trigger_ratio = min(max(trigger_ratio, 0.0), 1.0)
    hard_stop_ratio = 0.85
    return {
        "history_tokens": history_tokens,
        "available_budget": available_budget,
        "trigger_ratio": trigger_ratio,
        "hard_stop_ratio": hard_stop_ratio,
        "would_compact": should_trigger_compaction(history_tokens, available_budget, trigger_ratio),
        "must_compact": should_hard_stop(history_tokens, available_budget, hard_stop_ratio),
    }
