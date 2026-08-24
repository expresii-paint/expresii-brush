# Recorded XST Wire Format (Expresii Stroke File v0.7)

Decoded 2026-07-31 from the user-recorded "green ink cube.XST" (a real cube wireframe
recorded inside Expresii, 884 s-frames, 8+ strokes). This is what the app ITSELF
writes — the authoritative format. Reproduce it for maximum compatibility.

## Header (once per stroke batch)

```
# Expresii Stroke File v0.7
T   0.00000
w   0.65000
C   0.00000
B   2.00000
e   0.00000
k   0.00000
l 0 4 4 4          <- tip node: DARK ink
l 1 189 219 125    <- body color
l 2 246 249 240    <- near white
l 3..7 253 253 253 <- white
l 8 255 255 255    <- root: white
a   0.00000   0.00000
a   1.00000   0.00000
a   2.00000   0.00000
a   3.00000   0.00000
```

- `l <node> <R> <G> <B>` — 9 nodes, tip (0) → root (8). NO alpha column in the recording.
- The recorded gradient is **tip-dark**: node 0 is near-black (4,4,4), everything else fades
  to white. Stroke reads as dark ink at the touch point feathering out to paper.
- Four `a` lines (all zeros) follow the color nodes.
- **No `i` (scratch) line** in the recorded format.
- `C 0.00000` (contour) present — the flower skill documented C 4.0; this recording uses 0.

## Per-stroke body

```
s  x0 y0  0.02187  pitch roll 0.00000 0.00000   <- lifted frame (p=0, z=Z_LIFT)
b  0.01000 0.00000×24 0.01000 0.00000×4         <- brush-down marker (30 values)
s  x0 y0  0.02187  pitch roll 0.00000 0.00000   <- p=0 move frame (optional, mirrors recording)
s  x0 y0  z(p)     pitch roll 0.00000 p         <- geometric PRESS ramp, in place
s  x1 y1  z(0.75)  pitch roll 0.00000 0.75000   <- sweep at max pressure (moves)
s  x1 y1  z(p)     pitch roll 0.00000 p         <- geometric RELEASE ramp, in place
s  x1 y1  0.02187  pitch roll 0.00000 0.00000   <- lifted frame (p=0)
b  0.01000 0.00000×24 0.01000 0.00000×4         <- brush-up marker
```

- The `b` marker string is EXACTLY 30 values: `b   0.01000` + 24×`   0.00000` +
  `   0.01000` + 4×`   0.00000`. Copy verbatim; both brush-down and brush-up use it.
- Brush-down invariant: two consecutive `s` frames p=0 → p>0 with no non-`s` between
  (the lift frame + first pressed frame satisfy it; a p=0 move frame is optional).

## Numbers

- `z = 0.021875 − 0.154167 × pressure` (verified exactly: z = −0.09375 at p = 0.75)
- Pressure caps at **0.75** — the app never writes p > 0.75 in this recording.
- Lift z = +0.021875.
- Press ramp: `p_k = 0.75 × (1 − 0.8^k)`, k = 1.. until p ≥ 0.749.
- Release ramp: `p_k = 0.75 × 0.8^k`, until p < 0.001.
- Recorded tilt: Pitch = 14.0, Roll = −61.0, Turn = 0.0 constant on EVERY frame
  (cube edges). Wash strokes use near-flat tilt (≈ 4, −6) or broad-fan tilt (−55, −25).

## s-frame column order (verification-critical)

`s <x> <y> <z> <tY> <tX> <barrel> <pressure>` — pressure is index **7**, z is index **3**.
Verifiers that read y (idx 2) as z or barrel (idx 6) as pressure produce garbage.

## Layer command

`L <layer>` selects the active layer BEFORE subsequent strokes; `L 0` = topmost,
`L 1` = below it. Emit `L 1` once before the first background/fill stroke group and
`L 0` before the linework group. Verified: mid-XST layer switches render correctly.
