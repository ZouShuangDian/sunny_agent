#!/usr/bin/env python3
"""Clean up test data environment."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _lib import setup_logging, load_config, DataManager

setup_logging()
DataManager(load_config()).teardown()
print("\nData teardown complete.")
