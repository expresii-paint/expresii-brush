# Pressure / Wetness / Scratch Profiles (from Amami Inker)

These profiles are taken from the **Amami Inker** web app
(`github.com/expresii-paint/Amami`, `Amami.html`) — the official Expresii
companion for designing stroke dynamics. Each profile is a **piecewise-linear
curve over stroke progress `t` ∈ [0, 1]** (start → end of the stroke).

## How the three parameters map to XST

| Concept | Amami scale | XST command | Notes |
|---|---|---|---|
| **Pressure** | `p` ∈ [0.1, 0.8] | per `s` frame: `… <pressure>` | Varies **per frame**. Drives `z = 0.0625 − 0.125·p` automatically. |
| **Wetness** | `w` ∈ levels **1..12** | `w <level/12>` | Brush-global. **1 = driest, 12 = wettest.** XST `w` = level ÷ 12. Re-issuable mid-stroke (brush stays down). |
| **Scratch** | `s` ∈ [0, 1] | `i <value>` | Brush-global. **0 = smooth, 1 = maximum broken texture.** Re-issuable mid-stroke. |

> **Mid-stroke re-issue works.** You can emit `w …` / `i …` *between* `s`
> frames and Expresii keeps the brush down — the stroke stays continuous, no
> lift/seam between segments. So a "wet → dry" or "build-up scratch" profile is
> one continuous stroke, not separate sub-strokes. (Verified against the live
> app: a wet head + dry tail rendered as a single unbroken line.)

## Pressure profiles (`PRESSURE_PROFILES`)

| Name | Shape (p over t) | Look |
|---|---|---|
| **Standard** | `0.1 → 0.57 (t=0.016) → 0.74 (t=0.81) → 0.1` | Pressed body, **light/dry-feeling ends**. The default for natural strokes. |
| **Smooth Bell** | `0.1 → 0.4 → 0.8 → 0.4 → 0.1` | Even, fat belly, soft ends. Calligraphic. |
| **Constant** | `0.6 → 0.6` | Uniform line, same weight throughout. |
| **Fade In** | `0 → 0.3 → 0.8` | Starts thin/light, ends heavy. |
| **Fade Out** | `0.8 → 0.3 → 0` | Starts heavy, ends thin/**dry-looking tail**. |

## Wetness profiles (`WETNESS_PROFILES`, levels 1–12 → XST w = level/12)

| Name | Levels (t=0 → t=1) | Look |
|---|---|---|
| **Level 5 — Medium** | 5 → 5 | Balanced. |
| **Level 1 — Driest** | 1 → 1 | **Dry brush**: broken, skipping pigment, scratchy. |
| **Level 12 — Wettest** | 12 → 12 | **Wet**: smooth, juicy, full coverage. |
| **Dry to Wet** | 1 → 12 | Dry start, wet finish. |
| **Wet to Dry** | 12 → 1 | Wet start, **dry/scratchy finish**. |
| **Wet Middle** | 1 → 1 → 12 → 1 → 1 | Dry ends, wet belly. |

## Scratch profiles (`SCRATCH_PROFILES`, 0–1 → XST i)

| Name | Values (t=0 → t=1) | Look |
|---|---|---|
| **None** | 0 → 0 | Smooth, no broken texture. |
| **Light / Medium / Heavy / Maximum** | 0.2 / 0.5 / 0.8 / 1.0 (flat) | Increasing broken-brush texture. |
| **Build Up** | 0 → 1 | **Scratch grows toward the end** → scratchy tail. |
| **Fade Out** | 1 → 0 | Scratchy start, smooth end. |
| **Mid Spike** | 0 → 0 → 0.8 → 0 → 0 | Scratchy only in the middle. |

## Recipes for common looks

| Goal | Pressure | Wetness | Scratch |
|---|---|---|---|
| **Wet, juicy line** | Smooth Bell | Level 12 Wettest | None |
| **Dry brush, scratchy ENDS** | Standard (light ends) | Level 1 — Driest | **Build Up** |
| **Dry brush, scratchy START** | Standard | Level 1 — Driest | Fade Out |
| **Dry brush, scratchy MIDDLE only** | Standard | Level 1 — Driest | Mid Spike |
| **Wet head → dry tail** | Standard | **Wet to Dry** | Build Up |
| **Uniform dry-brush scribble** | Constant (0.6) | Level 1 — Driest | Heavy |
| **Calligraphic swell** | Smooth Bell | Level 5 — Medium | None |

### Key insight (verified)
Dry-brush *scratchiness* needs **all three** working together:
- **Low wetness** (level 1–3) so the brush has little moisture to carry pigment,
- **Low pressure at the ends** (Standard's 0.1 ends, or Fade Out) so the brush
  barely touches as it lifts, letting hairs skip,
- **Higher scratch** (Build Up / Heavy) to break the deposit.

A dry wetness alone with *high* pressure just makes a thin-but-solid line
(brush forced down, no skipping). The skipping — the broken, scratchy texture —
comes from **light touch + dry + scratch**, not dry alone.

## Using from the helper

```bash
# Dry, scratchy-ended stroke
python send_strokes.py --host 127.0.0.1 --port 9000 \
  --pstroke=-1,-0.3 --pstroke=0,0.3 --pstroke=1,-0.3 \
  --pprofile=Standard --wprofile="Level 1 — Driest" --sprofile="Build Up" \
  --size 5 --verify dry.png
```

Or build in Python:

```python
from send_strokes import build_profile_stroke
xst = build_profile_stroke(
    [(-1, -0.3, 0.5), (0, 0.3, 0.5), (1, -0.3, 0.5)],
    size=5, pprofile="Standard",
    wprofile="Level 1 Driest", sprofile="Build Up", segments=8)
```
