# Expresii Stroke File Format Reference

Full reference for the XST (Expresii Stroke) text format. Sourced from
[ExpresiiStrokeFileFormatDescription.txt](https://github.com/expresii-paint/Amami/blob/main/ExpresiiStrokeFileFormatDescription.txt)
in the upstream Amami repo. The Expresii Paint app parses these files to
replay brush strokes.

## File structure

Plain text. One command per line. Space-separated parameters. Lines starting
with `#` are comments and are ignored. The file is read top-to-bottom; command
state persists (so setting `B 4` then drawing frames uses size 4 for the
whole stroke until you change it).

```
# Comment line — anything starting with # is ignored
c                     # clear canvas
B 4.00000             # set brush size
w 0.50000             # set brush wetness
i 0.50000             # set brush scratchiness
l 0 78 150 220 255    # color node 0 (tip): RGBA = (78, 150, 220, 255)
...
s -2.5 -2.8 0.0 -33 -28 0 0.0   # one stroke frame
s -2.4 -2.7 0.0 -33 -28 0 0.15  # next frame
```

## Commands

### `s` — Stroke frame (defines one brush posture)

```
s <x> <y> <z> <Pitch> <Roll> <Turn> <Pressure>
```

| Param | Meaning | Notes |
|-------|---------|-------|
| `x` `y` `z` | Position of the brush tuft base in 3D | Normalized canvas units, typically `[-3, 3]` for x/y, `[-0.5, 0.5]` for z |
| `Pitch` | Brush tilt around the axis pointing away from the viewer | ~0 = brush held near-vertical; larger = leaning back/forward |
| `Roll` | **Sideways splay** — rotation that lays the tuft flat | THIS is what spreads the tuft across the stroke. A gradient stroke uses Roll ≈ −44 (leaning toward 3 o'clock) so the node-0→node-8 color gradient shows across the WIDTH. Verified against a real recorded stroke. |
| `Turn` | Brush roll around its own axis | Usually 0 |
| `Pressure` | How hard the brush is pressed | Range `[0, 1]`, where 0 = no contact, 1 = max |

A *stroke* is a series of `s` lines with gradually changing x, y, and pressure
while tilt stays roughly constant. Each frame is a snapshot of the brush
posture at one moment in time. Expresii interpolates between frames.

**Brush-down registration (critical):** Expresii detects the brush touching
the paper ONLY from **two consecutive `s` frames** where pressure goes
`0 → >0`, with **NO other command between them** (`w`, `i`, `b`, `l`, … all
break it). So a stroke must open with a lift frame `… 0.00000` immediately
followed by a press frame `… <p> ` (p>0). Emit any `w`/`i` re-issues *after*
that first press frame, never between the lift and the first press. A trailing
lift (last frame of an open stroke) may be followed by nothing — that is
brush-up, not brush-down, and is fine.

### `c` — Clear canvas

No parameters. Wipes the canvas. Use this to start a fresh painting; do NOT
use it between strokes of the same composition.

### `L` — Select active layer

> Newer Expresii feature; not yet in the official stroke-file spec
> (`ExpresiiStrokeFileFormatDescription.txt` documents only `s c C B w l i`).
> Documented here from the app's actual behavior.

```text
# 'L' for selecting the active Layer by index. Format:
# L layer_index
# layer_index 0 is the TOPMOST layer; 1 is the layer directly below it; 2 the
# one below that, and so on (top-down). Indices < 0 or > (layer_count - 1) are
# IGNORED (no-op). Select a layer before a 'c'/'B'/'w'/'i'/'s' sequence to
# paint on it.
# Examples:
L 0
# ... c / B / w / i / s-frames for the long 'mid' stroke ...
L 1
# ... c / B / w / i / s-frames for the short 'mid_short' stroke ...
```

Selects the active layer by index. `x = 0` is the **topmost** layer, `1` is
the layer directly below it, `2` the one below that, and so on. Indices are
**top-down**: smaller x = higher in the stack.

- `x < 0` → ignored (no-op).
- `x > (layer count − 1)` → ignored (no-op).

Example — paint two overlapping dry-brush strokes on separate layers so each
keeps its own trajectory/look, then let the layers composite:

```text
L 0
# ... c / B / w / i / s-frames for the long 'mid' stroke ...
L 1
# ... c / B / w / i / s-frames for the short 'mid_short' stroke ...
```

### `B` — Brush size

```
B <size>
```

Range `[1.0, 7.0]`. Larger = thicker, broader strokes. Default is around 4.

```
B 4.00000
```

### `w` — Brush wetness

```
w <wetness>
```

Range `[0.01, 1.0]`. Higher = more water, more flowy/washy behavior. Lower =
dryer, sharper, more control. The spec's example walks from `1.0` (very wet)
down to `0.01` (almost dry) across "Wetness Level 12 to Wetness Level 1":

```
w 1.00000    # level 12 — very wet, watercolor wash
w 0.65000    # level 10
w 0.40000    # level 8
w 0.19000    # level 6
w 0.15000    # level 5
w 0.10000    # level 4
w 0.09000
w 0.08000
w 0.06000
w 0.04000
w 0.03000
w 0.01000    # level 1 — almost dry
```

### `l` — Color loading (per brush node)

```
l <NodeIndex> <R> <G> <B> <A>
```

The brush has 9 color nodes along its length, indexed `0` to `8`, going from
**tip (0) to root (8)**. R, G, B, A are integers in `[0, 255]`. Setting
different colors at different nodes creates a gradient: the brush will pick
up pigment at each node as the stroke is laid down, with the tip leaving
node-0 color first and node-8 color last.

```
l 0 78 150 220 255     # tip: a sky blue
l 1 143 118 188 255    # gradient toward purple
l 2 222 78 149 255
l 3 240 68 139 255
l 4 225 78 147 255     # middle: hot pink
l 5 208 96 158 255
l 6 212 142 181 255
l 7 245 223 226 255
l 8 255 255 255 255    # root: white
```

### `i` — Brush scratchiness

```
i <scratchiness>
```

Range `[0.0, 1.0]`. Higher = more dry-brush texture, the brush "skips" on
the canvas. `0` = smooth, no texture. Typical ink-wash: `0.0`–`0.2`.
Typical dry-brush: `0.6`–`1.0`.

```
i 0.5
```

## Coordinate system details

- **Origin (0, 0, 0):** canvas center
- **X axis:** right
- **Y axis:** up
- **Z axis:** out of the canvas toward the viewer (positive z = brush lifted, negative z = brush pressed in)
- **Tuft base** is the geometric anchor of the brush, *not* the tip — the tip extends from the tuft in the direction of the brush's normal
- **Pressure 0** = brush not touching canvas (z = +0.0625); **Pressure 1** = fully pressed (z = −0.0625)

### The z-pressure coupling (empirical)

For a flat brush posture, the brush's z height is *coupled to pressure* by this
formula, derived from sample strokes in the upstream spec:

```
z = 0.0625 − 0.125 × pressure
```

| pressure | z          | meaning                       |
|----------|------------|-------------------------------|
| 0.00     | +0.0625    | fully lifted, no contact      |
| 0.25     | +0.0313    | hovering, near paper          |
| 0.50     |  0.0000    | tip just touching             |
| 0.75     | −0.0313    | pressed in, normal stroke     |
| 1.00     | −0.0625    | max press, max deposit        |

If you set `z = 0` with non-zero pressure, you get a thin, almost-invisible
stroke — the brush is at the threshold of contact. To get a clearly visible
line, use `pressure ≥ 0.7` with `z ≤ −0.025`, or use the formula above and
let the helper compute z for you.

## Tilts and barrel rotation

The `s` frame's orientation fields are **Pitch, Roll, Turn** (not "Tilt-Y/Tilt-X/Barrel-Rotation" — that was a misreading). The brush TUFT is splayed in **2D** so the 9-node color gradient fans across the paper:

- `Pitch`: rotation around the axis pointing away from the viewer. ~0 = brush near-vertical; **Pitch > 0 → tuft points North (toward you); Pitch < 0 → South.**
- `Roll`: rotation around the vertical axis. **Roll > 0 → tuft points West; Roll < 0 → East.** This is the primary splay axis for a side-on gradient.
- `Turn`: roll of the brush around its own axis. Usually 0.

The **splay magnitude** (`|Roll| + |Pitch|`) controls how much of the root (node-8) color shows: a bigger splay lays the tuft flatter, exposing more of the bristle base.

Verified against two recorded samples:

| Sample | Direction | `s x y z Pitch Roll Turn` |
|--------|-----------|---------------------------|
| 4-direction dabs | East  | `… 1 -54 0`  → Roll=−54 |
| 4-direction dabs | North | `… 57 3 0`   → Pitch=+57 |
| 4-direction dabs | West  | `… 0 72 0`   → Roll=+72 |
| 4-direction dabs | South | `… -67 -2 0` → Pitch=−67 |
| tilt+rotate ring | lean 3 o'clock | `… 1 -44 0` → Roll=−44 |

So the canonical mapping is: **East = Roll(−), West = Roll(+), North = Pitch(+), South = Pitch(−).**

## Source

This reference is a structured rewrite of the upstream spec at
[ExpresiiStrokeFileFormatDescription.txt](https://github.com/expresii-paint/Amami/blob/main/ExpresiiStrokeFileFormatDescription.txt).
All numeric ranges and command parameters come from that file. If they
disagree, the upstream file is canonical.
