"""Generate reference outputs for AAPS Avg/Exp/Trio SG by running the
already-published Python ports from `multi_user/full_analysis.py`.

These ports were used to produce the prior multi-user paper. By extracting
their function definitions (without importing the module, which would run its
own analysis on import) and freezing their outputs as JSON fixtures, we get a
real reference fixture for the new ports to be tested against.

Combined with the orthogonal sympy hand-derived checks in the parity tests,
this constitutes the fixture-based validation for the three non-UKF smoothers.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
from scipy.signal import savgol_filter

REPO_ROOT = Path(__file__).resolve().parents[2]
MULTI_USER_SRC = REPO_ROOT / "multi_user" / "full_analysis.py"
INPUTS = Path(__file__).parent / "fixtures" / "inputs.json"
OUT_DIR = Path(__file__).parent / "fixtures" / "python_ref"


def extract_reference_functions() -> dict:
    """Parse multi_user/full_analysis.py and exec only the smoother functions."""
    src = MULTI_USER_SRC.read_text()
    tree = ast.parse(src)
    wanted = {"avg_smooth", "_exp_win", "exp_smooth", "sgf_smooth"}
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ns = {"np": np, "savgol_filter": savgol_filter}
    mod = ast.Module(body=keep, type_ignores=[])
    exec(compile(mod, str(MULTI_USER_SRC), "exec"), ns)
    return {
        "aaps_average": ns["avg_smooth"],
        "aaps_exponential": ns["exp_smooth"],
        "trio_sgolay": ns["sgf_smooth"],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fns = extract_reference_functions()
    inputs = json.loads(INPUTS.read_text())

    for algo, fn in fns.items():
        per_fixture = {}
        for name, fixture in inputs.items():
            ts_ms = np.array(fixture["ts_ms"], dtype="int64")
            glucose = np.array(fixture["glucose"], dtype="float64")
            if algo == "trio_sgolay":
                out = fn(glucose.copy())
            else:
                out = fn(glucose.copy(), ts_ms)
            per_fixture[name] = {
                "n": len(out),
                "output": [float(v) for v in np.asarray(out)],
            }
        out_path = OUT_DIR / f"{algo}.json"
        out_path.write_text(json.dumps(per_fixture, indent=2))
        n_total = sum(v["n"] for v in per_fixture.values())
        print(f"  {algo:18s} → {out_path.name}  ({n_total} readings across {len(per_fixture)} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
