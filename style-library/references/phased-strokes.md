# Phased Stroke Model (per-phase wetness / tilt / gradient)

A stroke is three phases — **beginning**, **mid**, **end** — and each phase carries
its OWN look: wetness, tilt, gradient-loading, scratch, pressure peak. This is the
composable stroke model (`scripts/phased_stroke.py`).

## Why

The user's mental model (2026-08-21): *"different wet and dry strokes; tilt=0 vs
tilt to the side; painting with gradient; and there could be stroke beginning,
mid-stroke, and stroke ending that can be assigned such stroke variations
(wetness, tilt, gradient loading)."* The older builders (`build_profile_stroke`,
`build_path_stroke`) set `w`/`i`/tilt/color **stroke-wide**. The phased builder
re-issues those per phase so one continuous stroke can start dry+tilted, become
wet+flat in the middle, and finish dry+tilted with a different gradient.

## Phase assignment

By normalized progress `t` along the path (`[0,1]`):

```
begin : t in [0.00, begin_end)        default begin_end = 0.18
mid   : t in [begin_end, 1 - end_len)
end   : t in [1 - end_len, 1.00)      default end_len = 0.18
```

Overlapping/empty bands are allowed (e.g. `begin_end=0.5, end_len=0.5` → only
begin+end, no mid).

## Phase config dict

| Key | Meaning | Default |
|-----|---------|---------|
| `wetness` | brush wetness level `0..1` (XST `w` = level/12) | 0.5 |
| `tilt` | `(pitch, roll)` degrees, or a single scalar (roll) | 0 |
| `gradient` | `(tip_rgb, root_rgb)` → 9-node tuft gradient | None |
| `solid` | `rgb` → all 9 nodes same color | None |
| `scratch` | brush scratchiness `0..1` (XST `i`) | 0 |
| `peak` | pressure peak multiplier in this phase (bell shape shared) | 0.7 |

If a phase has neither `gradient` nor `solid`, its color is left as whatever the
previous phase set (Expresii keeps the loaded color until re-issued).

## Tilt & gradient interaction

A tilt (Roll/Pitch) lays the tuft sideways so a tuft **gradient** fans across the
stroke WIDTH. Flat (`tilt=0`) shows the gradient only along the tuft length. The
builder auto-applies `_AUTOTILT` when a phase has a gradient but zero tilt, so the
gradient is visible without you specifying a tilt.

## Brush-down invariant preserved

The leading lift bookend (p=0) → first pressed frame (p>0) with **no command
between** is emitted, so the stroke deposits. Phases re-issue `w`/`i`/`l`/`tilt`
ONLY at phase boundaries — Expresii keeps the brush down across re-issues, so the
stroke stays continuous.

## Usage

```python
from phased_stroke import build_phased_stroke

xst = build_phased_stroke(
    [(0.0, 0.0), (1.5, 0.3), (3.0, 0.0)],   # path (Expresii space, +Y up in v0.8+)
    size=5.0,
    begin={"wetness": 0.05, "tilt": (-50, -15),
           "gradient": ((253, 208, 54), (255, 255, 255)), "scratch": 1.0, "peak": 0.6},
    mid={"wetness": 0.95, "tilt": 0.0,
         "gradient": ((30, 90, 200), (200, 40, 40)), "peak": 0.8},
    end={"wetness": 0.05, "tilt": (50, 15), "solid": (40, 40, 40), "scratch": 1.0},
    segments=28,
)
# send xst via send_strokes.send_xst(...) or the --command helper
```

Or from the command line (prints demo strokes):

```bash
python scripts/phased_stroke.py --self-test   # invariant checks, no server
python scripts/phased_stroke.py               # prints 3 demo strokes as .xst
```

## z-coupling note

`phased_stroke` emits `z = 0.021875 − 0.154167·p` (the **recorded** coupling from
`references/recorded-wire-format.md`), with lift z = `+0.021875`. Pressure is
capped at the recorded max `p = 0.75`, so z never drops below the recorded
footprint floor (`−0.09375`). This matches the brush-down/footprint rule: contact
is detected by pressure going 0→>0 (nothing between the two `s` frames), and
while p>0 the brush is lowered so the tuft intersects the paper and leaves a mark.

- brush-down invariant holds on a 3-phase stroke
- z-coupling matches `z = 0.021875 − 0.154167·p` on every frame (recorded floor)
- wetness varies across phases (≥2 distinct `w` levels)
- tilt varies across phases (≥2 distinct tilt states)
- color re-issued ≥3 times (begin/mid/end)
- a single-phase (all-equal) stroke emits exactly one wetness level
