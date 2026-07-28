"""Standalone regression test for brush-down registration.

Expresii registers brush-down ONLY from two consecutive `s` frames going
pressure 0 -> >0, with NO other command (w/i/b/l/...) between them. This test
asserts every stroke emitter opens each stroke with a lift `s` (p=0)
immediately followed by a press `s` (p>0) -- no non-`s` command in between.

Runs under bare `python` (no pytest/venv needed):
    python tests/test_brushdown.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import send_strokes as S  # noqa: E402


def _leading_lift_violations(xst: str):
    """Return [(stroke_name_hint, lift_line, offender)] for any stroke whose
    first `s` frame is a lift (p=0) immediately followed by a non-`s` line."""
    L = [l for l in xst.strip().split("\n") if l.strip()]
    bad = []
    starts = [i for i, l in enumerate(L) if l.startswith("B ")]
    for si in starts:
        j = next(k for k in range(si + 1, len(L)) if L[k].startswith("s "))
        lift = L[j]
        if not (lift.startswith("s ") and lift.split()[-1] == "0.00000"):
            bad.append(("first s not a lift", lift[:45], ""))
            continue
        nxt = L[j + 1] if j + 1 < len(L) else ""
        if not nxt.startswith("s "):
            bad.append(("lift->non-s", lift[:30], nxt[:30]))
    return bad


def test_all_emitters_register_brush_down():
    emitters = []
    for name in S.BRUSH_STYLES:
        emitters.append(("style:" + name,
                         S.build_style_stroke(name, waypoints=[(-2, 0, 0), (2, 0, 0)])))
    emitters += [
        ("circle", S.build_circle()),
        ("star", S.build_star()),
        ("dab", S.build_dab((0, 0), "E")),
        ("stroke_cmd", S.build_stroke_command([(-2, 0, 0.5), (2, 0, 0.5)], 5, 0.1, 0)),
    ]
    total = 0
    for label, xst in emitters:
        bad = _leading_lift_violations(xst)
        total += len(bad)
        for b in bad:
            print(f"FAIL {label}: {b}")
    assert total == 0, f"{total} brush-down violations across {len(emitters)} emitters"


if __name__ == "__main__":
    try:
        test_all_emitters_register_brush_down()
    except AssertionError as e:
        print(f"\nASSERTION FAILED: {e}")
        sys.exit(1)
    print("PASS: all stroke emitters register brush-down "
          "(consecutive s p=0->>0, no command between).")
