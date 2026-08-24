#!/usr/bin/env python3
"""
phased_stroke — composable Expresii brush strokes with PER-PHASE variation.

The user's model (2026-08-21): a stroke is three phases — beginning, mid, end —
and each phase can carry its OWN wetness, tilt, and gradient-loading. So you can
say "start dry + tilted, become wet + flat in the middle, finish dry + tilted
with a different gradient" on a single continuous stroke.

This replaces the stroke-wide w/i/tilt/color of build_profile_stroke with a
phase-keyed scheme. Phases are assigned by normalized progress t in [0,1]:

    begin : t in [0.00, begin_end)              default 0.18
    mid   : t in [begin_end, 1 - end_len)       middle band
    end   : t in [1 - end_len, 1.00)            default 0.18

Each phase config is a dict:
    {
      "wetness":   float 0..1      (XST `w` level/12)      default 0.5
      "tilt":      (pitch, roll)   degrees                 default 0
      "gradient":  (tip_rgb, root_rgb) or None  (tuft gradient)
      "solid":     rgb or None                          (all-9-node fill)
      "scratch":   float 0..1      (XST `i`)             default 0
      "peak":      float 0..1      pressure peak in this phase  default 0.7
    }

Unsupported per-phase: pressure *shape* is shared (bell) but its PEAK scales per
phase; tilt and color/gradient re-issue at phase boundaries (Expresii keeps the
brush down across `w`/`i`/`l` re-issues, so this is safe).

The emitter reuses the recorded-format preamble (_star_header) + brush-down
invariant (lift -> press, nothing between) so strokes actually deposit.

Run directly to print demo strokes:
    python phased_stroke.py            # prints several phase-combos as .xst
    python phased_stroke.py --self-test  # asserts invariants (no server)
"""
import argparse
import math
import os
import sys

# Import the parent repo's helpers (build_path_stroke lives one dir up).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts"))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from send_strokes import (  # noqa: E402
    _resolve_color, _color_l_gradient, _color_l_all_nodes,
    _color_l_ramp, _interp, _AUTOTILT, PRESSURE_PROFILES, WETNESS_PROFILES,
    SCRATCH_PROFILES,
)

Z_LIFT = 0.06250
Z_COUP = 0.25  # z = Z_LIFT - Z_COUP * p  (matches build_path_stroke)


def _default_phase():
    return {"wetness": 0.5, "tilt": 0.0, "gradient": None, "solid": None,
            "scratch": 0.0, "peak": 0.7}


def _phase_at(t, begin_end, end_len):
    if t < begin_end:
        return "begin"
    if t > 1.0 - end_len:
        return "end"
    return "mid"


def _emit_color_lines(cres):
    """Given a _resolve_color() result, return `l` lines (may be empty)."""
    if cres is None:
        return []
    kind, *cargs = cres
    if kind == "gradient":
        return _color_l_gradient(*cargs)
    if kind == "ramp":
        return _color_l_ramp(cargs[0])
    return _color_l_all_nodes(cargs[0])


def _tilt_of(phase):
    t = phase.get("tilt", 0.0)
    # normalize to (pitch, roll)
    if isinstance(t, (int, float)):
        return float(t), 0.0
    return float(t[0]), float(t[1])


def build_phased_stroke(waypoints, size=6.0,
                        begin=None, mid=None, end=None,
                        begin_end=0.18, end_len=0.18,
                        segments=24, closed=False, color_mode="gradient"):
    """
    Build one continuous stroke whose look changes across phases.

    waypoints : list of (x, y) path points (Expresii space; +Y up in v0.8+).
    begin/mid/end : phase config dicts (see module docstring). Missing keys
                    fall back to _default_phase().
    color_mode : how a phase "gradient"/"solid" spec is turned into `l` lines.
                 Each phase may override; this picks the default resolver.

    Returns XST text (header + frames, no `c`).
    """
    b = dict(_default_phase(), **(begin or {}))
    m = dict(_default_phase(), **(mid or {}))
    e = dict(_default_phase(), **(end or {}))

    n = len(waypoints)
    if n < 2:
        raise ValueError("need at least 2 waypoints")

    # arc-length table for t-param sampling
    pts = [(float(x), float(y)) for (x, y) in waypoints]
    seg_len = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(n - 1)]
    total = sum(seg_len) or 1.0

    def xy_at(t):
        if t <= 0:
            return pts[0]
        if t >= 1:
            return pts[-1]
        target = t * total
        acc = 0.0
        for i, sl in enumerate(seg_len):
            if acc + sl >= target:
                f = (target - acc) / sl if sl > 0 else 0.0
                return (pts[i][0] + f * (pts[i + 1][0] - pts[i][0]),
                        pts[i][1] + f * (pts[i + 1][1] - pts[i][1]))
            acc += sl
        return pts[-1]

    # ---- header (recorded-format preamble: makes it PAINT) ----
    # Build the T/w/C/B/e/k preamble manually (NOT via _star_header, which always
    # emits its rainbow default) so the begin phase's color is the first color.
    lines = [
        "T   0.00000",
        f"w   {b['wetness']:.5f}",
        "C   4.00000",
        f"B   {size:.5f}",
        "e   0.00000",
        "k   0.00000",
    ]

    def _color_resolve(ph):
        if ph.get("gradient"):
            return _resolve_color(ph["gradient"])
        if ph.get("solid"):
            return _resolve_color(ph["solid"])
        return None
    cres0 = _color_resolve(b)
    if cres0 is not None:
        lines += _emit_color_lines(cres0)

    # leading lift bookend (brush DOWN) — pressure 0
    x0, y0 = pts[0]
    lines.append(f"s {x0:.5f} {y0:.5f} {Z_LIFT:.5f} 0 0 0 0.00000")

    # ---- frame emission with per-phase state ----
    last_phase = None
    for k in range(segments + 1):
        t = k / segments
        phase_name = _phase_at(t, begin_end, end_len)
        ph = {"begin": b, "mid": m, "end": e}[phase_name]

        x, y = xy_at(t)
        # pressure: bell peak scaled by this phase's peak
        base = _interp(PRESSURE_PROFILES["Smooth Bell"], t)
        p = max(0.0, min(1.0, base * ph["peak"] / 0.75))  # bell peaks ~0.75
        # ensure first pressed frame is >0 even at low phase-peak
        if k > 0 and p < 0.02:
            p = 0.02
        z = Z_LIFT - Z_COUP * p

        # re-issue tilt + color + w + i whenever the phase changes
        if phase_name != last_phase:
            pitch, roll = _tilt_of(ph)
            if pitch == 0.0 and roll == 0.0 and (ph.get("gradient") or ph.get("solid")):
                pitch, roll = _AUTOTILT
            cres = _color_resolve(ph)
            if cres is not None:
                lines += _emit_color_lines(cres)
            lines.append(f"w {ph['wetness']:.5f}")
            lines.append(f"i {ph['scratch']:.5f}")
            last_phase = phase_name
        else:
            pitch, roll = _tilt_of(ph)

        lines.append(
            f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")
        if k > 0:
            lines.append(f"w {ph['wetness']:.5f}")
            lines.append(f"i {ph['scratch']:.5f}")

    if not closed:
        x1, y1 = pts[-1]
        lines.append(f"s {x1:.5f} {y1:.5f} {Z_LIFT:.5f} 0 0 0 0.00000")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Self-test (deterministic, no server)
# --------------------------------------------------------------------------
def _self_test():
    path = [(0.0, 0.0), (2.0, 0.5), (4.0, 0.0)]
    xst = build_phased_stroke(
        path,
        begin={"wetness": 0.05, "tilt": (-50, -15),
               "gradient": ((253, 208, 54), (255, 255, 255)), "scratch": 1.0,
               "peak": 0.6},
        mid={"wetness": 0.9, "tilt": 0.0,
             "gradient": ((30, 90, 200), (200, 40, 40)), "scratch": 0.0,
             "peak": 0.8},
        end={"wetness": 0.05, "tilt": (50, 15),
             "solid": (40, 40, 40), "scratch": 1.0, "peak": 0.5},
        segments=30,
    )
    lines = xst.splitlines()
    s_frames = [l for l in lines if l.startswith("s ")]
    # 1. brush-down invariant: first two s frames are p 0 -> >0, consecutive
    p0 = float(s_frames[0].split()[7])
    p1 = float(s_frames[1].split()[7])
    assert p0 == 0.0 and p1 > 0.0, f"brush-down broken: {p0} -> {p1}"
    # 2. every frame z matches coupling
    for l in s_frames:
        parts = l.split()
        p, z = float(parts[7]), float(parts[3])
        assert abs(z - (Z_LIFT - Z_COUP * p)) < 1e-4, f"z coupling off: {l}"
    # 3. phase re-issues happened (multiple w levels -> gradient in wetness)
    ws = [float(l.split()[1]) for l in lines if l.startswith("w ")]
    assert len(set(round(w, 3) for w in ws)) >= 2, "phases did not change wetness"
    # 4. tilt changed across phases
    tilts = {(float(l.split()[4]), float(l.split()[5])) for l in s_frames}
    assert len(tilts) >= 2, "tilt did not vary across phases"
    # 5. three color blocks (l lines) issued (begin + mid + end)
    lblocks = sum(1 for i, l in enumerate(lines)
                  if l.startswith("l 0 ") and (i == 0 or not lines[i - 1].startswith("l")))
    assert lblocks >= 3, f"expected >=3 color re-issues, got {lblocks}"
    print("phased_stroke self-test: PASS "
          f"({len(s_frames)} frames, {len(ws)} w-cmds, {len(tilts)} tilt states)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="composable phased Expresii strokes")
    ap.add_argument("--self-test", action="store_true", help="run invariant checks")
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        sys.exit(0)

    # Demo: a few phase combos the user asked about
    demo_path = [(0.0, 0.0), (1.5, 0.3), (3.0, 0.0)]
    combos = {
        "dry-tilt -> wet-flat -> dry-tilt": dict(
            begin={"wetness": 0.05, "tilt": (-50, -15), "scratch": 1.0,
                   "gradient": ((253, 208, 54), (255, 255, 255))},
            mid={"wetness": 0.95, "tilt": 0.0,
                 "gradient": ((30, 90, 200), (200, 40, 40))},
            end={"wetness": 0.05, "tilt": (50, 15), "scratch": 1.0,
                 "solid": (40, 40, 40)}),
        "all-wet flat": dict(
            begin={"wetness": 0.8, "tilt": 0.0, "solid": (120, 30, 30)},
            mid={"wetness": 0.9, "tilt": 0.0, "solid": (30, 120, 60)},
            end={"wetness": 0.8, "tilt": 0.0, "solid": (30, 30, 120)}),
        "all-dry tilted": dict(
            begin={"wetness": 0.04, "tilt": (-55, -20), "scratch": 1.0,
                   "gradient": ((250, 159, 4), (255, 255, 255))},
            mid={"wetness": 0.04, "tilt": (-55, -20), "scratch": 1.0,
                 "gradient": ((250, 159, 4), (255, 255, 255))},
            end={"wetness": 0.04, "tilt": (-55, -20), "scratch": 1.0,
                 "gradient": ((250, 159, 4), (255, 255, 255))}),
    }
    for name, kw in combos.items():
        print(f"\n# ===== {name} =====")
        print(build_phased_stroke(demo_path, size=5.0, segments=28, **kw))
