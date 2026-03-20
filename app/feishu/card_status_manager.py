import asyncio
import structlog

from app.feishu.block_streaming import BlockStreamingManager, get_block_streaming_manager
from app.feishu.client import FeishuClient, get_feishu_client
from app.feishu.markdown_sanitizer import normalize_markdown_headings

logger = structlog.get_logger()


class CardStatusManager:
    """Thin final-reply sender for Feishu."""

    def __init__(
        self,
        *,
        block_streaming_manager: BlockStreamingManager,
        feishu_client: FeishuClient,
        open_id: str,
        chat_id: str,
        app_id: str = "",
    ):
        self.block_streaming_manager = block_streaming_manager
        self.feishu_client = feishu_client
        self.open_id = open_id
        self.chat_id = chat_id
        self.app_id = app_id

    async def complete(
        self,
        final_answer: str,
        send_as_message: bool = True,
        receive_id: str | None = None,
        receive_id_type: str | None = None,
        open_id: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        if not send_as_message:
            logger.error("Unsupported final reply mode", send_as_message=send_as_message)
            return False

        active_receive_id = receive_id
        active_receive_id_type = receive_id_type
        if not (active_receive_id and active_receive_id_type):
            logger.error("Missing final reply target", open_id=open_id or self.open_id, chat_id=chat_id or self.chat_id)
            return False

        normalized_answer = normalize_markdown_headings(final_answer or "", max_level=4)
        chunks = self.block_streaming_manager.chunk_text(normalized_answer) if normalized_answer else [""]

        try:
            for chunk_index, chunk in enumerate(chunks, start=1):
                try:
                    await self.block_streaming_manager.send_message(
                        receive_id=active_receive_id,
                        content=chunk,
                        msg_type="interactive_markdown",
                        receive_id_type=active_receive_id_type,
                    )
                except Exception as send_err:
                    logger.warning(
                        "Failed to send markdown final reply chunk, falling back to text",
                        chunk_index=chunk_index,
                        error=str(send_err),
                    )
                    await self.block_streaming_manager.send_message(
                        receive_id=active_receive_id,
                        content=chunk,
                        msg_type="text",
                        receive_id_type=active_receive_id_type,
                    )
                await asyncio.sleep(0.3)

            logger.info(
                "Final answer sent",
                receive_id=active_receive_id,
                answer_length=len(normalized_answer),
                chunk_count=len(chunks),
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to send final answer",
                receive_id=active_receive_id,
                error=str(exc),
            )
            return False


async def get_card_status_manager(
    open_id: str,
    chat_id: str,
    app_id: str = "",
    feishu_client: FeishuClient = None,
    block_streaming_manager: BlockStreamingManager = None,
) -> CardStatusManager:
    client = feishu_client or (await get_feishu_client(app_id) if app_id else await get_feishu_client())
    manager = block_streaming_manager or await get_block_streaming_manager(client, app_id=app_id)
    return CardStatusManager(
        block_streaming_manager=manager,
        feishu_client=client,
        open_id=open_id,
        chat_id=chat_id,
        app_id=app_id,
    )
