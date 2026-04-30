"""Adaptive UKF + RTS smoother — Python port of `UnscentedKalmanFilterPlugin.kt`.

Constants are pulled verbatim from the Kotlin source. Public API takes
chronological arrays (oldest → newest); internally we flip to Kotlin's
newest-first indexing so the algorithm mirrors the original line-for-line
and the parity test can compare element-wise.

Two execution modes:
  * `process()` — single batch call over the entire array. Used for offline
    analysis where the RTS backward smoother sees the full series.
  * `online_process()` — production-realistic sliding window: at each
    chronological t, builds a fresh UKF over `chrono[t-W+1 .. t]` (W = 36)
    and emits the smoothed value at the leading edge (= chronological t).
    This is what AAPS dose-engine actually sees at decision time.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import exp, sqrt
from typing import Optional

import numpy as np
import pandas as pd

from .base import SmootherResult

# --- UKF parameters (Merwe scaled) ---
N = 2
ALPHA = 0.1
BETA = 2.0
KAPPA = 0.0
LAMBDA = ALPHA * ALPHA * (N + KAPPA) - N
GAMMA = sqrt(N + LAMBDA)

# --- Process noise (FIXED) ---
Q_GLUCOSE = 1.0
Q_RATE = 0.35
Q_FIXED = np.array([Q_GLUCOSE, 0.0, 0.0, Q_RATE], dtype="float64")

# --- Measurement noise (adaptive R bounds, in mg/dL² variance) ---
R_INIT = 25.0
R_MIN = 16.0
R_MAX = 225.0
R_EFF_MAX = 400.0

# --- Innovation tracking ---
INNOVATION_WINDOW = 18
CHI_SQUARED_THRESHOLD = 15.13
OUTLIER_ABSOLUTE = 65.0
INNOVATION_RESET_THRESHOLD = 12.0
INNOVATION_VALIDATION_SAMPLES = 15

# --- Covariance limits ---
MAX_GLUCOSE_VARIANCE = 400.0
MAX_RATE_VARIANCE = 4.0

# --- Gap handling ---
MINOR_GAP_THRESHOLD = 7.0  # min
MAJOR_GAP_THRESHOLD = 60.0  # min
RATE_DECAY_TIME_CONSTANT = 30.0  # min

# --- Online sliding-window size (mirrors Kotlin Main.kt UKF_WINDOW) ---
ONLINE_WINDOW = 36

# --- R adaptation ---
ADAPT_MIN_INNOVATIONS = 12
ADAPT_TRIM_FRACTION = 0.20
K_UP = 0.18
K_DN = 0.12
UP_CAP_UP = 1.20
UP_CAP_DN = 1.00
DN_CAP_UP = 1.00
DN_CAP_DN = 0.90
ETA = 0.25

# --- Sigma weights (precomputed) ---
WM = np.zeros(2 * N + 1)
WC = np.zeros(2 * N + 1)
WM[0] = LAMBDA / (N + LAMBDA)
WC[0] = LAMBDA / (N + LAMBDA) + (1 - ALPHA * ALPHA + BETA)
_w = 1.0 / (2.0 * (N + LAMBDA))
WM[1:] = _w
WC[1:] = _w


def rate_damp(dt: float) -> float:
    return exp(-dt / RATE_DECAY_TIME_CONSTANT)


@dataclass
class _FilterState:
    x: np.ndarray   # [G, Ġ] before update
    P: np.ndarray   # 2x2 row-major before update
    x_pred: np.ndarray
    P_pred: np.ndarray
    dt: float


@dataclass
class _UKFRunMeta:
    """Per-run mutable state, mirrors Kotlin's persistent fields."""
    learned_R: float = R_INIT
    last_processed_timestamp_ms: int = 0
    sensor_session_id: int = 0
    session_meas_count: int = 0
    session_outlier_count: int = 0
    innovations: deque = field(default_factory=lambda: deque(maxlen=INNOVATION_WINDOW))
    raw_innovation_var: deque = field(default_factory=lambda: deque(maxlen=INNOVATION_WINDOW))
    pred_var_history: deque = field(default_factory=lambda: deque(maxlen=INNOVATION_WINDOW))


class UKF:
    """Stateful UKF — instantiate fresh for each user, then call process()."""
    name = "ukf"

    def __init__(self) -> None:
        self.meta = _UKFRunMeta()

    def process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        instrument: bool = False,
        sensor_change_idx: Optional[list[int]] = None,
    ) -> SmootherResult:
        n = len(glucose)
        if n == 0:
            return SmootherResult(smoothed=np.array([], dtype="float64"))

        # Convert to Kotlin's newest-first indexing.
        ts_ms = (ts_sec.astype("int64") * 1000)
        chrono_idx = np.arange(n)
        rev_idx = chrono_idx[::-1]  # rev_idx[i] = original chronological index of the i-th newest

        z = glucose.astype("float64")[rev_idx].copy()
        t = ts_ms[rev_idx].copy()

        # Sensor-change reset (manual flag): if any chrono idx is in sensor_change_idx,
        # treat that timestamp as the latest sensor change → reset learning at that point.
        # We implement this by triggering reset at the start of each new segment whose
        # newest reading is at or after the most recent sensor change.
        sensor_change_ts = (
            int(ts_ms[max(sensor_change_idx)])
            if sensor_change_idx else 0
        )

        smoothed_kotlin_order = np.full(n, np.nan, dtype="float64")
        trace_rows: list[dict] = [] if instrument else []  # type: ignore[var-annotated]

        # Reset learning at the start of each call (matches Kotlin behavior:
        # smoothInternal calls shouldResetLearning(data[0].timestamp) on the newest ts).
        if self._should_reset(int(t[0]), sensor_change_ts):
            self._reset_learning()

        prev_processed_ts = self.meta.last_processed_timestamp_ms
        self.meta.last_processed_timestamp_ms = int(t[0])

        segments = self._find_segments(z, t)
        for seg_idx, (start_idx, end_idx) in enumerate(segments):
            self._process_segment(
                z, t, start_idx, end_idx, prev_processed_ts,
                smoothed_kotlin_order,
                trace_rows if instrument else None,
                seg_idx,
                sensor_change_ts,
            )

        # Anything still NaN → fallback to raw, floored at 39
        unprocessed = np.isnan(smoothed_kotlin_order)
        if unprocessed.any():
            smoothed_kotlin_order[unprocessed] = np.maximum(z[unprocessed], 39.0)

        # Flip back to chronological order
        smoothed_chrono = smoothed_kotlin_order[::-1]

        if not instrument:
            return SmootherResult(smoothed=smoothed_chrono)

        trace = pd.DataFrame(trace_rows)
        trace["reading_idx"] = (n - 1 - trace["kotlin_idx"]).astype("int64")
        trace = trace.sort_values("reading_idx").reset_index(drop=True)
        trace["ts_sec"] = (trace["ts_ms"] // 1000).astype("int64")
        return SmootherResult(smoothed=smoothed_chrono, trace=trace)

    def online_process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        window: int = ONLINE_WINDOW,
        instrument: bool = False,
    ) -> SmootherResult:
        """Production-realistic sliding-window mode.

        At each chronological index t, builds a fresh `UKF` over
        `glucose[max(0, t - window + 1) : t + 1]` (chronological order) and
        returns the smoothed value at the leading edge. Mirrors the Kotlin
        online driver `runOnlineSliding(window=UKF_WINDOW)`. Fresh state per
        call matches Kotlin's `{ UkfStandalone().smooth(it) }` pattern.

        With `instrument=True`, captures the leading-edge predict/update/
        outlier-flag from each window into a per-step trace. RTS does not
        modify the leading edge (it only revises older positions in the
        backward sweep), so `output_glucose` equals the forward-pass
        `x_upd_g` at every t.
        """
        n = len(glucose)
        if n == 0:
            return SmootherResult(smoothed=np.array([], dtype="float64"))

        glucose_f = glucose.astype("float64")
        ts_sec_i = ts_sec.astype("int64")
        out = np.zeros(n, dtype="float64")

        if not instrument:
            for t in range(n):
                lo = max(0, t - window + 1)
                window_g = glucose_f[lo : t + 1]
                window_t = ts_sec_i[lo : t + 1]
                if len(window_g) < 2:
                    out[t] = max(float(window_g[0]), 39.0)
                    continue
                res = UKF().process(window_g, window_t)
                out[t] = res.smoothed[-1]
            return SmootherResult(smoothed=out)

        rows: list[dict] = []
        for t in range(n):
            lo = max(0, t - window + 1)
            window_g = glucose_f[lo : t + 1]
            window_t = ts_sec_i[lo : t + 1]
            if len(window_g) < 2:
                out[t] = max(float(window_g[0]), 39.0)
                rows.append({
                    "reading_idx": t,
                    "ts_sec": int(ts_sec_i[t]),
                    "input_glucose": float(glucose_f[t]),
                    "step_name": "ukf_online",
                    "x_pred_g": float("nan"),
                    "x_upd_g": float("nan"),
                    "is_outlier": False,
                    "output_glucose": float(out[t]),
                })
                continue
            res = UKF().process(window_g, window_t, instrument=True)
            out[t] = res.smoothed[-1]
            # Leading edge in window's chronological coords = last position.
            le = res.trace[res.trace["reading_idx"] == len(window_g) - 1]
            if len(le) == 0:
                rows.append({
                    "reading_idx": t,
                    "ts_sec": int(ts_sec_i[t]),
                    "input_glucose": float(glucose_f[t]),
                    "step_name": "ukf_online",
                    "x_pred_g": float("nan"),
                    "x_upd_g": float("nan"),
                    "is_outlier": False,
                    "output_glucose": float(out[t]),
                })
                continue
            r = le.iloc[0]
            rows.append({
                "reading_idx": t,
                "ts_sec": int(ts_sec_i[t]),
                "input_glucose": float(glucose_f[t]),
                "step_name": "ukf_online",
                "x_pred_g": float(r["x_pred_g"]),
                "x_upd_g": float(r["x_upd_g"]),
                "is_outlier": bool(r["is_outlier"]),
                "output_glucose": float(out[t]),
            })

        return SmootherResult(smoothed=out, trace=pd.DataFrame(rows))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _should_reset(self, current_ts_ms: int, sensor_change_ts_ms: int) -> bool:
        m = self.meta
        if sensor_change_ts_ms > 0 and sensor_change_ts_ms > m.last_processed_timestamp_ms:
            return True
        if m.last_processed_timestamp_ms == 0:
            return True
        time_diff_min = (current_ts_ms - m.last_processed_timestamp_ms) / 60_000.0
        if time_diff_min < 0:
            return True
        if time_diff_min > 1440.0:
            return True
        if len(m.innovations) >= INNOVATION_VALIDATION_SAMPLES:
            avg = sum(m.innovations) / len(m.innovations)
            if avg > INNOVATION_RESET_THRESHOLD:
                return True
        return False

    def _reset_learning(self) -> None:
        m = self.meta
        m.learned_R = R_INIT
        m.innovations.clear()
        m.raw_innovation_var.clear()
        m.pred_var_history.clear()
        m.sensor_session_id += 1
        m.session_meas_count = 0
        m.session_outlier_count = 0

    def _find_segments(self, z: np.ndarray, t: np.ndarray) -> list[tuple[int, int]]:
        n = len(z)
        if n < 2:
            return []
        segments: list[tuple[int, int]] = []
        seg_start = 0
        for i in range(n - 1):
            dt_min = (t[i] - t[i + 1]) / 60_000.0
            if dt_min > MAJOR_GAP_THRESHOLD or dt_min < 2.0 or z[i] == 38.0:
                if i - seg_start >= 2:
                    segments.append((seg_start, i))
                seg_start = i + 1
        if n - seg_start >= 2:
            segments.append((seg_start, n - 1))
        return segments

    def _process_segment(
        self,
        z: np.ndarray, t: np.ndarray,
        start_idx: int, end_idx: int,
        previous_ts_ms: int,
        smoothed_out: np.ndarray,
        trace_rows: Optional[list[dict]],
        seg_idx: int,
        sensor_change_ts: int,
    ) -> None:
        m = self.meta
        seg_size = end_idx - start_idx + 1
        if seg_size < 2:
            smoothed_out[start_idx] = max(z[start_idx], 39.0)
            return

        # Init from oldest point in segment.
        initial_glucose = z[end_idx]
        initial_rate = 0.0
        if seg_size >= 2 and end_idx > 0:
            dt_init = (t[end_idx - 1] - t[end_idx]) / 60_000.0
            if 3.0 <= dt_init <= 7.0:
                initial_rate = (z[end_idx - 1] - z[end_idx]) / dt_init
                initial_rate = max(-4.0, min(4.0, initial_rate))

        x = np.array([initial_glucose, initial_rate], dtype="float64")
        P = np.array([16.0, 0.0, 0.0, 1.0], dtype="float64")
        R = m.learned_R

        forward_states: deque[_FilterState] = deque()
        forward_results = np.zeros(seg_size, dtype="float64")
        forward_results[seg_size - 1] = x[0]

        recent_signs: deque[int] = deque(maxlen=3)

        # Forward pass: oldest → newest, i decreasing.
        for i in range(end_idx - 1, start_idx - 1, -1):
            dt = (t[i] - t[i + 1]) / 60_000.0

            if MINOR_GAP_THRESHOLD < dt <= MAJOR_GAP_THRESHOLD:
                x[1] *= rate_damp(dt)

            # Covariance sanity
            P[0] = max(0.1, min(MAX_GLUCOSE_VARIANCE, P[0]))
            P[3] = max(0.001, min(MAX_RATE_VARIANCE, P[3]))

            x_pred_base, P_pred_base = _predict(x, P, Q_FIXED, dt)
            zi = z[i]
            is_new_data = bool(t[i] > previous_ts_ms)

            if zi <= 38.0:
                state_before = _FilterState(x.copy(), P.copy(), x_pred_base.copy(), P_pred_base.copy(), dt)
                x[:] = x_pred_base
                P[:] = P_pred_base
                forward_results[i - start_idx] = x[0]
                forward_states.appendleft(state_before)
                if trace_rows is not None:
                    trace_rows.append(_make_trace(
                        i, t[i], zi, seg_idx, dt,
                        x_pred_base, P_pred_base,
                        innov=np.nan, innov_var=np.nan, chi2=np.nan, is_outlier=False,
                        R_live=R, R_eff=np.nan, K=(np.nan, np.nan),
                        x_upd=x.copy(), P_upd=P.copy(),
                        R_after=R, is_rts=False, output=x[0],
                        session=m,
                    ))
                continue

            innovation = zi - x_pred_base[0]
            inn_var_raw = P_pred_base[0] + R
            std_raw = sqrt(inn_var_raw)
            norm_raw = innovation / std_raw

            sign = 1 if norm_raw > 0 else (-1 if norm_raw < 0 else 0)
            tag = sign if abs(norm_raw) > 2.0 else 0
            recent_signs.appendleft(tag)
            same_sign_count = 0 if sign == 0 else sum(1 for s in recent_signs if s == sign)
            q_inflate = same_sign_count >= 2 and sign != 0

            absn = abs(norm_raw)
            r_scale = 1.0 + max(0.0, absn - 2.0)
            R_eff = min(R * r_scale, min(R + 100.0, R_EFF_MAX))

            z_score = max(absn, 1.0)
            q_scale = max(1.0, min(3.0, z_score)) if q_inflate else 1.0
            if q_scale > 1.0:
                temp_Q = Q_FIXED.copy()
                temp_Q[0] = Q_GLUCOSE * min(q_scale, 2.0)
                temp_Q[3] = Q_RATE * q_scale
                x_pred_eff, P_pred_eff = _predict(x, P, temp_Q, dt)
            else:
                x_pred_eff, P_pred_eff = x_pred_base, P_pred_base

            state_before = _FilterState(x.copy(), P.copy(), x_pred_eff.copy(), P_pred_eff.copy(), dt)

            inn_var_eff = P_pred_eff[0] + R_eff
            mahal_sq_eff = (innovation * innovation) / inn_var_eff

            m.pred_var_history.appendleft(P_pred_eff[0])

            K_arr = _update(x_pred_eff, P_pred_eff, zi, R_eff, x, P)

            _track_innovation(m, innovation, inn_var_eff)

            skip_R_update = q_inflate or absn > 3.0
            if not skip_R_update:
                R = _adapt_R(R, m)

            is_outlier = mahal_sq_eff > CHI_SQUARED_THRESHOLD or abs(innovation) > OUTLIER_ABSOLUTE
            if is_new_data:
                m.session_meas_count += 1
                if is_outlier:
                    m.session_outlier_count += 1

            forward_results[i - start_idx] = x[0]
            forward_states.appendleft(state_before)

            if trace_rows is not None:
                trace_rows.append(_make_trace(
                    i, t[i], zi, seg_idx, dt,
                    x_pred_eff, P_pred_eff,
                    innov=innovation, innov_var=inn_var_eff,
                    chi2=mahal_sq_eff, is_outlier=is_outlier,
                    R_live=R, R_eff=R_eff, K=(K_arr[0], K_arr[1]),
                    x_upd=x.copy(), P_upd=P.copy(),
                    R_after=R, is_rts=False, output=x[0],
                    session=m,
                ))

        # Persist learned R after segment.
        m.learned_R = R

        # === RTS backward smoothing ===
        smoothed_results = forward_results.copy()
        if seg_size >= 3 and len(forward_states) > 0:
            max_steps = min(seg_size - 1, len(forward_states))
            x_smooth = np.array([forward_results[0], x[1]], dtype="float64")
            for k in range(1, max_steps + 1):
                state = forward_states[k - 1]
                C = _smoother_gain(state.P, state.P_pred, state.dt)
                dx0 = x_smooth[0] - state.x_pred[0]
                dx1 = x_smooth[1] - state.x_pred[1]
                x_smooth[0] = forward_results[k] + C[0] * dx0 + C[1] * dx1
                x_smooth[1] = state.x[1] + C[2] * dx0 + C[3] * dx1
                smoothed_results[k] = x_smooth[0]

        for i in range(start_idx, end_idx + 1):
            smoothed_out[i] = max(smoothed_results[i - start_idx], 39.0)

        # Patch RTS-modified outputs into trace
        if trace_rows is not None:
            kotlin_to_row = {row["kotlin_idx"]: row for row in trace_rows
                             if row["segment_idx"] == seg_idx}
            for i in range(start_idx, end_idx + 1):
                row = kotlin_to_row.get(i)
                if row is not None:
                    rts_val = max(smoothed_results[i - start_idx], 39.0)
                    row["output_glucose"] = rts_val
                    row["is_rts"] = (i != end_idx)


# ----------------------------------------------------------------------
# Pure UKF math (no state, no meta)
# ----------------------------------------------------------------------

def _matrix_sqrt_2x2(P: np.ndarray) -> np.ndarray:
    a = P[0]
    b = (P[1] + P[2]) / 2.0
    d = P[3]
    l11 = sqrt(max(a, 1e-9))
    l21 = b / l11
    discriminant = d - l21 * l21
    if discriminant < -1e-9:
        return np.array([sqrt(max(a, 0.1)), 0.0, 0.0, sqrt(max(d, 0.01))])
    l22 = sqrt(max(discriminant, 1e-9))
    return np.array([l11, l21, 0.0, l22])


def _generate_sigma_points(x: np.ndarray, P: np.ndarray) -> np.ndarray:
    sqrt_P = _matrix_sqrt_2x2(P)  # column-major [l11, l21, 0, l22]
    sp = np.zeros((2 * N + 1, N))
    sp[0] = x
    # i=0: column 0 of sqrtP = [l11, l21]
    sp[1, 0] = x[0] + GAMMA * sqrt_P[0]
    sp[1, 1] = x[1] + GAMMA * sqrt_P[1]
    sp[3, 0] = x[0] - GAMMA * sqrt_P[0]
    sp[3, 1] = x[1] - GAMMA * sqrt_P[1]
    # i=1: column 1 of sqrtP = [0, l22]
    sp[2, 0] = x[0] + GAMMA * sqrt_P[2]
    sp[2, 1] = x[1] + GAMMA * sqrt_P[3]
    sp[4, 0] = x[0] - GAMMA * sqrt_P[2]
    sp[4, 1] = x[1] - GAMMA * sqrt_P[3]
    return sp


def _predict(x: np.ndarray, P: np.ndarray, Q: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
    sp = _generate_sigma_points(x, P)
    sp_pred = np.zeros_like(sp)
    damp = rate_damp(dt)
    sp_pred[:, 0] = sp[:, 0] + sp[:, 1] * dt
    sp_pred[:, 1] = sp[:, 1] * damp

    x_pred = np.array([
        float(np.sum(WM * sp_pred[:, 0])),
        float(np.sum(WM * sp_pred[:, 1])),
    ])
    dx0 = sp_pred[:, 0] - x_pred[0]
    dx1 = sp_pred[:, 1] - x_pred[1]
    P_pred = np.array([
        float(np.sum(WC * dx0 * dx0)),
        float(np.sum(WC * dx0 * dx1)),
        float(np.sum(WC * dx1 * dx0)),
        float(np.sum(WC * dx1 * dx1)),
    ])

    q_scale = dt / 5.0
    P_pred[0] += Q[0] * q_scale
    P_pred[3] += Q[3] * q_scale
    P_pred[0] = max(P_pred[0], 0.1)
    P_pred[3] = max(P_pred[3], 0.001)
    return x_pred, P_pred


def _update(
    x_pred: np.ndarray, P_pred: np.ndarray, z: float, R: float,
    x: np.ndarray, P: np.ndarray,
) -> np.ndarray:
    """In-place update; returns Kalman gain K=[Kg, Kr]."""
    sp = _generate_sigma_points(x_pred, P_pred)
    z_sigma = sp[:, 0]
    z_pred = float(np.sum(WM * z_sigma))
    dz = z_sigma - z_pred
    P_zz = float(np.sum(WC * dz * dz)) + R
    if P_zz < 1e-6:
        x[:] = x_pred
        P[:] = P_pred
        return np.array([0.0, 0.0])

    dx0 = sp[:, 0] - x_pred[0]
    dx1 = sp[:, 1] - x_pred[1]
    Pxz0 = float(np.sum(WC * dx0 * dz))
    Pxz1 = float(np.sum(WC * dx1 * dz))
    K0 = Pxz0 / P_zz
    K1 = Pxz1 / P_zz

    innovation = z - z_pred
    x[0] = x_pred[0] + K0 * innovation
    x[1] = x_pred[1] + K1 * innovation
    x[1] = max(-4.0, min(4.0, x[1]))

    P[0] = P_pred[0] - K0 * P_zz * K0
    P[1] = P_pred[1] - K0 * P_zz * K1
    P[2] = P_pred[2] - K1 * P_zz * K0
    P[3] = P_pred[3] - K1 * P_zz * K1
    P[0] = max(P[0], 0.1)
    P[3] = max(P[3], 0.001)
    return np.array([K0, K1])


def _smoother_gain(P: np.ndarray, P_pred: np.ndarray, dt: float) -> np.ndarray:
    damp = rate_damp(dt)
    PFt00 = P[0] + P[1] * dt
    PFt01 = P[1] * damp
    PFt10 = P[2] + P[3] * dt
    PFt11 = P[3] * damp
    det = P_pred[0] * P_pred[3] - P_pred[1] * P_pred[2]
    if abs(det) < 1e-10:
        return np.zeros(4)
    inv00 = P_pred[3] / det
    inv01 = -P_pred[1] / det
    inv10 = -P_pred[2] / det
    inv11 = P_pred[0] / det
    return np.array([
        PFt00 * inv00 + PFt01 * inv10,
        PFt00 * inv01 + PFt01 * inv11,
        PFt10 * inv00 + PFt11 * inv10,
        PFt10 * inv01 + PFt11 * inv11,
    ])


def _track_innovation(meta: _UKFRunMeta, innov: float, var: float) -> None:
    meta.innovations.appendleft((innov * innov) / var)
    meta.raw_innovation_var.appendleft(innov * innov)


def _trimmed_mean(values: list[float], trim: float = ADAPT_TRIM_FRACTION) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(int(len(s) * trim), (len(s) - 1) // 2)
    core = s[k: len(s) - k] if k > 0 else s
    return sum(core) / len(core) if core else 0.0


def _adapt_R(current_R: float, meta: _UKFRunMeta) -> float:
    if len(meta.innovations) < ADAPT_MIN_INNOVATIONS or not meta.pred_var_history:
        return current_R
    n_use = len(meta.innovations)
    raw_window = list(meta.raw_innovation_var)[:n_use]
    pyy_window = list(meta.pred_var_history)[:n_use]
    m_raw = _trimmed_mean(raw_window)
    pyy = _trimmed_mean(pyy_window)
    R_hat_raw = max(R_MIN, m_raw - pyy)
    R_hat = max(R_MIN, min(R_MAX, R_hat_raw))
    diff = R_hat - current_R
    going_up = diff > 0.0
    k = K_UP if going_up else K_DN
    step = current_R + k * diff
    up_cap = UP_CAP_UP if going_up else UP_CAP_DN
    dn_cap = DN_CAP_UP if going_up else DN_CAP_DN
    clamped = max(current_R * dn_cap, min(current_R * up_cap, step))
    clamped = max(R_MIN, min(R_MAX, clamped))
    return (1.0 - ETA) * current_R + ETA * clamped


def _make_trace(
    kotlin_idx: int, ts_ms: int, input_g: float,
    seg_idx: int, dt: float,
    x_pred: np.ndarray, P_pred: np.ndarray,
    *,
    innov: float, innov_var: float, chi2: float, is_outlier: bool,
    R_live: float, R_eff: float, K: tuple[float, float],
    x_upd: np.ndarray, P_upd: np.ndarray,
    R_after: float, is_rts: bool, output: float,
    session: _UKFRunMeta,
) -> dict:
    return {
        "kotlin_idx": int(kotlin_idx),
        "ts_ms": int(ts_ms),
        "input_glucose": float(input_g),
        "step_name": "ukf",
        "segment_idx": int(seg_idx),
        "dt_min": float(dt),
        "x_pred_g": float(x_pred[0]),
        "x_pred_r": float(x_pred[1]),
        "P_pred_gg": float(P_pred[0]),
        "P_pred_rr": float(P_pred[3]),
        "P_pred_gr": float(P_pred[1]),
        "innov": float(innov),
        "innov_var": float(innov_var),
        "chi2": float(chi2),
        "is_outlier": bool(is_outlier),
        "R_live": float(R_live),
        "R_eff": float(R_eff),
        "K_g": float(K[0]),
        "K_r": float(K[1]),
        "x_upd_g": float(x_upd[0]),
        "x_upd_r": float(x_upd[1]),
        "P_upd_gg": float(P_upd[0]),
        "P_upd_rr": float(P_upd[3]),
        "R_after": float(R_after),
        "is_rts": bool(is_rts),
        "output_rate": float(x_upd[1]),
        "session_id": int(session.sensor_session_id),
        "session_meas_n": int(session.session_meas_count),
        "session_outlier_n": int(session.session_outlier_count),
        "output_glucose": float(output),
    }
