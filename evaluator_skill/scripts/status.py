#!/usr/bin/env python3
"""Check data environment status."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, DataManager

setup_logging()
dm = DataManager(load_config())
r = dm.status()
print("\n=== Data Environment Status ===")
pg = r["pg"]
print(f"  PG ({pg.get('schema','?')}): {pg['status']}", end="")
if pg["status"] == "ready":
    print(f" — {pg['row_count']} rows, {pg['date_range']}")
elif pg["status"] == "error":
    print(f" — {pg['message']}")
else:
    print()
mv = r["milvus"]
print(f"  Milvus ({mv.get('collection','?')}): {mv['status']}", end="")
if mv["status"] == "ready":
    print(f" — {mv['entity_count']} entities")
elif mv["status"] == "error":
    print(f" — {mv['message']}")
else:
    print()
