# Expresii Brush Style Library Reference

Full catalog of BRUSH_STYLES entries, valid profile keys, and grid layout conventions for the expresii-brush-style-library skill.

## BRUSH_STYLES (23 entries, as of session 2026-07-29)

### Wet family (11)
- `wet_wash` — Constant pressure, Level 12 Wettest, smooth wash
- `wet_smooth` — Smooth Bell pressure, Level 5 Medium, smooth solid
- `wet_satin` — Smooth Bell + Light scratch + Level 5, gentle side wobble
- `wet_wiggle` — Smooth Bell + Level 5, path wobble (0.12, 14)
- `ink` — Fade Out pressure, Level 5, calligraphic tail
- `ink_drybrush` — Constant + Build Up + Level 5, wet head / scratchy tail
- `dry_wiggle` — Triple Bell + Level 1 Driest + Maximum scratch + BlueToDeep ramp + tilt (-50,50)
- `wet_gravel` — *(removed)* was: Standard + Level 2 Dry + Maximum
- `wet_pitted` — *(removed)* was: Sketchy + Level 3 Dry + Maximum
- `wet_scratch` — *(removed)* was: Flick + Level 2 Dry + Maximum
- `dot_line` — *(removed)* was: Flick + Level 2 Dry + Maximum + flick ending
- `zigzag` — *(removed)* was: Sketchy + Level 3 Dry + Maximum + tilt(-40,40) + wobble + scratch
- `tilt_dash` — *(removed)* was: Standard + Level 3 Dry + Maximum + tilt(-60,0)
- `scratch_wave` — *(removed)* was: Triple Bell + Level 2 Dry + Maximum + wobble(0.22,18) + scratch

### Dry family (10)
- `dry_ends` — scheme "ends", size 4.0, hard tilt + max scratch
- `dry_mid` — scheme "mid", size 4.0, grain through whole stroke
- `dry_mid_short` — scheme "mid_short", size 4.0, ~0.3 length scratch
- `dry_speed` — scheme "speed" (missing size key, uses default 3.5)
- `dry_progression` — scheme "progression" (missing size key, uses default 3.5)
- `dry_wiggle` — size 3.0, Triple Bell, Level 1 Driest, Maximum, BlueToDeep, tilt(-50,50), wobble(0.1,10)
- `dry_scratch` — scheme "ends", size 3.5, hard tilt + max scratch + Triple Bell
- `dry_wobble` — scheme "ends", size 3.0, tilt(-25,25), wobble(0.15,14)
- `dry_staccato` — scheme "mid_short", size 2.0, Level 1 Driest, Maximum, tilt(-30,-10)
- `dry_tilt_stroke` — scheme "ends", size 3.0, tilt(-60,0), Level 1 Driest, Maximum
- `dry_dot_chain` — scheme "ends", size 2.5, Flick pressure, Level 1 Driest, Maximum, tilt(-20,-10)

## Dry Scratch Styles (current, as of 2026-07-29)
- `dry_scratch` — scheme "ends", size 3.5, hard tilt + max scratch + Triple Bell
- `dry_wobble` — scheme "ends", size 3.0, tilt(-25,25), wobble(0.15,14)
- `dry_wobble_wide` — scheme "ends", size 3.0, tilt(-20,20), wobble(0.25,10), phase 1.57
- `dry_wobble_tight` — scheme "ends", size 3.0, tilt(-30,30), wobble(0.08,24)
- `dry_staccato` — scheme "mid_short", size 2.0, Level 1 Driest, Maximum, tilt(-30,-10)
- `dry_tilt_stroke` — scheme "ends", size 3.0, tilt(-60,0), Level 1 Driest, Maximum
- `dry_dot_chain` — scheme "ends", size 2.5, Flick pressure, Level 1 Driest, Maximum, tilt(-20,-10)

**Note:** `dry_wiggle` is a WET style (kind="wet") despite its name — it uses `build_profile_stroke`. The dry wobble styles above use `build_dry_strokes` via the `ends` scheme. The `wobble` parameter in dry style specs is a no-op (see Pitfalls in SKILL.md).

## Color Ramp Profiles
- `WarmToCool` — warm to cool across tuft width
- `CoolToWarm` — cool to warm
- `BlueToDeep` — bright sky-blue → deep navy

## Valid Profile Keys

### WETNESS_PROFILES
Level 1 — Driest through Level 12 — Wettest plus transitions (Dry to Wet, Wet to Dry).

### SCRATCH_PROFILES
None (0.0), Light (0.2), Medium (0.5), Heavy (0.8), Maximum (1.0), Build Up, Fade Out, Mid Spike.

### PRESSURE_PROFILES
Standard, Smooth Bell, Triple Bell (A/B/C/D), Constant, Fade In, Fade Out, Flick, Bell, Sketchy.

### ENDING_PROFILES
none, taper, flick, blunge.

### CORNER_PROFILES
none, dwell.

### NOISE_PROFILES
none, shiver, scratch, skitter.

## Grid Layout Convention

- Full paper: ±5 units (10-unit span)
- Visible band: y ∈ [-0.6, +0.6]
- 3×3 equal-third cells, cell width ≈ 3.33 units
- Row y-positions: 0.45, 0.0, -0.45 (or ±3.5 for full-paper vertical spread)
- Column x-ranges: [-5.0, -1.667], [-1.667, 1.667], [1.667, 5.0]
- Tilt centroid shift compensation: offset path midpoint by `size * sin(|max_tilt|) * 0.4` toward cell center
- Clamp x to [-4.8, +4.8] to avoid off-paper clipping
- Use small sizes (0.7–1.1) so brush footprints fit within cells with margin
- Vision model is unreliable for positioning audits — use programmatic Y-projection or coordinate checks instead