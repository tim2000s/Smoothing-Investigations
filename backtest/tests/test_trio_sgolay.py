"""Parity test: Trio Savitzky-Golay port vs the published `multi_user/full_analysis.py`
reference, plus a closed-form first-pass check on the central convolution kernel.

Tolerance: bit-exact (the algorithm is `scipy.signal.savgol_filter` with the
same params, plus integer rounding between passes — both deterministic).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import savgol_filter

from backtest.smoothers.trio_sgolay import TrioSGolay

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
PY_REF = FIXTURES / "python_ref" / "trio_sgolay.json"


def _run(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (np.array(inputs["ts_ms"], dtype="int64") // 1000).astype("int64")
    return TrioSGolay().process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_bit_exact_vs_published_reference(fixture_name: str):
    expected = np.array(json.loads(PY_REF.read_text())[fixture_name]["output"], dtype="float64")
    actual = _run(fixture_name)
    assert actual.shape == expected.shape
    diff = np.abs(actual - expected)
    assert diff.max() == 0.0, (
        f"Trio SG parity broke on {fixture_name}: max|Δ|={diff.max():.6f} "
        f"at idx {int(np.argmax(diff))}"
    )


def test_first_pass_matches_savgol_kernel():
    """The first pass of the algorithm is exactly `savgol_filter(g, 7, 2, mode='interp')`
    with integer rounding. Verify by reconstructing it directly via scipy on a
    central interior region (avoiding the boundary handling that 'interp' applies)."""
    fixture = json.loads(INPUTS.read_text())["step"]
    g = np.array(fixture["glucose"], dtype="float64")

    # First pass output via scipy (matches the algorithm's pass1)
    pass1_full = np.round(savgol_filter(g, window_length=7, polyorder=2, mode="interp"))

    # Run the smoother with instrument=True to read pass1 from the trace
    res = TrioSGolay().process(g, np.arange(len(g)) * 300, instrument=True)
    pass1_trace = res.trace[res.trace.step_name == "pass1"].sort_values("reading_idx")
    pass1_actual = pass1_trace.output_glucose.to_numpy()

    # Compare on the central interior (the kernel is unambiguous there)
    center = slice(10, len(g) - 10)
    np.testing.assert_array_equal(pass1_actual[center], pass1_full[center])
