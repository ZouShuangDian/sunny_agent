from typing import List

import structlog

from app.feishu.client import FeishuClient
from app.feishu.markdown_chunker import ChunkerConfig, MarkdownAwareChunker
from app.feishu.markdown_sanitizer import normalize_markdown_headings

logger = structlog.get_logger()

DEFAULT_MIN_CHARS = 1500
DEFAULT_MAX_CHARS = 2400
DEFAULT_IDLE_MS = 1000
DEFAULT_CHUNK_SIZE = 2000


class BlockStreamingManager:
    """Minimal Feishu reply helper used by the current product path."""

    def __init__(
        self,
        feishu_client: FeishuClient,
        config: dict | None = None,
    ):
        self.feishu_client = feishu_client
        self.config = config or {}
        self.min_chars = self.config.get("min_chars", DEFAULT_MIN_CHARS)
        self.max_chars = self.config.get("max_chars", DEFAULT_MAX_CHARS)
        self.idle_ms = self.config.get("idle_ms", DEFAULT_IDLE_MS)
        self.chunk_size = self.config.get("chunk_size", DEFAULT_CHUNK_SIZE)

    def chunk_text(self, text: str) -> List[str]:
        normalized_text = normalize_markdown_headings(text, max_level=4)
        if len(normalized_text) <= self.chunk_size:
            return [normalized_text]

        chunker = MarkdownAwareChunker(
            ChunkerConfig(
                min_chars=min(self.min_chars, self.chunk_size),
                max_chars=self.chunk_size,
                idle_ms=self.idle_ms,
                hard_limit_chars=max(self.chunk_size, self.max_chars),
            )
        )
        chunks = chunker.append(normalized_text, now_ms=0)
        chunks.extend(chunker.flush_final())
        return [chunk for chunk in chunks if chunk]

    async def send_message(
        self,
        receive_id: str,
        content: str,
        msg_type: str = "text",
        receive_id_type: str = "open_id",
    ) -> dict:
        try:
            if msg_type == "text":
                return await self.feishu_client.send_text_message(
                    receive_id=receive_id,
                    text=content,
                    receive_id_type=receive_id_type,
                )
            if msg_type == "interactive_markdown":
                return await self.feishu_client.send_markdown_card_message(
                    receive_id=receive_id,
                    markdown_content=content,
                    receive_id_type=receive_id_type,
                )
            raise ValueError(f"Unsupported msg_type: {msg_type}")
        except Exception as exc:
            logger.error(
                "Failed to send message",
                receive_id=receive_id,
                msg_type=msg_type,
                error=str(exc),
            )
            raise


_block_streaming_managers: dict[str, BlockStreamingManager] = {}


async def get_block_streaming_manager(
    feishu_client: FeishuClient = None,
    config: dict = None,
    app_id: str | None = None,
) -> BlockStreamingManager:
    manager_app_id = app_id
    if manager_app_id is None and feishu_client is not None:
        manager_app_id = feishu_client.app_id
    if not manager_app_id:
        manager_app_id = "__default__"

    if manager_app_id not in _block_streaming_managers:
        from app.feishu.client import get_feishu_client

        client = feishu_client or await get_feishu_client(
            None if manager_app_id == "__default__" else manager_app_id
        )
        _block_streaming_managers[manager_app_id] = BlockStreamingManager(client, config)
    return _block_streaming_managers[manager_app_id]
