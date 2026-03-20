#!/usr/bin/env python3
"""Validate all connections defined in eval_config.yaml."""

import json
import sys
import os
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "eval_config.yaml"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def check_pg(raw: dict) -> dict:
    pg = raw.get("data", {}).get("pg", {})
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=pg.get("host", "127.0.0.1"),
            port=pg.get("port", 5432),
            dbname=pg.get("dbname", "postgres"),
            user=pg.get("user", "root"),
            password=pg.get("password", ""),
        )
        conn.close()
        return {"status": "ok", "message": "连接成功"}
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def check_milvus(raw: dict) -> dict:
    mv = raw.get("data", {}).get("milvus", {})
    try:
        from pymilvus import connections
        connections.connect(
            alias="_validate",
            uri=mv.get("uri", "http://localhost:19530"),
            user=mv.get("user", "root"),
            password=mv.get("password", ""),
            db_name="default",
        )
        connections.disconnect("_validate")
        return {"status": "ok", "message": "连接成功"}
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def check_langfuse(raw: dict) -> dict:
    lf = raw.get("langfuse", {})
    try:
        import httpx
        resp = httpx.get(
            f"{lf.get('host', 'http://localhost:3000').rstrip('/')}/api/public/health",
            auth=(lf.get("public_key", ""), lf.get("secret_key", "")),
            timeout=10,
        )
        if resp.status_code == 200:
            return {"status": "ok", "message": "连接成功"}
        return {"status": "fail", "message": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def check_target(raw: dict) -> dict:
    t = raw.get("target", {})
    try:
        import httpx
        resp = httpx.get(
            f"{t.get('base_url', 'http://localhost:8000').rstrip('/')}/docs",
            timeout=5,
        )
        if resp.status_code == 200:
            return {"status": "ok", "message": "可达"}
        return {"status": "warn", "message": f"HTTP {resp.status_code}（可能正常）"}
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def main():
    raw = load_config()
    results = {
        "postgresql": check_pg(raw),
        "milvus": check_milvus(raw),
        "langfuse": check_langfuse(raw),
        "target": check_target(raw),
    }

    all_ok = True
    for name, r in results.items():
        icon = "OK" if r["status"] == "ok" else ("WARN" if r["status"] == "warn" else "FAIL")
        print(f"  {name}: {icon} — {r['message']}")
        if r["status"] == "fail":
            all_ok = False

    if all_ok:
        print("\n  所有连接验证通过!")
    else:
        print("\n  部分连接失败，请检查配置")
        sys.exit(1)


if __name__ == "__main__":
    main()
