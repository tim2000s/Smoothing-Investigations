"""AAPS 3-point average smoother.

Verbatim port of `avg_smooth()` at `multi_user/full_analysis.py:49-57`, which is
itself a port of `AvgSmoothingPlugin.kt` from AndroidAPS.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import SmootherResult


class AapsAverage:
    name = "aaps_average"

    def process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        instrument: bool = False,
    ) -> SmootherResult:
        ts_ms = (ts_sec.astype("int64") * 1000)
        return _avg_smooth(glucose, ts_ms, instrument=instrument)


def _avg_smooth(values: np.ndarray, ts_ms: np.ndarray, *, instrument: bool) -> SmootherResult:
    n = len(values)
    out = values.astype("float64").copy()
    if not instrument:
        if n < 5:
            return SmootherResult(smoothed=out)
        for i in range(1, n - 1):
            v0, v1, v2 = values[i - 1], values[i], values[i + 1]
            if (39 < v0 < 401) and (39 < v1 < 401) and (39 < v2 < 401):
                if abs((ts_ms[i] - ts_ms[i - 1]) - (ts_ms[i + 1] - ts_ms[i])) < 30_000:
                    out[i] = (v0 + v1 + v2) / 3.0
        return SmootherResult(smoothed=out)

    rows = {
        "reading_idx": np.arange(n, dtype="int64"),
        "ts_sec": (ts_ms // 1000).astype("int64"),
        "input_glucose": values.astype("float64"),
        "step_name": np.full(n, "avg", dtype=object),
        "prev_g": np.full(n, np.nan),
        "curr_g": values.astype("float64"),
        "next_g": np.full(n, np.nan),
        "dt_prev_s": np.full(n, np.nan),
        "dt_next_s": np.full(n, np.nan),
        "in_range": np.zeros(n, dtype=bool),
        "evenly_spaced": np.zeros(n, dtype=bool),
        "window_avg": np.full(n, np.nan),
    }
    if n >= 5:
        for i in range(1, n - 1):
            v0, v1, v2 = values[i - 1], values[i], values[i + 1]
            in_r = (39 < v0 < 401) and (39 < v1 < 401) and (39 < v2 < 401)
            even = abs((ts_ms[i] - ts_ms[i - 1]) - (ts_ms[i + 1] - ts_ms[i])) < 30_000
            rows["prev_g"][i] = v0
            rows["next_g"][i] = v2
            rows["dt_prev_s"][i] = (ts_ms[i] - ts_ms[i - 1]) / 1000.0
            rows["dt_next_s"][i] = (ts_ms[i + 1] - ts_ms[i]) / 1000.0
            rows["in_range"][i] = in_r
            rows["evenly_spaced"][i] = even
            if in_r and even:
                w = (v0 + v1 + v2) / 3.0
                rows["window_avg"][i] = w
                out[i] = w

    rows["output_glucose"] = out
    trace = pd.DataFrame(rows)
    return SmootherResult(smoothed=out, trace=trace)
