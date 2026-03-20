#!/usr/bin/env python3
"""Set config values in eval_config.yaml. Accepts key=value arguments.

Usage:
    python config_set.py target.token=eyJxxx data.pg.password=abc123!
"""

import sys
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "eval_config.yaml"


def set_nested(d: dict, key: str, value):
    parts = key.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    # Try to preserve type (int for port, etc.)
    old = d.get(parts[-1])
    if isinstance(old, int):
        try:
            value = int(value)
        except ValueError:
            pass
    elif isinstance(old, float):
        try:
            value = float(value)
        except ValueError:
            pass
    d[parts[-1]] = value


def main():
    if len(sys.argv) < 2:
        print("Usage: config_set.py key1=value1 [key2=value2 ...]")
        sys.exit(1)

    raw = {}
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}

    for arg in sys.argv[1:]:
        if "=" not in arg:
            print(f"Invalid argument (expected key=value): {arg}")
            sys.exit(1)
        key, value = arg.split("=", 1)
        set_nested(raw, key, value)
        # Mask sensitive values in output
        display = "****" if any(s in key for s in ("password", "secret", "token")) else value
        print(f"  Set {key} = {display}")

    CONFIG_PATH.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"\nSaved to {CONFIG_PATH}")


if __name__ == "__main__":
    main()
