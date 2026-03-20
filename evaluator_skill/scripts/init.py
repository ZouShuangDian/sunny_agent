#!/usr/bin/env python3
"""Interactive configuration initializer for evaluator_skill.

Checks eval_config.yaml, prompts for missing values, validates connections.
"""

import getpass
import sys
import os
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "eval_config.yaml"


def load_raw_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def save_config(raw: dict) -> None:
    CONFIG_PATH.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
                           encoding="utf-8")
    print(f"\n  Config saved to {CONFIG_PATH}")


def prompt_field(label: str, current: str, secret: bool = False) -> str:
    """Prompt user for a field value. Shows current value for confirmation."""
    if current:
        display = "****" if secret else current
        hint = input(f"  {label} [{display}]: ").strip()
        return hint if hint else current
    else:
        if secret:
            val = getpass.getpass(f"  {label}: ")
        else:
            val = input(f"  {label}: ").strip()
        return val


def prompt_section(title: str):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}")


def configure_target(raw: dict) -> dict:
    t = raw.setdefault("target", {})
    prompt_section("业务系统配置")
    t["base_url"] = prompt_field("业务系统地址 (base_url)", t.get("base_url", "http://localhost:8000"))
    t["chat_endpoint"] = t.get("chat_endpoint", "/api/chat")
    t["token"] = prompt_field("Bearer Token", t.get("token", ""), secret=True)
    t["timeout"] = int(t.get("timeout", 120))
    return raw


def configure_langfuse(raw: dict) -> dict:
    lf = raw.setdefault("langfuse", {})
    prompt_section("Langfuse 配置")
    lf["host"] = prompt_field("Langfuse 地址 (host)", lf.get("host", "http://localhost:3000"))
    lf["public_key"] = prompt_field("Public Key", lf.get("public_key", ""))
    lf["secret_key"] = prompt_field("Secret Key", lf.get("secret_key", ""), secret=True)
    return raw


def configure_data(raw: dict) -> dict:
    d = raw.setdefault("data", {})
    prompt_section("数据环境配置")

    # Excel file
    excel = d.get("excel_file", "")
    while True:
        excel = prompt_field("Excel 数据文件路径", excel)
        if not excel:
            print("    请提供 Excel 文件路径")
            continue
        p = Path(excel)
        if not p.is_absolute():
            p = Path.cwd() / p
        if p.exists():
            d["excel_file"] = excel
            print(f"    文件存在: {p}")
            break
        else:
            print(f"    文件不存在: {p}，请重新输入")
            excel = ""

    # PG
    print("\n  -- PostgreSQL --")
    pg = d.setdefault("pg", {})
    pg["host"] = prompt_field("PG Host", pg.get("host", "127.0.0.1"))
    pg["port"] = int(prompt_field("PG Port", str(pg.get("port", 5432))))
    pg["dbname"] = prompt_field("PG Database", pg.get("dbname", "postgres"))
    pg["user"] = prompt_field("PG User", pg.get("user", "root"))
    pg["password"] = prompt_field("PG Password", pg.get("password", ""), secret=True)
    pg["schema"] = prompt_field("PG Schema", pg.get("schema", "chat_qms"))

    # Milvus
    print("\n  -- Milvus --")
    mv = d.setdefault("milvus", {})
    mv["uri"] = prompt_field("Milvus URI", mv.get("uri", "http://localhost:19530"))
    mv["db"] = prompt_field("Milvus Database", mv.get("db", "sunny_agent"))
    mv["user"] = prompt_field("Milvus User", mv.get("user", "root"))
    mv["password"] = prompt_field("Milvus Password", mv.get("password", ""), secret=True)
    mv["collection"] = prompt_field("Milvus Collection", mv.get("collection", "quality_knowledge"))

    return raw


def configure_judge(raw: dict) -> dict:
    j = raw.setdefault("judge", {})
    prompt_section("评分 LLM 配置")
    j["model"] = prompt_field("评分模型 (litellm格式)", j.get("model", "deepseek/deepseek-chat"))
    if "dimensions" not in j or not j["dimensions"]:
        j["dimensions"] = [
            {"name": "correctness", "description": "数据和数字的准确性，关键事实是否正确", "weight": 0.5},
            {"name": "completeness", "description": "是否涵盖了期望输出中的关键信息点", "weight": 0.3},
            {"name": "relevance", "description": "回答是否紧扣问题，没有跑题或添加无关内容", "weight": 0.2},
        ]
    return raw


def validate_connections(raw: dict):
    prompt_section("验证连接")
    errors = []

    # PG
    print("  检查 PostgreSQL...", end=" ")
    try:
        import psycopg2
        pg = raw.get("data", {}).get("pg", {})
        conn = psycopg2.connect(host=pg["host"], port=pg["port"], dbname=pg["dbname"],
                                user=pg["user"], password=pg["password"])
        conn.close()
        print("OK")
    except Exception as e:
        print(f"FAIL ({e})")
        errors.append("PG")

    # Milvus
    print("  检查 Milvus...", end=" ")
    try:
        from pymilvus import connections
        mv = raw.get("data", {}).get("milvus", {})
        connections.connect(alias="_init_check", uri=mv["uri"], user=mv["user"],
                            password=mv["password"], db_name="default")
        connections.disconnect("_init_check")
        print("OK")
    except Exception as e:
        print(f"FAIL ({e})")
        errors.append("Milvus")

    # Langfuse
    print("  检查 Langfuse...", end=" ")
    try:
        import httpx
        lf = raw.get("langfuse", {})
        resp = httpx.get(f"{lf['host'].rstrip('/')}/api/public/health",
                         auth=(lf["public_key"], lf["secret_key"]), timeout=10)
        if resp.status_code == 200:
            print("OK")
        else:
            print(f"FAIL (HTTP {resp.status_code})")
            errors.append("Langfuse")
    except Exception as e:
        print(f"FAIL ({e})")
        errors.append("Langfuse")

    # Target
    print("  检查业务系统...", end=" ")
    try:
        import httpx
        t = raw.get("target", {})
        resp = httpx.get(f"{t['base_url'].rstrip('/')}/docs", timeout=5)
        print("OK" if resp.status_code == 200 else f"WARN (HTTP {resp.status_code})")
    except Exception as e:
        print(f"FAIL ({e})")
        errors.append("Target")

    if errors:
        print(f"\n  连接失败: {', '.join(errors)}，请检查配置后重试")
    else:
        print("\n  所有连接验证通过!")
    return len(errors) == 0


def main():
    print("=" * 50)
    print("  SunnyAgent 评测工具 - 配置初始化")
    print("=" * 50)
    print(f"  配置文件: {CONFIG_PATH}")
    print("  (回车保留当前值，输入新值覆盖)")

    raw = load_raw_config()

    raw = configure_target(raw)
    raw = configure_langfuse(raw)
    raw = configure_data(raw)
    raw = configure_judge(raw)

    save_config(raw)
    validate_connections(raw)

    print("\n  初始化完成！可以使用以下命令开始评测：")
    print("  poetry run python evaluator_skill/scripts/status.py")
    print()


if __name__ == "__main__":
    main()
