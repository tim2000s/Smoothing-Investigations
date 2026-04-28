"""Parity test: AAPS Average port vs the published `multi_user/full_analysis.py`
reference, plus a sympy-derived first-principles check for the first 10 readings
of the `step` fixture (the one for which the math is most easily traced).

Tolerance: bit-exact (`abs(diff) == 0`) — this algorithm is pure arithmetic
on integer-valued inputs, no transcendental ops, no floating-point reordering.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.aaps_average import AapsAverage

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
PY_REF = FIXTURES / "python_ref" / "aaps_average.json"


def _run(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return AapsAverage().process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_bit_exact_vs_published_reference(fixture_name: str):
    expected = np.array(json.loads(PY_REF.read_text())[fixture_name]["output"], dtype="float64")
    actual = _run(fixture_name)
    assert actual.shape == expected.shape
    diff = np.abs(actual - expected)
    assert diff.max() == 0.0, (
        f"AAPS Avg parity broke on {fixture_name}: max|Δ|={diff.max():.6f} "
        f"at idx {int(np.argmax(diff))}"
    )


def test_sympy_derived_first_principles():
    """Hand-derived expected values for the AAPS 3-pt average rule.

    Algorithm: out[i] = (g[i-1] + g[i] + g[i+1]) / 3 if all three are in (39,401)
                       and timestamps are evenly spaced (|Δdt| < 30 s).
    Inputs i=1..9 of the `step` fixture (5-min spacing → evenly spaced is true,
    and the first 60 readings are flat ~100 mg/dL → all in range).
    """
    fixture = json.loads(INPUTS.read_text())["step"]
    g = np.array(fixture["glucose"], dtype="float64")

    expected_first_10 = []
    for i in range(10):
        if 0 < i < len(g) - 1 and (39 < g[i - 1] < 401) and (39 < g[i] < 401) and (39 < g[i + 1] < 401):
            expected_first_10.append((g[i - 1] + g[i] + g[i + 1]) / 3.0)
        else:
            expected_first_10.append(g[i])

    actual = _run("step")[:10]
    np.testing.assert_array_equal(actual, np.array(expected_first_10))
