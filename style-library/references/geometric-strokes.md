# Geometric / Parametric Strokes (cube wireframe, polygons, 3D→2D)

How to draw math-defined shapes (cube wireframes, polygons, projected 3D forms) as
Expresii strokes. Verified live 2026-07-31: 12-edge cube at yaw 45°/elev 45° rendered
with 8 distinct projected vertices; 24-frame spin animation generated.

## Cube projection (yaw around Y, then elevation)

```python
def project(v, yaw_deg, elev_deg):
    x, y, z = v
    yaw, elev = math.radians(yaw_deg), math.radians(elev_deg)
    x1 = x * math.cos(yaw) + z * math.sin(yaw)   # yaw around Y
    z1 = -x * math.sin(yaw) + z * math.cos(yaw)
    y2 = y * math.cos(elev) - z1 * math.sin(elev)  # elevation around X
    z2 = y * math.sin(elev) + z1 * math.cos(elev)  # depth (far edges lighter)
    return (x1, y2, z2)
```

- 12 edges = all vertex pairs differing in exactly ONE coordinate (cube_edges helper).
- Half-size 1.6 fits paper (projected x ∈ [−2.26, 2.26], y ∈ [−2.73, 2.73] at 45/45).
- **Expresii XST v0.8 changed the Y convention: `+Y is now UP` (Cartesian /
  SVG-aligned), the same as standard math coordinates. So `y2` from the
  projection is emitted directly — NO negation.** (Pre-v0.8, +Y was DOWN and
  you had to send `-y2`; that flip is gone.) If the cube looks like it's
  "viewed from below", the cause is now a sign error in your own projection,
  not an Expresii flip — re-check `project()` rather than negating Y at emit.

## Degenerate views (animation pitfall — caught by verifier)

At elevation 45°, yaw values that are multiples of 90° (0, 90, 180, 270, 360)
collapse the projection: the cube is seen edge-on, vertices overlap, and only
**6 distinct projected points** remain (looks flat/hexagonal). For a spinning
animation with uniform yaw steps this hits 4 frames per full spin.

**Fix: offset the start yaw by half a step** (e.g. start at 52.5° with 15° steps
instead of 45°), so the uniform sequence never lands on a multiple of 90°.
Verify every frame has exactly 8 distinct rounded endpoints:
```python
eps = set()
for st in strokes:
    eps.add((round(float(st[0].split()[1]), 3), round(float(st[0].split()[2]), 3)))
    eps.add((round(float(st[-1].split()[1]), 3), round(float(st[-1].split()[2]), 3)))
assert len(eps) == 8
```

## Per-edge stroke recipe

- n=30 interpolated frames per edge, straight line, bell pressure `peak*sin(pi*t)**1.2`
  modulated by depth: `p *= 0.75 + 0.25*depth01` (far edges lighter).
- Brush: `B 1.8`, `w 0.35`, `i 0.20`, ink color (38,38,52), peak 0.72.
- Brush-down rule applies per edge: leading lift frame, first frame clamped ≥ 0.02,
  trailing lift frame (see Brush-Down Invariant in SKILL.md).
- 24 frames × 15° = full spin; generate each as its own XST with the yaw baked in.

## Animation pipeline notes

- Each frame is a full cube XST (12 strokes); per frame: `c` clear (own request +
  7s settle) → send XST → render → collect PNG → assemble GIF/MP4 with PIL/ffmpeg.
- Render wedge: on older server builds (v2026.07.26) the render API can sit in
  `rendering` indefinitely even for light strokes — the strokes still apply
  (HTTP 200), only the PNG never comes back. Newer builds (v2026.07.31+) render
  in ~8s. Check `/info` for version before relying on render.
- Recorder oddity seen during debugging: total-frames count can read −1 after a
  failed/large batch; the XST itself was structurally valid (verifier PASS) — the
  −1 is a recorder display state, not necessarily a malformed file. A/B test by
  sending a minimal no-preamble stroke to isolate.
