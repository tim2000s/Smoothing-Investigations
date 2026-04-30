"""Kotlin-reference parity test for AAPS Average.

Compares the Python port against the standalone Kotlin compile of
`AvgSmoothingPlugin.kt` running on three fixture inputs (synthetic step,
sinusoid, real 24-hour cohort slice). Tolerances:
  - bit-exact (max |Δ| == 0.0) on the smoothed output

Pre-run the Kotlin driver to refresh the reference fixture:
  cd backtest/reference/kotlin_driver
  PATH=/opt/homebrew/opt/openjdk@21/bin:$PATH gradle --no-daemon -q run \
    --args="../../tests/fixtures/inputs.json ../../tests/fixtures/kotlin/"
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.aaps_average import AapsAverage

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
KOTLIN_REF = FIXTURES / "kotlin" / "aaps_average.json"


def _load_kotlin(name: str):
    return json.loads(KOTLIN_REF.read_text())[name]


def _run_python(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return AapsAverage().process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_aaps_average_kotlin_parity(fixture_name: str):
    if not KOTLIN_REF.exists():
        pytest.skip(
            "Kotlin reference fixture not built — run the Kotlin driver first."
        )

    kotlin = _load_kotlin(fixture_name)
    py_smoothed = _run_python(fixture_name)
    n = kotlin["n"]
    assert len(py_smoothed) == n, f"length mismatch: py={len(py_smoothed)} kotlin={n}"

    # Build kotlin-smoothed array indexed by reading_idx (chronological).
    ko_smoothed = np.full(n, np.nan)
    for row in kotlin["trace"]:
        ko_smoothed[row["reading_idx"]] = float(row["smoothed_glucose"])
    # Production behaviour: AAPS Avg leaves data[0] (newest, AAPS convention) and
    # data[lastIndex] (oldest) UNSMOOTHED. In Kotlin convention, those are reading_idx
    # n-1 and 0 respectively (chronological). Our Python port matches.
    # Kotlin emits 0.0 for unsmoothed slots when they were never assigned. We
    # treat 0.0 as "not smoothed" and replace with raw input for comparison.
    for row in kotlin["trace"]:
        if row["smoothed_glucose"] == 0.0:
            ko_smoothed[row["reading_idx"]] = float(row["input_glucose"])

    diff = np.abs(py_smoothed - ko_smoothed)
    max_diff = float(diff.max())
    assert max_diff == 0.0, (
        f"AAPS Average parity broke on {fixture_name}: "
        f"max|Δ|={max_diff:.6f} at idx {int(np.argmax(diff))} "
        f"(py={py_smoothed[int(np.argmax(diff))]:.6f} "
        f"ko={ko_smoothed[int(np.argmax(diff))]:.6f})"
    )
