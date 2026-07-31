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
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPT_DIR))

from send_strokes import (  # noqa: E402
    PRESSURE_PROFILES,
    WETNESS_PROFILES,
    SCRATCH_PROFILES,
    COLOR_PROFILES,
    COLOR_RAMP_PROFILES,
    STROKE_LIBRARY,
    BRUSH_STYLES,
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
    # Invariant: every BRUSH_STYLE references profiles that actually exist in
    # the catalogs (a style that references a missing profile would crash at
    # build time). NOT a snapshot of catalog sizes — catalogs grow over time.
    for name, spec in BRUSH_STYLES.items():
        if "pprofile" in spec:
            assert spec["pprofile"] in PRESSURE_PROFILES, f"{name}: pprofile {spec['pprofile']!r}"
        if "wprofile" in spec:
            assert spec["wprofile"] in WETNESS_PROFILES, f"{name}: wprofile {spec['wprofile']!r}"
        if "sprofile" in spec:
            assert spec["sprofile"] in SCRATCH_PROFILES, f"{name}: sprofile {spec['sprofile']!r}"
    # sanity: each catalog non-empty and carries the profiles the library leans on
    assert "Standard" in PRESSURE_PROFILES
    assert "Smooth Bell" in PRESSURE_PROFILES
    assert "Level 2 — Dry" in WETNESS_PROFILES
    assert "None" in SCRATCH_PROFILES


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
    # w/i are re-issued after each press frame (k>=1), so the first sample is at
    # t=1/segments, not t=0. The Build Up profile ramps 0 -> 1 across the path.
    assert iscr[0] < 0.2  # starts near 0 (first sample at t=1/8)
    assert abs(iscr[-1] - 1.0) < 1e-6  # ends at full scratch
    assert all(iscr[i] <= iscr[i + 1] + 1e-9 for i in range(len(iscr) - 1))  # monotonic


# ── Bookend rules ─────────────────────────────────────────────────────────

def _lifts(xst):
    sf = [l for l in xst.split("\n") if l.startswith("s ")]
    # a lift = brush raised: z field (4th token, index 3) exactly 0.06250 and
    # pressure (last token) 0.00000. Exact-field match avoids substring false
    # positives from negative z like -0.06250.
    return [l for l in sf if l.split()[3] == "0.06250" and l.split()[-1] == "0.00000"]


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
    # Wet to Dry: starts wet (~1.0 at t=0, first sample t=1/8 ~ 0.885) ends dry (~0.083)
    assert ws[0] > 0.8
    assert ws[-1] < 0.15
    assert all(ws[i] >= ws[i + 1] - 1e-9 for i in range(len(ws) - 1))  # monotonic down


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
    assert _resolve_color("WarmToCool")[0] == "ramp"
    assert _resolve_color("HueCycle")[0] == "ramp"
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


def test_color_ramp_sweeps_full_hue_wheel():
    # A multi-stop HSL ramp (e.g. HueCycle 0->180->360) must sweep the FULL
    # wheel across the 9 tuft nodes, not collapse to a flat color. HueCycle's
    # first and last stops are both hue 0 (==360), so naive first/last lerp
    # would give a flat red; the ramp emitter interpolates every stop.
    from send_strokes import _color_l_ramp, COLOR_RAMP_PROFILES
    stops = [(t, h, s, l) for t, (h, s, l) in COLOR_RAMP_PROFILES["HueCycle"]]
    nodes = _color_l_ramp(stops)
    assert len(nodes) == 9
    # all 9 node colors distinct -> a real rainbow, not flat
    cols = {tuple(n.split()[1:]) for n in nodes}
    assert len(cols) == 9, cols
    # endpoints are red (hue 0 == 360); middle node is cyan (hue 180)
    assert nodes[0] == "l 0 230 25 25"
    assert nodes[8] == "l 8 230 25 25"
    assert nodes[4] == "l 4 25 229 230"


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
    # No `b` markers: brush-down is the lifted posture frame (p=0) immediately
    # followed by the first press frame (p>0) — consecutive s, no command between
    # (a `b` would obstruct the mark).
    assert d.count("b ") == 0
    # lifted posture frame opens the dab, splayed West (Roll=+72)
    assert " 0.06250 0.00000 72.00000 0 0.00000" in d
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
    # No b-markers (dropped; see test_build_dab_emits_b_sandwich_and_splays_direction).
    # Each dab's directional splay (Roll/Pitch) must survive the composite join.
    assert four.count("b ") == 0
    assert "-54.00000" in four and "57.00000" in four and "72.00000" in four and "-67.00000" in four
    # brush-down invariant per dab: a lift frame (p=0) directly followed by a
    # press frame (p>0) with no intervening command line
    frames = [l for l in four.splitlines() if l.startswith("s ")]
    downs = sum(
        1 for i in range(len(frames) - 1)
        if frames[i].split()[-1] == "0.00000" and float(frames[i + 1].split()[-1]) > 0
    )
    assert downs >= 4, downs


