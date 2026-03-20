"""Self-contained eval library — no dependency on evaluation/ module.

Consolidates: config, parser, judge, runner, data_manager.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import litellm
import psycopg2
import yaml
from langfuse import Langfuse

logger = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "eval_config.yaml"


# ============================================================
# Config
# ============================================================

@dataclass
class TargetConfig:
    base_url: str = "http://localhost:8000"
    chat_endpoint: str = "/api/chat"
    usernumb: str = ""
    password: str = ""
    token: str = ""
    timeout: int = 120


@dataclass
class LangfuseConfig:
    host: str = "http://localhost:3000"
    public_key: str = ""
    secret_key: str = ""


@dataclass
class JudgeDimension:
    name: str = ""
    description: str = ""
    weight: float = 1.0


@dataclass
class JudgeConfig:
    model: str = "deepseek/deepseek-chat"
    dimensions: list[JudgeDimension] = field(default_factory=list)


@dataclass
class PgConfig:
    host: str = "127.0.0.1"
    port: int = 5432
    dbname: str = "postgres"
    user: str = "root"
    password: str = "abc123!"
    schema: str = "chat_qms"


@dataclass
class MilvusConfig:
    uri: str = "http://localhost:19530"
    db: str = "sunny_agent"
    user: str = "root"
    password: str = "Milvus"
    collection: str = "quality_knowledge"


@dataclass
class DataConfig:
    excel_file: str = ""
    pg: PgConfig = field(default_factory=PgConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)


@dataclass
class EvalConfig:
    target: TargetConfig = field(default_factory=TargetConfig)
    langfuse: LangfuseConfig = field(default_factory=LangfuseConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    data: DataConfig = field(default_factory=DataConfig)


def load_config(config_path: str | Path | None = None) -> EvalConfig:
    if config_path is None:
        config_path = CONFIG_PATH
    config_path = Path(config_path)
    raw: dict = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    t = raw.get("target", {})
    target = TargetConfig(
        base_url=os.environ.get("EVAL_TARGET_URL", t.get("base_url", "http://localhost:8000")),
        chat_endpoint=t.get("chat_endpoint", "/api/chat"),
        usernumb=os.environ.get("EVAL_USERNUMB", t.get("usernumb", "")),
        password=os.environ.get("EVAL_PASSWORD", t.get("password", "")),
        token=os.environ.get("EVAL_TARGET_TOKEN", t.get("token", "")),
        timeout=int(t.get("timeout", 120)),
    )
    lf = raw.get("langfuse", {})
    langfuse = LangfuseConfig(
        host=os.environ.get("LANGFUSE_HOST", lf.get("host", "http://localhost:3000")),
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", lf.get("public_key", "")),
        secret_key=os.environ.get("LANGFUSE_SECRET_KEY", lf.get("secret_key", "")),
    )
    jr = raw.get("judge", {})
    dims = [JudgeDimension(d.get("name", ""), d.get("description", ""), float(d.get("weight", 1.0)))
            for d in jr.get("dimensions", [])]
    judge_cfg = JudgeConfig(
        model=os.environ.get("EVAL_JUDGE_MODEL", jr.get("model", "deepseek/deepseek-chat")),
        dimensions=dims or [
            JudgeDimension("correctness", "数据和数字的准确性", 0.5),
            JudgeDimension("completeness", "是否涵盖关键信息", 0.3),
            JudgeDimension("relevance", "回答是否紧扣问题", 0.2),
        ],
    )
    dr = raw.get("data", {})
    pr = dr.get("pg", {})
    mr = dr.get("milvus", {})
    data = DataConfig(
        excel_file=dr.get("excel_file", ""),
        pg=PgConfig(pr.get("host", "127.0.0.1"), int(pr.get("port", 5432)),
                    pr.get("dbname", "postgres"), pr.get("user", "root"),
                    pr.get("password", "abc123!"), pr.get("schema", "chat_qms")),
        milvus=MilvusConfig(mr.get("uri", "http://localhost:19530"), mr.get("db", "sunny_agent"),
                            mr.get("user", "root"), mr.get("password", "Milvus"),
                            mr.get("collection", "quality_knowledge")),
    )
    return EvalConfig(target=target, langfuse=langfuse, judge=judge_cfg, data=data)


# ============================================================
# Markdown Parser
# ============================================================

def parse_markdown(file_path: str | Path) -> list[dict]:
    text = Path(file_path).read_text(encoding="utf-8")
    items: list[dict] = []
    pattern = re.compile(
        r"##\s+(Q\d+)\s*[:：]\s*(.+?)(?:\n)(.*?)(?=\n##\s+Q\d+|\Z)",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        item_key = m.group(1).strip()
        question = m.group(2).strip()
        body = m.group(3).strip()
        out = re.search(r"\*\*输出[:：]?\*\*\s*\n(.*?)(?:\n---\s*$|\Z)", body, re.DOTALL | re.MULTILINE)
        expected = out.group(1).strip() if out else ""
        items.append({"item_key": item_key, "input_text": question, "expected_output": expected})
    return items


# ============================================================
# LLM-as-a-Judge
# ============================================================

JUDGE_SYSTEM_PROMPT = """\
你是一个专业的质量评估专家。你的任务是对比"期望答案"和"实际答案"，从多个维度进行评分。

评分规则：
- 每个维度的分数范围是 0.0 到 1.0
- 0.0 = 完全不符合，0.5 = 部分符合，1.0 = 完全符合
- 如果期望答案为空，则所有维度评分为 1.0

你必须以 JSON 格式返回评分结果，不要输出其他内容。
"""


async def judge(question: str, expected: str, actual: str,
                dimensions: list[JudgeDimension], model: str) -> dict:
    if not expected.strip():
        r = {d.name: 1.0 for d in dimensions}
        r["reasoning"] = "无期望答案，默认满分"
        r["weighted_score"] = 1.0
        return r

    dim_desc = "\n".join(f"- {d.name}: {d.description}" for d in dimensions)
    dim_keys = ", ".join(f'"{d.name}": <float>' for d in dimensions)
    prompt = f"## 用户问题\n{question}\n\n## 期望答案\n{expected}\n\n## 实际答案\n{actual}\n\n## 评分维度\n{dim_desc}\n\n请返回 JSON 格式：\n{{{{\n  {dim_keys},\n  \"reasoning\": \"<简要评分理由>\"\n}}}}"

    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}],
            temperature=0.0, response_format={"type": "json_object"},
        )
        scores = json.loads(resp.choices[0].message.content.strip())
    except Exception as e:
        logger.warning("Judge failed: %s", e)
        scores = {d.name: 0.0 for d in dimensions}
        scores["reasoning"] = f"评分失败: {e}"

    tw = sum(d.weight for d in dimensions)
    ws = sum(scores.get(d.name, 0.0) * d.weight for d in dimensions)
    scores["weighted_score"] = round(ws / tw, 4) if tw > 0 else 0.0
    return scores


# ============================================================
# Eval Runner (Langfuse)
# ============================================================

class EvalRunner:
    def __init__(self, config: EvalConfig):
        self.config = config
        self.langfuse = Langfuse(
            public_key=config.langfuse.public_key,
            secret_key=config.langfuse.secret_key,
            host=config.langfuse.host,
        )

    async def import_dataset(self, md_file: str | Path, dataset_name: str) -> int:
        items = parse_markdown(md_file)
        if not items:
            return 0
        self.langfuse.create_dataset(name=dataset_name)
        for item in items:
            self.langfuse.create_dataset_item(
                dataset_name=dataset_name,
                input=item["input_text"],
                expected_output=item["expected_output"],
                metadata={"item_key": item["item_key"]},
            )
            logger.info("  Imported %s: %s", item["item_key"], item["input_text"][:50])
        self.langfuse.flush()
        logger.info("Imported %d items into dataset '%s'", len(items), dataset_name)
        return len(items)

    async def run(self, dataset_name: str, run_name: str) -> dict:
        dataset = self.langfuse.get_dataset(dataset_name)
        results = []
        async with httpx.AsyncClient(timeout=self.config.target.timeout) as client:
            await self._ensure_token(client)
            for i, item in enumerate(dataset.items):
                item_key = (item.metadata or {}).get("item_key", f"item_{i}")
                question = item.input if isinstance(item.input, str) else str(item.input)
                logger.info("[%d/%d] %s: %s", i + 1, len(dataset.items), item_key, question[:60])

                actual = await self._call_chat(client, question)
                scores = await judge(question, item.expected_output or "", actual,
                                     self.config.judge.dimensions, self.config.judge.model)

                with item.run(run_name=run_name) as span:
                    span.update(name="eval_run", input=question, output=actual,
                                metadata={"dataset": dataset_name, "run_name": run_name, "item_key": item_key})
                    for dim in self.config.judge.dimensions:
                        self.langfuse.create_score(trace_id=span.trace_id, observation_id=span.id,
                                                   name=dim.name, value=scores.get(dim.name, 0.0))
                    self.langfuse.create_score(trace_id=span.trace_id, observation_id=span.id,
                                               name="weighted_score", value=scores.get("weighted_score", 0.0))

                entry = {
                    "item_key": item_key, "question": question, "actual_output": actual[:200],
                    "scores": {d.name: scores.get(d.name, 0.0) for d in self.config.judge.dimensions},
                    "weighted_score": scores.get("weighted_score", 0.0),
                    "reasoning": scores.get("reasoning", ""),
                }
                results.append(entry)
                logger.info("  → weighted=%.2f | %s", entry["weighted_score"],
                            " | ".join(f"{d.name}={scores.get(d.name, 0.0):.2f}" for d in self.config.judge.dimensions))

        self.langfuse.flush()
        avg = sum(r["weighted_score"] for r in results) / len(results) if results else 0.0
        return {"dataset": dataset_name, "run_name": run_name,
                "total_items": len(results), "avg_weighted_score": round(avg, 4), "results": results}

    async def _ensure_token(self, client: httpx.AsyncClient) -> None:
        if self.config.target.token:
            return
        cfg = self.config.target
        if not cfg.usernumb or not cfg.password:
            logger.warning("No token and no usernumb/password configured")
            return
        try:
            resp = await client.post(f"{cfg.base_url.rstrip('/')}/api/auth/login",
                                     json={"usernumb": cfg.usernumb, "password": cfg.password})
            resp.raise_for_status()
            token = resp.json().get("data", {}).get("access_token", "")
            if token:
                self.config.target.token = token
                logger.info("Auto-login successful for '%s'", cfg.usernumb)
        except Exception as e:
            logger.error("Auto-login failed: %s", e)

    async def _call_chat(self, client: httpx.AsyncClient, message: str) -> str:
        url = f"{self.config.target.base_url.rstrip('/')}{self.config.target.chat_endpoint}"
        headers = {"Authorization": f"Bearer {self.config.target.token}"} if self.config.target.token else {}
        try:
            resp = await client.post(url, json={"message": message}, headers=headers)
            resp.raise_for_status()
            return resp.json().get("data", {}).get("reply", "")
        except Exception as e:
            logger.error("Chat API failed: %s", e)
            return f"ERROR: {e}"

    def list_datasets(self) -> list[dict]:
        result = self.langfuse.api.datasets.list()
        return [{"name": ds.name, "item_count": getattr(ds, "items_count", "?"),
                 "created_at": str(ds.created_at) if hasattr(ds, "created_at") else ""}
                for ds in result.data]


# ============================================================
# Data Manager (PG + Milvus)
# ============================================================

class DataManager:
    def __init__(self, config: EvalConfig):
        self.config = config

    def setup(self, skip_milvus: bool = False) -> None:
        self._setup_pg()
        if not skip_milvus:
            self._setup_milvus()

    def teardown(self) -> None:
        self._teardown_pg()
        self._teardown_milvus()

    def status(self) -> dict:
        return {"pg": self._pg_status(), "milvus": self._milvus_status()}

    # -- PG --

    def _setup_pg(self) -> None:
        cfg = self.config.data
        excel_path = Path(cfg.excel_file)
        if not excel_path.is_absolute():
            excel_path = Path.cwd() / excel_path
        if not excel_path.exists():
            logger.error("Excel not found: %s", excel_path)
            sys.exit(1)

        import pandas as pd
        sys.path.insert(0, str(SKILL_DIR))
        from qmsdata import import_to_local_pg as pg_script

        pg_script.DB_CONFIG = {"host": cfg.pg.host, "port": cfg.pg.port,
                               "dbname": cfg.pg.dbname, "user": cfg.pg.user, "password": cfg.pg.password}
        pg_script.SCHEMA = cfg.pg.schema

        logger.info("PG setup: %s → %s.%s", excel_path, cfg.pg.dbname, cfg.pg.schema)
        df = pg_script.clean_excel(pd.read_excel(excel_path))
        conn = pg_script.get_connection()
        try:
            pg_script.init_table(conn)
            pg_script.import_data(conn, df)
            pg_script.verify(conn)
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()
        logger.info("PG setup complete")

    def _teardown_pg(self) -> None:
        cfg = self.config.data.pg
        try:
            conn = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname,
                                    user=cfg.user, password=cfg.password)
            conn.autocommit = True
            conn.cursor().execute(f"DROP SCHEMA IF EXISTS {cfg.schema} CASCADE")
            conn.close()
            logger.info("PG teardown: dropped schema '%s'", cfg.schema)
        except Exception as e:
            logger.error("PG teardown failed: %s", e)

    def _pg_status(self) -> dict:
        cfg = self.config.data.pg
        try:
            conn = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname,
                                    user=cfg.user, password=cfg.password)
            cur = conn.cursor()
            cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s", (cfg.schema,))
            if not cur.fetchone():
                conn.close()
                return {"status": "not_initialized", "schema": cfg.schema}
            cur.execute(f"SET search_path TO {cfg.schema}")
            cur.execute("SELECT COUNT(*) FROM complaints")
            count = cur.fetchone()[0]
            cur.execute("SELECT MIN(report_date), MAX(report_date) FROM complaints")
            mn, mx = cur.fetchone()
            conn.close()
            return {"status": "ready", "schema": cfg.schema, "row_count": count, "date_range": f"{mn} ~ {mx}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # -- Milvus --

    def _setup_milvus(self) -> None:
        cfg = self.config.data
        sys.path.insert(0, str(SKILL_DIR))
        from qmsdata import import_to_local_milvus as mv

        mv.PG_CONFIG = {"host": cfg.pg.host, "port": cfg.pg.port,
                        "dbname": cfg.pg.dbname, "user": cfg.pg.user, "password": cfg.pg.password}
        mv.PG_SCHEMA = cfg.pg.schema
        mv.MILVUS_URI = cfg.milvus.uri
        mv.MILVUS_DB = cfg.milvus.db
        mv.MILVUS_USER = cfg.milvus.user
        mv.MILVUS_PASSWORD = cfg.milvus.password
        mv.COLLECTION_NAME = cfg.milvus.collection

        logger.info("Milvus setup: %s.%s → %s/%s", cfg.pg.dbname, cfg.pg.schema, cfg.milvus.uri, cfg.milvus.collection)
        self._ensure_milvus_db(cfg.milvus)
        mv.main()
        logger.info("Milvus setup complete")

    @staticmethod
    def _ensure_milvus_db(mcfg) -> None:
        from pymilvus import connections, db
        connections.connect(alias="_edb", uri=mcfg.uri, user=mcfg.user, password=mcfg.password, db_name="default")
        if mcfg.db not in db.list_database(using="_edb"):
            db.create_database(mcfg.db, using="_edb")
            logger.info("Created Milvus database '%s'", mcfg.db)
        connections.disconnect("_edb")

    def _teardown_milvus(self) -> None:
        cfg = self.config.data.milvus
        try:
            from pymilvus import connections, utility
            connections.connect(alias="default", uri=cfg.uri, user=cfg.user, password=cfg.password, db_name=cfg.db)
            if utility.has_collection(cfg.collection):
                utility.drop_collection(cfg.collection)
                logger.info("Milvus teardown: dropped '%s'", cfg.collection)
            connections.disconnect("default")
        except Exception as e:
            logger.error("Milvus teardown failed: %s", e)

    def _milvus_status(self) -> dict:
        cfg = self.config.data.milvus
        try:
            from pymilvus import Collection, connections, utility
            connections.connect(alias="default", uri=cfg.uri, user=cfg.user, password=cfg.password, db_name=cfg.db)
            if not utility.has_collection(cfg.collection):
                connections.disconnect("default")
                return {"status": "not_initialized", "collection": cfg.collection}
            count = Collection(cfg.collection).num_entities
            connections.disconnect("default")
            return {"status": "ready", "collection": cfg.collection, "entity_count": count}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ============================================================
# CLI helpers
# ============================================================

def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
