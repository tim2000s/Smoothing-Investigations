"""Kotlin-reference parity test for AAPS Exponential (online sliding window).

The Python `AapsExponential.process()` API emits, at each chronological index t,
the value the production AAPS dose engine would actually see at decision time t —
i.e. it calls the EMA over the trailing 24-reading window ending at t and takes
the leading-edge (newest) smoothed value. The Kotlin online driver
(`runOnlineSliding(window=EXP_WINDOW)` in `Main.kt`) does the same thing
directly against the upstream Kotlin algorithm.

Tolerance: bit-exact on output. Both ports apply the same `max(round(...), 39.0)`
final step, so they should agree exactly modulo floating-point determinism.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.aaps_exponential import AapsExponential

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
KOTLIN_REF = FIXTURES / "kotlin" / "aaps_exponential_online.json"

OUTPUT_TOL = 0.0


def _run_python(name: str) -> np.ndarray:
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return AapsExponential().process(glucose, ts_sec).smoothed


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_aaps_exponential_kotlin_parity(fixture_name: str):
    if not KOTLIN_REF.exists():
        pytest.skip(
            "Kotlin reference fixture not built — run the Kotlin driver first."
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
        f"AAPS Exp parity exceeded tolerance on {fixture_name}: "
        f"max|Δ|={max_diff:.6f} > {OUTPUT_TOL} at idx {int(np.argmax(diff))} "
        f"(py={py_smoothed[int(np.argmax(diff))]:.6f} "
        f"ko={ko_smoothed[int(np.argmax(diff))]:.6f})"
    )
