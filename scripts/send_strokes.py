#!/usr/bin/env python3
"""
send_strokes.py — Send Expresii stroke commands to a running Expresii Paint app.

Expresii Paint (v2026.04.30+) runs a local web API server on port 9000 by default.
This script sends an XST-format stroke file to it via the /confirm-ajax endpoint.

Usage:
    # Send a pre-made .xst file
    python send_strokes.py painting.xst

    # Build strokes inline
    python send_strokes.py --host 192.168.1.50 --port 9000 --command 'B 4' --command 'w 0.5'

    # Build strokes from a list of (x, y, pressure) waypoints
    python send_strokes.py --size 4 --wetness 0.5 --stroke -2.5,-2.8,0 -2.4,-2.7,0.15

    # Check if the server is up
    python send_strokes.py --ping
"""

import argparse
import http.client
import json
import math
import socket
import sys
import urllib.parse
from pathlib import Path


def ping(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if Expresii's stroke server is reachable on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def send_xst(host: str, port: int, xst_text: str, timeout: float = 10.0,
            max_width: int = 1200, max_height: int = 1200) -> dict:
    """
    POST the XST text to <host>:<port>/confirm-ajax as a multipart form.

    Mirrors the official Command Console client, which sends `message` plus
    `maxWidth`/`maxHeight`. NOTE: those two are placeholder fields on the server
    (they don't actually change the render size) — they're included only to
    match the client wire format exactly. The reliable-result behavior comes
    from fetch_render() waiting for status == 'done', not from these fields.
    Returns a dict with 'ok', 'status', 'sent_chars', 'request_id', and
    optional 'error'.
    """
    boundary = "----amamiBoundary" + str(hash(xst_text) & 0xFFFFFFFF)
    fields = [
        ("message", xst_text),
        ("maxWidth", str(max_width)),
        ("maxHeight", str(max_height)),
    ]
    parts = []
    for name, value in fields:
        parts.append(f"--{boundary}\r\n")
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        parts.append(f"{value}\r\n")
    body = ("".join(parts) + f"--{boundary}--\r\n").encode("utf-8")

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        conn.request("POST", "/confirm-ajax", body=body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        # The API returns JSON with a requestId we can poll for the rendered image.
        rid = None
        try:
            rid = json.loads(resp_body).get("requestId")
        except (ValueError, AttributeError):
            pass
        return {
            "ok": 200 <= resp.status < 300,
            "status": resp.status,
            "sent_chars": len(xst_text),
            "request_id": rid,
            "response": resp_body[:500],
        }
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        return {"ok": False, "status": "no_response", "error": str(e)}


# ── Brush tilt (2D orientation) ─────────────────────────────────────────────
# A real recorded stroke (see references/xst-format.md) uses the `s` frame
# fields `s <x> <y> <z> <Pitch> <Roll> <Turn> <Pressure>`. The brush TUFT is
# splashed sideways (so the 9-node color gradient paints across the stroke
# WIDTH) by combininng ROLL and PITCH:
#   Roll  > 0 -> tuft points West ;  Roll  < 0 -> East
#   Pitch > 0 -> tuft points North;  Pitch < 0 -> South
# The splay MAGNITUDE (|roll|+|pitch|) controls how much of the root color
# shows. We express tilt as (roll, pitch).
CARDINAL = {
    # (roll, pitch) — verified against the recorded 4-direction dab sample:
    #   East  -> Roll=-54   North -> Pitch=+57
    #   West  -> Roll=+72   South -> Pitch=-67
    "E": (-54.0, 0.0), "East": (-54.0, 0.0),
    "W": (72.0, 0.0),  "West": (72.0, 0.0),
    "N": (0.0, 57.0),  "North": (0.0, 57.0),
    "S": (0.0, -67.0), "South": (0.0, -67.0),
}
_AUTOTILT = (-44.0, 0.0)  # default splay (Roll) for a gradient stroke


def _resolve_tilt(tilt):
    """Normalize a tilt argument to (roll, pitch).

    Accepts:
      - None / 0.0           -> (0.0, 0.0)  (no splay; tip-only contact)
      - scalar (float/int)   -> (0.0, scalar)  == Roll only (back-compat)
      - (roll, pitch) tuple  -> as-is
      - "E"/"W"/"N"/"S"       -> CARDINAL direction tuple
    """
    if tilt is None:
        return (0.0, 0.0)
    if isinstance(tilt, str):
        if tilt not in CARDINAL:
            raise ValueError(f"unknown tilt direction {tilt!r}; use one of {list(CARDINAL)}")
        return CARDINAL[tilt]
    if isinstance(tilt, (int, float)):
        return (0.0, float(tilt))
    if isinstance(tilt, (tuple, list)) and len(tilt) == 2:
        return (float(tilt[0]), float(tilt[1]))
    raise ValueError(f"bad tilt {tilt!r}: expected scalar, (roll,pitch), or cardinal name")


def build_circle(cx: float = 0.0, cy: float = 0.0, radius: float = 1.0,
                size: float = 6.0, wetness: float = 0.5, scratch: float = 0.5,
                n_frames: int = 96, pressure_plateau: float = 0.75,
                clear_first: bool = True, color=None, tilt=0.0) -> str:
    """
    Build a closed circle XST. Returns a ready-to-send stroke string.

    The stroke is n_frames+1 points around the circle. Pressure ramps 0.5 ->
    plateau over the first 10%, holds the plateau, ramps back to 0.5 over the
    last 10%. A leading lift bookend (pressure 0, z=+0.0625) puts the brush
    DOWN onto the paper so Expresii registers contact. We do NOT add a trailing
    lift: for a closed loop the last contact frame meets the first at the seam
    with pressure on both sides, so the ring closes continuously. A trailing
    lift would raise the brush exactly at the join and open a visible gap.

    (Open strokes — build_stroke_command — DO need a trailing lift to lift the
    brush off the paper at the end.)
    """
    lines = []
    if clear_first:
        lines.append("c")
    lines += [f"B {size:.5f}", f"w {wetness:.5f}"]
    # Color: set the brush NODES (0..8, tip->root). A tuft gradient (tip!=root)
    # paints a color transition across the brush; with a 2D tilt (see s frames)
    # the tuft lies sideways so the gradient shows across the stroke WIDTH.
    roll, pitch = _resolve_tilt(tilt)
    cres = _resolve_color(color)
    if cres is not None:
        kind, *cargs = cres
        if kind == "gradient":
            for line in _color_l_gradient(*cargs):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT  # splay the tuft sideways so the gradient shows
        else:
            for line in _color_l_all_nodes(cargs[0]):
                lines.append(line)
    lines.append(f"i {scratch:.5f}")

    # Bookend (brush down): lifted, just before the seam. This puts the brush
    # down onto the paper so Expresii registers contact. We do NOT add a trailing
    # lift: the ring's last contact frame and first contact frame meet at the
    # seam with pressure applied on both sides, so the loop closes continuously.
    # A trailing lift frame would raise the brush exactly at the join and open
    # a gap, so we omit it.
    seam_pad = 2 * math.pi / n_frames   # one frame-step of overlap

    a0 = -seam_pad
    lines.append(f"s {cx + radius*math.cos(a0):.5f} {cy + radius*math.sin(a0):.5f} "
                 f"0.06250 {pitch:.5f} {roll:.5f} 0 0.00000")

    # Real frames: from -pad through 2pi+pad (overlapping the seam).
    n_total = n_frames + 2
    for i in range(n_total + 1):
        theta = -seam_pad + (i / n_total) * (2 * math.pi + 2 * seam_pad)
        x = cx + radius * math.cos(theta)
        y = cy + radius * math.sin(theta)
        tp = (theta + seam_pad) / (2 * math.pi)
        tp = max(0.0, min(1.0, tp))
        if tp < 0.1:
            p = 0.5 + (pressure_plateau - 0.5) * (tp / 0.1)
        elif tp > 0.9:
            p = pressure_plateau - (pressure_plateau - 0.5) * ((tp - 0.9) / 0.1)
        else:
            p = pressure_plateau
        z = 0.0625 - 0.125 * p
        # s <x> <y> <z> <Pitch> <Roll> <Turn> <Pressure>
        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")

    return "\n".join(lines) + "\n"


def parse_waypoint(s: str) -> tuple:
    """Parse 'x,y,p' or 'x,y' (pressure defaults to 0.5) into a tuple."""
    parts = s.split(",")
    if len(parts) == 2:
        x, y = float(parts[0]), float(parts[1])
        return (x, y, 0.5)
    elif len(parts) == 3:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    else:
        raise ValueError(f"Bad waypoint '{s}': expected 'x,y' or 'x,y,pressure'")


def build_stroke_command(waypoints: list, size: float, wetness: float, scratch: float,
                         z_base: float = 0.0, tilt_y: float = 0.0, tilt_x: float = 0.0,
                         barrel_rot: float = 0.0) -> str:
    """
    Build a minimal XST script: setup (size/wetness/scratch) + brush bookends
    + stroke frames from waypoints.

    waypoints: list of (x, y, pressure) tuples. Pressure range [0.0, 1.0].
    z for each frame is auto-derived from pressure using z = 0.0625 - 0.125 *
    pressure (the empirically correct coupling for a flat stroke).

    BOOKEND (brush up/down) FRAMES ARE REQUIRED: Expresii registers a stroke
    only when the brush transitions lifted (pressure 0, z=+0.0625) -> contact
    -> lifted. We emit a leading bookend (brush down at the first waypoint) and
    a trailing bookend (brush up at the last waypoint) so the up/down events
    are recorded. Without them the brush never touches the paper and nothing draws.
    """
    lines = [
        "# Generated by expresii-brush skill",
        f"B {size:.5f}",
        f"w {wetness:.5f}",
        f"i {scratch:.5f}",
    ]
    if waypoints:
        x0, y0, _ = waypoints[0]
        lines.append(f"s {x0:.5f} {y0:.5f} 0.06250 {tilt_y:.5f} {tilt_x:.5f} {barrel_rot:.5f} 0.00000")
    for x, y, p in waypoints:
        z = 0.0625 - 0.125 * p
        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {tilt_y:.5f} {tilt_x:.5f} {barrel_rot:.5f} {p:.5f}")
    if waypoints:
        x1, y1, _ = waypoints[-1]
        lines.append(f"s {x1:.5f} {y1:.5f} 0.06250 {tilt_y:.5f} {tilt_x:.5f} {barrel_rot:.5f} 0.00000")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Pressure / wetness / scratch PROFILES
# Sourced from the Amami Inker app (github.com/expresii-paint/Amami, Amami.html).
# Each profile is a piecewise-linear curve over stroke progress t in [0, 1].
#   Pressure  p in [0.1, 0.8]   -> per-frame stroke pressure (drives z too)
#   Wetness   w in levels 1..12 -> XST `w` = level / 12  (1=driest, 12=wettest)
#   Scratch   s in [0, 1]       -> XST `i`  (0=smooth, 1=maximum broken texture)
# Pressure varies PER FRAME (the `s` command carries it). Wetness/scratch are
# brush-global in XST, but Expresii accepts re-issuing `w`/`i` MID-STROKE
# (verified: the stroke stays continuous, no lift between re-issues), so a
# varying wetness/scratch profile is built by re-emitting `w`/`i` per segment.
# ---------------------------------------------------------------------------
PRESSURE_PROFILES = {
    "Standard":   [("t", "p"), (0.0, 0.1), (0.016, 0.568), (0.812, 0.739), (1.0, 0.1)],
    "Smooth Bell":[(0.0, 0.1), (0.25, 0.4), (0.5, 0.8), (0.75, 0.4), (1.0, 0.1)],
    "Constant":   [(0.0, 0.6), (1.0, 0.6)],
    "Fade In":    [(0.0, 0.0), (0.5, 0.3), (1.0, 0.8)],
    "Fade Out":   [(0.0, 0.8), (0.5, 0.3), (1.0, 0.0)],
}
WETNESS_PROFILES = {  # values are levels 1..12 (XST w = level/12)
    "Level 5 — Medium":     [(0.0, 5), (1.0, 5)],
    "Level 1 — Driest":     [(0.0, 1), (1.0, 1)],
    "Level 12 — Wettest":   [(0.0, 12), (1.0, 12)],
    "Dry to Wet":         [(0.0, 1), (1.0, 12)],
    "Wet to Dry":         [(0.0, 12), (1.0, 1)],
    "Wet Middle":         [(0.0, 1), (0.3, 1), (0.5, 12), (0.7, 1), (1.0, 1)],
}
SCRATCH_PROFILES = {  # values 0..1
    "None":       [(0.0, 0.0), (1.0, 0.0)],
    "Light":      [(0.0, 0.2), (1.0, 0.2)],
    "Medium":     [(0.0, 0.5), (1.0, 0.5)],
    "Heavy":      [(0.0, 0.8), (1.0, 0.8)],
    "Maximum":    [(0.0, 1.0), (1.0, 1.0)],
    "Build Up":   [(0.0, 0.0), (1.0, 1.0)],
    "Fade Out":   [(0.0, 1.0), (1.0, 0.0)],
    "Mid Spike":  [(0.0, 0.0), (0.3, 0.0), (0.5, 0.8), (0.7, 0.0), (1.0, 0.0)],
}

# ---------------------------------------------------------------------------
# Color
# Expresii sets brush color with `l <node> R G B` (each 0..255, NO alpha). The
# color is brush-global: it applies to subsequent strokes until changed. We
# expose named fixed colors plus color-RAMP profiles (HSL endpoints interpolated
# along the
# stroke) for gradients. `parse_color` also accepts "r,g,b" or "#rrggbb".
# ---------------------------------------------------------------------------
COLOR_PROFILES = {  # fixed RGB (0..255)
    "Black":      (0, 0, 0),
    "White":      (245, 245, 245),
    "Indigo":     (40, 50, 120),
    "Cobalt":     (30, 80, 180),
    "SapGreen":    (40, 110, 50),
    "Viridian":   (20, 120, 110),
    "Vermilion":  (210, 60, 30),
    "Cadmium":    (220, 110, 20),
    "Ochre":      (180, 140, 60),
    "Magenta":    (170, 40, 110),
    "PaynesGray": (50, 60, 70),
    "Sepia":      (90, 60, 40),
}
COLOR_RAMP_PROFILES = {  # HSL ramps (h 0..360, s/l 0..1) along progress t in [0,1]
    "WarmToCool": [(0.0, (15, 0.8, 0.5)), (1.0, (220, 0.7, 0.45))],
    "CoolToWarm": [(0.0, (220, 0.7, 0.45)), (1.0, (15, 0.8, 0.5))],
    "LightToDark":[(0.0, (40, 0.3, 0.8)), (1.0, (40, 0.9, 0.2))],
    "HueCycle":   [(0.0, (0, 0.8, 0.5)), (0.5, (180, 0.8, 0.5)), (1.0, (360, 0.8, 0.5))],
}


def _hsl_to_rgb(h: float, s: float, l: float):
    """h in [0,360), s/l in [0,1] -> (r,g,b) in 0..255."""
    h = ((h % 360) + 360) % 360 / 360.0
    if s == 0:
        v = round(l * 255)
        return (v, v, v)
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    def hue(pc):
        if pc < 0:
            pc += 1
        if pc > 1:
            pc -= 1
        if pc < 1 / 6:
            return p + (q - p) * 6 * pc
        if pc < 1 / 2:
            return q
        if pc < 2 / 3:
            return p + (q - p) * (2 / 3 - pc) * 6
        return p
    return tuple(round(c * 255) for c in (hue(h + 1/3), hue(h), hue(h - 1/3)))


def parse_color(spec) -> tuple:
    """Resolve a color spec to an (r,g,b) 0..255 tuple.

    Accepts: a COLOR_PROFILES name, a "r,g,b" string, or a "#rrggbb" hex.
    Raises ValueError if unparseable.
    """
    if isinstance(spec, (tuple, list)) and len(spec) == 3:
        return tuple(int(round(c)) for c in spec)
    s = str(spec).strip()
    if s in COLOR_PROFILES:
        return COLOR_PROFILES[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    if "," in s:
        parts = [float(v) for v in s.split(",")]
        if len(parts) == 3:
            return tuple(int(round(v)) for v in parts)
    raise ValueError(f"unknown color: {spec!r}")


def _lerp_rgb(a, b, f):
    return tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(3))


def _color_l_command(rgb, node: int = 0) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return f"l {node} {r} {g} {b}"


def _color_l_all_nodes(rgb) -> list:
    """Expresii loads color per brush NODE (0..8, tip->root). Set all 9 so the
    whole brush carries the same color (a single l 0 leaves the rest default)."""
    return [_color_l_command(rgb, node) for node in range(9)]


def _color_l_gradient(rgb_tip, rgb_root) -> list:
    """Set a color GRADIENT across the brush tuft: node 0 (tip) = rgb_tip,
    node 8 (root) = rgb_root, linearly interpolated between. Tilting the brush
    (Roll/Pitch in the `s` frames) lays the tuft sideways so this gradient
    paints across the stroke WIDTH. Flat (no tilt) shows it only along the
    tuft length."""
    return [_color_l_command(_lerp_rgb(rgb_tip, rgb_root, i / 8), i)
            for i in range(9)]


def _color_l_ramp(hsl_stops) -> list:
    """Set a multi-stop HSL RAMP across the tuft (nodes 0..8). Stops are
    (t, h, s, l) with t in [0,1]. Hue is interpolated linearly (NOT
    shortest-path) so a HueCycle 0->180->360 sweep actually traverses the full
    wheel across the brush width — giving a rainbow rather than collapsing to
    a flat color. Used for COLOR_RAMP_PROFILES names."""
    stops = sorted(hsl_stops, key=lambda st: st[0])
    out = []
    for i in range(9):
        t = i / 8
        # find the segment [a, b] containing t
        a, b = stops[0], stops[-1]
        for j in range(len(stops) - 1):
            if stops[j][0] <= t <= stops[j + 1][0]:
                a, b = stops[j], stops[j + 1]
                break
        span = (b[0] - a[0]) or 1.0
        f = (t - a[0]) / span
        h = a[1] + (b[1] - a[1]) * f          # linear hue (full wheel)
        s = a[2] + (b[2] - a[2]) * f
        l = a[3] + (b[3] - a[3]) * f
        rgb = _hsl_to_rgb(h % 360, s, l)
        out.append(_color_l_command(rgb, i))
    return out


def _resolve_color(spec):
    """Resolve a color spec into one of:
      None                         -> leave default brush color
      ("solid",   rgb)             -> all 9 nodes same
      ("gradient", rgb_tip, rgb_root) -> tuft gradient (tip->root)
    Accepts: a name / "r,g,b" / "#rrggbb" (solid); a COLOR_RAMP_PROFILES name
    (gradient using its start/end HSL); or "tip:root" / (c1, c2) pair
    (explicit tuft gradient between two colors)."""
    if spec is None:
        return None
    if isinstance(spec, (tuple, list)) and len(spec) == 2 and not (
            isinstance(spec[0], (int, float)) and len(spec) == 3):
        # pair of colors -> explicit tuft gradient
        return ("gradient", parse_color(spec[0]), parse_color(spec[1]))
    if isinstance(spec, str):
        ramp = COLOR_RAMP_PROFILES.get(spec)
        if ramp is not None:
            # Multi-stop HSL ramp. Keep the stops so the emitter can sweep the
            # full hue wheel across the tuft (first/last alone would collapse a
            # HueCycle 0->360 to a flat color, since h0 == h360).
            return ("ramp", [(_t, _h, _s, _l) for _t, (_h, _s, _l) in ramp])
        if ":" in spec:
            a, b = spec.split(":", 1)
            return ("gradient", parse_color(a), parse_color(b))
        return ("solid", parse_color(spec))
    return ("solid", parse_color(spec))


# ---------------------------------------------------------------------------
# Stroke library — named presets bundling path + profile choices, so the agent
# can call paint("dry_brush_line") instead of re-deriving parameters each time.
# Each entry: dict with 'waypoints' (or 'shape'), and profile names. Used by
# paint() and the --preset CLI flag.
# ---------------------------------------------------------------------------
STROKE_LIBRARY = {
    "dry_brush_line": {
        "waypoints": [(-1.5, 0.0), (1.5, 0.0)],
        "pprofile": "Standard", "wprofile": "Level 1 — Driest", "sprofile": "Build Up",
        "size": 5,
    },
    "wet_wash_line": {
        "waypoints": [(-1.5, 0.0), (1.5, 0.0)],
        "pprofile": "Smooth Bell", "wprofile": "Level 12 — Wettest", "sprofile": "None",
        "size": 6,
    },
    "calligraphy_curve": {
        "waypoints": [(-1.5, -0.6), (-0.5, 0.4), (0.5, 0.4), (1.5, -0.6)],
        "pprofile": "Fade In", "wprofile": "Level 5 — Medium", "sprofile": "Medium",
        "size": 5,
    },
    "scratchy_loop": {
        "closed": True, "waypoints": [(-1.0, 0.0), (0.0, 1.0), (1.0, 0.0), (0.0, -1.0)],
        "pprofile": "Constant", "wprofile": "Level 1 — Driest", "sprofile": "Heavy",
        "size": 5,
    },
    "bold_dot": {
        "radius": 0.4, "pprofile": None, "wprofile": "Level 8 — Medium", "sprofile": "None",
        "size": 7,
    },
}


def paint(name: str, color=None, **overrides) -> str:
    """Build one stroke from the STROKE_LIBRARY preset, with optional overrides.

    color: a name / "r,g,b" / "#rrggbb" (solid); "tip:root" or a
           COLOR_RAMP_PROFILES name (tuft gradient across the brush).
    tilt:  brush tilt in degrees (Tilt-Y). A gradient auto-defaults to 45° so
           the tuft lies sideways and the gradient shows across the stroke.
    overrides: any preset key (pprofile, wprofile, sprofile, size, tilt, ...).
    """
    if name not in STROKE_LIBRARY:
        raise ValueError(f"unknown preset: {name!r} (have: {sorted(STROKE_LIBRARY)})")
    cfg = dict(STROKE_LIBRARY[name])
    cfg.update(overrides)
    closed = cfg.get("closed", False)
    tilt = cfg.get("tilt", 0.0)
    wps = cfg.get("waypoints")
    if wps is not None:
        waypoints = [(float(x), float(y), 0.5) for (x, y) in wps]
        return build_profile_stroke(
            waypoints, size=cfg.get("size", 5),
            pprofile=cfg.get("pprofile", "Standard"),
            wprofile=cfg.get("wprofile", "Level 5 — Medium"),
            sprofile=cfg.get("sprofile", "None"),
            segments=cfg.get("segments", 16), closed=closed, color=color, tilt=tilt)
    # shape-based preset (e.g. bold_dot uses a circle)
    return build_circle(
        cx=cfg.get("cx", 0.0), cy=cfg.get("cy", 0.0), radius=cfg.get("radius", 1.0),
        size=cfg.get("size", 6), wetness=cfg.get("wetness", 0.5),
        scratch=cfg.get("scratch", 0.5), clear_first=False, color=color, tilt=tilt)



def _interp(pts: list, t: float) -> float:
    """Piecewise-linear interpolation of a [(t, v), ...] curve at progress t."""
    pts = [p for p in pts if isinstance(p, (tuple, list)) and len(p) == 2 and p[0] != "t"]
    if not pts:
        return 0.0
    pts = sorted(pts, key=lambda p: p[0])
    if t <= pts[0][0]:
        return float(pts[0][1])
    if t >= pts[-1][0]:
        return float(pts[-1][1])
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return float(v1)
            return float(v0 + (v1 - v0) * (t - t0) / (t1 - t0))
    return float(pts[-1][1])


def build_profile_stroke(waypoints: list, size: float = 6.0,
                         pprofile: str = "Standard", wprofile: str = "Level 5 — Medium",
                         sprofile: str = "None", segments: int = 16,
                         closed: bool = False, color=None, tilt=0.0) -> str:
    """
    Build an XST stroke using named pressure/wetness/scratch profiles.

    waypoints : list of (x, y, pressure) tuples defining the PATH (pressure here
                is used only as a fallback if a profile is unknown; normally the
                pressure profile drives per-frame pressure).
    pprofile  : key in PRESSURE_PROFILES  (per-frame pressure curve)
    wprofile  : key in WETNESS_PROFILES   (levels 1..12 -> XST w = level/12)
    sprofile  : key in SCRATCH_PROFILES   (0..1 -> XST i)
    segments  : how many chunks to split the path into (more = smoother wet/scratch
                variation along the stroke)
    closed    : if True, NO trailing lift (loop closes itself; see build_circle rule)

    The stroke is emitted as one continuous path. `w`/`i` are re-issued per
    segment (Expresii keeps the brush down across re-issues). Pressure is
    interpolated per frame from the pressure profile. A leading lift bookend
    puts the brush down; a trailing lift is added ONLY for open strokes.
    """
    pp = PRESSURE_PROFILES.get(pprofile)
    wp = WETNESS_PROFILES.get(wprofile)
    sp = SCRATCH_PROFILES.get(sprofile)
    if not (pp and wp and sp):
        raise ValueError(f"unknown profile: p={pprofile!r} w={wprofile!r} s={sprofile!r}")

    n = len(waypoints)
    if n < 2:
        raise ValueError("need at least 2 waypoints")

    # Precompute cumulative arc-length of the polyline path so we can sample
    # an (x, y) at any progress t in [0, 1] (even with few waypoints).
    pts = [(x, y) for (x, y, _) in waypoints]
    seg_len = [((pts[i+1][0]-pts[i][0])**2 + (pts[i+1][1]-pts[i][1])**2) ** 0.5
               for i in range(n - 1)]
    total = sum(seg_len) or 1.0

    def xy_at(t: float):
        if t <= 0:
            return pts[0]
        if t >= 1:
            return pts[-1]
        target = t * total
        acc = 0.0
        for i, sl in enumerate(seg_len):
            if acc + sl >= target:
                f = (target - acc) / sl if sl > 0 else 0.0
                x = pts[i][0] + f * (pts[i+1][0] - pts[i][0])
                y = pts[i][1] + f * (pts[i+1][1] - pts[i][1])
                return (x, y)
            acc += sl
        return pts[-1]

    lines = ["# Generated by expresii-brush (profile stroke)", f"B {size:.5f}"]

    # Color: set the brush NODES (0..8, tip->root). A tuft gradient paints a
    # transition across the brush; with a 2D tilt the tuft lies sideways so the
    # gradient shows across the stroke WIDTH. Set ONCE here (brush-global),
    # not per path-segment — the tuft gradient is a brush property, not a
    # path-position property.
    roll, pitch = _resolve_tilt(tilt)
    cres = _resolve_color(color)
    if cres is not None:
        kind, *cargs = cres
        if kind == "gradient":
            for line in _color_l_gradient(*cargs):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT  # splay the tuft sideways so the gradient shows
        elif kind == "ramp":
            for line in _color_l_ramp(cargs[0]):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT
        else:
            for line in _color_l_all_nodes(cargs[0]):
                lines.append(line)

    # leading lift bookend (brush down) at first waypoint
    x0, y0, _ = waypoints[0]
    lines.append(f"s {x0:.5f} {y0:.5f} 0.06250 {pitch:.5f} {roll:.5f} 0 0.00000")
    # engage the brush (pen down). Without a `b` marker Expresii treats the `s`
    # frames as a move-without-paint and nothing is deposited — see the recorded
    # sample, where every painted mark is wrapped in `b ... b`.
    lines.append("b " + " ".join(f"{0.01:.5f}" if i in (0, 25) else "0.00000"
                                  for i in range(30)))

    seg = max(1, segments)
    # one continuous stroke: emit an `s` frame at EVERY segment boundary so the
    # brush actually travels (a 2-waypoint path would otherwise produce only
    # endpoint frames with empty segments between -> nothing draws).
    for k in range(seg + 1):
        t = k / seg
        x, y = xy_at(t)
        p = _interp(pp, t)
        z = 0.0625 - 0.25 * p
        wlvl = _interp(wp, t)
        sval = _interp(sp, t)
        # re-issue wetness/scratch so it tracks the profile (brush stays down)
        lines.append(f"w {wlvl / 12:.5f}")
        lines.append(f"i {sval:.5f}")
        # s <x> <y> <z> <Pitch> <Roll> <Turn> <Pressure>
        # The 2D tilt (roll, pitch) splays the tuft sideways so the node
        # gradient shows across the stroke WIDTH (see references/xst-format.md).
        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")

    if not closed:
        x1, y1, _ = waypoints[-1]
        lines.append(f"s {x1:.5f} {y1:.5f} 0.06250 0 0 0 0.00000")
    # release the brush (pen up) — mirrors the leading `b` engage
    lines.append("b " + " ".join("0.00000" for _ in range(30)))
    return "\n".join(lines) + "\n"



def build_dab(pos, direction="E", tilt_deg: float = 54.0, color="Cobalt:Vermilion",
              size: float = 6.0, wetness: float = 0.3, scratch: float = 0.1,
              pressure: float = 0.75) -> str:
    """
    Build a single pressed dab — the primitive behind the user's 4-direction
    tilt sample. A dab is the simplest expressive mark: the brush is lowered
    onto one spot and splayed in a direction so the 9-node tuft gradient shows
    across the paper.

    The dab is emitted as a `b`-sandwiched block (matching a real recorded
    stroke):
        b ...            # brush pen-down marker
        s x y z P R T 0  # pressed (z dips as pressure rises)
        ... pressure pulse up to `pressure`, then back to 0 ...
        s x y z P R T 0  # released
        b ...            # brush pen-up marker

    Parameters
    ----------
    pos       : (x, y) center of the dab in normalized canvas units.
    direction : tilt DIRECTION. One of "E"/"W"/"N"/"S" (cardinal, see CARDINAL),
                OR an explicit (roll, pitch) tuple, OR a scalar (Roll only).
                The dab splays the tuft toward this direction so the
                tip->root color gradient fans out that way.
    tilt_deg  : splay MAGNITUDE in degrees. Larger = more of the root color
                shows (the tuft lies flatter). Default 54 matches the sample's
                East dab; the sample's West dab used 72 (more root/red).
    color     : solid name/"r,g,b"/"#hex", or "tip:root" / ramp-profile for a
                tuft gradient (tip = brush tip, root = bristle base).
    size      : brush size (XST B).
    wetness   : XST w (0..1).
    scratch   : XST i (0..1).
    pressure  : peak pressure of the dab (0..1); the frame pulses 0 -> peak -> 0.
    """
    x, y = pos
    # Resolve direction -> (roll, pitch). Cardinal names already carry a
    # sign; tilt_deg sets the overall splay magnitude.
    if isinstance(direction, str):
        base_r, base_p = CARDINAL[direction]
        roll = math.copysign(tilt_deg, base_r) if base_r != 0.0 else 0.0
        pitch = math.copysign(tilt_deg, base_p) if base_p != 0.0 else 0.0
    else:
        roll, pitch = _resolve_tilt(direction)
        # honor tilt_deg as the overall magnitude for scalar/tuple direction
        if roll == 0.0 and pitch == 0.0:
            roll = -tilt_deg  # default East-ish splay
        else:
            mag = abs(roll) + abs(pitch)
            roll = roll / mag * tilt_deg
            pitch = pitch / mag * tilt_deg

    lines = [f"B {size:.5f}", f"w {wetness:.5f}"]
    cres = _resolve_color(color)
    if cres is not None:
        kind, *cargs = cres
        if kind == "gradient":
            for line in _color_l_gradient(*cargs):
                lines.append(line)
        elif kind == "ramp":
            for line in _color_l_ramp(cargs[0]):
                lines.append(line)
        else:
            for line in _color_l_all_nodes(cargs[0]):
                lines.append(line)
    lines.append(f"i {scratch:.5f}")
    # b marker (pen down) — 30 base values like the recorded sample
    lines.append("b " + " ".join(f"{0.01:.5f}" if i in (0, 25) else "0.00000"
                                  for i in range(30)))
    # pen-down posture (lifted), then pressed frames with the splay
    lines.append(f"s {x:.5f} {y:.5f} 0.06250 {pitch:.5f} {roll:.5f} 0 0.00000")
    z0 = 0.0625
    # pressure pulse: rise to peak, hold a few frames, fall to 0 (cosine-ish)
    n_up = 8
    for k in range(1, n_up + 1):
        p = pressure * (0.5 - 0.5 * math.cos(math.pi * k / n_up))
        z = z0 - 0.5 * p
        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")
    n_hold = 4
    for _ in range(n_hold):
        z = z0 - 0.5 * pressure
        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {pressure:.5f}")
    for k in range(n_up, -1, -1):
        p = pressure * (0.5 - 0.5 * math.cos(math.pi * k / n_up))
        z = z0 - 0.5 * p
        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")
    # pen-up posture (lifted)
    lines.append(f"s {x:.5f} {y:.5f} 0.06250 {pitch:.5f} {roll:.5f} 0 0.00000")
    lines.append("b " + " ".join(f"{0.01:.5f}" if i in (0, 25) else "0.00000"
                                  for i in range(30)))
    return "\n".join(lines) + "\n"


def build_composite(strokes: list, clear_first: bool = True) -> str:
    """
    Combine several stroke blocks into ONE XST, ready to send.

    `strokes` is a list of XST text blocks (each from build_circle(),
    build_stroke_command(), build_profile_stroke(), or a raw string). They are
    joined as-is; each block is responsible for its own bookends (open strokes
    must include lead+trail lifts, closed loops only a leading lift). A single
    `c` (clear) is prepended unless clear_first is False.

    Example:
        build_composite([
            build_circle(cx=0, cy=1.4),
            build_profile_stroke([...], pprofile="Standard",
                                  wprofile="Level 1 — Driest", sprofile="Build Up"),
            build_profile_stroke([...], pprofile="Smooth Bell",
                                  wprofile="Wet to Dry", sprofile="Build Up"),
        ])
    """
    parts = ["c"] if clear_first else []
    for s in strokes:
        block = s.strip()
        # strip a leading `c` from each block so the composite has exactly one
        # clear (the sub-builders like build_circle emit their own `c`).
        if block.startswith("c\n"):
            block = block[2:].strip()
        elif block == "c":
            block = ""
        parts.append(block)
    return "\n".join(parts) + "\n"


def fetch_render(host: str, port: int, request_id: int, out_path: str,
                  tries: int = 60, interval: float = 0.9,
                  initial_wait: float = 0.0) -> dict:
    """
    Poll GET /result/<requestId> and save the rendered paper (base64 PNG).

    Mirrors the official Command Console client: poll every ~0.9s and accept the
    frame only once the server reports status == 'done' (it carries the final
    imageBase64). This is what makes the client "always return the correct
    image" — it waits for the server's done signal instead of grabbing whatever
    frame is served first (which can be a previous/stale render). We accept the
    FIRST done frame; Expresii serves it for a short window after playback +
    ~5s wet-paint buffer, so poll fast once done.

    Returns {'ok': True, 'bytes': N, 'path': out_path} on success, else
    {'ok': False, 'error': ...}.
    """
    import base64
    import time
    if initial_wait > 0:
        time.sleep(initial_wait)
    last_status = None
    for _ in range(tries):
        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", f"/result/{request_id}")
            payload = json.loads(conn.getresponse().read().decode("utf-8", errors="replace"))
            conn.close()
            last_status = payload.get("status")
            if last_status == "done":
                b64 = payload.get("imageBase64")
                err = payload.get("error")
                if err:
                    return {"ok": False, "error": f"render failed: {err}"}
                if b64:
                    raw = base64.b64decode(b64)
                    Path(out_path).write_bytes(raw)
                    return {"ok": True, "bytes": len(raw), "path": out_path}
                return {"ok": False, "error": "done but no imageBase64"}
        except (ConnectionError, OSError, ValueError, KeyError) as e:
            return {"ok": False, "error": str(e)}
        time.sleep(interval)
    return {"ok": False, "error": f"no 'done' frame after {tries} polls (last status: {last_status!r})"}


def main():
    ap = argparse.ArgumentParser(
        description="Send XST stroke commands to an Expresii Paint stroke server.",
        epilog="Pass a .xst file OR use --command/--stroke to build inline.",
    )
    ap.add_argument("xst_file", nargs="?", help="Path to a pre-made .xst file to send")
    ap.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=9000, help="Server port (default: 9000)")
    ap.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds (default: 10)")
    ap.add_argument("--max-width", type=int, default=1200, help="Max render width (default: 1200)")
    ap.add_argument("--max-height", type=int, default=1200, help="Max render height (default: 1200)")
    ap.add_argument("--ping", action="store_true", help="Just check if the server is up; exit 0 if yes")
    # Inline stroke building
    ap.add_argument("--size", type=float, default=4.0, help="Brush size 1-7 (default: 4)")
    ap.add_argument("--wetness", type=float, default=0.5, help="Brush wetness 0.01-1.0 (default: 0.5)")
    ap.add_argument("--scratch", type=float, default=0.5, help="Brush scratchiness 0.0-1.0 (default: 0.5)")
    ap.add_argument("--command", action="append", default=[], help="Add a raw XST line (e.g. 'B 4'). Repeatable.")
    ap.add_argument("--stroke", action="append", default=[],
                    help="Add a stroke waypoint 'x,y,pressure'. Pressure defaults to 0.5. Repeatable. "
                         "Use the = form (--stroke=-1,-1,0) when values are negative.")
    ap.add_argument("--pstroke", action="append", default=[],
                    help="Profile stroke waypoint 'x,y' (path only; pressure comes from the "
                         "pressure profile). Repeatable. Used with --pprofile/--wprofile/--sprofile.")
    ap.add_argument("--pprofile", default="Standard",
                    help="Pressure profile name (PRESSURE_PROFILES). Default: Standard.")
    ap.add_argument("--wprofile", default="Level 5 — Medium",
                    help="Wetness profile name (WETNESS_PROFILES, levels 1-12). Default: Level 5 — Medium.")
    ap.add_argument("--sprofile", default="None",
                    help="Scratch profile name (SCRATCH_PROFILES, 0-1). Default: None.")
    ap.add_argument("--segments", type=int, default=16,
                    help="Chunks to split a profile stroke into for wet/scratch variation (default: 16)")
    ap.add_argument("--closed", action="store_true",
                    help="Treat --pstroke as a closed loop (no trailing lift; loop self-closes)")
    ap.add_argument("--json", action="store_true", help="Emit a machine-readable JSON result on stdout")
    ap.add_argument("--circle", nargs="?", const="1.0", type=float, metavar="RADIUS",
                    help="Build a closed circle (bookended) instead of inline strokes. "
                         "Pass a radius (default: 1.0) or use --circle with no value. "
                         "Sends c (clear) + B6/w0.5/i0.5 + overlapping-seam circle. Use --verify to see it.")
    ap.add_argument("--verify", nargs="?", const="render.png", metavar="OUT.png",
                    help="After sending, poll /result/<id> for the rendered paper and save it. "
                         "Uses the same wait-for-'done' protocol as the official Command Console "
                         "client (which 'always returns the correct image'), so it captures THIS "
                         "render, not a stale frame. Pass a path to choose output (default: render.png). "
                         "Raise --verify-wait / --verify-retries if the server is slow.")
    ap.add_argument("--verify-retries", type=int, default=3,
                    help="How many times to re-send if --verify keeps getting a blank frame (default: 3)")
    ap.add_argument("--verify-wait", type=float, default=2.0,
                    help="Seconds to wait BEFORE polling /result (the server needs a moment to "
                         "start playback). The poll then waits for status='done' (default: 2.0). "
                         "Raise this if the first polls return before the render starts.")
    ap.add_argument("--composite", metavar="SPEC.json",
                    help="Build a multi-stroke composite from a JSON spec file. The spec is a "
                         "JSON array of stroke descriptors, e.g. "
                         '[{"type":"circle","cy":1.4},{"type":"profile","waypoints":[[-1.5,0],[1.5,0]],'
                         '"pprofile":"Standard","wprofile":"Level 1 — Driest","sprofile":"Build Up"}]. '
                         "Each descriptor maps to build_circle()/build_profile_stroke().")
    ap.add_argument("--preset", metavar="NAME",
                    help="Build a stroke from the STROKE_LIBRARY preset (e.g. dry_brush_line, "
                         "wet_wash_line, calligraphy_curve, scratchy_loop, bold_dot). Use with --color.")
    ap.add_argument("--color", metavar="SPEC",
                    help="Brush color for --preset / --pstroke / --circle / --composite. A "
                         "COLOR_PROFILES name (e.g. Vermilion), 'r,g,b', '#rrggbb', or a tuft "
                         "GRADIENT: a COLOR_RAMP_PROFILES name (WarmToCool, CoolToWarm, "
                         "LightToDark, HueCycle) or 'tip:root' (two colors). A gradient "
                         "auto-tilts the brush 45° so it shows across the stroke; override "
                         "with --tilt.")
    ap.add_argument("--tilt", type=float, default=0.0, metavar="DEG",
                    help="Brush Tilt-Y in degrees (tilts the tuft sideways). A gradient "
                         "color defaults to 45°; set 0 for a flat (lengthwise) tuft.")
    args = ap.parse_args()

    if args.ping:
        up = ping(args.host, args.port)
        result = {"ok": up, "host": args.host, "port": args.port}
        if args.json:
            print(json.dumps(result))
        else:
            print(f"{'UP' if up else 'DOWN'}  {args.host}:{args.port}")
        sys.exit(0 if up else 1)

    # Build the XST text
    if args.xst_file:
        path = Path(args.xst_file)
        if not path.exists():
            print(f"error: file not found: {args.xst_file}", file=sys.stderr)
            sys.exit(2)
        xst_text = path.read_text(encoding="utf-8")
    elif args.circle is not None:
        xst_text = build_circle(radius=args.circle, color=args.color, tilt=args.tilt)
    elif args.preset:
        try:
            xst_text = paint(args.preset, color=args.color)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
    elif args.pstroke:
        try:
            waypoints = [tuple(float(v) for v in s.split(",")) for s in args.pstroke]
            if any(len(wp) != 2 for wp in waypoints):
                raise ValueError("each --pstroke must be 'x,y'")
            waypoints = [(x, y, 0.5) for (x, y) in waypoints]
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        try:
            xst_text = build_profile_stroke(
                waypoints, size=args.size, pprofile=args.pprofile,
                wprofile=args.wprofile, sprofile=args.sprofile,
                segments=args.segments, closed=args.closed, color=args.color, tilt=args.tilt)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
    elif args.composite:
        spec_path = Path(args.composite)
        if not spec_path.exists():
            print(f"error: composite spec not found: {args.composite}", file=sys.stderr)
            sys.exit(2)
        try:
            import json as _json
            spec = _json.loads(spec_path.read_text(encoding="utf-8"))
            blocks = []
            for desc in spec:
                t = desc.get("type", "profile")
                if t == "circle":
                    blocks.append(build_circle(
                        cx=desc.get("cx", 0.0), cy=desc.get("cy", 0.0),
                        radius=desc.get("radius", 1.0), size=desc.get("size", 6.0),
                        wetness=desc.get("wetness", 0.5), scratch=desc.get("scratch", 0.5),
                        color=desc.get("color"), tilt=desc.get("tilt", 0.0)))
                elif t == "profile":
                    wps = [tuple(float(v) for v in wp) for wp in desc["waypoints"]]
                    wps = [(x, y, 0.5) for (x, y) in wps]
                    blocks.append(build_profile_stroke(
                        wps, size=desc.get("size", 6.0),
                        pprofile=desc.get("pprofile", "Standard"),
                        wprofile=desc.get("wprofile", "Level 5 — Medium"),
                        sprofile=desc.get("sprofile", "None"),
                        segments=desc.get("segments", 16),
                        closed=desc.get("closed", False),
                        color=desc.get("color"), tilt=desc.get("tilt", 0.0)))
                elif t == "raw":
                    blocks.append(desc["xst"])
                else:
                    raise ValueError(f"unknown stroke type: {t!r}")
            xst_text = build_composite(blocks)
        except (ValueError, KeyError, _json.JSONDecodeError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
    elif args.command or args.stroke:
        lines = list(args.command)
        if args.stroke:
            try:
                waypoints = [parse_waypoint(s) for s in args.stroke]
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                sys.exit(2)
            lines.append(build_stroke_command(waypoints, args.size, args.wetness, args.scratch))
        xst_text = "\n".join(lines) + "\n"
    else:
        ap.error("provide an XST file or at least one --command/--stroke")

    # Send it
    result = send_xst(args.host, args.port, xst_text, args.timeout)
    result["host"] = args.host
    result["port"] = args.port

    if args.json:
        print(json.dumps(result))
    else:
        if result["ok"]:
            print(f"OK  sent {result['sent_chars']} chars to {args.host}:{args.port} (HTTP {result['status']})")
        else:
            print(f"FAIL  {result.get('status', '?')}  {result.get('error', '')}", file=sys.stderr)
            if "response" in result:
                print(result["response"], file=sys.stderr)

    # Optional self-verification: poll /result/<id> for the painted paper.
    # Uses the client's wait-for-'done' protocol, so it returns the actual
    # render for this request (not a stale previous frame). If the server is
    # slow, raise --verify-wait or --verify-retries.
    if args.verify and result.get("request_id") is not None:
        out = args.verify if isinstance(args.verify, str) else "render.png"
        for attempt in range(1 + args.verify_retries):
            r = fetch_render(args.host, args.port, result["request_id"], out,
                             initial_wait=args.verify_wait)
            if r.get("ok"):
                print(f"RENDER saved: {r['path']} ({r['bytes']} bytes) — read it to confirm the stroke")
                break
            # Re-send to get a fresh requestId and another shot at the poll.
            if attempt < args.verify_retries:
                result = send_xst(args.host, args.port, xst_text, args.timeout,
                                 max_width=args.max_width, max_height=args.max_height)
                if not result.get("ok"):
                    print("RE-SEND fail, stopping verify", file=sys.stderr)
                    break
                print(f"  verify retry {attempt + 1}: re-sent, polling new requestId {result.get('request_id')}")
            else:
                print(f"RENDER failed after {attempt + 1} tries: {r.get('error', '')} "
                      f"Screenshot the Expresii window to confirm.", file=sys.stderr)

    sys.exit(0 if result["ok"] else 3)


if __name__ == "__main__":
    main()
