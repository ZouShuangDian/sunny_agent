"""
Milvus 客户端：管理 L1 Prompt 模板的向量集合

集合名称：l1_prompt_templates
用途：根据用户输入语义检索最匹配的 Prompt 模板
"""

import structlog
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    connections,
    utility,
)

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

# 集合名称
COLLECTION_NAME = "l1_prompt_templates"

# 集合 Schema 定义
_FIELDS = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="template_id", dtype=DataType.VARCHAR, max_length=64, description="PG 表主键 UUID"),
    FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=100, description="模板名称"),
    FieldSchema(name="tier", dtype=DataType.VARCHAR, max_length=10, description="执行层级: L1/L3"),
    FieldSchema(name="match_text", dtype=DataType.VARCHAR, max_length=2000, description="用于 embedding 匹配的文本"),
    FieldSchema(name="prompt_content", dtype=DataType.VARCHAR, max_length=8000, description="Prompt 正文"),
    FieldSchema(name="intent_tags", dtype=DataType.VARCHAR, max_length=500, description="意图标签 JSON"),
    FieldSchema(name="is_default", dtype=DataType.BOOL, description="是否为默认 prompt"),
    FieldSchema(name="version", dtype=DataType.VARCHAR, max_length=16, description="版本号"),
    FieldSchema(
        name="embedding",
        dtype=DataType.FLOAT_VECTOR,
        dim=settings.EMBEDDING_DIM,
        description="match_text 的向量表示",
    ),
]

_SCHEMA = CollectionSchema(
    fields=_FIELDS,
    description="L1 Prompt 模板向量集合，用于语义检索最匹配的 Prompt",
)


def get_milvus_connection():
    """获取 Milvus 连接（使用 alias 管理）"""
    alias = "default"
    # 检查是否已有活跃连接
    if connections.has_connection(alias):
        return alias

    connections.connect(
        alias=alias,
        uri=settings.MILVUS_URI,
        user=settings.MILVUS_USER,
        password=settings.MILVUS_PASSWORD,
        db_name=settings.MILVUS_DB,
    )
    log.info("Milvus 已连接", uri=settings.MILVUS_URI, db=settings.MILVUS_DB)
    return alias


def ensure_collection() -> Collection:
    """
    确保集合存在并创建索引。
    如果集合不存在则创建；如果已存在则直接返回。
    """
    alias = get_milvus_connection()

    if utility.has_collection(COLLECTION_NAME, using=alias):
        collection = Collection(name=COLLECTION_NAME, using=alias)
        collection.load()
        return collection

    # 创建集合
    collection = Collection(
        name=COLLECTION_NAME,
        schema=_SCHEMA,
        using=alias,
    )
    log.info("Milvus 集合已创建", collection=COLLECTION_NAME)

    # 创建 HNSW 索引（小数据集高召回率）
    index_params = {
        "metric_type": "COSINE",
        "index_type": "HNSW",
        "params": {"M": 16, "efConstruction": 256},
    }
    collection.create_index(field_name="embedding", index_params=index_params)
    log.info("Milvus 索引已创建", index_type="HNSW", metric="COSINE")

    collection.load()
    return collection


def search_prompt(
    collection: Collection,
    query_embedding: list[float],
    tier: str = "L1",
    top_k: int | None = None,
) -> list[dict]:
    """
    语义检索最匹配的 Prompt 模板。

    Args:
        collection: Milvus 集合
        query_embedding: 查询向量
        tier: 执行层级过滤
        top_k: 返回数量

    Returns:
        匹配结果列表，每项包含 template_id, name, prompt_content, score 等
    """
    top_k = top_k or settings.PROMPT_SEARCH_TOP_K

    results = collection.search(
        data=[query_embedding],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"ef": 128}},
        limit=top_k,
        expr=f'tier == "{tier}"',
        output_fields=["template_id", "name", "prompt_content", "match_text", "intent_tags", "is_default"],
    )

    matches = []
    for hit in results[0]:
        matches.append({
            "template_id": hit.entity.get("template_id"),
            "name": hit.entity.get("name"),
            "prompt_content": hit.entity.get("prompt_content"),
            "match_text": hit.entity.get("match_text"),
            "intent_tags": hit.entity.get("intent_tags"),
            "is_default": hit.entity.get("is_default"),
            "score": hit.score,
        })

    return matches
