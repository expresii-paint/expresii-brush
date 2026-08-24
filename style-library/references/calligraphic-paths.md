# Calligraphic Path Deformation (書法家 結體 / 黃庭堅 體勢外張)

Technique for making Expresii strokes look hand-written by a *calligrapher*, not
a machine — by **deforming the stroke centerline** before emitting `s` frames.
Learned 2026-08-03 while generating 天道酬勤 (tiān dào chóu qín) in 黃庭堅 style.

## When to use
- User asks for 書法 (calligraphy) look, "手寫感", "書法家的字", structural balance
  like 中宮收緊 / 四方放射, or a named master's posture (黃庭堅 體勢外張).
- **Distinguish from random "hand wobble":** a calligrapher's beauty is
  *structural* (deliberate shape), not noise. Random jitter is NOT 書法 — the
  user explicitly rejected a pure-wobble version as "not obvious / not calligraphy".

## Huang Tingjian 結體 rules (authoritative, user-given verbatim)
1. **中宮收緊** — interior crossings crowd tight toward the character center.
2. **體勢外張** — ONLY the peripheral *extending* strokes (撇/捺/長橫/鈎/長豎)
   radiate outward + lift at the tips; the character seems to burst its frame.
3. **SQUARE PARTS STAY 方** — enclosed components (目/口/田/日) must NOT be
   stretched. They get ~0 radiation; instead they are gently NARROWED (收窄)
   toward their own centroid, staying square but tighter. (User: "'方'的部分，如
   '道'字中間的'目'，要保持'方'，但可以收窄".)

## Algorithm — calligraphic_path(pts, centroid, amount, seed, mode="huang", char_rad)
`pts` = **world-space** centerline (already transformed through the generator's
`wpt()`). Returns deformed **world-space** points. Self-contained implementation:
`scripts/calligraphic_path.py` (importable: `from calligraphic_path import calligraphic_path`).

- **Per-stroke `extend` gate** from the stroke's MAX radius to the character centroid:
  `extend = clamp((rmax_stroke / char_rad - 0.30) / 0.45, 0, 1)`.
  Interior/enclosed stroke (small rmax) -> `extend ≈ 0` (no radiation); peripheral
  stroke -> `extend ≈ 1` (full 體勢外張).
- **Radiation** (outward push) and **tip lift** are scaled by `extend`. Interior
  strokes get ~0 -> they keep their square shape.
- **`tighten`** (inward pull toward the CHARACTER centroid, the 中宮收緊 term) is
  ALSO gated by `extend`. If it is NOT gated, it drags interior boxes off-square
  toward center while `narrow` pulls them to their own centroid — competing pulls
  inflate/distort the box (aspect 1.0 -> 1.9, extent grows). Gate both by `extend`.
- **`narrow`** (收窄): pull every point toward the STROKE's OWN centroid by
  `0.5 * amount * (1 - extend)`. Keeps enclosed boxes square AND tighter.
- **Tip lift**: `dy -= 0.5 * amount * extend * end_w` where `end_w = (i/(n-1))**2`,
  so only the tip rises (negative y = up in world space).
- Tiny **organic tremor** on top (`±0.004*amount` per point, seeded) for hand feel.

## PITFALLS (both hit live; both costed a repaint)
1. **Apply deformation in WORLD space, not glyph space.** Generators build the
   centerline in glyph pixels (0..1024) then `wpt(gx/1024 - 0.5 - cx) * CHAR_SCALE`
   collapses it to ~±0.5 world units. If you deform BEFORE `wpt` with glyph-unit
   amplitudes (~0.001), the /1024 scaling makes the wobble ~1000x too small ->
   invisible (measured deviation ~1e-5 world units). Deform AFTER `wpt` (world
   coords) so `amount ~ 0.1` reads as ~10% of a cell.
2. **Gate radiation by per-stroke MAX radius, and gate `tighten` too.** Using the
   whole-stroke MEAN radius under-gates short strokes with far-reaching tips (tip
   reaches out but mean is small -> no radiation). MAX radius fixes it. And an
   un-gated `tighten` distorts enclosed boxes (see above). Both gates are required.
3. **`char_rad` must be the character's overall radius in WORLD space** (max
   distance of any char point to its centroid AFTER `wpt`), not glyph radius. In a
   grid, compute a per-character centroid + radius (each char has its own), not one
   global value.

## Tunings that worked
- `calli` amount: **0.22 was GARISH** (peak deviation ~42% of cell height, tips
  flying upward). **0.10 is tasteful** (peak ~12%, mean ~3.6% of cell). Start at
  0.10 and let the user dial up.
- Keep the ink look (e.g. `w 0.12`, `B 1`) UNCHANGED — only the PATH changes, so
  the deformation is isolated and comparable.

## Verification
- **Mock test** (deterministic, no render): interior 8-point box -> aspect stays
  ~1.0 and narrows (extent shrinks); peripheral horizontal bar -> tip x increases
  (radiates outward). `amount=0` -> exact pass-through. `classic` mode != `huang`.
- **Integration**: hand XST centerline deviates from the straight version (max dev
  ~0.10-0.14 world); 42 `w` / 42 `b` for the 4-char grid; z-coupling intact; no `L`.
- **Visual**: vision model confirmed the 目 box in 道 stays square while outer
  strokes radiate = "convincing Huang Tingjian structure". (Eyeball the square
  parts specifically — that is the part most likely to regress.)
