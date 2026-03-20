#!/usr/bin/env python3
"""Data environment setup: Excel → PG (→ Milvus)."""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, DataManager

setup_logging()
parser = argparse.ArgumentParser()
parser.add_argument("--skip-milvus", action="store_true")
args = parser.parse_args()
DataManager(load_config()).setup(skip_milvus=args.skip_milvus)
print("\nData setup complete.")
