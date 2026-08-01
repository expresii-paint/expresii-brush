#!/usr/bin/env bash
# Run the expresii-brush skill's self-tests (no network, no Expresii needed).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== brush-down regression =="
python tests/test_brushdown.py
echo "== pytest suite (deterministic, HTTP-mocked) =="
if command -v uv >/dev/null 2>&1; then
    uv run --with pytest pytest tests/test_send_strokes.py -q
else
    python -m pytest tests/test_send_strokes.py -q
fi
echo "ALL SKILL TESTS PASS"
