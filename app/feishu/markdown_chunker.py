from dataclasses import dataclass


@dataclass
class ChunkerConfig:
    min_chars: int = 1500
    max_chars: int = 2400
    idle_ms: int = 1000
    max_wait_ms: int = 2500
    hard_limit_chars: int = 3200


class MarkdownAwareChunker:
    def __init__(self, config: ChunkerConfig | None = None):
        self.config = config or ChunkerConfig()
        self.buffer = ""
        self.first_buffer_ts: int | None = None
        self.last_update_ts: int | None = None
        self.in_fenced_code_block = False

    def append(self, text: str, now_ms: int | None = None) -> list[str]:
        if not text:
            return []

        now_ms = now_ms or 0
        if not self.buffer:
            self.first_buffer_ts = now_ms

        self.buffer += text
        self.last_update_ts = now_ms
        self._refresh_state(text)
        return self._drain_ready_chunks()

    def flush_idle(self, now_ms: int | None = None) -> list[str]:
        if not self.buffer or self.last_update_ts is None:
            return []

        now_ms = now_ms or 0
        idle_elapsed = now_ms - self.last_update_ts
        total_elapsed = now_ms - (self.first_buffer_ts or now_ms)

        if idle_elapsed < self.config.idle_ms:
            return []

        idx = self._find_safe_split_index(self.buffer)
        if idx is not None:
            return [self._pop_prefix(idx)]

        if total_elapsed >= self.config.max_wait_ms and not self.in_fenced_code_block:
            idx = self._find_fallback_split_index(self.buffer)
            if idx is not None:
                return [self._pop_prefix(idx)]

        return []

    def flush_final(self) -> list[str]:
        if not self.buffer:
            return []

        chunk = self.buffer.strip()
        self.buffer = ""
        self.first_buffer_ts = None
        self.last_update_ts = None
        self.in_fenced_code_block = False
        return [chunk] if chunk else []

    def _drain_ready_chunks(self) -> list[str]:
        chunks: list[str] = []

        while len(self.buffer) >= self.config.min_chars:
            candidate = self.buffer[: self.config.max_chars]
            idx = self._find_safe_split_index(candidate)
            if idx is not None:
                chunks.append(self._pop_prefix(idx))
                continue

            if len(self.buffer) >= self.config.hard_limit_chars and not self.in_fenced_code_block:
                idx = self._find_fallback_split_index(self.buffer)
                if idx is None:
                    idx = min(len(self.buffer), self.config.hard_limit_chars)
                chunks.append(self._pop_prefix(idx))
                continue

            break

        return chunks

    def _refresh_state(self, text: str) -> None:
        fence_count = text.count("```")
        if fence_count % 2 == 1:
            self.in_fenced_code_block = not self.in_fenced_code_block

    def _find_safe_split_index(self, text: str) -> int | None:
        candidates = [
            text.rfind("\n\n"),
            text.rfind("\n#### "),
            text.rfind("\n- "),
            text.rfind("\n* "),
            text.rfind("\n> "),
            text.rfind("\n1. "),
            text.rfind("```\n"),
        ]
        idx = max(candidates)
        if idx <= 0:
            return None
        if text[idx: idx + 2] == "\n\n":
            return idx + 2
        return idx + 1

    def _find_fallback_split_index(self, text: str) -> int | None:
        idx = text.rfind("\n", 0, self.config.max_chars)
        if idx <= 0:
            return None
        return idx + 1

    def _pop_prefix(self, idx: int) -> str:
        chunk = self.buffer[:idx].rstrip()
        self.buffer = self.buffer[idx:].lstrip()
        if not self.buffer:
            self.first_buffer_ts = None
            self.last_update_ts = None
        return chunk
