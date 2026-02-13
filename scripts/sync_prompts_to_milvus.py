"""
Milvus 同步脚本：从 PG 读取 L1 Prompt 模板，生成 embedding 后写入 Milvus

运行方式：
    poetry run python scripts/sync_prompts_to_milvus.py

同步策略：
1. 从 PG 读取所有 is_active=True 的 Prompt 模板
2. 使用 Embedding API 对 match_text 生成向量
3. 清空 Milvus 中旧数据，全量写入新数据（简单可靠）

后续可优化为增量同步（基于 updated_at 比较）。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import get_settings
from app.db.engine import async_session
from app.db.models.template import PromptTemplate
from app.vectorstore.embedding import EmbeddingClient
from app.vectorstore.milvus_client import (
    COLLECTION_NAME,
    ensure_collection,
    get_milvus_connection,
)

settings = get_settings()


async def load_prompts_from_pg() -> list[dict]:
    """从 PG 读取所有启用的 Prompt 模板"""
    async with async_session() as session:
        stmt = select(PromptTemplate).where(PromptTemplate.is_active.is_(True))
        result = await session.execute(stmt)
        templates = result.scalars().all()

        records = []
        for t in templates:
            records.append({
                "template_id": str(t.id),
                "name": t.name,
                "tier": t.tier,
                "match_text": t.match_text,
                "prompt_content": t.template,
                "intent_tags": json.dumps(t.intent_tags, ensure_ascii=False),
                "is_default": t.is_default,
                "version": t.version,
            })

        return records


async def generate_embeddings(records: list[dict]) -> list[list[float]]:
    """批量生成 embedding"""
    client = EmbeddingClient()
    texts = [r["match_text"] for r in records]
    return await client.embed(texts)


def sync_to_milvus(records: list[dict], embeddings: list[list[float]]):
    """写入 Milvus（全量替换）"""
    collection = ensure_collection()

    # 先清空旧数据
    count_before = collection.num_entities
    if count_before > 0:
        # 删除所有数据
        collection.delete(expr="id >= 0")
        collection.flush()
        print(f"  已清空旧数据 ({count_before} 条)")

    # 准备插入数据
    insert_data = [
        [r["template_id"] for r in records],      # template_id
        [r["name"] for r in records],              # name
        [r["tier"] for r in records],              # tier
        [r["match_text"] for r in records],        # match_text
        [r["prompt_content"] for r in records],    # prompt_content
        [r["intent_tags"] for r in records],       # intent_tags
        [r["is_default"] for r in records],        # is_default
        [r["version"] for r in records],           # version
        embeddings,                                 # embedding
    ]

    collection.insert(insert_data)
    collection.flush()

    print(f"  已写入 {len(records)} 条 Prompt 模板到 Milvus")
    print(f"  集合: {COLLECTION_NAME}")
    print(f"  向量维度: {len(embeddings[0])}")


async def main():
    """主流程"""
    # 1. 从 PG 读取
    print("\n[1/3] 从 PG 读取 Prompt 模板...")
    records = await load_prompts_from_pg()
    if not records:
        print("  未找到任何启用的 Prompt 模板，退出")
        return

    print(f"  读取到 {len(records)} 条模板：")
    for r in records:
        print(f"    - {r['name']} (tier={r['tier']}, default={r['is_default']})")

    # 2. 生成 Embedding
    print("\n[2/3] 生成 Embedding 向量...")
    embeddings = await generate_embeddings(records)
    print(f"  生成完成，维度: {len(embeddings[0])}")

    # 3. 写入 Milvus
    print("\n[3/3] 写入 Milvus...")
    sync_to_milvus(records, embeddings)

    print("\n=== 同步完成 ===")


if __name__ == "__main__":
    print("=== 开始同步 L1 Prompt 模板到 Milvus ===")
    asyncio.run(main())
