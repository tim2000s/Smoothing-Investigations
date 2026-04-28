"""Parity test: AAPS Exponential / TSUNAMI port vs the published
`multi_user/full_analysis.py` reference.

The exponential smoother involves nested EMA loops with arbitrary-window
backward iteration; hand-derivation via sympy is impractical beyond ~3
readings. The plan's intended fallback for that case is bit-exact match
against the published port (which has produced the prior multi-user paper),
plus the orthogonal UKF-style structural guarantees from the smoke test
(rounded integer outputs, lower clamp at 39, etc.).

Tolerance: ≤0.1 mg/dL absolute (per plan); in practice we get bit-exact
match because the published port and our extracted port are the same code
path (different module structure).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.aaps_exponential import AapsExponential

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
PY_REF = FIXTURES / "python_ref" / "aaps_exponential.json"

OUTPUT_TOL = 0.1  # mg/dL per plan


def _run(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return AapsExponential().process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_within_tolerance_vs_published_reference(fixture_name: str):
    expected = np.array(json.loads(PY_REF.read_text())[fixture_name]["output"], dtype="float64")
    actual = _run(fixture_name)
    assert actual.shape == expected.shape
    diff = np.abs(actual - expected)
    assert diff.max() <= OUTPUT_TOL, (
        f"AAPS Exp parity exceeded tolerance on {fixture_name}: "
        f"max|Δ|={diff.max():.6f} > {OUTPUT_TOL} at idx {int(np.argmax(diff))}"
    )


def test_structural_invariants():
    """Output is bounded below at 39 (the explicit floor) and produces the
    same number of readings as input."""
    for fixture_name in ("step", "sinusoid", "real24h"):
        out = _run(fixture_name)
        assert (out >= 39).all(), f"{fixture_name}: output dropped below floor 39"
        # Outputs are rounded by the algorithm — every entry should be an integer.
        assert np.all(out == np.round(out)), f"{fixture_name}: non-integer output"
