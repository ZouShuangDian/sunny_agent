#!/usr/bin/env python3
"""Run evaluation against a Langfuse dataset."""
import argparse, asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, EvalRunner

setup_logging()
parser = argparse.ArgumentParser()
parser.add_argument("--dataset-name", "-d", required=True)
parser.add_argument("--run-name", "-r", required=True)
args = parser.parse_args()

async def main():
    runner = EvalRunner(load_config())
    summary = await runner.run(args.dataset_name, args.run_name)
    print(f"\n{'='*60}")
    print(f"Dataset:        {summary['dataset']}")
    print(f"Run:            {summary['run_name']}")
    print(f"Total items:    {summary['total_items']}")
    print(f"Avg score:      {summary['avg_weighted_score']:.4f}")
    print(f"{'='*60}")
    for r in summary["results"]:
        scores_str = " | ".join(f"{k}={v:.2f}" for k, v in r["scores"].items())
        print(f"  {r['item_key']:>4}: weighted={r['weighted_score']:.2f} | {scores_str}")

asyncio.run(main())
