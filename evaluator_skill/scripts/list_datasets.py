#!/usr/bin/env python3
"""List all datasets in Langfuse."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, EvalRunner

setup_logging()
runner = EvalRunner(load_config())
datasets = runner.list_datasets()
if not datasets:
    print("No datasets found.")
else:
    print(f"{'Name':<30} {'Items':<8} {'Created'}")
    print("-" * 60)
    for ds in datasets:
        print(f"{ds['name']:<30} {ds['item_count']:<8} {ds['created_at']}")
