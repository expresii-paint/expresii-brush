---
name: expresii-brush-style-library
description: Stroke style library, noise/ending/corner profiles, dry scratch styles, and brush-down invariant for the expresii-brush skill.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [creative, drawing, painting, brush, expresii, art, automation, style-library]
    related_skills: [expresii-brush]
    category: creative
---

# Expresii Brush Style Library

Reusable stroke-style catalog and brush-down invariant for the `expresii-brush` skill. Companion to the `expresii-brush` skill; covers the style registry, noise/ending/corner axes, and dry scratch variants.

## Phased Stroke Model (per-phase wetness / tilt / gradient)

A stroke is three phases — **beginning**, **mid**, **end** — each carrying its OWN
wetness, tilt, gradient-loading, scratch, and pressure peak. This is the composable
stroke model you asked for: *wet vs dry, tilt=0 vs tilt-to-side, gradient painting,
and per-phase variations.* Implemented in `scripts/phased_stroke.py`
(`build_phased_stroke`). Full detail + phase-config table + usage:
`references/phased-strokes.md`.

Quick example (dry+tilted → wet+flat → dry+tilted with a different gradient):

```python
from phased_stroke import build_phased_stroke
xst = build_phased_stroke(
    [(0.0, 0.0), (1.5, 0.3), (3.0, 0.0)], size=5.0,
    begin={"wetness": 0.05, "tilt": (-50, -15),
           "gradient": ((253, 208, 54), (255, 255, 255)), "scratch": 1.0},
    mid={"wetness": 0.95, "tilt": 0.0, "gradient": ((30, 90, 200), (200, 40, 40))},
    end={"wetness": 0.05, "tilt": (50, 15), "solid": (40, 40, 40), "scratch": 1.0},
)
```

Phases re-issue `w`/`i`/`l`/tilt only at phase boundaries; the brush stays down
(across re-issues) so the stroke is continuous, and the brush-down invariant
(lift→press, nothing between) is preserved. `verify_xst.py` and the suite test
(`tests/test_phased_stroke.py`) confirm both.

**Coordinate convention (Expresii XST v0.8+):** `+Y is UP` (Cartesian/SVG-aligned)
— emit y directly, do NOT negate it. Pre-v0.8 needed a `-y` flip; that's gone.
(Tilt-Y/Tilt-X signs: Tilt-Y+ = North/up, Tilt-X− = East — unchanged.)



## Z-Pressure Coupling (authoritative, recorded v0.7)

**The definitive calibration comes from the app's OWN recorded output** (user-recorded "green ink cube.XST", 2026-07-31):

```
z = 0.021875 − 0.154167 × pressure      (pressure caps at 0.75)
```

- `p = 0.0` → `z = +0.021875` (lifted)
- `p = 0.14` → `z ≈ 0.0000` (tip touches)
- `p = 0.5` → `z = −0.0552`
- `p = 0.75` → `z = −0.09375` (max recorded press; app never writes p > 0.75)

Earlier formulas tried in sessions: `0.0625 − 0.125p` (WRONG — strokes peaking below p≈0.5 never cross z≤0 → blank canvas) and `0.0875 − 0.4625p` (deposits pigment but presses far deeper than the app ever records). **Decode from a fresh recording whenever the app version changes** — treat the coupling as build-dependent.

Recorded strokes also use: geometric pressure ramps (press `p = 0.75×(1−0.8^k)`, release `p = 0.75×0.8^k`), a `b` brush-down/up marker (30-value string) bracketing each stroke, four `a 0..3` command lines after the color nodes, and NO `i` scratch line. Full decoded wire format: `references/recorded-wire-format.md`.

## Ink-Painting Washes (user-taught, 2026-07-31)

**A background wash is NOT a grid/array of thin strokes.** Doing that was corrected hard: "stop do that array of lines!", "that's not the way to do a background wash", "it gives a window blind look." The user's ink-painting technique:

1. **Broad stroke**: large brush (`B` 7 max) + **strong tilt** (pitch ≈ −55°, roll ≈ −25°) → the tilt fans the brush into a broad footprint.
2. **Load a gradient**: fully-loaded 9-node gradient (ALL nodes colored, slight tip darkening, no white root fade) so pigment spans the whole footprint — a tip-dark gradient deposits only part of it.
3. **More water so ink flows**: `w ≈ 1.0`. Water flows from the high-water zone of the stroke outward to dry paper, carrying light/dark ink with it — the feathering IS the flow doing the spreading.
4. **Space broad washes ~1.3 paper units apart** so adjacent blooms overlap and the flows MERGE into one continuous wash.

Verified numbers (5 broad washes at y = −2.6, −1.3, 0, 1.3, 2.6; B=7, tilt (−55,−25), w=1.0, fully-loaded gradient, peak 0.45): **59.9% canvas coverage, only 2 row-edge transitions (top+bottom) = one merged wash, no internal banding**. A single wash blooms ~1.6 units tall (326px of 2048).

## Paint Order / Wet-on-Wet Physics (verified 2026-07-31)

**Thin strokes painted AFTER soaking-wet background washes disperse into nothing.** Verified: staghorn branches (orange) painted after 14 wet washes → **0 pigment**; identical strokes painted first on dry paper → fully visible (109k warm px). The wet canvas swallows the ink.

Rule: **paint fine details FIRST on dry paper (layer 0), then background washes LAST on a lower layer (`L 1`)**. Layers keep the wash under the details, but order still matters for pigment survival.

## 3D / Geometric Stroke Ordering

For multi-stroke 3D projections (cube wireframe, landscapes from data series), **sort strokes farthest→nearest** so later (nearer) strokes overlay in front at crossings — painter's algorithm on the projected depth (`z2`), ascending. Also: offset the start yaw by half a step (e.g. 52.5° with 15° steps) to avoid yaw multiples of 90° at elev 45°, where the projection degenerates (edges overlap → 6 distinct vertices instead of 8). See `references/geometric-strokes.md` for projection math and the degenerate-view pitfall.



**Builders are real (2026-08-21 update):** the full builders — `build_style_stroke()`, `build_dry_strokes()`, `build_path_stroke()`, `build_dry_path_stroke()`, `build_profile_stroke()`, `build_circle()`, `build_star()`, `build_dab()`, `paint()`, and the `BRUSH_STYLES` dict (25 entries) — ALL live in the parent `scripts/send_strokes.py` in this repo (the `expresii-brush` clone). The older "minimal helper only" gap noted 2026-07-31 is resolved: the repo now ships the complete generator. Import from `scripts/send_strokes` (add the parent `scripts/` dir to `sys.path`) or call the `expresii-brush` skill's helper. The style-library's own `scripts/phased_stroke.py` is the per-phase composable builder built on top of those.

The `BRUSH_STYLES` dict in the parent `scripts/send_strokes.py` is the canonical catalog (25 entries as of 2026-08-21). Each entry maps a style name to a recipe dict:

| Key | Description |
|-----|-------------|
| `kind` | `"wet"` or `"dry"` |
| `size` | Brush size (XST `B`) |
| `pprofile` | Pressure profile key (see PRESSURE_PROFILES) |
| `wprofile` | Wetness profile key (see WETNESS_PROFILES) |
| `sprofile` | Scratch profile key (see SCRATCH_PROFILES) |
| `tilt` | `(pitch, roll)` in degrees |
| `wobble` | `(amplitude, cycles)` — lateral path offset |
| `wobble_phase` | Phase offset for multi-stroke desync |
| `noise` | Noise profile key (see NOISE_PROFILES) |
| `ending` | Ending profile key (see ENDING_PROFILES) |
| `corner` | Corner profile key (see CORNER_PROFILES) |
| `ramp` | Color ramp key (see COLOR_RAMP_PROFILES) |
| `scheme` | Dry brush scheme (`ends`, `mid`, `mid_short`, `speed`, `progression`) |
| `desc` | Human-readable description |

## Style Axes

### ENDING_PROFILES
- `none` — simple lift bookend
- `taper` — tapered tail
- `flick` — flick-off at tail end
- `blunge` — blunted end

Applied to the last N tail frames of an open stroke.

### CORNER_PROFILES
- `none` — waypoints connect straight
- `dwell` — extra pressed frames at waypoint transitions

### NOISE_PROFILES
- `none` — no perturbation
- `shiver` — fine high-frequency jitter
- `scratch` — coarse irregular path offset
- `skitter` — discrete step-like perturbations

Compiled to `(t, dx, dy)` offsets along stroke progress.

### PRESSURE_PROFILES
`Standard`, `Smooth Bell`, `Triple Bell` (A/B/C/D), `Constant`, `Fade In`, `Fade Out`, `Flick`, `Bell`, `Sketchy`.

Triple Bell variants have shifted peak positions for desynced thick/thin alignment across multi-stroke layouts.

### Dry Scratch Styles

Added for exploring scratchiness without wet strokes:

| Style | Traits |
|-------|--------|
| `dry_scratch` | scheme `ends`, size 3.5, hard tilt + max scratch + Triple Bell |
| `dry_wobble` | scheme `ends`, size 3.0, tilt(−25,25), wobble(0.15,14) |
| `dry_wobble_wide` | scheme `ends`, size 3.0, tilt(−20,20), wobble(0.25,10), phase 1.57 — pronounced snake |
| `dry_wobble_tight` | scheme `ends`, size 3.0, tilt(−30,30), wobble(0.08,24) — tight zig-zag |
| `dry_staccato` | scheme `mid_short`, size 2.0, driest, max scratch → dot-dash chain |
| `dry_tilt_stroke` | scheme `ends`, size 3.0, tilt(−60,0), driest, max scratch → stippled line |
| `dry_dot_chain` | scheme `ends`, size 2.5, Flick pressure, driest, max scratch → dotted chain |

**Note:** `dry_wiggle` is a WET style (kind="wet") despite its name — it uses `build_profile_stroke` with Triple Bell pressure + BlueToDeep ramp. The dry wobble styles above use `build_dry_strokes` via the `ends` scheme.

## Calligraphic Path Deformation (書法家 結體 / 黃庭堅 體勢外張)

To make strokes look hand-written by a *calligrapher* (not a machine), **deform the
stroke centerline** before emitting `s` frames. Random jitter is NOT 書法 — the
user rejected a pure-wobble version as "not obvious / not calligraphy". The beauty
is *structural*. Full detail + pitfalls + verified tunings: `references/calligraphic-paths.md`;
re-usable importable impl + self-test: `scripts/calligraphic_path.py`.

Huang Tingjian 結體 rules (authoritative, user-given):
1. **中宮收緊** — interior crossings crowd tight toward the character center.
2. **體勢外張** — ONLY the peripheral extending strokes (撇/捺/長橫/鈎/長豎) radiate
   outward + lift at the tips; character bursts its frame.
3. **SQUARE PARTS STAY 方** — enclosed components (目/口/田/日) must NOT be stretched;
   they get ~0 radiation, instead gently NARROWED (收窄) toward their own centroid —
   square but tighter.

Algorithm (`calligraphic_path(pts, centroid, amount, seed, mode="huang", char_rad)`,
`pts` in **world space**): per-stroke `extend = clamp((rmax_stroke/char_rad - 0.30)/0.45, 0, 1)`
gates BOTH the outward radiation/tip-lift AND the inward `tighten` (中宮收緊); interior
strokes (small rmax) → extend≈0 → only 收窄 applies → box stays 方. Enclosed boxes pull
toward their OWN centroid (not the character center), so they don't distort.

**PITFALLS (both hit live, both costed a repaint):**
- Deform in **WORLD space** (after the generator's `wpt()`), not glyph space. Glyph-unit
  amplitudes get /1024-scaled to invisibility (~1e-5 world units). World-space `amount~0.1`
  reads as ~10% of a cell.
- Gate radiation by per-stroke **MAX radius** (not mean) AND gate `tighten` too. Mean
  under-gates short strokes with far-reaching tips; un-gated `tighten` drags boxes off-square.
- `char_rad` = character's overall radius in **world space** (per-character in a grid).

Tuning: `calli` amount **0.22 was garish** (peaks ~42% of cell, tips flying up); **0.10 is
tasteful** (peak ~12%, mean ~3.6%). Keep the ink look (e.g. `w 0.12`, `B 1`) unchanged — only
the PATH changes. Verify with the mock test in `scripts/calligraphic_path.py` (box stays square
+ narrows; peripheral tip radiates; `amount=0` pass-through).

## Brush-Down Invariant

**Two consecutive `s` frames must go `p=0 → p>0` with no non-`s` command between.**

Enforced in:
- `build_profile_stroke()` — consecutive `s` for press after lift
- `_dry_line()` — same rule for dry schemes
- `build_style_stroke()` — delegates to the above

**Emitter rule for peak-starting profiles (verified 2026-07-31):** pressure profiles that START at peak — `fade_out`, `flick` — emit their first frame already pressed, which violates brush-down (first two frames would be `p>0 → p<...`, no lift). Any generator that emits arbitrary profiles must, per stroke:
1. Prepend an explicit leading lift frame (`p=0`, `z = Z_LIFT = 0.0875`) at the first waypoint,
2. Clamp the first profile frame to `p ≥ 0.02` so the lift→press transition is `0 → >0` across consecutive `s` frames with nothing between,
3. Append a trailing lift frame (`p=0`) at the last waypoint.

A structural verifier that checks "first frame p=0, second frame p>0" will catch strokes emitted without this rule — use it, don't eyeball.

`c` (clear) must be sent as its own HTTP request + 7s settle before the stroke batch. This avoids the race where strokes clear themselves before rendering.

## Tilt Centroid Shift

Brush tilt shifts the footprint centroid ≈ `B * sin(|tilt|)` in the X direction. When placing strokes in a grid, offset the path midpoint by half the centroid shift toward the cell center so the visual center of each stroke stays inside its allocated cell. This is critical for avoiding overlap in tight grid layouts.

## Flower Generation (Petals)

Flower petals are a distinct stroke class learned from recorded `.xst` files (see `references/flower-petals.md` for full session details). The key technique: each petal is a separate stroke with its own `b` brush-down/up markers, 9-node color gradient, and pitch/roll aligned to the petal direction.

### Petal Stroke Structure

| Element | Value from recordings |
|---------|----------------------|
| `B` (brush size) | 3.0–4.0 |
| `w` (wetness) | 0.06–0.08 (dry) |
| `C` (contour) | 4.0 |
| Color gradient | 9 nodes (tip→root), e.g. yellow ramp |
| Frames per petal | 150–200 `s` frames |
| Pressure profile | Smooth Bell (0 → 0.6–0.7 → 0) |
| Scratch profile | Light or None |
| Wetness profile | Level 2–3 — Dry |
| Path | Curved from center outward with perpendicular S-swing |
| Pressure taper | Aggressive drop in last 15% for pointed tip |

### Petal Path Shape

To get pointed-at-base natural petals (not sausages/ovals), use waypoints with a width profile:

```
t=0 (base):     width~0 (pointed, near receptacle)
t=0.25:         width~40% of max
t=0.5 (middle): width~80-100% of max (widest)
t=0.75:         width~40% of max (tapering)
t=1 (tip):      width~0 (pointed)
```

Apply width as perpendicular offset from the center line. Use a pure sine S-curve (`0.1 × sin(πt)`) — avoid the 2nd harmonic (`sin(2πt)`) which makes the petal look "sided" (bulge offset to one side, root appears disconnected from center). See the "Petal Path Shape: Pure Sine" section below for the correct formula.

### Clock Convention

Specify petal directions using clock hours (not math angles):

```
clock_to_angle(hour) → internal angle
  12 o'clock = up    → 270°
  3 o'clock  = right → 0°
  6 o'clock  = down  → 90°
  9 o'clock  = left  → 180°
  1 o'clock  = up-right → 300°
```

Formula: `angle = (270 + hour × 30) % 360` where 0°=right (East), 90°=down (South), 180°=left (West), 270°=up (North). This is the clock→angle mapping for **Expresii XST v0.8+ where +Y is UP** (up = North = 270°), so "12 o'clock = up" maps directly to +Y. Under pre-v0.8 (+Y down) the same clock numbers pointed the opposite vertical way; the formula above assumes v0.8+.

### Pitch/Roll Per Angle (from recorded 5-petal yellow flower)

| Petal direction | Pitch | Roll |
|----------------|-------|------|
| ~0° (right/3 o'clock) | +14 | −49 |
| ~72° (down-right/4:30) | +12 | −48 |
| ~144° (down-left/7:30) | −29 | −47 |
| ~216° (up-left/10:30) | +51 | −52 |
| ~288° (up-right/1:00) | +11 | +40 |

Roll stays ~−50 for bottom half (<180°), flips to ~+45 for top half (>180°). Pitch is positive on right side, negative on left, positive again for top-right.

### 3D Foreshortening Model

For a side/3/4 view, model the flower as a disk tilted around the x-axis:

```python
# 3D position on tilted disk (tilt_deg = how much disk tilts, 0=flat on)
petal_3d_z = -math.sin(rad) * math.sin(tilt_rad)  # >0 = away from viewer
foreshortened_length = length_3d * sqrt(cos²(θ) + sin²(θ)·cos²(tilt_deg))
```

**Depth cues (positive z = away from viewer):**
- Away (z > 0): **darker**, smaller, drier
- Toward (z < 0): **brighter**, larger, wetter
- Away = `color_adj = 1.0 - z × 0.15` (max ±15% — user found ±35% too strong)
- Away = `size_adj = size × max(0.4, 1.0 - z × 0.2)`
- Away = `wetness_adj = wetness × (1.0 - z × 0.12)`

**IMPORTANT: Get the sign right.** The 1 o'clock petal (upper-right) is the furthest from viewer in a top-away tilt; it must be darkest. The 6 o'clock petal (down) is closest; it must be brightest. This was a recurring bug (inverted brightness) — verify by eye, not vision model (vision models are unreliable for brightness comparison).

### Petal Path Shape: Pure Sine for Symmetric Bulge

Avoid the 2nd harmonic in the S-curve — it makes the petal look "sided" (bulge shifted to one side):

**WRONG** (produces "sided" offset bulge):
```python
curve = 0.15 * math.sin(math.pi * t) - 0.03 * math.sin(2 * math.pi * t)
```

**RIGHT** (symmetric bulge, petal root centered):
```python
curve = 0.1 * math.sin(math.pi * t)   # or 0.12×sin(πt) for more organic shape
```

Both start and end at zero, so the root and tip are on the center line.

### Double-Stroke Symmetric Petals

For the closest/bottom petal (6 o'clock), the single-stroke bulge can still look uneven. Fix by drawing TWO symmetric strokes — one offset left, one offset right:

```python
for sign in (-1, 1):  # left (-1) and right (+1)
    path = []
    for i in range(n_path):
        t = i / (n_path - 1)
        dist = 0.05 + t * app_length  # base starts at 0.05 from center
        curve = sign * 0.12 * math.sin(math.pi * t)  # each side gets half the bulge
        x = cx + dist * dir_2d_x + perp_x * curve
        y = cy + dist * dir_2d_y + perp_y * curve
```

Both strokes start at the same base point (0.05 from center) and use slightly different roll offsets (±5°) so the brush tuft splays toward the center line.

### Base Connection (Root Centeredness)

Start the petal path **very close to center** (`dist = 0.05`) so the root visually connects to the center dot. The earlier version used `dist = 0.25` which left a visible gap between the petal root and the center.

### XST Wire Format for Petals

Each petal is a complete stroke with its own header + frames:

```
T   0.00000
w   {wetness}
C   4.00000
B   {size}
e   0.00000
k   0.00000
l 0 {r} {g} {b}  ... (9 node colors)
a   0.00000   0.00000
a   1.00000   0.00000
a   2.00000   0.00000
a   3.00000   0.00000
s {x0} {y0} 0.05188 {pitch} {roll} 0.00000 0.00000   ← lifted frame
b   0.01000 ...                                            ← brush-down marker
s {x1} {y1} {z1} {pitch} {roll} 0.00000 {p1}            ← pressed frame
w {wlvl/12}
i {sval}
... (same pattern for all 160 frames)
s {last_x} {last_y} 0.05188 0 0 0 0.00000               ← lift
b   0.01000 ...                                            ← brush-up marker
```

The `b` markers have a 30-value binary/float string; copy from a known-good recording verbatim.

## Layers (linework vs fills)

Expresii supports multiple layers; `L <x>` selects the active layer BEFORE subsequent strokes. **`x = 0` is the TOPMOST layer**, `1` is directly below it, `2` below that (top-down index). Out-of-range values are ignored (no-op). `send_strokes.py` exposes `select_layer(x) -> "L <x>\n"`, and `build_style_stroke(style, layer=N)` prepends `L N` automatically. (`build_path_stroke` does NOT take `layer` — only `build_style_stroke` does; for path strokes prepend `select_layer(N)` manually.)

**User's workflow request (this session):** split a drawing across layers — e.g. **linework on layer 0, fills on layer 1**. Assumption: at least two layers exist (layer-count query not yet available).

Canonical two-layer XST assembly:
```
c               ← clear (own request + 7s settle, see two-phase clear)
<layer-0 strokes>   ← linework / outlines
L 1             ← switch to layer 1 (select_layer(1))
<layer-1 strokes>   ← fills (use clear_first=False / --no-clear so they ADD)
```
Verified live (server at LAN address): clear → dark circle outline on layer 0 → `L 1` → yellow fill circle on layer 1 rendered both (744 KB, correct layering). Layer switches mid-XST work; keep each layer's strokes grouped and order `L` before the first stroke of that layer.

## Procedure

1. **Confirm the server is up.** Run `--ping` first.
2. **Choose a style** from `BRUSH_STYLES` or compose a custom recipe.
3. **Build the stroke** with `build_profile_stroke()` (wet), `build_style_stroke()` (dry), or raw XST for petals.
4. **Clear canvas** with `c` as its own request + 7s settle.
5. **Send the XST** via the helper or POST directly to `/confirm-ajax`.
6. **Render and verify** with `fetch_render()`. Run `bash scripts/run_tests.sh` to confirm brush-down invariant.

## Pitfalls

- **`c` clears the entire canvas.** Send `c` once at the start, not between strokes. Send `c` as its own HTTP request + 7s settle before the stroke batch.
- **Low-pressure strokes silently leave NO mark.** Any stroke whose peak pressure stays below the z-coupling contact threshold never touches the paper: with the documented coupling (`z = 0.0625 − 0.125×p`) the threshold is p = 0.5, so a wash peaking at 0.3 renders nothing. A later empirical session found a steeper coupling (`z = 0.0875 − 0.4625×p`, contact at p ≈ 0.19). Either raise wash peaks above the threshold for the coupling you emit, or use the steep coupling. Diagnose "some strokes didn't mark" by checking peak pressure vs contact threshold — do NOT re-send blindly.
- **`s` frame columns: `s x y z tY tX barrel pressure` — pressure is index 7, z is index 3.** Verification code that reads y (index 2) as z or barrel (index 6) as pressure produces nonsense diagnostics. When validating z-vs-pressure consistency, parse the right columns.
- **Do not create wet scratch styles yet.** Feathering support is not implemented; wet styles are currently solid fills. Focus on dry scratch variants for now.
- **Tilt footprint expansion.** Hard tilt (pitch ±40°+) fans the brush wider; use smaller `B` and wider cell spacing to compensate.
- **Verifier tolerance must match 5-dp formatting.** Generated z-values are printed with `%.5f`, so comparing against the z-formula needs tolerance ~1e-4; a 1e-6 tolerance false-fails every frame on rounding (e.g. `0.04189 != 0.04189`). Don't "fix" the XST — fix the tolerance. (2026-07-31, two false-fail loops.)
- **Vision module down? Use pixel analysis on the rendered PNG.** When the auxiliary vision model 404s / rate-limits, verify renders programmatically with PIL+numpy: per-target RGB distance masks (tol ~70) confirm each planned pigment deposited; coverage fraction + per-band horizontal span check composition; a grid color-map (8×4 cells, nearest-target by mean RGB over pigmented pixels) substitutes for a spatial description. This caught "all 7 pigments present" when vision was unavailable. Downscale to ≤1024px before sending to vision when it does work.
- **Vision model is unreliable for positioning audits.** Prefer programmatic Y-projection checks or coordinate-based verification over visual inspection. Vision consistently misreports grid cell counts, overlap, and paper coverage.
- **Dry `wobble` parameter has no effect.** `build_dry_strokes()` / `_dry_line()` does not read `wobble` — dry styles use hardcoded yaw/roll sweeps. The `wobble` key in dry style specs is a no-op. For path-level wiggle variation in dry strokes, the yaw/roll sweep in `_dry_line` is the only mechanism.
- **Grid layout for 3×3 style comparison:** use ±5 unit paper with rows at y = ±3.5 (or ±0.52 for tight layouts within the visible band) and columns at equal thirds. Clamp x to [−4.8, +4.8] to avoid off-paper clipping. Apply tilt centroid shift compensation (offset path midpoint by `size * sin(|max_tilt|) * 0.4` toward cell center) to prevent overlap.
- **Flower 3D depth: brightness sign must be correct.** The 1 o'clock petal (upper-right) is furthest from viewer in a top-away tilt → must be darkest. The 6 o'clock petal (down) is closest → must be brightest. Getting this sign wrong (inverted depth → away petals are brighter) was a recurring bug. Test by eye, not vision model.
- **Flower petals: narrow at base, wide middle, tapered tip.** A sausage/oval shape means the width profile is wrong. The base (near receptacle) must be pointed/narrow. Apply a width profile that goes narrow→wide→taper across the petal length.

## Verification

```bash
# Run brush-down invariant test
bash scripts/run_tests.sh

# Structurally verify a GENERATED batch before sending it (brush-down
# invariant, z-coupling consistency, pressure bounds, contact threshold):
# catches hand-authored s-frames drifting from the z formula — ALWAYS
# code-generate frames, never hand-type them, then verify.
python scripts/verify_xst.py painting.xst --steep   # or --flat (default)
python scripts/verify_xst.py painting.xst --steep --strict

# Check Python syntax
python -m py_compile scripts/send_strokes.py

# Render a style grid
python - <<'PY'
# ... build_style_stroke calls in a grid layout ...
PY
```

See `references/style-library.md` for the full BRUSH_STYLES catalog, valid profile keys, and grid layout conventions. See `references/recorded-wire-format.md` for the app's own recorded v0.7 XST wire format (z-coupling, b-markers, geometric ramps, tip-dark gradient). See `references/calligraphic-paths.md` for the 書法家 centerline-deformation technique (Huang Tingjian 體勢外張, world-space pitfall, max-radius `extend` gate). See `scripts/calligraphic_path.py` for the re-usable `calligraphic_path()` impl + self-test. See `scripts/run_tests.sh` for the test runner wrapper.