"""Trio Savitzky-Golay smoother (7-pt × 3 passes, integer round between).

Port of `sgf_smooth()` at `multi_user/full_analysis.py:89-97`, citing
`SavitzkyGolayFilter.swift` from FreeAPS X / Trio.

Trace emits one row per pass — `step_name ∈ {pass1, pass2, pass3}`.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

from .base import SmootherResult

WINDOW = 7
POLYORDER = 2
N_PASSES = 3
FLOOR = 39.0


class TrioSGolay:
    name = "trio_sgolay"

    def process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        instrument: bool = False,
    ) -> SmootherResult:
        return _sgf_smooth(glucose, ts_sec, instrument=instrument)


def _sgf_smooth(values: np.ndarray, ts_sec: np.ndarray, *, instrument: bool) -> SmootherResult:
    n = len(values)
    base = values.astype("float64").copy()
    if n < WINDOW:
        return SmootherResult(smoothed=base)

    if not instrument:
        out = base.copy()
        try:
            for _ in range(N_PASSES):
                out = savgol_filter(out, window_length=WINDOW, polyorder=POLYORDER, mode="interp")
                out = np.round(out)
            return SmootherResult(smoothed=np.maximum(out, FLOOR))
        except Exception:
            return SmootherResult(smoothed=base.copy())

    out = base.copy()
    rows = []
    try:
        prev = base.copy()
        for p in range(N_PASSES):
            pre = savgol_filter(prev, window_length=WINDOW, polyorder=POLYORDER, mode="interp")
            rounded = np.round(pre)
            post = np.maximum(rounded, FLOOR) if p == N_PASSES - 1 else rounded
            for i in range(n):
                lo = max(0, i - WINDOW // 2)
                hi = min(n, i + WINDOW // 2 + 1)
                rows.append({
                    "reading_idx": i,
                    "ts_sec": int(ts_sec[i]),
                    "input_glucose": float(prev[i]),
                    "step_name": f"pass{p + 1}",
                    "pass_idx": p,
                    "window_values_json": json.dumps(prev[lo:hi].tolist()),
                    "weighted_sum_num": float(pre[i]),
                    "pre_clamp": float(rounded[i]),
                    "post_clamp": float(post[i]),
                    "output_glucose": float(post[i]),
                })
            prev = post
        out = prev
    except Exception:
        return SmootherResult(smoothed=base.copy())

    return SmootherResult(smoothed=out, trace=pd.DataFrame(rows))
