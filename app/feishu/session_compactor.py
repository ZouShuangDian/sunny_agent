import json
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from app.config import get_settings
from app.feishu.context_budget import build_budget_snapshot, estimate_history_tokens
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import ConversationHistory, Message
from app.memory.working_memory import WorkingMemory

log = structlog.get_logger()
settings = get_settings()

if TYPE_CHECKING:
    from app.llm.client import LLMClient

COMPACTION_PROMPT = """??????????????????? 1500 ???????
1. ????
2. ?????
3. ????????????????
4. ???????
5. ?????
??????????????????????????????????????"""


@dataclass
class CompactionResult:
    compacted: bool
    history_tokens: int
    would_compact: bool
    summary_message_id: str | None = None
    kept_messages: int = 0
    compacted_messages: int = 0


class SessionCompactor:
    def __init__(self, llm: "LLMClient", persistence: ChatPersistence):
        self.llm = llm
        self.persistence = persistence

    async def maybe_compact_session(
        self,
        session_id: str,
        memory: WorkingMemory,
        model: str,
    ) -> CompactionResult:
        history = await memory.get_history(session_id)
        llm_messages = history.to_llm_messages()
        history_tokens = await estimate_history_tokens(llm_messages, model)
        snapshot = build_budget_snapshot(history_tokens)

        if not snapshot["would_compact"]:
            return CompactionResult(False, history_tokens, False)

        raw_messages = history.messages
        if len(raw_messages) < 4:
            return CompactionResult(False, history_tokens, True)

        protected = self._select_protected_messages(raw_messages)
        compressible_count = len(raw_messages) - len(protected)
        if compressible_count <= 0:
            return CompactionResult(False, history_tokens, True)

        compressible = raw_messages[:compressible_count]
        summary_content = await self._summarize_messages(compressible, model)
        if not summary_content:
            return CompactionResult(False, history_tokens, True)

        compaction_msg = Message(
            role="system",
            content=summary_content.strip(),
            timestamp=self._build_compaction_timestamp(compressible, protected),
            message_id=f"cmp_{uuid.uuid4().hex}",
            model=model,
            is_compaction=True,
        )

        rebuilt = ConversationHistory(max_turns=settings.WORKING_MEMORY_MAX_TURNS)
        rebuilt.append(compaction_msg)
        for msg in protected:
            rebuilt.append(msg)

        await memory.set_history(session_id, rebuilt)
        if settings.CHAT_PERSIST_ENABLED:
            await self.persistence.save_message(session_id, compaction_msg)

        log.info(
            "Session compacted",
            session_id=session_id,
            compacted_messages=len(compressible),
            kept_messages=len(protected),
            summary_message_id=compaction_msg.message_id,
        )
        return CompactionResult(
            compacted=True,
            history_tokens=history_tokens,
            would_compact=True,
            summary_message_id=compaction_msg.message_id,
            kept_messages=len(protected),
            compacted_messages=len(compressible),
        )

    def _select_protected_messages(self, messages: list[Message]) -> list[Message]:
        protected: list[Message] = []
        accumulated = 0

        for msg in reversed(messages):
            token_est = self._estimate_message_tokens(msg)
            if protected and accumulated + token_est > settings.PRUNE_PROTECT_TOKENS:
                break
            protected.insert(0, msg)
            accumulated += token_est

        if not protected and messages:
            protected = [messages[-1]]

        return protected

    async def _summarize_messages(self, messages: list[Message], model: str) -> str:
        history_text = "\n\n".join(self._render_message(msg) for msg in messages)
        response = await self.llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": f"{COMPACTION_PROMPT}\n\n?????\n{history_text}",
                }
            ],
            model=model,
            max_tokens=settings.COMPACTION_MAX_TOKENS,
            temperature=0.0,
        )
        return response.content.strip()

    def _render_message(self, msg: Message) -> str:
        parts = [f"[{msg.role}] {msg.content}"]
        if msg.tool_calls:
            parts.append(f"tool_calls={json.dumps([tc.model_dump() for tc in msg.tool_calls], ensure_ascii=False)}")
        if msg.tool_name:
            parts.append(f"tool_name={msg.tool_name}")
        return "\n".join(parts)

    def _estimate_message_tokens(self, msg: Message) -> int:
        text = self._render_message(msg)
        return max(1, len(text) // 2)

    def _build_compaction_timestamp(
        self,
        compressible: list[Message],
        protected: list[Message],
    ) -> float:
        if not compressible:
            return time.time()
        if not protected:
            return time.time()

        last_compressible_ts = compressible[-1].timestamp
        first_protected_ts = protected[0].timestamp
        if first_protected_ts - last_compressible_ts > 0.000002:
            return (last_compressible_ts + first_protected_ts) / 2
        return max(0.0, first_protected_ts - 0.000001)
