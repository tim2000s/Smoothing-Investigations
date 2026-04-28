"""AAPS Exponential / TSUNAMI smoother (dual-order ES).

Port of `exp_smooth()` at `multi_user/full_analysis.py:79-87` and `_exp_win()` at
`:59-77`, which port `ExponentialSmoothingPlugin.smooth()` from AndroidAPS.

Constants pulled from the original:
- window=24 readings, 12-min gap break, floor 39 mg/dL
- o1: weight=0.4, a=0.5
- o2: a=0.4, b=1.0 (i.e. blend = 0.4*o1 + 0.6*o2)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import SmootherResult

WINDOW = 24
GAP_BREAK_MIN = 12
FLOOR = 39.0
O1_A = 0.5
O2_A = 0.4
O2_B = 1.0
BLEND_O1 = 0.4
BLEND_O2 = 0.6


class AapsExponential:
    name = "aaps_exponential"

    def process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        instrument: bool = False,
    ) -> SmootherResult:
        ts_ms = (ts_sec.astype("int64") * 1000)
        return _exp_smooth(glucose, ts_ms, instrument=instrument)


def _exp_window(vn: list[float], tn: list[float]) -> tuple[list[float], dict]:
    """Returns smoothed window plus optional intermediates for tracing."""
    n = len(vn)
    ws = max(n - 1, 0)
    for i in range(ws):
        if round((tn[i] - tn[i + 1]) / 60000.0) >= GAP_BREAK_MIN:
            ws = i + 1
            break
        if vn[i] == 38.0:
            ws = i
            break
    if ws < 4:
        return [max(v, FLOOR) for v in vn], {
            "window_size": ws, "o1_anchor": np.nan, "o1_final": np.nan,
            "o2_anchor": np.nan, "o2_final": np.nan,
            "o2_d_anchor": np.nan, "o2_d_final": np.nan, "blended": np.nan,
        }

    o1 = [vn[ws - 1]]
    o1_anchor = vn[ws - 1]
    for i in range(ws):
        o1.insert(0, o1[0] + O1_A * (vn[ws - 1 - i] - o1[0]))
    o2 = [vn[ws - 1]]
    od = [vn[ws - 2] - vn[ws - 1]]
    o2_anchor = vn[ws - 1]
    o2_d_anchor = vn[ws - 2] - vn[ws - 1]
    for i in range(ws - 1):
        o2.insert(0, O2_A * vn[ws - 2 - i] + (1.0 - O2_A) * (o2[0] + od[0]))
        od.insert(0, O2_B * (o2[0] - o2[1]) + 0.0 * od[0])
    blended = [BLEND_O1 * o1[j] + BLEND_O2 * o2[j] for j in range(len(o2))]

    res: list[float] = [None] * n  # type: ignore[list-item]
    for i in range(min(len(blended), n)):
        res[i] = max(round(blended[i]), FLOOR)
    for i in range(n):
        if res[i] is None:
            res[i] = max(vn[i], FLOOR)

    intermediates = {
        "window_size": ws,
        "o1_anchor": o1_anchor,
        "o1_final": o1[0] if o1 else np.nan,
        "o2_anchor": o2_anchor,
        "o2_final": o2[0] if o2 else np.nan,
        "o2_d_anchor": o2_d_anchor,
        "o2_d_final": od[0] if od else np.nan,
        "blended": blended[0] if blended else np.nan,
    }
    return res, intermediates


def _exp_smooth(values: np.ndarray, ts_ms: np.ndarray, *, instrument: bool) -> SmootherResult:
    n = len(values)
    out = np.zeros(n, dtype="float64")
    if not instrument:
        for i in range(n):
            s = max(0, i - WINDOW + 1)
            wv = list(reversed(values[s:i + 1].tolist()))
            wt = list(reversed(ts_ms[s:i + 1].tolist()))
            if len(wv) < 2:
                out[i] = max(values[i], FLOOR)
                continue
            res, _ = _exp_window(wv, wt)
            out[i] = res[0]
        return SmootherResult(smoothed=out)

    cols = {
        "reading_idx": np.arange(n, dtype="int64"),
        "ts_sec": (ts_ms // 1000).astype("int64"),
        "input_glucose": values.astype("float64"),
        "step_name": np.full(n, "exp", dtype=object),
        "window_size": np.zeros(n, dtype="int64"),
        "gap_to_prev_min": np.full(n, np.nan),
        "o1_anchor": np.full(n, np.nan),
        "o1_final": np.full(n, np.nan),
        "o2_anchor": np.full(n, np.nan),
        "o2_final": np.full(n, np.nan),
        "o2_d_anchor": np.full(n, np.nan),
        "o2_d_final": np.full(n, np.nan),
        "blended": np.full(n, np.nan),
    }
    for i in range(n):
        s = max(0, i - WINDOW + 1)
        wv = list(reversed(values[s:i + 1].tolist()))
        wt = list(reversed(ts_ms[s:i + 1].tolist()))
        if i > 0:
            cols["gap_to_prev_min"][i] = (ts_ms[i] - ts_ms[i - 1]) / 60000.0
        if len(wv) < 2:
            out[i] = max(values[i], FLOOR)
            continue
        res, ints = _exp_window(wv, wt)
        out[i] = res[0]
        for k, v in ints.items():
            cols[k][i] = v if v is not None else np.nan

    cols["output_glucose"] = out
    trace = pd.DataFrame(cols)
    return SmootherResult(smoothed=out, trace=trace)
