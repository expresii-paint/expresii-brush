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
import os
import socket
import sys
import tempfile
import threading
import time
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

    CONCURRENCY (post-v2026.07.26 server): the server now has its
    own inbound queue, but flooding it with parallel POSTs still risks the
    old "wedged render" failure mode. To avoid that, ALL sends from this
    process are serialized through a module-level lock (`_SEND_LOCK`) —
    exactly like the console's client-side `isRendering`/`commandQueue`
    gate that disables the "Send & Render" button while one command is
    in flight. Callers that want to pipeline should enqueue their XST
    strings and await each result in turn, never fire-and-forget.

    Returns a dict with 'ok', 'status', 'sent_chars', 'request_id', and
    optional 'error'.
    """
    with _SEND_LOCK:
        return _send_xst_unsafe(host, port, xst_text, timeout,
                                 max_width, max_height)


# Module-level client-side send lock: serializes every POST through this
# process so we never send a new stroke command while one is still rendering
# on the server. Mirrors the console's "Send & Render" button disabling
# itself (isRendering flag) the moment a command is queued.
_SEND_LOCK = threading.Lock()


def _send_xst_unsafe(host: str, port: int, xst_text: str, timeout: float,
                       max_width: int, max_height: int) -> dict:
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
        elif kind == "ramp":
            for line in _color_l_ramp(cargs[0]):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT
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
    "Triple Bell":[(0.0, 0.12), (0.20, 0.78), (0.40, 0.04), (0.55, 0.88),
                   (0.75, 0.04), (0.90, 0.78), (1.0, 0.12)],  # 3 thick peaks, very sparse troughs
    "Triple Bell B":[(0.0, 0.12), (0.12, 0.78), (0.35, 0.04), (0.48, 0.88),
                    (0.70, 0.04), (0.83, 0.78), (1.0, 0.12)],  # 3 thick peaks, slightly left-shifted
    "Triple Bell C":[(0.0, 0.12), (0.28, 0.78), (0.48, 0.04), (0.62, 0.88),
                    (0.82, 0.04), (0.97, 0.78), (1.0, 0.12)],  # 3 thick peaks, slightly right-shifted
    "Triple Bell D":[(0.0, 0.12), (0.22, 0.78), (0.44, 0.04), (0.58, 0.88),
                    (0.78, 0.04), (0.92, 0.78), (1.0, 0.12)],  # 3 thick peaks, asymmetric spacing
    "Constant":   [(0.0, 0.6), (1.0, 0.6)],
    "Fade In":    [(0.0, 0.0), (0.5, 0.3), (1.0, 0.8)],
    "Fade Out":   [(0.0, 0.8), (0.5, 0.3), (1.0, 0.0)],
    "Flick":      [(0.0, 0.05), (0.4, 0.55), (0.85, 0.9), (1.0, 0.0)],  # snap off
    "Bell":       [(0.0, 0.1), (0.5, 0.7), (1.0, 0.05)],  # single hump dry line
    "Sketchy":    [(0.0, 0.2), (0.2, 0.5), (0.4, 0.15), (0.6, 0.55), (0.8, 0.1), (1.0, 0.35)],
}
WETNESS_PROFILES = {  # values are levels 1..12 (XST w = level/12)
    "Level 5 — Medium":     [(0.0, 5), (1.0, 5)],
    "Level 1 — Driest":     [(0.0, 1), (1.0, 1)],
    "Level 2 — Dry":        [(0.0, 2), (1.0, 2)],
    "Level 3 — Dry":        [(0.0, 3), (1.0, 3)],
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
    "WarmToCool":  [(0.0, (15, 0.8, 0.5)), (1.0, (220, 0.7, 0.45))],
    "CoolToWarm":  [(0.0, (220, 0.7, 0.45)), (1.0, (15, 0.8, 0.5))],
    "LightToDark":[(0.0, (40, 0.3, 0.8)), (1.0, (40, 0.9, 0.2))],
    "BlueToDeep":  [(0.0, (210, 0.75, 0.55)), (1.0, (230, 0.85, 0.30))],  # bright mid-blue -> deep indigo
    "HueCycle":    [(0.0, (0, 0.8, 0.5)), (0.5, (180, 0.8, 0.5)), (1.0, (360, 0.8, 0.5))],
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


NOISE_PROFILES = {  # additive (dx, dy) world-unit path offsets sampled along progress
    "none":    lambda t: (0.0, 0.0),
    "shiver":  lambda t: (0.04 * math.sin(17.0 * math.pi * t), 0.03 * math.cos(19.0 * math.pi * t)),
    "scratch": lambda t: (0.06 * (1.0 if (t * 13.0) % 1.0 < 0.35 else 0.0)
                          * math.sin(13.0 * math.pi * t), 0.0),
    "skitter": lambda t: (0.07 * math.sin(21.0 * math.pi * t) * (0.5 + 0.5 * math.sin(11.0 * math.pi * t)),
                          0.05 * math.cos(23.0 * math.pi * t) * (0.5 + 0.5 * math.cos(9.0 * math.pi * t))),
}

ENDING_PROFILES = {  # tail-frame modifications applied to the last N frames of an open stroke
    "none":   lambda p, z, i: (p, z, i),
    "taper":  lambda p, z, i: (max(0.0, p * 0.35), 0.0625, max(0.0, i * 0.4)),
    "flick":  lambda p, z, i: (max(0.0, 0.85 * p), z, i),  # pressure stays, caller presses end via pressure profile
    "blunge": lambda p, z, i: (max(0.0, p * 0.25), 0.0725, min(1.0, i * 1.3)),  # puddle on exit
}

CORNER_PROFILES = {  # corner behaviors applied around waypoint vertices
    "none":   None,
    "dwell":  2,      # insert 2 extra pressed frames at each waypoint transition
}


def build_profile_stroke(waypoints: list, size: float = 6.0,
                         pprofile: str = "Standard", wprofile: str = "Level 5 — Medium",
                         sprofile: str = "None", segments: int = 16,
                         closed: bool = False, color=None, tilt=0.0,
                         wobble: tuple = (0, 0), wobble_phase: float = 0.0,
                         noise: str = "none", ending: str = "none", corner: str = "none") -> str:
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
    wobble    : (amp, cycles) — brush-yaw (Turn field) oscillation along the stroke,
                the side-to-side "wiggle". amp in degrees (0 = straight). Mirrors the
                dry-brush mid scheme's lateral sweep so wet strokes can wiggle too.
    wobble_phase: phase offset applied to the wobble sine so repeated strokes can
                  be desynchronized visually.
    noise     : key in NOISE_PROFILES — additive path perturbation along the stroke.
    ending    : key in ENDING_PROFILES — tail-frame behavior applied to the last N
                frames of an open stroke (taper / flick / blunge).
    corner    : key in CORNER_PROFILES — behavior around waypoint transitions
                (none / dwell).

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

    noise_fn = NOISE_PROFILES.get(noise, NOISE_PROFILES["none"])
    ending_fn = ENDING_PROFILES.get(ending, ENDING_PROFILES["none"])
    corner_n = CORNER_PROFILES.get(corner, 0)

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

    # Color + tilt
    roll, pitch = _resolve_tilt(tilt)
    cres = _resolve_color(color)
    if cres is not None:
        kind, *cargs = cres
        if kind == "gradient":
            for line in _color_l_gradient(*cargs):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT
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

    seg = max(1, segments)
    wob_amp, wob_cyc = wobble if isinstance(wobble, (tuple, list)) else (0, 0)

    # Collect ends metadata for ending apply
    ending_frames = 4 if ending not in {"none", "flick"} else 3 if ending == "flick" else 0
    tail = []

    for k in range(seg + 1):
        t = k / seg
        x, y = xy_at(t)
        p = _interp(pp, t)
        z = 0.0625 - 0.25 * p
        wlvl = _interp(wp, t)
        sval = _interp(sp, t)

        # Path noise/dwell/wobble
        dx, dy = noise_fn(t)
        yw = y + dy + (wob_amp * math.sin(wob_cyc * math.pi * t + wobble_phase) if wob_amp else 0.0)
        xw = x + dx

        tail_idx = (seg - k)
        if ending_fn is not None and tail_idx < ending_frames and ending_frames > 0:
            p, z, sval = ending_fn(p, z, sval)

        lines.append(f"s {xw:.5f} {yw:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")
        if k > 0:
            lines.append(f"w {wlvl / 12:.5f}")
            lines.append(f"i {sval:.5f}")

    if not closed:
        x1, y1, _ = waypoints[-1]
        lines.append(f"s {x1:.5f} {y1:.5f} 0.06250 0 0 0 0.00000")
    return "\n".join(lines) + "\n"


def _star_header(size: float = 6.0, wetness: float = 0.09,
                 node_colors: list = None, a_axes: tuple = None) -> list:
    """Emit the standard Expresii record header that precedes every `s` frame
    in a real recorded .XST (see references/star.XST). Mirroring this exactly
    is what makes a generated stroke actually deposit — the server ignores a
    stroke that lacks the T/w/C/B/e/k/l/a preamble (a generated star without it
    returned a blank 409589-byte frame; the recorded star with it renders).

    node_colors: list of 9 (r,g,b) for brush nodes 0..8 (tip->root); defaults to
    the recorded star's rainbow ramp.
    a_axes: list of 4 axis ids for the `a` lines. Default (0,1,2,3) rotates
    the tuft through its 4 axes; all-zero (0,0,0,0) collapses to a fixed
    node mapping (pre-update rainbow used this).
    """
    lines = [
        "T   0.00000",
        f"w   {wetness:.5f}",
        "C   4.00000",
        f"B   {size:.5f}",
        "e   0.00000",
        "k   0.00000",
    ]
    if node_colors is None:
        node_colors = [
            (230, 25, 25), (230, 179, 25), (128, 230, 25), (25, 230, 77),
            (25, 229, 230), (25, 76, 230), (127, 25, 230), (230, 25, 178),
            (230, 25, 25),
        ]
    for i, (r, g, b) in enumerate(node_colors):
        lines.append(f"l {i} {int(r)} {int(g)} {int(b)}")
    for ax in (a_axes or (0, 1, 2, 3)):
        lines.append(f"a   {ax}.00000   0.00000")
    return lines


def build_path_stroke(waypoints: list, size: float = 6.0, wetness: float = 0.09,
                      color=None, tilt=0.0, pressure_profile: str = "Standard",
                      wetness_profile: str = "Level 5 — Medium",
                      scratch_profile: str = "None", segments: int = 16,
                      closed: bool = False, node_colors: list = None,
                      a_axes: tuple = None) -> str:
    """
    Build an XST stroke from a list of waypoints that RENDERS in Expresii.
    This function includes the full T/w/C/B/e/k/l/a preamble header so the
    server actually deposits paint — unlike build_profile_stroke which lacks
    the header and produces flat scribbles.

    waypoints : list of (x, y, pressure) tuples defining the PATH.
    size      : brush size (XST `B`).
    wetness   : base wetness level 0..1 (XST `w`).
    color     : solid color, gradient tuple, or COLOR_RAMP_PROFILES name.
    tilt      : (pitch, roll) tuple or single value for brush orientation.
    pressure_profile : key in PRESSURE_PROFILES for per-frame pressure curve.
    wetness_profile  : key in WETNESS_PROFILES for wetness along stroke.
    scratch_profile  : key in SCRATCH_PROFILES for scratch along stroke.
    segments  : how many chunks to split the path into.
    closed    : if True, NO trailing lift (loop closes itself).
    node_colors : custom 9-node color list (tip->root) for tuft gradient.
    a_axes    : 4 axis IDs for the `a` lines.
    """
    pp = PRESSURE_PROFILES.get(pressure_profile)
    wp = WETNESS_PROFILES.get(wetness_profile)
    sp = SCRATCH_PROFILES.get(scratch_profile)
    if not (pp and wp and sp):
        raise ValueError(f"unknown profile: p={pressure_profile!r} w={wetness_profile!r} s={scratch_profile!r}")

    n = len(waypoints)
    if n < 2:
        raise ValueError("need at least 2 waypoints")

    # Precompute cumulative arc-length for t-param sampling
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

    # Build full header preamble (the part that makes it PAINT)
    roll, pitch = _resolve_tilt(tilt)
    cres = _resolve_color(color)
    lines = _star_header(size=size, wetness=wetness,
                         node_colors=node_colors, a_axes=a_axes)
    if cres is not None:
        kind, *cargs = cres
        if kind == "gradient":
            for line in _color_l_gradient(*cargs):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT
        elif kind == "ramp":
            for line in _color_l_ramp(cargs[0]):
                lines.append(line)
            if roll == 0.0 and pitch == 0.0:
                roll, pitch = _AUTOTILT
        else:
            for line in _color_l_all_nodes(cargs[0]):
                lines.append(line)

    # Leading lift bookend (brush down) at first waypoint
    x0, y0, _ = waypoints[0]
    lines.append(f"s {x0:.5f} {y0:.5f} 0.06250 {pitch:.5f} {roll:.5f} 0 0.00000")

    seg = max(1, segments)

    # Emit path frames
    for k in range(seg + 1):
        t = k / seg
        x, y = xy_at(t)
        p = _interp(pp, t)
        z = 0.0625 - 0.25 * p
        wlvl = _interp(wp, t)
        sval = _interp(sp, t)

        lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {pitch:.5f} {roll:.5f} 0 {p:.5f}")
        if k > 0:
            lines.append(f"w {wlvl / 12:.5f}")
            lines.append(f"i {sval:.5f}")

    if not closed:
        x1, y1, _ = waypoints[-1]
        lines.append(f"s {x1:.5f} {y1:.5f} 0.06250 0 0 0 0.00000")
    return "\n".join(lines) + "\n"


def build_star(cx: float = 0.0, cy: float = 0.0, outer: float = 3.2,
               inner: float = 1.3, points: int = 5, rainbow: bool = True,
               size: float = 6.0, clear_first: bool = True,
               a_axes: tuple = None,
               pprofile: str = None, wprofile: str = None,
               sprofile: str = None) -> str:
    """
    Build a 5-pointed (or N-pointed) star outline that RENDERS in Expresii.

    The format mirrors a real recorded star .XST (references/star.XST), which is
    the only wire format confirmed to paint:
      * a T/w/C/B/e/k/l*9/a*4 header preamble,
      * a single continuous open stroke through all 2*points vertices,
      * a `b` pen-down marker, then dense `s` frames (lifted at z~+0.45,
        pressed to z~-0.35) with the brush ROLL+PITCH rotated smoothly along
        the path so the FIXED rainbow tuft gradient sweeps around the perimeter
        (the rainbow comes from the brush orientation rotating, not from
        per-edge color changes),
      * a `b` pen-up marker.

    rainbow=True (default): use the recorded rainbow node gradient.
    rainbow=False: use a single-hue gradient (tip=root color) — a one-color star.
    clear_first=True (default): prepend a `c` clear so the rendered image reflects
    ONLY these commands (otherwise the canvas accumulates prior drawings and the
    result is unverifiable / can mask a bad stroke).

    Returns a ready-to-send XST string.
    """
    # 2*points vertices alternating outer/inner, starting at the top (-y).
    verts = []
    for i in range(points * 2):
        ang = -math.pi / 2 + i * math.pi / points
        r = outer if i % 2 == 0 else inner
        verts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    # The `total` loop below already returns to verts[0] at k==total, so we
    # trace only the vertices (no duplicate closing point — that would retrace
    # the first edge and leave a stray inner star).
    path = verts

    # Brush node gradient.
    if rainbow:
        node_colors = [
            (230, 25, 25), (230, 179, 25), (128, 230, 25), (25, 230, 77),
            (25, 229, 230), (25, 76, 230), (127, 25, 230), (230, 25, 178),
            (230, 25, 25),
        ]
    else:
        node_colors = None  # caller can override via _star_header default

    lines = []
    if clear_first:
        lines.append("c")  # clear canvas so the result reflects ONLY these commands
    lines += _star_header(size=size, node_colors=node_colors, a_axes=a_axes)
    lines.append("i   0.50000")

    # --- New (updated) Expresii app contract, extracted from a star recorded
    # on the updated app ("blue star.XST", which renders a clean pentagram):
    #   * z LIFT  = +0.088 (brush off paper)
    #   * z PRESS = -0.375 (brush pressed into paper)
    #   * pressure ramps 0 -> 0.75 along each edge, then back to 0 at the end
    #   * exactly ONE `b` pen-down marker, emitted right after the first
    #     (lifted) frame; the stroke is ONE continuous path; only the very
    #     first and very last frames lift (p=0).
    #   * pitch/roll stay roughly constant (-27.5 / -24.25) — the color comes
    #     from the fixed tuft node gradient, not from rotating the brush.
    Z_LIFT = 0.08750
    Z_PRESS = -0.37500
    P_MAX = 0.75
    ROLL0, PITCH0 = -24.25, -27.50
    seg_per_edge = 28

    # First (lifted) frame at the start vertex.
    ax0, ay0 = verts[0]
    lines.append(f"s {ax0:.5f} {ay0:.5f} {Z_LIFT:.5f} {PITCH0:.5f} {ROLL0:.5f} 0 0.00000")
    # No `b` marker: brush-down is detected from the lifted frame (p=0) immediately
    # followed by the first edge's press frame (p>0) — consecutive s, no command
    # between. A `b` here would obstruct the mark (Expresii ignores b for contact).

    n_edges = len(verts)
    for ei in range(n_edges):
        ax, ay = verts[ei]
        bx, by = verts[(ei + 1) % n_edges]
        # pressure ramps up 0->P_MAX over the edge, then eases back toward 0
        for j in range(1, seg_per_edge + 1):
            f = j / seg_per_edge
            x = ax + (bx - ax) * f
            y = ay + (by - ay) * f
            # smooth ramp up then slight ease so the final frame is near 0
            p = P_MAX * (1.0 - (1.0 - f) ** 2) if f < 0.9 else P_MAX * (1.0 - (f - 0.9) / 0.1 * 0.25)
            z = Z_LIFT + (Z_PRESS - Z_LIFT) * p
            lines.append(f"s {x:.5f} {y:.5f} {z:.5f} {PITCH0:.5f} {ROLL0:.5f} 0 {p:.5f}")
    # Trailing lift at the final vertex (brush up off paper).
    lines.append(f"s {verts[-1][0]:.5f} {verts[-1][1]:.5f} {Z_LIFT:.5f} {PITCH0:.5f} {ROLL0:.5f} 0 0.00000")
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
    # No `b` marker (pen down): brush-down is the lifted posture frame (p=0)
    # immediately followed by the first press frame (p>0) below — consecutive s,
    # no command between. A `b` here would obstruct the mark.
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
    # pen-up posture (lifted) — last press frame (p>0) above immediately precedes
    # this (p=0), a consecutive s >0->0 pair = Expresii pen-up. No `b`.
    lines.append(f"s {x:.5f} {y:.5f} 0.06250 {pitch:.5f} {roll:.5f} 0 0.00000")
    return "\n".join(lines) + "\n"


# --- Dry-brush strokes (validated against recorded samples) -----------------
# Three recipes, all confirmed to paint in Expresii:
#   * "ends"        scratchy feathered tips + solid middle (horse sketch)
#   * "progression" low-wetness dry → wetter; bottom stroke skips (5-stroke)
#   * "speed"       constant wetness but fast mid-bursts create internal grain
# All emit NO `b` (brush-down is two consecutive `s` frames p=0→>0).
Z_LIFT_DRY = 0.08750


def _dry_header(size, color, scratch):
    """Shared preamble: brush select, scratch, 9 color nodes, no `c`."""
    R, G, B = color
    L = [f"B {size:.5f}", f"i {scratch:.5f}"]
    for n in range(9):
        L.append(f"l {n} {R} {G} {B}")
    return L


def _dry_ramp_ends(fx):
    """Wetness along the stroke: driest at both ends, wetter in the middle."""
    if fx < 0.15:
        return 0.010
    if fx < 0.30:
        return 0.030
    if fx < 0.55:
        return 0.055
    if fx < 0.70:
        return 0.040
    if fx < 0.85:
        return 0.025
    return 0.010


def _dry_line(scheme, y, x0, x1, idx, n, color, seed_tilt=(-5.0, -3.0), size=None):
    """One horizontal dry stroke for the given scheme. Returns XST lines.

    size: brush size (XST `B`). If None, the scheme's default is used.
          Pass e.g. 4-6 for a thicker, more visible wiggling dry stroke.
    """
    L = []
    if scheme == "ends":
        B, scratch, tilt = (size if size is not None else 1.0), 1.0, (-50.0, -15.0)
        zcoup = 0.165
        peak = 0.75
        seg = 90
        L += _dry_header(B, color, scratch)
        # brush DOWN: two lift dwells, then solid first press (no w/i between)
        L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
        L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
        L.append(f"s {x0:.5f} {y:.5f} 0.02944 {tilt[0]:.5f} {tilt[1]:.5f} 0 0.26448")
        f, last_w = 0.0, None
        while f < 1.0:
            f2 = min(1.0, f + 1.0 / seg)
            fx = (f + f2) / 2.0
            x = x0 + (x1 - x0) * fx
            p = peak * math.sin(math.pi * fx)
            if fx > 0.86:                      # end dip -> feathered tip
                p *= max(0.0, (1.0 - fx) / 0.14)
            z = Z_LIFT_DRY - zcoup * p
            w = _dry_ramp_ends(fx)
            if w != last_w:
                L.append(f"w {w:.5f}"); last_w = w
            L.append(f"s {x:.5f} {y:.5f} {z:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 {p:.5f}")
            f = f2
        L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
        L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
    else:
        B = size if size is not None else 6.0
        zcoup = 0.617
        peak = 0.75
        seg = 90
        if scheme == "progression":
            # low wetness at top (idx 0) → very low at bottom (skip)
            w = 0.060 if n <= 1 else 0.060 - 0.050 * (idx / (n - 1))
            scratch = 0.0
            speed_mult = lambda f: 1.0
        elif scheme == "speed":
            # constant mid wetness, fast bursts mid-stroke = internal grain
            w = 0.20
            scratch = 0.0
            speed_mult = lambda f: 3.5 if (0.40 <= f <= 0.48 or 0.62 <= f <= 0.70) else 1.0
        elif scheme == "mid":
            # dry-brush-stroke-mid: scratchy through the WHOLE stroke, not just
            # the ends. The lever is sweeping the tuft side-to-side (brush Yaw,
            # and a little Roll) WHILE FULLY PRESSED -- broken tracks without
            # lifting the tuft. Dry bristles (low wetness) + fast mid-bursts add
            # extra grain. FIX #1: pressure floor (min 0.45 at the ends) so the
            # stroke reaches its full length instead of truncating to ~0.3 like
            # the old sin(pi*fx) envelope did. The mid stays porous/grainy.
            B = size if size is not None else 1.0
            zcoup = 0.165
            peak = 0.70
            floor = 0.45
            seg = 240
            yaw_amp = 0.70
            yaw_cycles = 14.0
            roll_amp = 14.0
            speed_mult = lambda f: 3.0 if (0.28 <= f <= 0.52 or 0.56 <= f <= 0.74) else 1.0
            w = 0.004
            scratch = 1.0
            tilt = (-50.0, -15.0)
            L += _dry_header(B, color, scratch)
            L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            L.append(f"s {x0:.5f} {y:.5f} 0.02944 {tilt[0]:.5f} {tilt[1]:.5f} 0 0.26448")
            f, last_w = 0.0, None
            while f < 1.0:
                step = (1.0 / seg) / speed_mult(f)
                f2 = min(1.0, f + step)
                fx = (f + f2) / 2.0
                x = x0 + (x1 - x0) * fx
                # envelope 0..1, but floored so ends keep depositing -> full length
                env = floor + (1.0 - floor) * math.sin(math.pi * fx)
                p = peak * env
                z = Z_LIFT_DRY - zcoup * p
                yaw = yaw_amp * math.sin(yaw_cycles * math.pi * fx)
                roll = tilt[1] + roll_amp * math.sin(yaw_cycles * math.pi * fx + 1.5)
                if w != last_w:
                    L.append(f"w {w:.5f}"); last_w = w
                # s <x> <y> <z> <pitch> <roll> <yaw/heading> <pressure>
                L.append(f"s {x:.5f} {y:.5f} {z:.5f} {tilt[0]:.5f} {roll:.5f} {yaw:.5f} {p:.5f}")
                f = f2
            L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            return L
        elif scheme == "mid_short":
            # dry-brush-stroke-mid-short: the OLD intentionally-short scratchy
            # look -- pressure envelope sin(pi*fx) drives pressure to ~0 at both
            # ends, so the tuft barely deposits until mid-stroke and the visible
            # stroke truncates to ~0.3 of its nominal length. Useful layered
            # UNDER a full-length 'mid' (same trajectory): overlapping long +
            # short dry strokes give a dense-core / broken-ends combined look.
            B = size if size is not None else 1.0
            zcoup = 0.165
            peak = 0.70
            seg = 240
            yaw_amp = 0.70
            yaw_cycles = 14.0
            roll_amp = 14.0
            speed_mult = lambda f: 3.0 if (0.28 <= f <= 0.52 or 0.56 <= f <= 0.74) else 1.0
            w = 0.004
            scratch = 1.0
            tilt = (-50.0, -15.0)
            L += _dry_header(B, color, scratch)
            L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            L.append(f"s {x0:.5f} {y:.5f} 0.02944 {tilt[0]:.5f} {tilt[1]:.5f} 0 0.26448")
            f, last_w = 0.0, None
            while f < 1.0:
                step = (1.0 / seg) / speed_mult(f)
                f2 = min(1.0, f + step)
                fx = (f + f2) / 2.0
                x = x0 + (x1 - x0) * fx
                p = peak * math.sin(math.pi * fx)          # full pressure, no mid dip
                if fx > 0.88:                              # feathered end only
                    p *= max(0.0, (1.0 - fx) / 0.12)
                z = Z_LIFT_DRY - zcoup * p
                yaw = yaw_amp * math.sin(yaw_cycles * math.pi * fx)
                roll = tilt[1] + roll_amp * math.sin(yaw_cycles * math.pi * fx + 1.5)
                if w != last_w:
                    L.append(f"w {w:.5f}"); last_w = w
                # s <x> <y> <z> <pitch> <roll> <yaw/heading> <pressure>
                L.append(f"s {x:.5f} {y:.5f} {z:.5f} {tilt[0]:.5f} {roll:.5f} {yaw:.5f} {p:.5f}")
                f = f2
            L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
            return L
        else:
            raise ValueError(f"unknown dry scheme: {scheme!r}")
        tilt = seed_tilt
        L += _dry_header(B, color, scratch)
        L.append(f"s {x0:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
        # NOTE: do NOT emit `w` here. Expresii registers brush-down only from a
        # consecutive s pair (p=0 -> p>0) with NO other command between. Emit the
        # first press s FIRST, then the wetness. (The mid/mid_short branches do
        # the same; this branch previously broke detection by putting w here.)
        f = 0.0
        first = True
        while f < 1.0 - 1e-9:
            step = (1.0 / seg) / speed_mult(f)
            f2 = min(1.0, f + step)
            fx = (f + f2) / 2.0
            x = x0 + (x1 - x0) * fx
            p = peak * math.sin(math.pi * fx)
            z = Z_LIFT_DRY - zcoup * p
            L.append(f"s {x:.5f} {y:.5f} {z:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 {p:.5f}")
            if first:
                L.append(f"w {w:.5f}")   # wetness only AFTER first press s
                first = False
            f = f2
        L.append(f"s {x1:.5f} {y:.5f} {Z_LIFT_DRY:.5f} {tilt[0]:.5f} {tilt[1]:.5f} 0 0.00000")
    return L


def select_layer(x: int) -> str:
    """Return the layer-select command text.

    Expresii `L <x>` selects the active layer. `x = 0` is the TOPMOST layer,
    `1` is the layer directly below it, `2` the one below that, and so on
    (top-down index). `x < 0` or `x > (layer_count - 1)` is IGNORED by the
    app (no-op). We emit it literally — the client can't know the layer
    count, so no clamping here.

    Use it to paint overlapping strokes on separate layers (the same
    trajectory drawn once long / once short gives a dense-core, broken-ends
    combined look). Pair with clear_first=False / --no-clear on the 2nd layer
    so you don't wipe the first.
    """
    return f"L {int(x)}\n"


def build_dry_strokes(n: int = 3, x0: float = -3.0, x1: float = 3.0,
                      ytop: float = 2.5, ystep: float = -1.0,
                      scheme: str = "ends", color=(30, 90, 200),
                      clear_first: bool = True, layer: int = None,
                      size: float = None) -> str:
    """Build N horizontal dry-brush strokes (the three validated recipes).

    scheme:
      "ends"        hard tilt + max scratch + wetness ramp (dry ends, wet middle)
                    + end pressure-dip -> feathered scratchy tips, solid middle.
      "mid"         like 'ends' but the MIDDLE is broken too: dry bristles + a
                    tuft yaw/roll sweep at full pressure + fast mid-bursts =
                    grain through the whole stroke. Pressure FLOORED at the ends
                    so the stroke reaches its FULL length (fix #1).
      "mid_short"   the OLD intentionally-short look: feathered ends truncate it
                    to ~0.3 of its length. Layer UNDER 'mid' on the same
                    trajectory (separate layers) for a dense-core/broken-ends
                    combined look.
      "progression" gentle tilt, wetness 0.06→~0.01 top→bottom; bottom skips.
      "speed"       gentle tilt, mid-bursts of fast motion -> internal grain.

    clear_first prepends a `c` so the result reflects ONLY these strokes.
    layer (int|None): if not None, prepend an `L <layer>` select so the strokes
    land on a specific layer. See select_layer().
    NOTE: to actually clear the canvas reliably the caller should send the
    clear as its OWN request and wait (~7s) before sending the strokes
    (see clear_canvas()).
    """
    parts = [f"L {int(layer)}" if layer is not None else None]
    parts = [p for p in parts if p]
    if clear_first:
        parts.append("c")
    for i in range(n):
        y = ytop + i * ystep
        parts.append("\n".join(_dry_line(scheme, y, x0, x1, i, n, color, size=size)))
    return "\n".join(parts) + "\n"


def clear_canvas() -> str:
    """Return the clear-paper command text.

    Sending `c` mid-burst races the prior render, so always POST this as its
    OWN request (serialized via _SEND_LOCK) and sleep ~7s before drawing —
    that is what makes the next stroke land on a clean paper in Expresii.
    """
    return "c\n"


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
        if block == "c":
            block = ""
        parts.append(block)
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Brush Stroke Style Library
# A named catalog of looks. Each entry is a recipe that, given a path (list of
# (x, y, pressure) waypoints), returns an XST block painting that path in the
# style. Wet styles are pure profile recipes over build_profile_stroke() and
# work on ANY geometry. Dry styles reuse the horizontal dry-brush recipes
# (build_dry_strokes) — they ignore the path and draw a horizontal sample,
# since the dry tuft-sweep is geometry-coupled; for those, pass the geometry
# via n/x0/x1/ytop/ystep instead.
# ---------------------------------------------------------------------------
def _wet_waypoints(x0=-3.2, x1=3.2, y=0.0):
    """Default straight horizontal sample path for wet styles."""
    return [(x0, y, 0.0), (x1, y, 0.0)]


BRUSH_STYLES = {
    # ---- WET family (flowing, watercolor-like) ----
    "wet_wash": dict(kind="wet", size=6.0, pprofile="Constant",
                     wprofile="Level 12 — Wettest", sprofile="None",
                     tilt=0.0, desc="Fully wet, no texture: a flowing watercolor wash."),
    "wet_smooth": dict(kind="wet", size=5.0, pprofile="Smooth Bell",
                       wprofile="Level 5 — Medium", sprofile="None",
                       tilt=0.0, desc="Wet, smooth, medium: a clean solid stroke."),
    "wet_satin": dict(kind="wet", size=5.0, pprofile="Smooth Bell",
                      wprofile="Level 5 — Medium", sprofile="Light",
                      tilt=0.0, wobble=(0.07, 10.0),
                      desc="Medium wetness, slight texture, gentle side-to-side wiggle."),
    "wet_wiggle": dict(kind="wet", size=5.0, pprofile="Smooth Bell",
                       wprofile="Level 5 — Medium", sprofile="None",
                       tilt=0.0, wobble=(0.12, 14.0),
                       desc="Wet stroke with a full lateral wiggle (path snakes side-to-side)."),
    # ---- COMPOUND: scratch + wobble + thick/thin ----
    # dry_wiggle: scratchy brush (max i) on a modestly-tilted tuft, low wetness,
    # with the path wiggling side-to-side and the pressure oscillating three
    # times (Triple Bell) so the contact alternates THICK -> thin -> THICK ->
    # thin -> THICK -> thin across one stroke. Reads as a scratchy wiggling
    # ribbon with distinct thick and thin sections.
    "dry_wiggle": dict(kind="wet", size=3.0, pprofile="Triple Bell",
                       wprofile="Level 1 — Driest", sprofile="Maximum",
                       ramp="BlueToDeep",
                       tilt=(-50.0, 50.0), wobble=(0.10, 10.0),
                       desc="Scratchy wiggling stroke: tip->north (Pitch +50), splay eastward (Roll -50), size 3 to offset footprint. Triple Bell pressure with deeper troughs (0.02) for very sparse deposit, max i, driest wetness, BlueToDeep tuft gradient."),
    "ink": dict(kind="wet", size=4.0, pprofile="Fade Out",
                wprofile="Level 5 — Medium", sprofile="None",
                tilt=0.0, desc="Calligraphic: full at the head, drying to a tail (fade-out pressure)."),
    "ink_drybrush": dict(kind="wet", size=4.0, pprofile="Constant",
                         wprofile="Wet to Dry", sprofile="Build Up",
                         tilt=0.0, desc="Ink that runs dry across the stroke: wet head, scratchy tail."),
    # ---- DRY SCRATCH VARIANTS (explore: scratchiness, dotting, wobble, tilt, path noise) ----
    # Dry scratch: hard tilt splay + Triple Bell + max i + scratch path noise
    # Hard tilt (pitch ±40) fans brush across paper, triple-peak pressure makes
    # thick → thin → thick marks that read as dry scratch. scratch noise makes
    # every segment deposit erratically = more broken / spiky / dry-bristle look.
    "dry_scratch": dict(kind="dry", scheme="ends", size=3.5,
                         desc="Hard-tilt dry with max scratch + Triple Bell: rough broken bristle strokes."),
    # dry_wobble: adds lateral path wobble (0.15, 14) to the dry brush path,
    # so the stroke snakes sideways mid-stroke. The tilt remains moderate;
    # all the lateral variation comes from the path offset.
    "dry_wobble": dict(kind="dry", scheme="ends", size=3.0,
                        tilt=(-25,25), wobble=(0.15, 14.0), wobble_phase=0.0,
                        desc="Dry ends plus lateral path wobble: stroke snakes side-to-side."),
    # dry_wobble_wide: like dry_wobble but with wider amplitude + out-of-phase
    # so the snake pattern is more pronounced and visually distinct.
    "dry_wobble_wide": dict(kind="dry", scheme="ends", size=3.0,
                            tilt=(-20,20), wobble=(0.25, 10.0), wobble_phase=1.57,
                            desc="Wide lateral path wobble, 90° phase offset: pronounced snake pattern."),
    # dry_wobble_tight: high-frequency, low-amplitude wiggle — tight zig-zag
    "dry_wobble_tight": dict(kind="dry", scheme="ends", size=3.0,
                             tilt=(-30,30), wobble=(0.08, 24.0), wobble_phase=0.0,
                             desc="Tight high-frequency zig-zag: rapid side-to-side micro-wiggle."),
    # dry_staccato: tiny stroke length + very dry + max scratch + fast speed ->
    # staccato dot-dash pattern where the brush barely touches paper and lifts
    # quickly, leaving a broken chain of marks.
    "dry_staccato": dict(kind="dry", scheme="mid_short", size=2.0,
                          wprofile="Level 1 — Driest", sprofile="Maximum",
                          tilt=(-30,-10), desc="Tiny broken dot-dash: mid_short scheme, driest, max scratch."),
    # dry_tilt_stroke: hard pitch splay only (no yaw/roll) so the path stays
    # straight but brush footprint fans out → every point deposits differently,
    # giving a streaky scratch look. Tilt angle (-60 pitch only) + very dry.
    "dry_tilt_stroke": dict(kind="dry", scheme="ends", size=3.0,
                             tilt=(-60,0), sprofile="Maximum",
                             wprofile="Level 1 — Driest",
                             desc="Pitch-only heavy tilt splay: broken stippled line, dry footprint splay."),
    # dry_dot_chain: uses Flick pressure (snap-on/off) + very dry + max i
    # produces a dotted chain rather than continuous stroke.
    "dry_dot_chain": dict(kind="dry", scheme="ends", size=2.5,
                           pprofile="Flick", wprofile="Level 1 — Driest",
                           sprofile="Maximum", tilt=(-20,-10),
                           desc="Flick pressure + driest + max scratch: dotted chain of quick contact dots."),
    # ---- DRY family (dry-bristle, broken/grainy) ----
    # Dry styles reuse build_dry_strokes(); geometry via x0/x1/ytop/ystep.
    "dry_ends": dict(kind="dry", scheme="ends", size=4.0,
                     desc="Scratchy feathered tips, solid middle (dry bristles + hard tilt)."),
    "dry_mid": dict(kind="dry", scheme="mid", size=4.0,
                    desc="Broken grain through the WHOLE stroke; reaches full length (fix #1)."),
    "dry_mid_short": dict(kind="dry", scheme="mid_short", size=4.0,
                          desc="Intentionally SHORT scratchy look (~0.3 length). Layer under 'dry_mid'."),
    "dry_speed": dict(kind="dry", scheme="speed",
                     desc="Gentle tilt + fast mid-bursts -> internal grain."),
    "dry_progression": dict(kind="dry", scheme="progression",
                           desc="Wetness ramps 0.06->~0.01 top->bottom; bottom skips."),
    # ---- COMPOUND / expressive ----
    "ink_flick": dict(kind="wet", size=4.0, pprofile="Flick",
                      wprofile="Level 5 — Medium", sprofile="None",
                      tilt=(-20,20), ending="flick",
                      desc="Ink line that accelerates and snaps off at the end."),
    "dry_scatter": dict(kind="wet", size=3.5, pprofile="Triple Bell",
                        wprofile="Level 1 — Driest", sprofile="Maximum",
                        tilt=(-45,45), noise="skitter",
                        desc="Dry, broken, erratic path with skipped spots along a dry tuft."),
    "dry_hair": dict(kind="wet", size=3.0, pprofile="Bell",
                     wprofile="Level 1 — Driest", sprofile="Maximum",
                     tilt=(-40,40), noise="scratch", ending="taper",
                     desc="Thin, tapered, scratchy line — hair, grass, fibers."),
    "wet_sketch": dict(kind="wet", size=4.5, pprofile="Sketchy",
                       wprofile="Level 5 — Medium", sprofile="Light",
                       tilt=0.0, noise="shiver", corner="round",
                       desc="Loose wet sketch with rounded corners and hand tremor."),
}


def build_style_stroke(style: str, waypoints: list = None,
                       x0: float = -3.2, x1: float = 3.2, ytop: float = 2.5,
                       ystep: float = -1.0, n: int = 3, color=(30, 90, 200),
                       layer: int = None, size: float = None) -> str:
    """Build XST for a named brush style.

    wet styles: paint `waypoints` (falls back to a straight sample path).
    dry styles: ignore waypoints; draw n horizontal dry strokes from
                build_dry_strokes(x0,x1,ytop,ystep,n, scheme=...).
    layer: if not None, prepend `L <layer>`.
    """
    if style not in BRUSH_STYLES:
        raise ValueError(f"unknown brush style: {style!r}; known: {sorted(BRUSH_STYLES)}")
    spec = BRUSH_STYLES[style]
    effective_size = spec.get("size", 3.5)
    if spec["kind"] == "wet":
        wp = waypoints if waypoints else _wet_waypoints(x0, x1, ytop)
        block = build_profile_stroke(
            wp, size=effective_size, pprofile=spec["pprofile"],
            wprofile=spec["wprofile"], sprofile=spec["sprofile"],
            segments=max(16, len(wp) * 8), tilt=spec.get("tilt", 0.0),
            color=spec.get("ramp") or color, wobble=spec.get("wobble", (0, 0)),
            wobble_phase=spec.get("wobble_phase", 0.0),
            noise=spec.get("noise", "none"), ending=spec.get("ending", "none"),
            corner=spec.get("corner", "none"))
    else:  # dry
        block = build_dry_strokes(n=n, x0=x0, x1=x1, ytop=ytop, ystep=ystep,
                                  scheme=spec["scheme"], color=color,
                                  clear_first=False, size=effective_size)
    if layer is not None:
        block = f"L {int(layer)}\n" + block
    return block


def list_styles() -> str:
    """Human-readable catalog of all brush styles."""
    lines = ["Brush stroke styles:", ""]
    for name, spec in BRUSH_STYLES.items():
        lines.append(f"  {name:16s} [{spec['kind']}]  {spec['desc']}")
    return "\n".join(lines) + "\n"


def cancel_render(host: str, port: int, request_id: int,
                  timeout: float = 10.0) -> dict:
    """Ask the server to cancel a queued render job.

    Mirrors the official Command Console client's cancel button: sends
    `DELETE /cancel/<requestId>` and treats the response as a cancellation
    only when it reports status == 'cancelled'. The server refuses to cancel
    a job that has already left the queue (status 'rendering'/'done'), so a
    non-cancelled reply means "too late — it's already rendering".

    Returns:
        {'ok': True,  'status': 'cancelled', 'response': ...}  — removed from queue
        {'ok': False, 'status': <other>, 'error': ...}         — not cancelable / failed
    """
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("DELETE", f"/cancel/{request_id}")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        status = data.get("status", resp.status)
        if status == "cancelled":
            return {"ok": True, "status": "cancelled", "response": body[:500]}
        return {"ok": False, "status": status, "response": body[:500],
                "error": f"server refused cancel (status {status!r}): already rendering or unknown id"}
    except (ConnectionRefusedError, socket.timeout, OSError, ValueError) as e:
        return {"ok": False, "status": "no_response", "error": str(e)}


def fetch_render(host: str, port: int, request_id: int, out_path: str,
                 tries: int = 60, interval: float = 0.9,
                 initial_wait: float = 0.0,
                 cancel_cb=None) -> dict:
    """
    Poll GET /result/<requestId> and save the rendered paper (base64 PNG).

    Mirrors the official Command Console client: poll every ~0.9s and accept
    the frame only once the server reports status == 'done' (it carries the
    final imageBase64). This is what makes the client "always return the
    correct image" — it waits for the server's done signal instead of grabbing
    whatever frame is served first (which can be a previous/stale render).

    SERVER QUEUE (post-v2026.07.26): the server now has its own
    inbound queue, so a just-POSTed command may report status == 'queued'
    (waiting in line behind an earlier render) or 'rendering' (playback in
    progress) before it reaches 'done'. BOTH 'queued' and 'rendering'
    are treated as in-flight and polling continues — there is no longer a
    stuck 'rendering' wedge (the old failure mode on pre-queue servers),
    so a result is eventually returned for every sent command.

    cancel_cb: optional zero-arg callable checked on every 'queued'/'rendering'
    poll. When it returns truthy, the job is cancelled via DELETE /cancel/<id>
    (see cancel_render) and polling stops with
    {'ok': False, 'cancelled': True, 'error': 'cancelled by caller'}.
    Use it to abort a long wait (e.g. user pressed Ctrl-C / a timeout hit)
    while the job is still in the server queue.

    Returns {'ok': True, 'bytes': N, 'path': out_path} on success, else
    {'ok': False, 'error': ...} (plus 'cancelled': True when cancelled).
    """
    import base64
    import time
    if initial_wait > 0:
        time.sleep(initial_wait)
    last_status = None
    for _ in range(tries):
        if cancel_cb is not None:
            try:
                if cancel_cb():
                    cr = cancel_render(host, port, request_id)
                    return {"ok": False, "cancelled": True,
                            "error": f"cancelled by caller ({cr.get('status', '?')})"}
            except Exception:
                pass  # a failing cancel probe must not wedge the poll loop
        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", f"/result/{request_id}")
            payload = json.loads(conn.getresponse().read().decode("utf-8", errors="replace"))
            conn.close()
            last_status = payload.get("status")
            # In-flight: server-side queue or active rendering. Keep polling.
            if last_status in ("queued", "rendering"):
                time.sleep(interval)
                continue
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
    ap.add_argument("--pace", type=float, default=0.0,
                    help="Seconds to wait AFTER a request's 'done' frame before returning/sending "
                         "the next command. The Expresii server snapshots the paper on a ~5s "
                         "post-playback timer that RESETS on every new command, and its shared "
                         "stroke recorder + first-not-ready result slot mean two commands must "
                         "never be in flight at once. The lock + wait-for-'done' already serialize "
                         "sends; set --pace 6 (or more) only when you deliberately pipeline several "
                         "XSTs in one run, to fully clear that window between them. Default: 0.")
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
    ap.add_argument("--dry", nargs="?", const="ends", metavar="SCHEME",
                    choices=["ends", "progression", "speed", "mid", "mid_short"],
                    help="Build N horizontal dry-brush strokes. SCHEME: 'ends' (hard "
                         "tilt + max scratch + wetness ramp -> scratchy feathered tips, "
                         "solid middle), 'mid' (like 'ends' but the MIDDLE is broken too: "
                         "dry middle + fast mid-bursts = grain through the whole stroke), "
                         "'speed' (gentle tilt, fast mid-bursts -> internal grain), "
                         "'mid_short' (like 'mid' but the OLD intentionally-short look: "
                         "feathered ends truncate it to ~0.3 of its length -- layer it "
                         "UNDER 'mid' on the same trajectory for a dense-core/broken-ends "
                         "combined look). "
                         "Default scheme: ends. Use with --n, --x0, --x1, --ytop, "
                         "--ystep, --color. Always clears the canvas first (its own "
                         "request + settle), then sends the strokes.")
    ap.add_argument("--n", type=int, default=3, help="Number of dry strokes (--dry). Default: 3")
    ap.add_argument("--x0", type=float, default=-3.0, help="Dry stroke start x (--dry). Default: -3.0")
    ap.add_argument("--x1", type=float, default=3.0, help="Dry stroke end x (--dry). Default: 3.0")
    ap.add_argument("--ytop", type=float, default=2.5, help="Dry stroke top y (--dry). Default: 2.5")
    ap.add_argument("--ystep", type=float, default=-1.0, help="Dry stroke y spacing (--dry). Default: -1.0")
    ap.add_argument("--layer", type=int, default=None,
                    help="Select layer x before the --dry strokes (Expresii `L <x>`; "
                         "0=topmost, 1=below it, ...). For overlapping strokes on "
                         "separate layers, draw layer 0 normally, then use "
                         "--layer N --no-clear for each further layer so you don't "
                         "wipe the earlier ones.")
    ap.add_argument("--no-clear", action="store_true",
                    help="With --dry: skip the leading clear-canvas request so the "
                         "strokes ADD to the current canvas (use on layers 1+).")
    ap.add_argument("--styles", action="store_true",
                    help="List all brush stroke styles in the library and exit.")
    ap.add_argument("--swatches", action="store_true",
                    help="Render ONE sample stroke per style (a swatch sheet) so you "
                         "can eyeball/compare the looks. Clears the canvas, then draws "
                         "each style on its own row. Use --verify to save the render.")
    args = ap.parse_args()

    # List the style library and exit.
    if args.styles:
        print(list_styles(), end="")
        sys.exit(0)

    # Swatch sheet: one sample stroke per style, each on its own row.
    # All styles are combined into ONE XST (clear + every row) and sent as a
    # SINGLE request, because Expresii's single shared renderer drops earlier
    # strokes when a new request interrupts playback (sequential sends race).
    # Rows are kept inside the narrow visible paper band (~y in [-0.5,+0.5])
    # and spread thin so none clip or merge.
    if args.swatches:
        color = args.color or "30,90,200"
        if isinstance(color, str) and "," in color and color.replace(",", "").isdigit():
            color = tuple(int(v) for v in color.split(","))
        names = list(BRUSH_STYLES.keys())
        blocks = []
        for i, name in enumerate(names):
            y = 0.5 - (1.0 * i / max(1, len(names) - 1))   # +0.5 .. -0.5
            blocks.append(build_style_stroke(
                name, x0=-2.6, x1=2.6, ytop=y, ystep=0.0, n=1, color=color))
        xst_text = "\n".join(blocks) + "\n"
        # Reliable clear as its OWN request + settle, then send all rows at once.
        clr = send_xst(args.host, args.port, clear_canvas(), args.timeout,
                       max_width=args.max_width, max_height=args.max_height)
        if not clr.get("ok"):
            print(f"FAIL  clear: {clr.get('error', '')}", file=sys.stderr)
            sys.exit(3)
        time.sleep(7.0)
        result = send_xst(args.host, args.port, xst_text, args.timeout,
                          max_width=args.max_width, max_height=args.max_height)
        if not result.get("ok"):
            print(f"FAIL  {result.get('status', '?')}  {result.get('error', '')}",
                  file=sys.stderr)
            sys.exit(3)
        if args.verify:
            out = args.verify if isinstance(args.verify, str) else "swatches.png"
            rr = fetch_render(args.host, args.port, result["request_id"], out,
                             tries=240, interval=1.0, initial_wait=4.0)
            if rr.get("ok"):
                print(f"RENDER saved: {rr['path']} ({rr['bytes']} bytes) "
                      f"({len(names)} styles)")
            else:
                print(f"RENDER failed: {rr.get('error', '')} — the canvas likely "
                      f"painted; screenshot the Expresii window to confirm.",
                      file=sys.stderr)
        else:
            print(f"OK  sent {len(names)} swatches to {args.host}:{args.port}")
        sys.exit(0)

    # Dry-brush strokes: clear canvas as its OWN request + settle, then draw.
    if args.dry is not None:
        color = args.color or "30,90,200"
        if isinstance(color, str) and "," in color and color.replace(",", "").isdigit():
            color = tuple(int(v) for v in color.split(","))
        xst_text = build_dry_strokes(
            n=args.n, x0=args.x0, x1=args.x1, ytop=args.ytop, ystep=args.ystep,
            scheme=args.dry, color=color, clear_first=False, layer=args.layer,
            size=args.size)
        # Reliable clear: `c` as its own serialized request, then settle ~7s.
        # (Skipped with --no-clear so extra layers ADD to the existing canvas.)
        if not args.no_clear:
            clr = send_xst(args.host, args.port, clear_canvas(), args.timeout,
                           max_width=args.max_width, max_height=args.max_height)
            if not clr.get("ok"):
                print(f"FAIL  clear: {clr.get('error', '')}", file=sys.stderr)
                sys.exit(3)
            time.sleep(7.0)
        result = send_xst(args.host, args.port, xst_text, args.timeout,
                          max_width=args.max_width, max_height=args.max_height)
        result["host"] = args.host
        result["port"] = args.port
        if args.json:
            print(json.dumps(result))
        elif result["ok"]:
            print(f"OK  sent {result['sent_chars']} chars (scheme={args.dry}) to "
                  f"{args.host}:{args.port} (HTTP {result['status']})")
        else:
            print(f"FAIL  {result.get('status', '?')}  {result.get('error', '')}", file=sys.stderr)
        if args.verify and result.get("request_id") is not None:
            out = args.verify if isinstance(args.verify, str) else "render.png"
            # Dry strokes play back slowly (many segments + a 7s clear); poll
            # long enough to clear the server's single-slot renderer.
            r = fetch_render(args.host, args.port, result["request_id"], out,
                            tries=200, interval=1.0, initial_wait=3.0)
            if r.get("ok"):
                print(f"RENDER saved: {r['path']} ({r['bytes']} bytes)")
            else:
                print(f"RENDER failed: {r.get('error', '')} — the canvas likely painted; "
                      f"screenshot the Expresii window to confirm.", file=sys.stderr)
        sys.exit(0 if result["ok"] else 3)

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
                if args.pace > 0:
                    time.sleep(args.pace)
                result = send_xst(args.host, args.port, xst_text, args.timeout,
                                 max_width=args.max_width, max_height=args.max_height)
                if not result.get("ok"):
                    print("RE-SEND fail, stopping verify", file=sys.stderr)
                    break
                print(f"  verify retry {attempt + 1}: re-sent, polling new requestId {result.get('request_id')}")
            else:
                print(f"RENDER failed after {attempt + 1} tries: {r.get('error', '')} "
                      f"Screenshot the Expresii window to confirm.", file=sys.stderr)

    # Optional post-done pacing so a following command starts after the
    # server's ~5s snapshot window has fully closed (never two in flight).
    if args.pace > 0 and result.get("ok"):
        time.sleep(args.pace)

    sys.exit(0 if result["ok"] else 3)


if __name__ == "__main__":
    main()
