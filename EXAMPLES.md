# Expresii Brush — Examples

Worked examples for the `expresii-brush` skill. Each example is self-contained
and runnable once Expresii's stroke server is enabled on `localhost:9000`.

## 0. Verify the server is up

```bash
python "$SKILL_DIR/scripts/send_strokes.py" --ping
# UP  127.0.0.1:9000
```

If you get `DOWN`, tell the user to open Expresii Paint and enable the Web
API service from the menu. Do not retry forever.

## 1. The canonical example stroke (from the upstream spec)

The upstream `ExpresiiStrokeFileFormatDescription.txt` ships a 7-frame example
stroke. Save it as `example1.xst` and send it:

```bash
cat > /tmp/example1.xst <<'EOF'
# Example stroke from the upstream spec
s  -2.54733  -2.81821   0.03829 -33.00000 -28.00000   0.00000   0.00000
s  -2.41789  -2.73446  -0.00687 -33.00000 -28.00000   0.00000   0.15000
s  -2.39505  -2.72684  -0.04299 -33.00000 -28.00000   0.00000   0.26698
s  -2.38743  -2.72684  -0.07189 -33.00000 -28.00000   0.00000   0.28944
s  -2.37982  -2.72684  -0.09502 -33.00000 -28.00000   0.00000   0.31473
s  -2.37220  -2.71923  -0.11351 -33.00000 -28.00000   0.00000   0.35283
s  -2.35698  -2.71923  -0.12831 -33.00000 -28.00000   0.00000   0.39865
EOF

python "$SKILL_DIR/scripts/send_strokes.py" /tmp/example1.xst
# OK  sent 707 chars to 127.0.0.1:9000 (HTTP 200)
```

This is the smallest possible "real" stroke — a tiny pressure ramp from 0 to ~0.4
over 7 frames. Useful for verifying the end-to-end pipeline works.

## 2. Build a stroke inline with `--stroke`

A 5-frame pressure ramp from 0 → 0.5 → 1.0 → 0.5 → 0, drawn left-to-right at
`y = -2.7`, brush size 3, wetness 0.4 (drier, more control):

```bash
python "$SKILL_DIR/scripts/send_strokes.py" \
    --host 127.0.0.1 --port 9000 \
    --size 3 --wetness 0.5 --scratch 0.2 \
    --stroke=-1.0,-1.0,0.0 \
    --stroke=-0.5,-1.0,0.5 \
    --stroke=0.0,-1.0,0.0
```

The helper auto-generates the `B`, `w`, `i` setup lines, then emits one `s`
frame per `--stroke` flag.

## 3. Raw commands with `--command`

When you need the full setup (clear, colors, multi-stroke composition), use
`--command` to pass raw XST lines:

```bash
python "$SKILL_DIR/scripts/send_strokes.py" \
    --command 'c' \
    --command 'B 5' \
    --command 'w 0.7' \
    --command 'i 0.0' \
    --command 'l 0 78 150 220 255' \
    --command 'l 4 240 68 139 255' \
    --command 'l 8 255 255 255 255' \
    --command 's -2.5 -2.5 0.0 -33 -28 0 0.0' \
    --command 's -2.3 -2.5 0.0 -33 -28 0 0.5' \
    --command 's -2.1 -2.5 0.0 -33 -28 0 0.0'
```

This is the standard "in-process" workflow: write the full XST as a Python
list, join with newlines, send.

## 4. Multi-stroke composition (clear → two strokes)

The z-pressure coupling `z = 0.0625 − 0.125 × pressure` (see `references/xst-format.md`)
is load-bearing: setting `z = 0` with non-zero pressure produces a thin, almost-invisible
stroke. Either commit to the formula or use `pressure = 0` (which auto-gives `z = 0.0625`)
as the lift.

```bash
cat > /tmp/two_strokes.xst <<'EOF'
# Two disconnected strokes, with z coupled to pressure
c
B 4
w 0.5
i 0.0
# Stroke 1: a horizontal line at y = -1, pressure 0 -> 0.5 -> 0
s -2.0 -1.0  0.00000 0 0 0 0.50000
s -1.5 -1.0 -0.03125 0 0 0 0.75000
s -1.0 -1.0  0.00000 0 0 0 0.50000
s -0.5 -1.0 -0.03125 0 0 0 0.75000
s  0.0 -1.0  0.00000 0 0 0 0.50000
# Lift the brush: pressure 0 -> z = 0.0625 (auto)
s  0.0 -1.0  0.06250 0 0 0 0.00000
# Stroke 2: a horizontal line at y = 1
s -2.0  1.0  0.00000 0 0 0 0.50000
s -1.5  1.0 -0.03125 0 0 0 0.75000
s -1.0  1.0  0.00000 0 0 0 0.50000
s -0.5  1.0 -0.03125 0 0 0 0.75000
s  0.0  1.0  0.00000 0 0 0 0.50000
EOF

python "$SKILL_DIR/scripts/send_strokes.py" /tmp/two_strokes.xst
```

The `z 0.0625, pressure 0` lift frame between strokes is what tells Expresii the brush is
in the air, so it doesn't draw a connecting line from stroke 1 to stroke 2.

## 5. Programmatic generation (Python)

For anything non-trivial, generate the XST in Python and call the helper
with a temp file:

```python
import subprocess, tempfile, pathlib

frames = []
# a spiral-ish curve with pressure ramping up then back down
for i in range(40):
    t = i / 39.0
    angle = t * 6.28           # 2*pi
    r = 0.5 + t * 1.0          # growing radius
    x = r * (angle ** 0.5) * 0.3
    y = r * (angle ** 0.5) * 0.3
    p = 0.5 * (1 - abs(2*t - 1))  # triangle: 0, 0.5, 0
    frames.append(f"s {x:.5f} {y:.5f} 0.0 -33 -28 0 {p:.5f}")

xst = "\n".join([
    "# Spiral",
    "c",
    "B 3",
    "w 0.4",
    "i 0.2",
    *frames,
]) + "\n"

with tempfile.NamedTemporaryFile("w", suffix=".xst", delete=False) as f:
    f.write(xst)
    xst_path = f.name

subprocess.run(["python", "<SKILL_DIR>/scripts/send_strokes.py", xst_path], check=True)
```

## 6. Error recovery

```bash
# Server down
python "$SKILL_DIR/scripts/send_strokes.py" --ping
# DOWN  127.0.0.1:9000
# (exit 1)

# Now try to send anyway — you'll burn the 10s timeout
python "$SKILL_DIR/scripts/send_strokes.py" /tmp/example1.xst
# FAIL  no_response
# (exit 3)

# Fix: tell the user to enable the Web API service in Expresii, then re-ping.
```

## 7. Machine-readable output

For agents piping the result into other tools:

```bash
python "$SKILL_DIR/scripts/send_strokes.py" --ping --json
# {"ok": true, "host": "127.0.0.1", "port": 9000}

python "$SKILL_DIR/scripts/send_strokes.py" /tmp/example1.xst --json
# {"ok": true, "status": 200, "sent_chars": 707, "host": "127.0.0.1", "port": 9000, "response": "..."}
```
