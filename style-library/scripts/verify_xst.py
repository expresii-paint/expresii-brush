#!/usr/bin/env python3
"""
verify_xst.py — structural verification for generated Expresii XST batches.

Run BEFORE sending a generated batch to the server. Catches the failure
modes that cost real sessions: hand-authored s-frames that drift from the
z-coupling, strokes whose peak pressure never reaches the contact
threshold (silently render nothing), and malformed commands.

Usage:
    python verify_xst.py painting.xst [--flat|--steep] [--strict]

Couplings (z = a - b*p):
    --flat  (default)  z = 0.0625  - 0.125*p   (documented; contact at p = 0.5)
    --steep            z = 0.0875  - 0.4625*p  (later empirical; contact at p ~ 0.19)

Checks:
  1. brush-down invariant: first two `s` frames are consecutive, p 0 -> >0,
     no non-s command between them
  2. every frame: z matches the chosen coupling at its pressure
     (tolerance 1e-4 — values are 5-decimal formatted, so 1e-6 will false-fail)
  3. pressure within [0, 1]
  4. only known commands (T e k B C w i l s b a c)
  5. per-stroke peak pressure vs contact threshold warning — strokes whose
     peak stays below the threshold will NOT deposit pigment

Exit 0 = pass, 1 = structural failure, 2 = threshold warnings only (use --strict
to upgrade warnings to failures).

Column layout of `s`:  s x y z tY tX barrel pressure
                       0 1 2 3 4  5  6      7
"""
import argparse
import re
import sys
from pathlib import Path

COUPLINGS = {
    "flat": (0.0625, 0.125),
    "steep": (0.0875, 0.4625),
}
KNOWN = re.compile(r"^(T|e|k|B|C|w|i|l|s|b|a|c)\s|^#")

S_RE = re.compile(r"^s\s")


def z_of(p, coupling):
    a, b = COUPLINGS[coupling]
    return a - b * p


def contact_threshold(coupling):
    a, b = COUPLINGS[coupling]
    return a / b  # z = 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xst", help="path to the .xst file to verify")
    ap.add_argument("--flat", dest="coupling", action="store_const", const="flat",
                    default="flat", help="documented z coupling (default)")
    ap.add_argument("--steep", dest="coupling", action="store_const", const="steep",
                    help="later empirical z coupling")
    ap.add_argument("--strict", action="store_true",
                    help="treat contact-threshold warnings as failures")
    args = ap.parse_args()

    path = Path(args.xst)
    if not path.exists():
        print(f"FAIL  file not found: {args.xst}")
        sys.exit(1)

    lines = path.read_text(encoding="utf-8").splitlines()
    s_frames = [l for l in lines if S_RE.match(l)]
    errors, warnings = [], []

    if not s_frames:
        print("FAIL  no s frames")
        sys.exit(1)

    # 1. brush-down invariant
    first_s_idx = next(i for i, l in enumerate(lines) if S_RE.match(l))
    if first_s_idx + 1 >= len(lines) or not S_RE.match(lines[first_s_idx + 1]):
        errors.append("brush-down: no consecutive s after lift")
    else:
        p0 = float(lines[first_s_idx].split()[7])
        p1 = float(lines[first_s_idx + 1].split()[7])
        if not (p0 == 0.0 and p1 > 0.0):
            errors.append(f"brush-down: expected p 0->>0, got {p0}->{p1}")

    # 2 & 3. per-frame z coupling + pressure bounds
    for i, l in enumerate(s_frames):
        parts = l.split()
        if len(parts) != 8:
            errors.append(f"frame {i}: expected 8 columns (s x y z tY tX barrel p), got {len(parts)}")
            continue
        p, z = float(parts[7]), float(parts[3])
        if not (0.0 <= p <= 1.0):
            errors.append(f"frame {i}: pressure {p} out of [0,1]")
        if abs(z - z_of(p, args.coupling)) > 1e-4:
            errors.append(f"frame {i}: z={z} != coupling {z_of(p, args.coupling):.5f} at p={p}")

    # 4. known commands
    for l in lines:
        if not l.strip() or l.startswith("#"):
            continue
        if not KNOWN.match(l):
            errors.append(f"unknown command: {l[:48]}")

    # 5. per-stroke peak pressure vs contact threshold
    threshold = contact_threshold(args.coupling)
    strokes = [[]]
    for l in s_frames:
        p = float(l.split()[7])
        if p == 0.0 and strokes[-1]:
            strokes.append([])
        strokes[-1].append(p)
    for i, ps in enumerate(strokes):
        if not ps:
            continue
        peak = max(ps)
        if peak < threshold:
            warnings.append(f"stroke {i}: peak p={peak:.3f} < contact threshold {threshold:.2f} -> will NOT deposit pigment")

    # report
    print(f"frames={len(s_frames)} strokes={sum(1 for s in strokes if s)} "
          f"coupling={args.coupling} contact_threshold={threshold:.3f}")
    for w in warnings:
        print("WARN " + w)
    if errors:
        print("FAIL")
        for e in errors:
            print(" - " + e)
        sys.exit(1)
    if warnings and args.strict:
        print("FAIL  (--strict: threshold warnings upgraded)")
        sys.exit(1)
    print("PASS  (pigment contact still needs by-eye check on canvas)")
    sys.exit(0)


if __name__ == "__main__":
    main()
