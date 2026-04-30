"""AAPS 3-point central-window smoother — Python port of AvgSmoothingPlugin.kt.

Bit-for-bit equivalent to the upstream production Kotlin algorithm at
`backtest/reference/AvgSmoothingPlugin.kt`. The smoothed value at each interior
position i is `(prev + cur + next) / 3` where the three samples are the
chronological neighbours of i, and the gating conditions are:
  - all three values strictly between 39 and 401 mg/dL
  - timestamps evenly spaced within ±30 s

The newest and oldest readings in any call are NOT smoothed — they retain
their raw values. Production AAPS is called with a newest-first list and the
smoothed value for the newest reading (data[0]) is left unset; the dose engine
reads the raw value for the current reading. We mirror that behaviour: the
`process()` API takes a chronological array and returns a chronological array,
with the newest (last) and oldest (first) entries equal to their raw values.

`process()` is the natural batch operation. `online_process()` emits, for each
chronological time t, the smoothed value the production loop would actually see
at decision time t — which is `process(data[:t+1])[t]`. For AAPS Average this
means the leading edge is always the raw value (the algorithm does not smooth
the newest reading); the previously-newest reading t-1 becomes smoothed once t
arrives, and that smoothed value is then stable. So in online mode the loop's
"current reading" is raw, and 5-min-ago and earlier are smoothed.
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
        """Batch mode: 3-point central-window smoothing over the entire array.

        Returns a chronological array where the newest and oldest entries equal
        their raw values and each interior position is averaged with its
        immediate neighbours when in-range and evenly spaced.
        """
        ts_ms = (ts_sec.astype("int64") * 1000)
        return _avg_smooth(glucose, ts_ms, instrument=instrument)

    def online_process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        instrument: bool = False,
    ) -> SmootherResult:
        """Production-realistic online mode: leading-edge value at each
        chronological time t.

        AAPS Average's `smooth()` loop never touches the newest reading
        (`data[0]` in newest-first convention). Production AAPS treats the
        unset `.smoothed` as a fallback to `.value`, so the value the dose
        engine reads for "the current reading" at decision time t is always
        the RAW glucose at t. Thus the online leading-edge output equals
        the raw input at every t.

        AAPS Average DOES retroactively smooth recent past readings (t-1
        gets the 3-point average once t arrives), but that affects what
        the AID sees when looking BACK in history — not the current
        reading. For "current value" decisions this smoother is a no-op.
        """
        n = len(glucose)
        g = glucose.astype("float64")
        ts_ms = (ts_sec.astype("int64") * 1000)
        # Leading-edge = raw; floor at 39 to mirror Kotlin's max(value, 39).
        out = np.maximum(g, 39.0)

        if not instrument:
            return SmootherResult(smoothed=out)

        rows = {
            "reading_idx": np.arange(n, dtype="int64"),
            "ts_sec": (ts_ms // 1000).astype("int64"),
            "input_glucose": g,
            "step_name": np.full(n, "avg_online", dtype=object),
            "output_glucose": out,
        }
        return SmootherResult(smoothed=out, trace=pd.DataFrame(rows))


def _avg_smooth(values: np.ndarray, ts_ms: np.ndarray, *,
                instrument: bool, online: bool = False) -> SmootherResult:
    n = len(values)
    out = values.astype("float64").copy()  # default: raw value
    smoothed_flag = np.zeros(n, dtype=bool)
    in_range_flag = np.zeros(n, dtype=bool)
    evenly_spaced_flag = np.zeros(n, dtype=bool)

    if n >= 5:
        # Mirror the AAPS production loop: smooth every interior position
        # except the very newest. In chronological array convention, the
        # newest is index n-1; the production "newest" matches data[0] in
        # newest-first AAPS lists (which is left unsmoothed). So we smooth
        # i in [1, n-2] inclusive.
        upper = n - 1 if not online else n - 1  # online: same range; the leading edge is at n-1 and stays raw
        for i in range(1, upper):
            v0, v1, v2 = values[i - 1], values[i], values[i + 1]
            in_range = (39 < v0 < 401) and (39 < v1 < 401) and (39 < v2 < 401)
            spacing_ok = (
                abs((ts_ms[i] - ts_ms[i - 1]) - (ts_ms[i + 1] - ts_ms[i])) < 30_000
            )
            in_range_flag[i] = in_range
            evenly_spaced_flag[i] = spacing_ok
            if in_range and spacing_ok:
                out[i] = (v0 + v1 + v2) / 3.0
                smoothed_flag[i] = True

    if not instrument:
        return SmootherResult(smoothed=out)

    rows = {
        "reading_idx": np.arange(n, dtype="int64"),
        "ts_sec": (ts_ms // 1000).astype("int64"),
        "input_glucose": values.astype("float64"),
        "step_name": np.full(n, "avg", dtype=object),
        "prev_g": np.concatenate([[np.nan], values[:-1].astype("float64")]),
        "curr_g": values.astype("float64"),
        "next_g": np.concatenate([values[1:].astype("float64"), [np.nan]]),
        "dt_prev_s": np.concatenate([[np.nan],
                                     ((ts_ms[1:] - ts_ms[:-1]) / 1000.0)]).astype("float64"),
        "dt_next_s": np.concatenate([((ts_ms[1:] - ts_ms[:-1]) / 1000.0),
                                     [np.nan]]).astype("float64"),
        "in_range": in_range_flag,
        "evenly_spaced": evenly_spaced_flag,
        "smoothed_flag": smoothed_flag,
        "output_glucose": out,
    }
    trace = pd.DataFrame(rows)
    return SmootherResult(smoothed=out, trace=trace)
