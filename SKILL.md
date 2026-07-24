---
name: expresii-brush
description: Drive Expresii Paint brush strokes via its local HTTP API.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [creative, drawing, painting, brush, expresii, art, automation]
    related_skills: [ascii-art, baoyu-article-illustrator, baoyu-comic, concept-diagrams, pixel-art]
    category: creative
---

# Expresii Brush

Send stroke commands to a running [Expresii Paint](https://www.expresii.com/) app so it paints programmatically. The app exposes a local web API on port 9000 (enabled from `Expresii Paint` since v2026.04.30); this skill speaks that API and generates valid stroke command sequences.

## When to Use

- User asks you to draw or paint something in Expresii
- User says "paint a ___" and they have Expresii running
- User wants automated brushstroke generation, calligraphy, ink-wash, or any stroke-based artwork via the Expresii app
- User wants to convert text/curves/SVG into real watercolor-style brush strokes

## Prerequisites

- **Expresii Paint** v2026.04.30 or later running on the same machine (or reachable over the network)
- The **web API service** must be enabled inside Expresii: open Expresii, look in the menu for "Enable Web API" / "Start Stroke Server" (see `StartStrokeServer.gif` in the upstream repo for the exact UI). Default port: **9000**
- Verify the server is up by opening `http://localhost:9000` in a browser — you should see a tiny web interface served by Expresii
- No auth in the protocol — treat the server as local-network trusted

## How to Run

The skill bundles a Python helper at `scripts/send_strokes.py`. The agent constructs an XST-format command string and POSTs it to `http://<host>:9000/confirm-ajax` as a multipart form field named `message`.

The helper does three jobs: check the server is up (`--ping`), send a pre-made `.xst` file, or build a small stroke inline from `--command` / `--stroke` flags. For anything more elaborate, the agent composes the XST text itself in Python or shell, then pipes it to the helper via stdin or a temp file.

```bash
# 1. Is the server up?
python "$SKILL_DIR/scripts/send_strokes.py" --ping
# -> UP  127.0.0.1:9000

# 2. Send a pre-made file
python "$SKILL_DIR/scripts/send_strokes.py" /tmp/painting.xst

# 3. Build a stroke from waypoints
python "$SKILL_DIR/scripts/send_strokes.py" \
    --size 4 --wetness 0.5 --scratch 0.3 \
    --stroke -2.5,-2.8,0.0 -2.4,-2.7,0.15 -2.3,-2.6,0.3

# 4. Send raw XST commands
python "$SKILL_DIR/scripts/send_strokes.py" \
    --command 'c' --command 'B 4' --command 'w 0.5' \
    --command 'i 0.3' \
    --command 's -2.5 -2.8 0.0 -33 -28 0 0.0' \
    --command 's -2.4 -2.7 0.0 -33 -28 0 0.15'
`SKILL_DIR` is the directory this SKILL.md lives in. Resolve it the same way the meme-generation skill does:

```bash
SKILL_DIR=$(dirname "$(find ~/.hermes/skills -path '*/expresii-brush/SKILL.md' 2>/dev/null | head -1)")
```

## Quick Reference

### Stroke file format (.xst)

Plain text, one command per line, `#` for comments, space-separated params. The full spec lives in `references/xst-format.md`; here are the commands you'll use 99% of the time:

| Cmd | Format | Range | What it does |
|-----|--------|-------|--------------|
| `c` | (no params) | — | Clear the canvas |
| `B` | `B <size>` | 1.0–7.0 | Set brush size |
| `w` | `w <wetness>` | 0.01–1.0 | Set brush wetness (water/pigment ratio) |
| `i` | `i <scratch>` | 0.0–1.0 | Set brush scratchiness (dry-brush texture) |
| `l` | `l <node> <R> <G> <B> <A>` | 0–255 | Set color at brush node (9 nodes: 0=tip, 8=root) |
| `s` | `s <x> <y> <z> <tY> <tX> <barrel> <pressure>` | pressure 0–1 | One stroke frame |

**The z-pressure coupling is load-bearing, not decorative.** Empirically derived from sample strokes:

```
z = 0.0625 − 0.125 × pressure
```

- `pressure = 0.0` → `z = +0.0625` (brush fully lifted, no contact)
- `pressure = 0.5` → `z =  0.0000` (tip just touching paper)
- `pressure = 0.75` → `z = −0.03125` (pressed into paper, more pigment deposit)
- `pressure = 1.0` → `z = −0.0625` (max press, max deposit)

Setting `z = 0` with non-zero pressure produces a thin, barely-visible stroke — the brush is at the threshold of contact, not actually pressing in. To get a visible, pigmented line, either commit to the formula above, or use `pressure ≥ 0.7` with `z ≤ −0.025`.

To lift the brush between disconnected strokes, set `pressure = 0` and `z = 0.0625` (which is what the formula gives you for free — no need to compute it).

### Bookend frames (brush up/down events)

Expresii registers a stroke only when the brush transitions **lifted → contact → lifted**. For an **open** stroke (a line, a curve, anything that doesn't loop back to its start), bracket it with two bookend `s` frames:

```text
# brush DOWN (lifted) at the start point
s <x0> <y0> 0.06250 0 0 0 0.00000
# ... real stroke frames at pressure 0.5–0.8 ...
# brush UP (lifted) at the end point
s <x1> <y1> 0.06250 0 0 0 0.00000
```

**Closed loops are different.** For a circle/ring, do NOT add a trailing lift bookend — the loop's last contact frame already meets the first at the seam with pressure on both sides, so it closes continuously. A trailing lift raises the brush exactly at the join and opens a **visible gap**. Use only a leading (brush-down) bookend for closed loops. (This is why `build_circle()` emits a leading bookend and no trailing one; `build_stroke_command()` emits both, for open strokes.)

Without the leading bookend the brush never touches the paper and **the canvas stays blank** even though the POST returns HTTP 200. This is the single most common cause of "I sent it but nothing drew." The helper emits the right bookends automatically — if you hand-write XST, follow the rules above.

### The /confirm-ajax endpoint

```
POST http://<host>:<port>/confirm-ajax
Content-Type: multipart/form-data; boundary=...
--<boundary>
Content-Disposition: form-data; name="message"

<entire XST file contents here>
--<boundary>--
```

The entire XST text is one form field named `message`. The server returns HTTP 200 on success, no body of consequence. No response within ~10s means failure.

### Coordinate system

Expresii uses a normalized 3D coordinate system centered on the canvas. From the upstream spec, the brush tuft base sits in a small range like `x ∈ [-3, 3]`, `y ∈ [-3, 3]`, `z ∈ [-0.5, 0.5]`. Pressures cluster in `[0, 1]`. Tilts in degrees, typically `[-90, 90]`. Don't worry about exact bounds — start with values from the example stroke in the spec and tweak.

## Procedure

1. **Confirm the server is up.** Run `--ping` first. If it's down, tell the user to enable the Web API service in Expresii — do NOT try to start the server yourself.
2. **Decide what to paint.** Read the user's prompt. If they want a specific shape, plan the path: a few waypoints with x, y, and pressure profile (low at the start, peak in the middle, taper at the end).
3. **Choose brush parameters:**
   - `B` (size): start at 4. Bigger = bolder, smaller = finer detail.
   - `w` (wetness): 0.3–0.5 for inky, dry-ish strokes; 0.8–1.0 for watery washes.
   - `i` (scratchiness): 0 for smooth; 0.5+ for dry-brush, textured strokes.
   - **Or use named profiles** — the helper ships the Amami Inker pressure /
     wetness / scratch profiles. See `references/pressure-profiles.md` for the
     full catalog and recipes (e.g. "dry brush, scratchy ends" = Standard
     pressure + Level 1 Driest + Build Up scratch). Use `--pstroke x,y`
     (repeatable) with `--pprofile / --wprofile / --sprofile`.
4. **Multiple strokes in one canvas** — use `build_composite([...])` (Python) or
   the `--composite SPEC.json` CLI flag. The spec is a JSON array of stroke
   descriptors: `{"type":"circle","cy":1.4}` or
   `{"type":"profile","waypoints":[[-1.5,0],[1.5,0]],"pprofile":"Standard",
   "wprofile":"Level 1 — Driest","sprofile":"Build Up"}`. `build_composite()`
   prepends a single `c` (clear) and joins the blocks; each block keeps its own
   bookends (open = lead+trail lift, closed loop = leading lift only).
5. **Set the brush color** (optional). Expresii loads color per brush NODE
   (0=tip .. 8=root). The 9 node colors form a gradient **along the brush tuft**;
   to paint that gradient **across the stroke width** you tilt the brush
   (Tilt-Y in the `s` frames) so the tuft lies sideways. The helper does this
   for you:
   - **Solid color** — `paint("dry_brush_line", color="Vermilion")` or
     `--preset dry_brush_line --color Vermilion`. Also accepts `"r,g,b"`,
     `"#rrggbb"`, or a `COLOR_PROFILES` name (Indigo, Cobalt, SapGreen,
     Vermilion, Cadmium, Ochre, Magenta, PaynesGray, Sepia, Black, White).
     All 9 nodes get the same color.
   - **Tuft gradient** — pass two colors as `"tip:root"` (e.g.
     `"Cobalt:Vermilion"`) or a `COLOR_RAMP_PROFILES` name (WarmToCool,
     CoolToWarm, LightToDark, HueCycle). The helper sets node 0 = tip color,
     node 8 = root color, interpolated between — a gradient across the tuft.
     A gradient **auto-tilts the brush 45°** so the gradient shows across the
     stroke; override with `--tilt DEG` (set 0 for a flat, lengthwise tuft).
   - **Default** is white-to-grey if you pass no color.
6. **Use the stroke library** (optional, saves re-deriving params). The helper
   ships `STROKE_LIBRARY` presets you can call by name:
   `dry_brush_line`, `wet_wash_line`, `calligraphy_curve`, `scratchy_loop`,
   `bold_dot`. Build one with `paint(name, color=...)` (Python) or
   `--preset NAME [--color SPEC]` (CLI). Each preset bundles path + profiles;
   override any field (size, profiles, waypoints) via `paint(name, **overrides)`.
7. **Build the stroke frames.** Each `s` line is one brush posture; consecutive frames with changing x/y/pressure form a continuous stroke. Pressure usually ramps: 0 → peak → 0 over the stroke length. For multiple disconnected strokes, add a frame with pressure 0 (lift) before starting the next.
8. **Send via the helper.** Either write the XST to a temp file and pass it to the helper, or use `--command` / `--stroke` / `--pstroke` / `--preset` / `--circle` / `--composite` flags. Read the result — `OK  sent N chars` on success, `FAIL  no_response` if the server didn't reply.
9. **Iterate.** If the stroke looks wrong (Expresii shows a stroke-recorder window), adjust waypoints, pressure profile, color, or brush params and re-send. Use `c` to clear the canvas between attempts.

## Pitfalls

- **`c` clears the entire canvas.** Only send it when you actually want a clean slate. For multi-stroke paintings, send `c` once at the start, not between strokes.
- **Pressure 0 still draws** if the brush is touching the canvas. To "lift" the brush between disconnected strokes, you usually need a frame with pressure 0 *and* a small z-shift (lift the brush up). The `s` z parameter is your friend.
- **Tilts are in degrees, not radians.** Range typically `[-90, 90]`. A flat brush has tilt (0, 0). A brush held vertical from the side has tilt like (-33, -28) — see the example in the upstream spec.
- **Multipart form, not raw POST.** Sending the XST as the request body with `Content-Type: text/plain` will not work. It must be a `multipart/form-data` form with a field named `message`. The helper handles this; do not roll your own `curl -d @file` shortcut.
- **No auth in the protocol.** Anyone on the local network who can reach port 9000 can drive the brush. If you're on a shared network, treat that as a risk.
- **The server returns 200 on success even for invalid commands.** If your XST is malformed, the canvas just won't change. Verify by checking the stroke-recorder window in Expresii.
- **`--ping` is cheap, use it.** If you skip it and the server is down, you'll burn the 10s timeout on every send.

## Verification

After sending, the helper exits 0 on success and prints `OK  sent N chars to <host>:<port> (HTTP <code>)`. To verify the stroke actually rendered:

- **Authoritative:** look at the Expresii canvas/window directly (or have the user screenshot it).
- **Self-verify (reliable):** pass `--verify OUT.png`. The helper mirrors the official Command Console client: it POSTs `message` (+ `maxWidth`/`maxHeight` placeholder fields, for wire-format parity) to `/confirm-ajax`, then polls `GET /result/<id>` every ~0.9s and saves the frame only once the server reports `status == "done"` (carrying the final `imageBase64`). The reliable result comes from waiting for `done` — that's why the client "always returns the correct image" instead of grabbing a first-served (possibly previous/stale) frame. The captured frame is THIS render. If it still fails, the authoritative check is the Expresii window. Tune with `--verify-retries N` / `--verify-wait S`.
- For automated tests, count the frames in your XST and confirm `sent_chars` matches what you expected to send.

See `EXAMPLES.md` for complete worked examples (single ink wash stroke, calligraphy curve, multi-color flower, clearing the canvas, error recovery).
