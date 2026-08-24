# Flower Petal Generation — Session Detail

Learned from the recorded `yellow flower petals-2.XST` file (provided by user on 2026-07-30).

## Recorded XST Analysis

The file `yellow flower petals-2.XST` contains 5 petals + center of a yellow flower.

### Header Parameters (from first petal)

```
T   0.00000       # always 0
w   0.08000       # wetness (dry)
C   4.00000       # contour strength
B   4.00000       # brush size
e   0.00000       # always 0
k   0.00000       # always 0
```

### Color Gradient (Yellow)

9 nodes from tip (node 0) to root (node 8):

| Node | R   | G   | B   | Color |
|------|-----|-----|-----|-------|
| 0    | 253 | 208 | 54  | Bright yellow |
| 1    | 251 | 192 | 37  | Golden yellow |
| 2    | 250 | 168 | 9   | Deep gold |
| 3    | 250 | 159 | 4   | Deep gold (pure) |
| 4    | 250 | 159 | 4   | Deep gold (pure) |
| 5    | 250 | 163 | 4   | Deep gold |
| 6    | 249 | 165 | 13  | Golden yellow |
| 7    | 253 | 199 | 100 | Light yellow |
| 8    | 255 | 255 | 255 | White |

Nodes 3-5 are the purest gold (saturated). Nodes 7-8 are pale/white (root of tuft).

### Petal-by-petal Parameters

| Petal | Angle (approx) | Pitch | Roll | Start pos | End pos | Length |
|-------|---------------|-------|------|-----------|---------|--------|
| 0 | ~0° (right) | 14.34 | −48.94 | (−1.34, −0.03) | (−1.03, 0.02) | ~0.35 |
| 1 | ~72° (down-right) | 11.95 | −47.98 | (−1.31, −0.05) | (−0.93, 0.01) | ~0.40 |
| 2 | ~144° (down-left) | −28.57 | −46.56 | (−1.43, 0.05) | (−1.10, 0.08) | ~0.34 |
| 3 | ~216° (up-left) | 50.78 | −52.02 | (−1.42, −0.08) | (−1.30, −0.31) | ~0.25 |
| 4 | ~288° (up-right) | 11.16 | 40.27 | (−1.51, 0.11) | (−1.74, 0.08) | ~0.24 |

**Key insight:** The actual painted strokes are SHORT (~0.25–0.40 units from base to tip). The initial lifted frame may be much further away — that's the brush moving to position before pressing.

### Pitch/Roll Pattern

Roll stays in a narrow range per half-circle:
- **Bottom half** (petals pointing right/down-left, angles ~0°–170°): **roll ≈ −49 to −47**
- **Top half** (petals pointing up-left/up-right, angles ~180°–360°): **roll ≈ −52 (at 216°) to +40 (at 288°)**

The sign flips because the brush must splay in the opposite direction when the petal is on the opposite side of the center.

Pitch is positive (+14) for the right/down-right petals, negative (−28) for down-left, positive again (+11 to +51) for up-left/up-right petals.

## Petal Building Function

Working Python prototype (raw XST generation, no `send_strokes.py` helper — use the real `build_path_stroke` with `node_colors` when available):

```python
def make_petal(cx, cy, angle_deg, length_3d=1.8, size=3.5, wetness=0.065, 
               tilt_deg=50, color_ramp=None):
    """Build a single petal stroke. angle_deg uses clock→math conversion."""
    rad = math.radians(angle_deg)
    
    # 3D on tilted disk (positive z = away from viewer)
    petal_3d_z = -math.sin(rad) * math.sin(math.radians(tilt_deg))
    petal_3d_x = math.cos(rad)
    petal_3d_y = math.sin(rad) * math.cos(math.radians(tilt_deg))
    foreshorten = math.sqrt(petal_3d_x**2 + petal_3d_y**2)
    
    # Perpendicular offset curve
    dir_2d_x = petal_3d_x / foreshorten
    dir_2d_y = petal_3d_y / foreshorten
    app_length = length_3d * max(0.12, foreshorten)
    
    # Depth-based adjustments (gentle — user found ±35% too strong)
    z_depth = petal_3d_z
    size_adj = size * max(0.4, 1.0 - z_depth * 0.2)
    color_adj = 1.0 - z_depth * 0.15  # max ±15%, away = darker
    
    # Path: pure symmetric sine for even bulge (no 2nd harmonic — causes "sided" look)
    n_path = 20
    path = []
    for i in range(n_path):
        t = i / (n_path - 1)
        dist = 0.05 + t * app_length  # start at 0.05 for centered root
        curve = 0.1 * math.sin(math.pi * t)  # pure sine, zero at both ends
        perp_x = -dir_2d_y * curve
        perp_y = dir_2d_x * curve
        path.append((cx + dist * dir_2d_x + perp_x, cy + dist * dir_2d_y + perp_y))
    
    # Then emit header + b-maker + 160 s frames + trailing lift + b-marker
    # with pressure: fast rise (0→0.7 in 40%), hold, rapid drop in last 15%
    # w/i reissued per frame after first press
```

## Key Pitfalls

1. **Brightness/depth sign inversion is the #1 bug.** Verify by eye: 1 o'clock (upper-right) petal should be darkest (furthest away), 6 o'clock (down) should be brightest (closest). Vision models are unreliable here — trust your own eye. The brightness range is ±15%, not ±35% (user found ±35% too strong).

2. **Clock convention vs math convention.** When the user says "petal at 1 o'clock", convert via `angle = (270 + hour × 30) % 360`. Do NOT use raw math degrees (0=right).

3. **b-markers must be verbatim from a recording.** The 30-value b-string includes binary pressure-sensor calibration data. Copy-paste from a known-good XST; do not try to generate it.

4. **Frame count matters.** The recorded petals use 150-200 s frames. Too few frames (<50) produces choppy strokes. Too many (>300) wastes XST size.

5. **Size must be scaled for foreshortening.** An away petal should not just be darker — it should be SMALLER (brush size reduced by ~20%). This is critical for the 3D illusion.

6. **Pure sine curve, no 2nd harmonic.** The `0.03 * sin(2πt)` term shifts the bulge to one side, making the petal look "sided" — as if the root is not centered. Use `0.1 * sin(πt)` (pure symmetric sine) instead. The user specifically called this out.

7. **Base distance matters for root centerness.** Start the petal path at `dist = 0.05` (very close to the center dot). Using `0.25` leaves a visible gap between petal root and center, making the flower look disconnected.

8. **Double-stroke for symmetric petals.** For the closest petal (6 o'clock), a single center-line stroke with perpendicular curve shifts the bulge to one side. Fix by drawing TWO strokes: one offset left (-1×curve) and one offset right (+1×curve), each with slightly different roll (±5°). Both start at the same base point.
