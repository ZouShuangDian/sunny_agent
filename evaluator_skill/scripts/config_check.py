#!/usr/bin/env python3
"""Check eval_config.yaml completeness. Outputs JSON status for each field."""

import json
import sys
import os
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "eval_config.yaml"

SENSITIVE_FIELDS = {"target.token", "langfuse.secret_key", "data.pg.password", "data.milvus.password"}

FIELDS = [
    ("target.base_url", "业务系统地址"),
    ("target.token", "Bearer Token"),
    ("langfuse.host", "Langfuse 地址"),
    ("langfuse.public_key", "Langfuse Public Key"),
    ("langfuse.secret_key", "Langfuse Secret Key"),
    ("judge.model", "评分 LLM 模型"),
    ("data.excel_file", "Excel 数据文件路径"),
    ("data.pg.host", "PG Host"),
    ("data.pg.port", "PG Port"),
    ("data.pg.dbname", "PG Database"),
    ("data.pg.user", "PG User"),
    ("data.pg.password", "PG Password"),
    ("data.pg.schema", "PG Schema"),
    ("data.milvus.uri", "Milvus URI"),
    ("data.milvus.db", "Milvus Database"),
    ("data.milvus.user", "Milvus User"),
    ("data.milvus.password", "Milvus Password"),
    ("data.milvus.collection", "Milvus Collection"),
]


def get_nested(d: dict, key: str):
    parts = key.split(".")
    for p in parts:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def main():
    raw = {}
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}

    result = {}
    missing_count = 0

    for key, label in FIELDS:
        val = get_nested(raw, key)
        is_sensitive = key in SENSITIVE_FIELDS
        entry = {"label": label}

        if val is None or (isinstance(val, str) and val.strip() == ""):
            entry["status"] = "missing"
            entry["value"] = ""
            missing_count += 1
        else:
            entry["status"] = "ok"
            if is_sensitive:
                entry["value"] = "****"
                entry["masked"] = True
            else:
                entry["value"] = str(val)

        # Extra check for excel_file
        if key == "data.excel_file" and entry["status"] == "ok":
            p = Path(val)
            if not p.is_absolute():
                p = Path.cwd() / p
            entry["file_exists"] = p.exists()
            if not p.exists():
                entry["status"] = "file_not_found"
                missing_count += 1

        result[key] = entry

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if missing_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
