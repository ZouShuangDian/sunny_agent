"""
Milvus 向量数据导入脚本
数据源：本地 PG chat_qms.complaints
目标：Milvus quality_knowledge Collection（BM25 + BGE-M3 混合检索）

对齐 init_milvus_v2.py 的 quality_knowledge schema：
  id, doc_id, chunk_type, tags, original_text, dense_vector, sparse_vector

chunk_type: problem / cause / solution / q_a

改进点（相比原 import_milvus_data.py）：
1. 每个 chunk 带上下文元数据（客户/产品/工厂），向量搜索即返回有意义的结果
2. 文本拼接带字段标签，BM25 可按标签精准匹配
3. 幂等：每次 DROP + CREATE，重跑安全
4. tags 规则提取维度字段值（确定性、零成本）
5. q_a chunk：LLM 生成假设性问题，弥合用户口语提问与原始数据的语义鸿沟
"""

import hashlib
import json
import logging
import os
import re
import uuid
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    connections,
    utility,
)
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============ 从 .env 加载配置 ============
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv

_env_path = _Path(__file__).resolve().parent.parent / ".env"
_load_dotenv(_env_path)

PG_CONFIG = {
    "host": os.environ.get("PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("PG_PORT", "5432")),
    "dbname": os.environ.get("PG_DBNAME", "postgres"),
    "user": os.environ.get("PG_USER", "root"),
    "password": os.environ.get("PG_PASSWORD", ""),
}
PG_SCHEMA = os.environ.get("PG_SCHEMA", "chat_qms")

MILVUS_URI = os.environ.get("MILVUS_URI", "http://localhost:19530")
MILVUS_DB = os.environ.get("MILVUS_DB", "default")
MILVUS_USER = os.environ.get("MILVUS_USER", "root")
MILVUS_PASSWORD = os.environ.get("MILVUS_PASSWORD", "")

EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "")
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_TIMEOUT = int(os.environ.get("EMBEDDING_TIMEOUT", "60"))

LLM_MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-20250514")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "60"))

VECTOR_DIM = int(os.environ.get("VECTOR_DIM", "1024"))
COLLECTION_NAME = "quality_knowledge"


# ============ LLM 调用（生成假设性问题） ============
def generate_questions(record_text: str) -> List[str]:
    """调用 LLM 为一条客诉记录生成用户可能会问的假设性问题

    返回 3 个问题，用于创建 q_a chunk 提升检索召回率。
    使用 litellm 调用，支持任意模型（Claude、DeepSeek 等）。
    """
    import litellm

    prompt = f"""任务：基于以下质量问题记录，生成3个用户可能会问的问题。

要求：
1. 问题应该是用户在遇到类似问题时会搜索的自然语言查询。
2. 覆盖现象描述、原因分析和解决方案。
3. 例如："为什么摄像头画面偏红？", "OV64B模组偏色怎么处理？"
4. 输出JSON格式：{{"questions": ["question1", "question2", "question3"]}}

记录内容：
{record_text}"""

    try:
        response = litellm.completion(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
            timeout=LLM_TIMEOUT,
        )

        content = response.choices[0].message.content.strip()

        # 清理 markdown 代码块标记
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        data = json.loads(content)
        questions = data.get("questions", [])
        return [q for q in questions if isinstance(q, str) and q.strip()][:5]
    except Exception as e:
        logger.warning(f"LLM 生成问题失败: {e}")
        return []


# ============ Embedding 调用 ============
def embed_texts(texts: List[str]) -> List[List[float]]:
    """调用 BGE-M3 API 批量生成向量"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {EMBEDDING_API_KEY}",
    }
    payload = {"input": texts, "model": EMBEDDING_MODEL}

    with httpx.Client(timeout=EMBEDDING_TIMEOUT) as client:
        resp = client.post(EMBEDDING_API_BASE, headers=headers, json=payload)
        resp.raise_for_status()

    data = resp.json().get("data", [])
    return [item["embedding"] for item in data]


# ============ PG 读取 ============
def fetch_complaints() -> List[Dict[str, Any]]:
    """从本地 PG 读取客诉记录"""
    conn = psycopg2.connect(**PG_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"SET search_path TO {PG_SCHEMA}")
    cur.execute("SELECT * FROM complaints ORDER BY id")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    logger.info(f"从 PG 读取 {len(rows)} 条记录")
    return rows


# ============ 文本 & 标签构建 ============
def _safe(val) -> Optional[str]:
    """安全取值，None/空字符串返回 None"""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def build_metadata_prefix(row: Dict) -> str:
    """构建元数据前缀（客户/产品/工厂）"""
    parts = []
    for label, field in [("客户", "customer"), ("产品类型", "product_type"),
                          ("产品类别", "product_category"), ("工厂", "factory")]:
        val = _safe(row.get(field))
        if val:
            parts.append(f"【{label}】{val}")
    return " ".join(parts)


def extract_tags(row: Dict) -> List[str]:
    """从记录中提取标签（规则方式，确定性、零成本）

    提取逻辑：
    - 维度字段值：客户、供应商、产品类型、产品类别、责任方、投诉类别、质量因素
    - 短关键词（< 20字符的非空字段值去重）
    """
    tags = set()

    # 维度字段直接作为标签
    for field in ["customer", "supplier", "product_type", "product_category",
                  "responsibility_type", "complaint_category", "quality_factor",
                  "responsibility_unit", "factory"]:
        val = _safe(row.get(field))
        if val and len(val) <= 30:
            tags.add(val)

    # 限制标签数量（tags Array max_capacity=50）
    return list(tags)[:20]


def build_chunks(row: Dict) -> List[Dict]:
    """
    为一条客诉记录生成多个语义 chunk

    chunk_type: problem / cause / solution / q_a
    其中 q_a 通过 LLM 生成假设性问题，提升口语化查询的检索召回率
    """
    chunks = []
    prefix = build_metadata_prefix(row)
    doc_id = str(row["id"])
    tags = extract_tags(row)

    # 收集所有文本片段，用于拼接给 LLM 生成 q_a
    all_text_parts = []

    # 1. problem chunk（不良现象 + 复判）
    problem_parts = []
    for label, field in [("不良现象", "defect_description"),
                          ("智领复判", "internal_review"),
                          ("客户复判", "customer_review")]:
        val = _safe(row.get(field))
        if val:
            problem_parts.append(f"【{label}】{val}")
    if problem_parts:
        text = prefix + " " + " ".join(problem_parts)
        chunks.append({
            "chunk_type": "problem",
            "text": text,
            "doc_id": doc_id,
            "tags": tags,
        })
        all_text_parts.extend(problem_parts)

    # 2. cause chunk（四层原因）
    cause_parts = []
    for label, field in [("发生原因", "occurrence_cause"), ("流出原因", "outflow_cause"),
                          ("系统原因", "system_cause"), ("具体原因", "specific_cause")]:
        val = _safe(row.get(field))
        if val:
            cause_parts.append(f"【{label}】{val}")
    if cause_parts:
        text = prefix + " " + " ".join(cause_parts)
        chunks.append({
            "chunk_type": "cause",
            "text": text,
            "doc_id": doc_id,
            "tags": tags,
        })
        all_text_parts.extend(cause_parts)

    # 3. solution chunk（临时措施 + 三层对策 + 验证）
    solution_parts = []
    for label, field in [("临时措施", "temporary_measures"),
                          ("发生对策", "occurrence_countermeasure"),
                          ("流出对策", "outflow_countermeasure"),
                          ("系统对策", "system_countermeasure"),
                          ("验证情况", "verification_status")]:
        val = _safe(row.get(field))
        if val:
            solution_parts.append(f"【{label}】{val}")
    if solution_parts:
        text = prefix + " " + " ".join(solution_parts)
        chunks.append({
            "chunk_type": "solution",
            "text": text,
            "doc_id": doc_id,
            "tags": tags,
        })
        all_text_parts.extend(solution_parts)

    # 4. q_a chunks（LLM 生成假设性问题）
    if all_text_parts:
        full_record = prefix + "\n" + "\n".join(all_text_parts)
        if len(full_record) > 50:  # 内容太短则跳过
            questions = generate_questions(full_record)
            for idx, q in enumerate(questions):
                qa_text = f"Q: {q}"
                chunks.append({
                    "chunk_type": "q_a",
                    "text": qa_text,
                    "doc_id": doc_id,
                    "tags": [],  # q_a 不需要标签，靠语义向量匹配
                    "_qa_idx": idx,  # 用于生成唯一 ID
                })

    return chunks


def generate_chunk_id(doc_id: str, chunk_type: str, idx: int = 0) -> str:
    """生成确定性 chunk ID（基于内容哈希，重跑稳定）

    对于 q_a 类型，idx 区分同一 doc_id 下的多个问题
    """
    raw = f"{doc_id}:{chunk_type}:{idx}" if chunk_type == "q_a" else f"{doc_id}:{chunk_type}"
    return hashlib.md5(raw.encode()).hexdigest()


# ============ Milvus Collection 管理 ============
def create_collection():
    """创建 quality_knowledge Collection（幂等：先 DROP 再 CREATE）

    schema 对齐 init_milvus_v2.py：
    id, doc_id, chunk_type, tags(Array), original_text, dense_vector, sparse_vector
    """
    if utility.has_collection(COLLECTION_NAME):
        logger.info(f"删除已有 Collection: {COLLECTION_NAME}")
        utility.drop_collection(COLLECTION_NAME)

    analyzer_params = {"tokenizer": "jieba"}

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128,
                     is_primary=True, description="chunk 唯一 ID"),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64,
                     description="关联 complaints.id"),
        FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=32,
                     description="chunk 类型: problem/cause/solution/q_a"),
        FieldSchema(name="tags", dtype=DataType.ARRAY,
                     element_type=DataType.VARCHAR, max_capacity=50, max_length=64,
                     description="标签数组（客户/产品/责任方等维度值）"),
        FieldSchema(name="original_text", dtype=DataType.VARCHAR, max_length=4096,
                     analyzer_params=analyzer_params, enable_analyzer=True,
                     enable_match=True, description="带标签的语义文本"),
        FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM,
                     description="BGE-M3 语义向量"),
        FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR,
                     description="BM25 稀疏向量（自动生成）"),
    ]

    bm25_fn = Function(
        name="bm25_quality_knowledge",
        input_field_names=["original_text"],
        output_field_names=["sparse_vector"],
        function_type=FunctionType.BM25,
    )

    schema = CollectionSchema(
        fields=fields,
        functions=[bm25_fn],
        description="质量知识库向量索引 - 支持混合检索与语义分块",
    )

    collection = Collection(name=COLLECTION_NAME, schema=schema)

    # 创建索引
    collection.create_index("dense_vector", {
        "index_type": "IVF_FLAT",
        "metric_type": "COSINE",
        "params": {"nlist": 128},
    })
    collection.create_index("sparse_vector", {
        "index_type": "SPARSE_INVERTED_INDEX",
        "metric_type": "IP",
    })

    collection.load()
    logger.info(f"Collection '{COLLECTION_NAME}' 创建并加载完成")
    return collection


# ============ 数据导入 ============
def import_chunks(collection: Collection, all_chunks: List[Dict]):
    """批量向量化并插入 Milvus"""
    batch_size = 50
    total = len(all_chunks)
    inserted = 0

    for i in tqdm(range(0, total, batch_size), desc="导入 Milvus"):
        batch = all_chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]

        try:
            vectors = embed_texts(texts)
        except Exception as e:
            logger.error(f"Embedding 失败 (batch {i}): {e}")
            continue

        # 构造插入数据（列式，字段顺序对齐 schema）
        ids = [c["id"] for c in batch]
        doc_ids = [c["doc_id"] for c in batch]
        chunk_types = [c["chunk_type"] for c in batch]
        tags_list = [c["tags"] for c in batch]
        original_texts = [c["text"] for c in batch]

        try:
            collection.insert([ids, doc_ids, chunk_types, tags_list, original_texts, vectors])
            inserted += len(batch)
        except Exception as e:
            logger.error(f"插入失败 (batch {i}): {e}")

    collection.flush()
    logger.info(f"插入完成: {inserted}/{total} 条 chunk")
    return inserted


# ============ 主流程 ============
def main():
    # 1. 连接 Milvus
    logger.info(f"连接 Milvus: {MILVUS_URI}/{MILVUS_DB}")
    connections.connect(
        alias="default",
        uri=MILVUS_URI,
        user=MILVUS_USER,
        password=MILVUS_PASSWORD,
        db_name=MILVUS_DB,
    )

    # 2. 创建 Collection
    collection = create_collection()

    # 3. 从本地 PG 读取数据
    rows = fetch_complaints()

    # 4. 构建 chunks（含 LLM 生成 q_a，耗时较长）
    logger.info("构建语义 chunks（含 LLM 生成 q_a 假设性问题）...")
    all_chunks = []
    chunk_stats = {"problem": 0, "cause": 0, "solution": 0, "q_a": 0}

    for row in tqdm(rows, desc="构建 chunks"):
        chunks = build_chunks(row)
        for c in chunks:
            qa_idx = c.pop("_qa_idx", 0)
            c["id"] = generate_chunk_id(c["doc_id"], c["chunk_type"], qa_idx)
            chunk_stats[c["chunk_type"]] += 1
        all_chunks.extend(chunks)

    logger.info(
        f"共生成 {len(all_chunks)} 个 chunks: "
        f"problem={chunk_stats['problem']}, "
        f"cause={chunk_stats['cause']}, "
        f"solution={chunk_stats['solution']}, "
        f"q_a={chunk_stats['q_a']}"
    )

    # 5. 导入 Milvus
    inserted = import_chunks(collection, all_chunks)

    # 6. 验证
    logger.info(f"Collection 统计: {collection.num_entities} 条记录")

    connections.disconnect("default")
    logger.info("全部完成")


if __name__ == "__main__":
    main()
