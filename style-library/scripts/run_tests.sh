#!/bin/bash
# run_tests.sh — run the brush-style library test suite
# Usage: bash scripts/run_tests.sh  (from skill root)
set -e
cd "$(dirname "$0")/.."
python tests/test_brushdown.py
echo "PASS: all stroke emitters register brush-down (consecutive s p=0->>0, no command between)."