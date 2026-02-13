"""
Embedding 客户端：调用私有部署的 bge-m3 模型生成向量

接口兼容 OpenAI /v1/embeddings 协议。
"""

import structlog
import httpx

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()


class EmbeddingClient:
    """Embedding 向量化客户端"""

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ):
        self.api_base = (api_base or settings.EMBEDDING_API_BASE).rstrip("/")
        self.api_key = api_key or settings.EMBEDDING_API_KEY
        self.model = model or settings.EMBEDDING_MODEL
        self.timeout = timeout or settings.EMBEDDING_TIMEOUT

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        批量生成 embedding 向量。

        Args:
            texts: 待向量化的文本列表

        Returns:
            与 texts 等长的向量列表，每个向量维度为 EMBEDDING_DIM
        """
        if not texts:
            return []

        url = f"{self.api_base}/v1/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "input": texts,
        }

        log.debug("Embedding 请求", count=len(texts), model=self.model)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # OpenAI 协议：data.data[i].embedding
            embeddings = [item["embedding"] for item in data["data"]]

            log.debug("Embedding 完成", count=len(embeddings), dim=len(embeddings[0]))
            return embeddings

        except Exception as e:
            log.error("Embedding 调用失败", error=str(e), exc_info=True)
            raise

    async def embed_single(self, text: str) -> list[float]:
        """单条文本向量化"""
        results = await self.embed([text])
        return results[0]
