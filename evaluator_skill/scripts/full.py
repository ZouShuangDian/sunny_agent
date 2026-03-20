#!/usr/bin/env python3
"""One-click full pipeline: setup → import → run → (teardown)."""
import argparse, asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, DataManager, EvalRunner

setup_logging()
parser = argparse.ArgumentParser()
parser.add_argument("--testcase", "-t", required=True)
parser.add_argument("--dataset-name", "-d", required=True)
parser.add_argument("--run-name", "-r", required=True)
parser.add_argument("--cleanup", action="store_true")
parser.add_argument("--skip-milvus", action="store_true")
args = parser.parse_args()

async def main():
    config = load_config()
    dm = DataManager(config)
    runner = EvalRunner(config)

    print(">>> Step 1/4: Setting up data environment...")
    dm.setup(skip_milvus=args.skip_milvus)

    print(f"\n>>> Step 2/4: Importing test cases to '{args.dataset_name}'...")
    count = await runner.import_dataset(args.testcase, args.dataset_name)
    print(f"  Imported {count} items")

    print(f"\n>>> Step 3/4: Running evaluation '{args.run_name}'...")
    summary = await runner.run(args.dataset_name, args.run_name)
    print(f"\n{'='*60}")
    print(f"Avg score: {summary['avg_weighted_score']:.4f} ({summary['total_items']} items)")
    print(f"{'='*60}")
    for r in summary["results"]:
        scores_str = " | ".join(f"{k}={v:.2f}" for k, v in r["scores"].items())
        print(f"  {r['item_key']:>4}: weighted={r['weighted_score']:.2f} | {scores_str}")

    if args.cleanup:
        print("\n>>> Step 4/4: Cleaning up...")
        dm.teardown()
    else:
        print("\n>>> Step 4/4: Skipping cleanup (use --cleanup to auto-clean)")

asyncio.run(main())
