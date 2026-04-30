"""Kotlin-reference parity for AAPS Average online sliding window.

Compares `AapsAverage.online_process(glucose, ts_sec)` against the Kotlin
online driver for `AvgSmoothingPlugin`. AAPS Avg never sets `.smoothed` on
the newest reading, so the production dose engine falls back to the raw
value: every leading-edge output equals the raw input.

Tolerance: bit-exact (== 0.0).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.aaps_average import AapsAverage

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
KOTLIN_REF = FIXTURES / "kotlin" / "aaps_average_online.json"


def _run_python(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return AapsAverage().online_process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_aaps_average_online_kotlin_parity(fixture_name: str):
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
    assert max_diff == 0.0, (
        f"AAPS Avg online parity broke on {fixture_name}: "
        f"max|Δ|={max_diff:.6f} at idx {int(np.argmax(diff))}"
    )
