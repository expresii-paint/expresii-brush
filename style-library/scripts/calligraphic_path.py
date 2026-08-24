"""calligraphic_path — deform a stroke centerline to mimic a calligrapher's hand.

Verified implementation (2026-08-03, Huang Tingjian 體勢外張 for 天道酬勤).
Import:  from calligraphic_path import calligraphic_path
Self-test:  python calligraphic_path.py   (runs the mock geometry checks)

KEY POINTS (see references/calligraphic-paths.md):
  * pts are WORLD-space (already through the generator's wpt()). Deform AFTER wpt.
  * `extend` gate uses per-stroke MAX radius vs char_rad: interior/enclosed
    strokes -> extend~0 (square parts stay 方, only 收窄); peripheral -> extend~1
    (full 體勢外張 radiation + tip lift).
  * `tighten` (中宮收緊) is ALSO gated by `extend`, else it drags boxes off-square.
"""
import math
import random as _r
import numpy as np


def calligraphic_path(pts, centroid, amount, seed, mode="huang", char_rad=1.0):
    n = len(pts)
    if n < 3 or amount <= 0:
        return [tuple(p) for p in pts]
    cx, cy = centroid
    rng = _r.Random(seed)
    rs = [float(np.hypot(x - cx, y - cy)) for (x, y) in pts]
    rmax = max(rs) if max(rs) > 1e-9 else 1.0
    # per-stroke "interior-ness": gate by the stroke's MAX radius (does it reach
    # far out?). A short stroke with a far-reaching tip still counts as peripheral;
    # a fully enclosed stroke (small max radius) is interior -> extend~0 (no radiation).
    rmax_stroke = rmax
    norm = max(char_rad, 1e-6)
    extend = max(0.0, min(1.0, (rmax_stroke / norm - 0.30) / 0.45))
    # stroke's own centroid (for 收窄 of enclosed parts — keeps 方 but tighter)
    scx = sum(x for x, _ in pts) / n
    scy = sum(y for _, y in pts) / n
    narrow_k = 0.5 * amount * (1.0 - extend)   # only interior strokes narrow
    rmid = rmax * 0.45
    tight_k = 0.9 * amount
    sigma = rmax * 0.35 + 1e-6
    out = []
    for i, (x, y) in enumerate(pts):
        vx, vy = x - cx, y - cy
        r = rs[i]
        if r < 1e-9:
            # exactly at center: just apply 收窄 toward own centroid
            out.append((x + (scx - x) * narrow_k, y + (scy - y) * narrow_k))
            continue
        ux, uy = vx / r, vy / r             # radial unit vector (outward = +)
        # 中宮收緊: inward pull toward the character center, gated by `extend` so
        # enclosed/square strokes (extend~0) keep ONLY their 收窄 (toward own
        # centroid) and stay 方 — they are NOT dragged off-square toward center.
        w = np.exp(-((r - rmid) ** 2) / (2 * sigma ** 2))
        tin = tight_k * w * extend
        # endpoint emphasis (0 at start -> 1 at tip)
        end_w = (i / (n - 1)) ** 2
        if mode == "huang":
            # 體勢外張 gated by `extend`: only peripheral strokes radiate + lift.
            rad_k = 1.2 * amount * extend
            rout = rad_k * (0.30 + 0.70 * end_w) * (0.5 + 0.5 * r / rmax)
            dx = -ux * tin + ux * rout
            dy = -uy * tin + uy * rout - 0.5 * amount * extend * end_w   # tips lift up
        else:  # classic
            rad_k = 1.1 * amount * extend
            rout = rad_k * (0.35 + 0.65 * end_w) * (r / rmax)
            dx = -ux * tin + ux * rout
            dy = -uy * tin + uy * rout
        # 收窄: enclosed strokes pull toward their own centroid (stay 方, get tighter)
        dx += (scx - x) * narrow_k
        dy += (scy - y) * narrow_k
        dx += rng.uniform(-1, 1) * 0.004 * amount
        dy += rng.uniform(-1, 1) * 0.004 * amount
        out.append((x + dx, y + dy))
    return out


def _aspect(pts):
    xs = [x for x, y in pts]; ys = [y for x, y in pts]
    return (max(xs) - min(xs)) / (max(ys) - min(ys))


def _extent(pts):
    xs = [x for x, y in pts]; ys = [y for x, y in pts]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


if __name__ == "__main__":
    cent = (0.0, 0.0); char_rad = 0.6
    interior = [(0.05, 0.0), (0.10, 0.0), (0.15, 0.0), (0.15, 0.05),
                (0.15, 0.10), (0.10, 0.10), (0.05, 0.10), (0.05, 0.05)]
    periph = [(0.0, 0.0), (0.12, 0.0), (0.25, 0.0), (0.38, 0.0), (0.5, 0.0)]
    oi = calligraphic_path(interior, cent, 0.1, 1, char_rad=char_rad)
    op = calligraphic_path(periph, cent, 0.1, 1, char_rad=char_rad)
    ok = True
    if not (op[-1][0] > periph[-1][0]):
        print("FAIL: peripheral tip did not radiate outward"); ok = False
    if abs(_aspect(oi) - _aspect(interior)) > 0.15:
        print("FAIL: interior box not square"); ok = False
    if not (_extent(oi) < _extent(interior)):
        print("FAIL: interior did not narrow"); ok = False
    if calligraphic_path(interior, cent, 0.0, 1, char_rad=char_rad) != interior:
        print("FAIL: amount=0 not pass-through"); ok = False
    print(("PASS" if ok else "FAIL"),
          "| periph tip %.3f->%.3f" % (periph[-1][0], op[-1][0]),
          "| interior aspect %.2f->%.2f" % (_aspect(interior), _aspect(oi)),
          "| ext %.3f->%.3f" % (_extent(interior), _extent(oi)))
