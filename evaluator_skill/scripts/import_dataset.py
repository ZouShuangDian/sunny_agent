#!/usr/bin/env python3
"""Import markdown test cases into Langfuse dataset."""
import argparse, asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, EvalRunner

setup_logging()
parser = argparse.ArgumentParser()
parser.add_argument("--file", "-f", required=True)
parser.add_argument("--dataset-name", "-d", required=True)
args = parser.parse_args()

async def main():
    runner = EvalRunner(load_config())
    count = await runner.import_dataset(args.file, args.dataset_name)
    print(f"\nDone: imported {count} items into dataset '{args.dataset_name}'")

asyncio.run(main())
