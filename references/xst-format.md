# Expresii Stroke File Format Reference

Full reference for the XST (Expresii Stroke) text format. Sourced from
[ExpresiiStrokeFileFormatDescription.txt](https://github.com/expresii-paint/Amami/blob/main/ExpresiiStrokeFileFormatDescription.txt)
in the upstream Amami repo. The Expresii Paint app parses these files to
replay brush strokes.

## File structure

Plain text. One command per line. Space-separated parameters. Lines starting
with `#` are comments and are ignored.

> **Version line (line 1):** The XST file must start with `# Expresii Stroke File v0.8`.
> The helper (`send_xst` / `_ensure_version()`) always prepends this automatically,
> so generated strokes are safe. When hand-writing XST, lead with this line.

Command state persists (setting `B 4` then drawing frames uses size 4 for the whole
stroke until you change it). The file is read top-to-bottom.

```
# Expresii Stroke File v0.8
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

### `'` — Title (REQUIRED; stroke-set name)

```text
' <Title>
```

A single-quote character, then a space, then any text (no quotes inside). There
must be **exactly one `'` line in every XST file**. Expresii shows it in the
stroke-recorder window so the user can identify what was sent. **Always emit a `'`
title line** — every command set should be named. Place it right after the version
line and before any config (`B`/`w`/`i`/`l`/...) or stroke (`s`) commands — near the
top of the file. Example:

```text
# Expresii Stroke File v0.8
' landscape-sky
c
B 4.00000
w 0.45000
i 0.00000
l 0 135 185 225 255
...
```

### `s` — Stroke frame (defines one brush posture)

```text
s <x> <y> <z> <Tilt-Y> <Tilt-X> <Barrel-Rotation> <Pressure>
```

| Param | Meaning | Notes |
|-------|---------|-------|
| `x` `y` `z` | Position of the brush tuft base in 3D | **The Y extent is always −5 to +5 units** (a fixed 10-unit Y span centered on the canvas, independent of paper size). **The X extent comes from the paper's aspect ratio: call `GET /state` (returns JSON including `paperWidth` and `paperHeight`), compute `aspect_ratio = paperWidth / paperHeight`, then derive `x_extent = 5 × aspect_ratio` (half-width in Expresii units).** Calibrate your strokes' X range to the X extent you compute, but treat Y as a fixed ±5 range. z ∈ roughly [−0.06, +0.06] |
| `Tilt-Y` | Brush tilt in degrees, around the Y axis | **Tilt-Y+ = tip points North (up).** |
| `Tilt-X` | Brush tilt in degrees, around the X axis | **Tilt-X− = tip points East (right).** |
| `Barrel-Rotation` | Brush roll around its own axis, degrees | Usually 0 |
| `Pressure` | How hard the brush is pressed | Range `[0, 1]`, where 0 = no contact, 1 = max. **Every stroke's peak pressure should be ≥ 0.40** to be safely above the contact threshold (~0.19). |

A *stroke* is a series of `s` lines with gradually changing x, y, and pressure
while tilt stays roughly constant. Each frame is a snapshot of the brush
posture at one moment in time. Expresii interpolates between frames.

**Brush-down registration (critical):** Expresii detects the brush touching
the paper ONLY from **two consecutive `s` frames** where pressure goes
`0 → >0`, with **NO other command between them** (`w`, `i`, `l`, … all
break it). So a stroke must open with a lift frame `… 0.00000` immediately
followed by a press frame `… <p>` (p>0). Emit any `w`/`i` re-issues *after*
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

Selects the active layer by index. `x = 0` is the **topmost** layer, `1` is
the layer directly below it, `2` the one below that, and so on. Indices are
**top-down**: smaller x = higher in the stack.

- `x < 0` → ignored (no-op).
- `x > (layer count − 1)` → ignored (no-op).

### `B` — Brush size

```text
B <size>
```

Range `[1.0, 7.0]`. Larger = thicker, broader strokes. Default is around 4.

```text
B 4.00000
```

### `w` — Brush wetness

```text
w <wetness>
```

Range `[0.01, 1.0]`. Higher = more water, more flowy/washy behavior. Lower =
dryer, sharper, more control. The spec's example walks from `1.0` (very wet)
down to `0.01` (almost dry) across "Wetness Level 12 to Wetness Level 1":

```text
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

```text
l <NodeIndex> <R> <G> <B> <A>
```

The brush has 9 color nodes along its length, indexed `0` to `8`, going from
**tip (0) to root (8)**. R, G, B, A are integers in `[0, 255]`. Setting
different colors at different nodes creates a gradient: the brush will pick
up pigment at each node as the stroke is laid down, with the tip leaving
node-0 color first and node-8 color last.

### `i` — Brush scratchiness

```text
i <scratchiness>
```

Range `[0.0, 1.0]`. Higher = more dry-brush texture, the brush "skips" on
the canvas. `0` = smooth, no texture. Typical ink-wash: `0.0`–`0.2`.
Typical dry-brush: `0.6`–`1.0`.

### `basecolor` — Paper background color (setup block)

```text
basecolor r g b
```

Sets the paper's base background color. `r`, `g`, `b` are RGB values in bytes
(`0`–`255` each). **Place it in the setup block, before `# End of Setup`**, so it
applies to the whole painting. Emit it once near the top of the file (after `c`
if you also clear), not between strokes. Without `basecolor` the paper defaults
to its normal white/transparent; use it when you want a colored ground (e.g. a
toned paper or a colored backdrop behind transparent strokes).

## Coordinate system

- **Origin (0, 0, 0):** canvas center
- **X axis:** right
- **Y axis:** up **(+Y up, Cartesian/SVG-aligned — since Expresii XST v0.8).**
  **Do NOT negate Y when authoring for v0.8+.** **The Y extent is always −5 to +5
  units** (a fixed 10-unit Y span centered on the canvas, independent of paper size).
  The **X extent follows from the paper's aspect ratio**: call `GET /state` (returns
  JSON including `paperWidth` and `paperHeight`), compute `aspect_ratio = paperWidth
  / paperHeight`, then `x_extent = 5 × aspect_ratio`. Calibrate strokes to the X
  extent you read from the API, but treat Y as a fixed ±5 range.
- **Z axis:** out of the canvas toward the viewer (positive z = brush lifted,
  negative z = brush pressed in)

### The z-pressure coupling (canonical)

For a flat brush posture, the brush's z height is *coupled to pressure* by this
formula (canonical for v0.8 — derived from the XST format spec and confirmed against
live renders):

```text
z = 0.0625 − 0.125 × pressure
```

| pressure | z          | meaning                       |
|----------|------------|-------------------------------|
| 0.00     | +0.0625    | fully lifted, no contact      |
| 0.50     |  0.0000    | tip just touching             |
| 1.00     | −0.0625    | max press, max deposit        |

- **pressure = 0.0** → **z = +0.0625** (brush lifted just above the paper)
- **pressure = 0.5** → **z = 0.0** (tip just touching the paper surface)
- **pressure = 1.0** → **z = −0.0625** (max press; deepest the tip goes)
- **Every stroke's peak pressure should be ≥ 0.40** — safely above the contact
  threshold (~0.19).
- An over-deep z (more negative than the formula gives at your pressure) makes
  the brush pass *through* the paper plane → no footprint → blank stroke even
  though the POST returns 200.

To lift the brush between strokes, set `pressure = 0` and `z = +0.0625`.

## Tilts

The `s` frame's orientation fields are **Tilt-Y and Tilt-X** (brush tilt in degrees,
not "Pitch/Roll/Turn"). The brush TUFT is splayed in 2D so the 9-node color gradient
fans across the paper:

- **Tilt-Y:** rotation around the Y axis. **Tilt-Y+ → tip points North (up).**
  Tilt-Y− → tip points South.
- **Tilt-X:** rotation around the X axis. **Tilt-X− → tip points East (right).**
  Tilt-X+ → tip points West.

From the spec's example stroke, a "vertical-ish" brush posture is
`Tilt-Y: -33, Tilt-X: -28` — meaning the brush is leaning back (South) and to the
right (East) of vertical.

## Source

This reference is a structured rewrite of the upstream spec at
[ExpresiiStrokeFileFormatDescription.txt](https://github.com/expresii-paint/Amami/blob/main/ExpresiiStrokeFileFormatDescription.txt).
All numeric ranges and command parameters come from that file. If they
disagree, the upstream file is canonical.
