"""Generate the three fixture input series shared by all parity drivers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from backtest import cohort, io


def synthetic_step(seed: int = 0, n: int = 200, dt_s: int = 300) -> dict:
    """Flat 100 → step to 200 → flat → small drift, with light noise + 1 spike."""
    rng = np.random.default_rng(seed)
    g = np.concatenate([
        np.full(60, 100.0),
        np.linspace(100, 200, 30),
        np.full(60, 200.0),
        np.linspace(200, 160, 30),
        np.full(20, 160.0),
    ])[:n]
    g = g + rng.normal(0, 2, n)
    g[150] += 60  # outlier
    g = np.round(g).astype("float64")
    ts_ms = (np.arange(n) * dt_s * 1000).astype("int64")
    return {"name": "step", "n": int(n), "ts_ms": ts_ms.tolist(), "glucose": g.tolist()}


def synthetic_sinusoid(seed: int = 1, n: int = 200, dt_s: int = 300) -> dict:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    g = 130 + 35 * np.sin(2 * np.pi * t / 60.0) + rng.normal(0, 4, n)
    g = np.round(g).astype("float64")
    ts_ms = (t * dt_s * 1000).astype("int64")
    return {"name": "sinusoid", "n": int(n), "ts_ms": ts_ms.tolist(), "glucose": g.tolist()}


def real_24h(table: str = "oref_v6", user_id: str = "U029") -> dict:
    us = io.load_user(table, user_id, days=2)
    mask = np.isfinite(us.glucose)
    g = us.glucose[mask][:288]
    ts_sec = us.ts_sec[mask][:288]
    return {
        "name": f"real24h_{table}_{user_id}",
        "n": int(len(g)),
        "ts_ms": (ts_sec.astype("int64") * 1000).tolist(),
        "glucose": g.tolist(),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="backtest/tests/fixtures/inputs.json")
    args = p.parse_args()

    fixtures = {
        "step": synthetic_step(),
        "sinusoid": synthetic_sinusoid(),
        "real24h": real_24h(),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixtures, indent=2))
    print(f"Wrote {sum(f['n'] for f in fixtures.values())} readings across "
          f"{len(fixtures)} fixtures → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
