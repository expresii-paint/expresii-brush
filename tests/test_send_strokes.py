"""Deterministic generation tests for the expresii-brush skill.

These cover the PURE logic in send_strokes.py — profile catalog, curve
interpolation, bookend rules, polyline path interpolation, and composite
joining. They do NOT touch the network (no live Expresii server required),
so they run green in CI. The live send/verify path is covered by ad-hoc
verification against a running server.

Run with:  pytest tests/optional_skills/expresii_brush/test_send_strokes.py -v
"""

import os
import sys

import pytest

# Make the skill script importable without a package __init__.
SCRIPT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "optional-skills", "creative", "expresii-brush", "scripts",
)
sys.path.insert(0, os.path.abspath(SCRIPT_DIR))

from send_strokes import (  # noqa: E402
    PRESSURE_PROFILES,
    WETNESS_PROFILES,
    SCRATCH_PROFILES,
    COLOR_PROFILES,
    COLOR_RAMP_PROFILES,
    STROKE_LIBRARY,
    _interp,
    parse_color,
    _hsl_to_rgb,
    _lerp_rgb,
    _resolve_color,
    _color_l_gradient,
    _color_l_all_nodes,
    _resolve_tilt,
    build_circle,
    build_stroke_command,
    build_profile_stroke,
    build_dab,
    build_composite,
    paint,
)


# ── Catalog ────────────────────────────────────────────────────────────────

def test_profile_catalogs_loaded():
    assert len(PRESSURE_PROFILES) == 5
    assert len(WETNESS_PROFILES) == 6
    assert len(SCRATCH_PROFILES) == 8


def test_interp_endpoints_and_mid():
    # Standard pressure: light ends (0.1), pressed plateau (~0.74)
    assert abs(_interp(PRESSURE_PROFILES["Standard"], 0.0) - 0.1) < 1e-6
    assert abs(_interp(PRESSURE_PROFILES["Standard"], 1.0) - 0.1) < 1e-6
    mid = _interp(PRESSURE_PROFILES["Standard"], 0.5)
    assert 0.6 < mid < 0.75  # ~0.672
    # Constant profile is flat
    assert abs(_interp(PRESSURE_PROFILES["Constant"], 0.37) - 0.6) < 1e-6


def test_wetness_level_maps_to_xst_w():
    # Wetness levels 1..12 map to XST w = level/12
    stroke = build_profile_stroke(
        [(-1.0, 0.0, 0.5), (1.0, 0.0, 0.5)], size=5,
        pprofile="Constant", wprofile="Level 1 — Driest", sprofile="None", segments=4,
    )
    ws = [float(l.split()[1]) for l in stroke.split("\n") if l.startswith("w ")]
    assert min(ws) > 0.08 and max(ws) < 0.09  # 1/12 ≈ 0.0833

    stroke12 = build_profile_stroke(
        [(-1.0, 0.0, 0.5), (1.0, 0.0, 0.5)], size=5,
        pprofile="Constant", wprofile="Level 12 — Wettest", sprofile="None", segments=4,
    )
    ws12 = [float(l.split()[1]) for l in stroke12.split("\n") if l.startswith("w ")]
    assert abs(max(ws12) - 1.0) < 1e-6


def test_build_up_scratch_ramps_zero_to_one():
    stroke = build_profile_stroke(
        [(-1.0, 0.0, 0.5), (1.0, 0.0, 0.5)], size=5,
        pprofile="Constant", wprofile="Level 5 — Medium", sprofile="Build Up", segments=8,
    )
    iscr = [float(l.split()[1]) for l in stroke.split("\n") if l.startswith("i ")]
    assert abs(iscr[0]) < 1e-6
    assert abs(iscr[-1] - 1.0) < 1e-6


# ── Bookend rules ─────────────────────────────────────────────────────────

def _lifts(xst):
    sf = [l for l in xst.split("\n") if l.startswith("s ")]
    return [l for l in sf if "0.06250" in l and "0.00000" in l]


def test_circle_is_closed_loop_leading_lift_only():
    xst = build_circle(cx=0.0, cy=0.0, radius=1.0)
    assert xst.startswith("c\n")
    lifts = _lifts(xst)
    # closed loop: exactly ONE lift (leading), no trailing lift
    assert len(lifts) == 1, lifts


def test_open_stroke_has_leading_and_trailing_lift():
    xst = build_stroke_command(
        [(-1.0, -1.0, 0.5), (1.0, 1.0, 0.5)], size=4.0, wetness=0.5, scratch=0.5,
    )
    lifts = _lifts(xst)
    assert len(lifts) == 2, lifts  # open stroke needs both


def test_profile_open_vs_closed_bookends():
    open_s = build_profile_stroke(
        [(-1.0, 0.0, 0.5), (1.0, 0.0, 0.5)], size=5,
        pprofile="Standard", wprofile="Level 5 — Medium", sprofile="None", segments=8,
    )
    closed_s = build_profile_stroke(
        [(-1.0, 0.0, 0.5), (1.0, 0.0, 0.5)], size=5, closed=True,
        pprofile="Standard", wprofile="Level 5 — Medium", sprofile="None", segments=8,
    )
    assert len(_lifts(open_s)) == 2
    assert len(_lifts(closed_s)) == 1  # closed loop: no trailing lift


# ── Polyline interpolation (the sparse-waypoint fix) ───────────────────────

def test_sparse_waypoints_produce_dense_continuous_stroke():
    # A 2-waypoint straight line must emit one s-frame per segment, not just
    # two endpoint frames (the bug that produced blank strokes).
    xst = build_profile_stroke(
        [(-1.5, 0.0, 0.5), (1.5, 0.0, 0.5)], size=5,
        pprofile="Smooth Bell", wprofile="Wet to Dry", sprofile="Build Up", segments=16,
    )
    sf = [l for l in xst.split("\n") if l.startswith("s ")]
    # 1 leading lift + 17 segment frames + 1 trailing lift = 19
    assert len(sf) == 19, len(sf)
    # x must march monotonically from ~-1.5 to ~1.5 across the body
    xs = [float(l.split()[1]) for l in sf[1:-1]]
    assert xs[0] < -1.0 and xs[-1] > 1.0
    assert all(xs[i] <= xs[i + 1] + 1e-6 for i in range(len(xs) - 1))


def test_wet_to_dry_ramps_along_path():
    xst = build_profile_stroke(
        [(-1.0, 0.0, 0.5), (1.0, 0.0, 0.5)], size=5,
        pprofile="Constant", wprofile="Wet to Dry", sprofile="None", segments=8,
    )
    ws = [float(l.split()[1]) for l in xst.split("\n") if l.startswith("w ")]
    # Wet to Dry: starts wet (~1.0) ends dry (~0.083)
    assert ws[0] > 0.9
    assert ws[-1] < 0.15


# ── Composite joining ─────────────────────────────────────────────────────

def test_build_composite_single_clear_and_bookends():
    comp = build_composite([
        build_circle(cx=0, cy=1.4),
        build_profile_stroke([(-1.5, 0.0, 0.5), (1.5, 0.0, 0.5)], size=5,
                             pprofile="Standard", wprofile="Level 1 — Driest", sprofile="Build Up"),
        build_profile_stroke([(-1.5, -1.4, 0.5), (1.5, -1.4, 0.5)], size=5,
                             pprofile="Smooth Bell", wprofile="Wet to Dry", sprofile="Build Up"),
    ])
    assert comp.startswith("c\n")
    # exactly one leading clear (sub-builders' own `c` are stripped)
    assert comp.count("\nc\n") == 0
    lifts = _lifts(comp)
    # circle(1) + 2 open strokes(2 each) = 5
    assert len(lifts) == 5, lifts


def test_build_composite_strips_sub_builder_clear():
    # build_circle emits its own `c`; composite must not double it
    comp = build_composite([build_circle(), build_circle()])
    assert comp.count("c\n") == 1, comp.count("c\n")


# ── Color ─────────────────────────────────────────────────────────────────

def test_parse_color_name_hex_rgb():
    assert parse_color("Vermilion") == (210, 60, 30)
    assert parse_color("255,0,0") == (255, 0, 0)
    assert parse_color("#00ff00") == (0, 255, 0)
    assert parse_color("#abc") == (170, 187, 204)
    assert parse_color((10, 20, 30)) == (10, 20, 30)


def test_hsl_to_rgb_pure_red():
    r, g, b = _hsl_to_rgb(0, 0.8, 0.5)
    assert r > 200 and g < 80 and b < 80


def test_resolve_color_routing():
    assert _resolve_color(None) is None
    assert _resolve_color("Vermilion") == ("solid", (210, 60, 30))
    assert _resolve_color("WarmToCool")[0] == "gradient"
    assert _resolve_color("Cobalt:Vermilion")[0] == "gradient"
    assert _resolve_color(("Cobalt", "Vermilion"))[0] == "gradient"


def test_color_gradient_sets_9_nodes_tip_to_root():
    # Expresii loads color per brush node (0..8, tip->root). A tuft gradient
    # sets node 0 = tip, node 8 = root, interpolated between.
    g = _color_l_gradient((10, 20, 30), (200, 100, 50))
    assert len(g) == 9
    assert g[0] == "l 0 10 20 30"
    assert g[8] == "l 8 200 100 50"
    assert g[4] == "l 4 105 60 40"  # midpoint lerp


def test_color_all_nodes_emitted_for_solid():
    lines = _color_l_all_nodes((10, 20, 30))
    assert len(lines) == 9
    assert lines[0] == "l 0 10 20 30"
    assert lines[-1] == "l 8 10 20 30"


def test_gradient_stroke_emits_tuft_gradient_once_and_auto_tilts():
    # A tuft gradient is a brush property, set ONCE (not per path-segment).
    xst = build_profile_stroke([(-1.5, 0.0, 0.5), (1.5, 0.0, 0.5)], size=6,
                               color="Cobalt:Vermilion", segments=10)
    # 9 nodes, each emitted exactly once (tip!=root)
    assert xst.count("l 0 30 80 180") == 1
    assert xst.count("l 8 210 60 30") == 1
    assert xst.count("\nl ") == 9, xst.count("\nl ")
    # gradient auto-splays the tuft via ROLL (-44) so the gradient shows across
    # the stroke width. (Per the recorded samples: s x y z Pitch Roll Turn
    # Pressure — splay is (roll=-44, pitch=0) here.)
    assert " 0.00000 -44.00000 0 " in xst


def test_solid_color_no_autotilt_when_tilt_explicit_zero():
    xst = build_profile_stroke([(-1.5, 0.0, 0.5), (1.5, 0.0, 0.5)], size=6,
                               color="Vermilion", tilt=0.0)
    body = [l for l in xst.splitlines() if l.startswith("s ") and "0.06250" not in l]
    assert all(l.split()[4] == "0.00000" and l.split()[5] == "0.00000" for l in body)


def test_circle_gradient_emits_9_nodes_and_tilts():
    xst = build_circle(radius=1.0, color="Cobalt:Vermilion")
    assert xst.count("l 0 30 80 180") == 1
    assert xst.count("l 8 210 60 30") == 1
    assert xst.count("\nl ") == 9
    assert " 0.00000 -44.00000 0 " in xst  # auto-splay (Roll) for gradient


# ── Stroke library ────────────────────────────────────────────────────────

def test_stroke_library_presets_build():
    for name in STROKE_LIBRARY:
        blk = paint(name)
        assert "B" in blk, name
        assert blk.lstrip().startswith(("B", "#", "c")), name


def test_paint_override_color():
    p = paint("dry_brush_line", color="Vermilion")
    assert "l 0 210 60 30" in p


def test_paint_unknown_preset_raises():
    with pytest.raises(ValueError):
        paint("does_not_exist")


# ── 2D tilt model (learned from the recorded 4-direction dab sample) ──────

def test_resolve_tilt_axis_mapping():
    # s x y z Pitch Roll Turn Pressure  ->  (roll, pitch)
    # Roll = East(-)/West(+), Pitch = North(+)/South(-). Verified vs sample.
    assert _resolve_tilt("E") == (-54.0, 0.0)
    assert _resolve_tilt("W") == (72.0, 0.0)
    assert _resolve_tilt("N") == (0.0, 57.0)
    assert _resolve_tilt("S") == (0.0, -67.0)
    assert _resolve_tilt(45.0) == (0.0, 45.0)        # scalar -> Roll
    assert _resolve_tilt((57.0, 0.0)) == (57.0, 0.0)  # (roll, pitch) passthrough
    assert _resolve_tilt(0.0) == (0.0, 0.0)
    assert _resolve_tilt(None) == (0.0, 0.0)
    with pytest.raises(ValueError):
        _resolve_tilt("Q")


def test_circle_tilt_splays_both_axes():
    c = build_circle(radius=1.0, color="Cobalt:Vermilion", tilt="N")
    body = [l for l in c.splitlines() if l.startswith("s ") and "0.06250" not in l]
    # North -> Pitch=+57, Roll=0
    assert all(l.split()[4] == "57.00000" and l.split()[5] == "0.00000" for l in body)


def test_build_dab_emits_b_sandwich_and_splays_direction():
    d = build_dab((0.10, 0.41), direction="W", tilt_deg=72, color="Cobalt:Vermilion")
    assert d.count("b ") == 2  # pen-down + pen-up markers
    assert " 0.06250 0.00000 72.00000 0 0.00000" in d  # lifted West splay (Roll=+72)
    assert d.count("\nl ") == 9  # tuft gradient nodes
    # pressure pulse present (some frame at peak pressure)
    assert " 0.00000 72.00000 0 0.75000" in d


def test_four_dab_composite_matches_sample_directions():
    four = build_composite([
        build_dab((0.10, 0.41), direction="E", tilt_deg=54),
        build_dab((0.17, 0.46), direction="N", tilt_deg=57),
        build_dab((0.18, 0.44), direction="W", tilt_deg=72),  # more tilt -> more root/red
        build_dab((0.10, 0.43), direction="S", tilt_deg=67),
    ])
    # 2 b-markers per dab
    assert four.count("b ") == 8
    assert "-54.00000" in four and "57.00000" in four and "72.00000" in four and "-67.00000" in four


