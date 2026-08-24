#!/usr/bin/env python3
"""Tests for the phased_stroke composable stroke builder.

Run:  python tests/test_phased_stroke.py
Or via the suite wrapper:  bash scripts/run_tests.sh
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import phased_stroke as ps  # noqa: E402


def test_brushdown_invariant():
    xst = ps.build_phased_stroke(
        [(0.0, 0.0), (2.0, 0.5), (4.0, 0.0)],
        begin={"wetness": 0.05, "tilt": (-50, -15), "gradient": ((253, 208, 54), (255, 255, 255))},
        mid={"wetness": 0.9, "tilt": 0.0, "gradient": ((30, 90, 200), (200, 40, 40))},
        end={"wetness": 0.05, "tilt": (50, 15), "solid": (40, 40, 40)},
        segments=30,
    )
    frames = [l for l in xst.splitlines() if l.startswith("s ")]
    p0 = float(frames[0].split()[7])
    p1 = float(frames[1].split()[7])
    assert p0 == 0.0 and p1 > 0.0, f"brush-down broken: {p0} -> {p1}"


def test_z_coupling():
    xst = ps.build_phased_stroke([(0.0, 0.0), (3.0, 0.0)], segments=20)
    for l in xst.splitlines():
        if not l.startswith("s "):
            continue
        parts = l.split()
        p, z = float(parts[7]), float(parts[3])
        assert abs(z - (ps.Z_LIFT - ps.Z_COUP * p)) < 1e-4, f"z coupling off: {l}"


def test_phases_vary_wetness_tilt_color():
    xst = ps.build_phased_stroke(
        [(0.0, 0.0), (2.0, 0.3), (4.0, 0.0)],
        begin={"wetness": 0.05, "tilt": (-50, -15), "gradient": ((253, 208, 54), (255, 255, 255))},
        mid={"wetness": 0.9, "tilt": 0.0, "gradient": ((30, 90, 200), (200, 40, 40))},
        end={"wetness": 0.05, "tilt": (50, 15), "solid": (40, 40, 40)},
        segments=30,
    )
    lines = xst.splitlines()
    ws = [float(l.split()[1]) for l in lines if l.startswith("w ")]
    assert len(set(round(w, 3) for w in ws)) >= 2, "wetness did not change across phases"
    tilts = {(float(l.split()[4]), float(l.split()[5])) for l in lines if l.startswith("s ")}
    assert len(tilts) >= 2, "tilt did not vary across phases"
    # three color re-issues (begin, mid, end)
    color_blocks = 0
    for i, l in enumerate(lines):
        if l.startswith("l 0 ") and (i == 0 or not lines[i - 1].startswith("l")):
            color_blocks += 1
    assert color_blocks >= 3, f"expected >=3 color blocks, got {color_blocks}"


def test_single_phase_is_stroke_wide():
    # with all phases equal, only one w level should appear
    xst = ps.build_phased_stroke(
        [(0.0, 0.0), (3.0, 0.0)],
        begin={"wetness": 0.5, "solid": (10, 20, 30)},
        mid={"wetness": 0.5, "solid": (10, 20, 30)},
        end={"wetness": 0.5, "solid": (10, 20, 30)},
        segments=20,
    )
    ws = [float(l.split()[1]) for l in xst.splitlines() if l.startswith("w ")]
    assert len(set(round(w, 3) for w in ws)) == 1, "single-phase stroke varied wetness"


if __name__ == "__main__":
    test_brushdown_invariant()
    test_z_coupling()
    test_phases_vary_wetness_tilt_color()
    test_single_phase_is_stroke_wide()
    print("PASS: phased_stroke tests (brush-down, z-coupling, phase variation, single-phase)")
