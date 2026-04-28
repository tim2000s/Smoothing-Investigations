"""UKF parity test: Python port vs Kotlin reference output.

Tolerances per the plan:
- output_glucose ≤ 0.5 mg/dL absolute
- covariance entries (P_pred_gg, P_pred_rr, P_upd_gg, P_upd_rr) ≤ 1e-3
- chi2 and innov ≤ 1e-2
- is_outlier exact match
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backtest.smoothers.ukf import UKF

FIXTURES = Path(__file__).parent / "fixtures"
INPUTS = FIXTURES / "inputs.json"
KOTLIN_REF = FIXTURES / "kotlin" / "ukf.json"

OUTPUT_TOL = 0.5         # mg/dL
COVARIANCE_TOL = 1e-3    # variance units
CHI2_INNOV_TOL = 1e-2

CMP_FIELDS = {
    "x_pred_g": OUTPUT_TOL,
    "x_pred_r": OUTPUT_TOL,
    "P_pred_gg": COVARIANCE_TOL,
    "P_pred_rr": COVARIANCE_TOL,
    "P_pred_gr": COVARIANCE_TOL,
    "innov": CHI2_INNOV_TOL,
    "innov_var": COVARIANCE_TOL,
    "chi2": CHI2_INNOV_TOL,
    "R_live": COVARIANCE_TOL,
    "R_eff": COVARIANCE_TOL,
    "K_g": COVARIANCE_TOL,
    "K_r": COVARIANCE_TOL,
    "x_upd_g": OUTPUT_TOL,
    "x_upd_r": OUTPUT_TOL,
    "P_upd_gg": COVARIANCE_TOL,
    "P_upd_rr": COVARIANCE_TOL,
    "R_after": COVARIANCE_TOL,
    "output_glucose": OUTPUT_TOL,
}


def _load_kotlin_for(name: str):
    return json.loads(KOTLIN_REF.read_text())[name]


def _run_python(name: str):
    inputs = json.loads(INPUTS.read_text())[name]
    ts_ms = np.array(inputs["ts_ms"], dtype="int64")
    glucose = np.array(inputs["glucose"], dtype="float64")
    ts_sec = (ts_ms // 1000).astype("int64")
    return UKF().process(glucose, ts_sec, instrument=True)


@pytest.mark.parametrize("fixture_name", ["step", "sinusoid", "real24h"])
def test_ukf_parity(fixture_name: str):
    if not KOTLIN_REF.exists():
        pytest.skip(
            "Kotlin reference fixture not built — "
            "run `gradle -p backtest/reference/kotlin_driver run "
            "--args='../../tests/fixtures/inputs.json ../../tests/fixtures/kotlin/'`"
        )

    kotlin = _load_kotlin_for(fixture_name)
    py_result = _run_python(fixture_name)

    py_trace = py_result.trace.set_index("reading_idx")
    kotlin_trace = {row["reading_idx"]: row for row in kotlin["trace"]}

    assert len(py_trace) == kotlin["n_trace"], (
        f"Trace row count mismatch on {fixture_name}: "
        f"py={len(py_trace)} kotlin={kotlin['n_trace']}"
    )

    breaches: list[tuple[int, str, float, float, float]] = []
    outlier_mismatch = 0

    for r_idx, kotlin_row in kotlin_trace.items():
        if r_idx not in py_trace.index:
            continue
        py_row = py_trace.loc[r_idx]
        if py_row.get("is_outlier") != kotlin_row["is_outlier"]:
            outlier_mismatch += 1
        for field, tol in CMP_FIELDS.items():
            py_v = float(py_row[field])
            ko_v = float(kotlin_row[field])
            if np.isnan(py_v) and np.isnan(ko_v):
                continue
            diff = abs(py_v - ko_v)
            if not np.isfinite(diff) or diff > tol:
                breaches.append((r_idx, field, py_v, ko_v, diff))

    if breaches or outlier_mismatch:
        msg_lines = [f"Parity breaches in {fixture_name}:"]
        for r_idx, field, py_v, ko_v, diff in breaches[:20]:
            tol = CMP_FIELDS[field]
            msg_lines.append(
                f"  idx={r_idx} {field}: py={py_v:.6g} kotlin={ko_v:.6g} "
                f"diff={diff:.3g} tol={tol:g}"
            )
        if len(breaches) > 20:
            msg_lines.append(f"  ... and {len(breaches) - 20} more")
        msg_lines.append(f"  is_outlier mismatches: {outlier_mismatch}")
        raise AssertionError("\n".join(msg_lines))
