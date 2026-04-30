"""Kotlin-reference parity for UKF online sliding-window mode.

Compares `UKF.online_process(glucose, ts_sec)` against the Kotlin online
driver (`runOnlineSliding(windowSize=UKF_WINDOW)` in `Main.kt`). At each
chronological t both ports build a fresh UKF over the trailing 36-reading
window and emit the smoothed value at the leading edge.

Tolerance: ≤ 0.5 mg/dL absolute on output (matches the existing batch UKF
parity tolerance — the algorithm is intrinsically stochastic in its sigma
math and Kotlin/Python floating-point ordering can drift sub-mg/dL).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.ukf import UKF

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
KOTLIN_REF = FIXTURES / "kotlin" / "ukf_online.json"

OUTPUT_TOL = 0.5


def _run_python(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return UKF().online_process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_ukf_online_kotlin_parity(fixture_name: str):
    if not KOTLIN_REF.exists():
        pytest.skip(
            "Kotlin online reference fixture not built — run the Kotlin driver first."
        )

    kotlin = json.loads(KOTLIN_REF.read_text())[fixture_name]
    py_smoothed = _run_python(fixture_name)
    n = kotlin["n"]
    assert len(py_smoothed) == n

    ko_smoothed = np.full(n, np.nan)
    for row in kotlin["trace"]:
        ko_smoothed[row["reading_idx"]] = float(row["smoothed_glucose"])

    diff = np.abs(py_smoothed - ko_smoothed)
    max_diff = float(diff.max())
    assert max_diff <= OUTPUT_TOL, (
        f"UKF online parity exceeded tolerance on {fixture_name}: "
        f"max|Δ|={max_diff:.6f} > {OUTPUT_TOL} at idx {int(np.argmax(diff))} "
        f"(py={py_smoothed[int(np.argmax(diff))]:.6f} "
        f"ko={ko_smoothed[int(np.argmax(diff))]:.6f})"
    )
