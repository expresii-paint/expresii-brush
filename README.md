# expresii-brush

Hermes skill for driving [Expresii Paint](https://www.expresii.com/) through its
local stroke HTTP API — paint brush strokes, vary wetness / pressure / scratch
from the Amami Inker profiles, and compose several strokes on one canvas.

## Requirements

- [Expresii Paint](https://www.expresii.com/) running with the **Web API service
  enabled** (listens on port 9000 by default).
- Python 3.11+.

## Install (Hermes)

Copy or symlink this folder into your skills directory, e.g.
`~/.hermes/skills/expresii-brush/`.

## Use

See [SKILL.md](SKILL.md) for the full agent-facing reference — commands, the XST
stroke format, the pressure / wetness / scratch profile catalog, and how
verification works.

Quick reachability check from the CLI:

```bash
python scripts/send_strokes.py --host 127.0.0.1 --port 9000 --ping
```
