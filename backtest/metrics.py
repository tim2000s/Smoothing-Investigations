"""Per-smoother characterization metrics.

All metrics take chronological numpy arrays (oldest → newest) on a uniform
5-minute grid. Output units: minutes for delays, mg/dL for amplitudes,
dimensionless for noise ratios.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from scipy.signal import correlate, hilbert

GRID_S = 300
GRID_MIN = 5.0


# ----------------------------------------------------------------------
# Effective delay — three complementary estimators
# ----------------------------------------------------------------------

def xcorr_lag_min(raw: np.ndarray, smoothed: np.ndarray, *, max_lag_min: float = 60.0) -> float:
    """Argmax of normalized cross-correlation lag (raw vs smoothed), minutes.

    Positive value: smoothed lags raw (typical for causal smoothers).
    Sub-sample resolution via parabolic interpolation around the integer peak —
    real-world causal smoothers shift the correlation peak by less than one
    5-minute sample on CGM data, and the integer argmax alone collapses to 0.
    """
    raw = raw - np.nanmean(raw)
    smo = smoothed - np.nanmean(smoothed)
    valid = np.isfinite(raw) & np.isfinite(smo)
    raw = raw[valid]
    smo = smo[valid]
    if len(raw) < 4:
        return float("nan")
    max_lag = int(round(max_lag_min / GRID_MIN))
    n = len(raw)
    full = correlate(smo, raw, mode="full") / n
    center = n - 1
    lo = max(0, center - max_lag)
    hi = min(2 * n - 1, center + max_lag + 1)
    sub = full[lo:hi]
    arg = int(np.argmax(sub))
    lag_samples = arg - (center - lo)
    # Parabolic interpolation around the peak for sub-sample lag resolution.
    if 0 < arg < len(sub) - 1:
        y_m, y_0, y_p = sub[arg - 1], sub[arg], sub[arg + 1]
        denom = (y_m - 2.0 * y_0 + y_p)
        if abs(denom) > 1e-12:
            offset = 0.5 * (y_m - y_p) / denom
            offset = max(-0.5, min(0.5, offset))
            lag_samples = lag_samples + offset
    return float(lag_samples * GRID_MIN)


def step_response_delay_min(
    raw: np.ndarray,
    smoothed: np.ndarray,
    *,
    rate_threshold: float = 0.5,   # mg/dL/min on raw — relaxed from 1.0 to catch more events
    min_amplitude: float = 15.0,   # sustained move ≥ 15 mg/dL — relaxed from 30
    max_lag_min: float = 30.0,
    smoothed_threshold_frac: float = 0.7,
) -> dict:
    """Median time from raw rate-threshold crossings to smoothed crossings.

    For each sustained raw event (rate ≥ `rate_threshold` mg/dL/min, total
    amplitude ≥ `min_amplitude`), measure the time the smoothed series takes
    to cross the same rate threshold scaled by `smoothed_threshold_frac`
    (smoothing dampens the rate, so a strict same-threshold crossing under-counts
    smoother responses). Sub-sample resolution via linear interpolation between
    the bracketing samples.
    """
    if len(raw) < 10:
        return {"n_events": 0, "median_delay_min": float("nan"), "delay_by_amp": {}}

    dr = np.diff(raw) / GRID_MIN
    ds = np.diff(smoothed) / GRID_MIN
    smo_thresh = rate_threshold * smoothed_threshold_frac

    def _interp_cross(arr: np.ndarray, idx: int, thresh: float) -> float:
        """Sub-sample crossing time (in samples from idx-1) for |arr| crossing thresh."""
        if idx == 0:
            return 0.0
        a, b = abs(arr[idx - 1]), abs(arr[idx])
        if b == a:
            return float(idx)
        return (idx - 1) + (thresh - a) / (b - a)

    delays: list[tuple[float, float]] = []
    in_event = False
    event_start = -1
    raw_cross_sub = -1.0
    for i in range(len(dr)):
        if not in_event and abs(dr[i]) >= rate_threshold:
            in_event = True
            event_start = i
            raw_cross_sub = _interp_cross(dr, i, rate_threshold)
        elif in_event:
            if abs(dr[i]) < rate_threshold * 0.3:
                end = i
                amp = abs(raw[end] - raw[event_start])
                if amp >= min_amplitude:
                    smo_cross_sub = -1.0
                    max_search = min(len(ds), end + int(max_lag_min / GRID_MIN))
                    for j in range(int(raw_cross_sub), max_search):
                        if abs(ds[j]) >= smo_thresh:
                            smo_cross_sub = _interp_cross(ds, j, smo_thresh)
                            break
                    if smo_cross_sub >= 0:
                        delays.append((amp, (smo_cross_sub - raw_cross_sub) * GRID_MIN))
                in_event = False

    if not delays:
        return {"n_events": 0, "median_delay_min": float("nan"), "delay_by_amp": {}}

    arr = np.array(delays)
    bins = [(15, 30), (30, 60), (60, 120), (120, 1e9)]
    by_amp = {}
    for lo, hi in bins:
        mask = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
        label = f"{lo}-{int(hi) if hi < 1e9 else 'inf'}_mgdl"
        by_amp[label] = float(np.median(arr[mask, 1])) if mask.any() else float("nan")
    return {
        "n_events": len(delays),
        "median_delay_min": float(np.median(arr[:, 1])),
        "delay_by_amp": by_amp,
    }


def phase_shift_delay_min(raw: np.ndarray, smoothed: np.ndarray) -> float:
    """Median phase shift in minutes from a Hilbert-transform-based estimate.

    Filters both signals to the 1–6h cycle band (the range where physiological
    glucose dynamics dominate), computes the analytic-signal phase, and returns
    the median (smoothed_phase − raw_phase) converted to minutes at the band's
    center frequency.
    """
    valid = np.isfinite(raw) & np.isfinite(smoothed)
    if valid.sum() < 32:
        return float("nan")
    raw = raw[valid] - np.nanmean(raw[valid])
    smo = smoothed[valid] - np.nanmean(smoothed[valid])

    n = len(raw)
    if n & 1:
        n -= 1
        raw = raw[:n]
        smo = smo[:n]

    fs = 1.0 / GRID_S
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    band = (freqs >= 1.0 / (6 * 3600)) & (freqs <= 1.0 / 3600)
    if not band.any():
        return float("nan")

    raw_fft = np.fft.rfft(raw)
    smo_fft = np.fft.rfft(smo)
    raw_band = np.zeros_like(raw_fft)
    smo_band = np.zeros_like(smo_fft)
    raw_band[band] = raw_fft[band]
    smo_band[band] = smo_fft[band]
    raw_t = np.fft.irfft(raw_band, n=n)
    smo_t = np.fft.irfft(smo_band, n=n)

    raw_an = hilbert(raw_t)
    smo_an = hilbert(smo_t)
    phase_diff = np.angle(smo_an) - np.angle(raw_an)
    phase_diff = (phase_diff + np.pi) % (2 * np.pi) - np.pi
    median_rad = float(np.median(phase_diff))
    f_center = 1.0 / (3 * 3600)  # 3-hour cycle
    return median_rad / (2 * np.pi * f_center) / 60.0


# ----------------------------------------------------------------------
# Other characterization metrics
# ----------------------------------------------------------------------

def noise_reduction(raw: np.ndarray, smoothed: np.ndarray) -> dict:
    """Variance of first-difference noise removed by the smoother."""
    raw = raw[np.isfinite(raw)]
    smo = smoothed[np.isfinite(smoothed)]
    if len(raw) < 2 or len(smo) < 2:
        return {"raw_diff_var": float("nan"), "smoothed_diff_var": float("nan"),
                "absolute_reduction": float("nan"), "ratio": float("nan")}
    raw_var = float(np.var(np.diff(raw)))
    smo_var = float(np.var(np.diff(smo)))
    return {
        "raw_diff_var": raw_var,
        "smoothed_diff_var": smo_var,
        "absolute_reduction": raw_var - smo_var,
        "ratio": (smo_var / raw_var) if raw_var > 0 else float("nan"),
    }


def attenuation_at_signal_freq(
    raw: np.ndarray,
    smoothed: np.ndarray,
    *,
    band_hz: tuple[float, float] = (1.0 / (6 * 3600), 1.0 / 3600),
) -> float:
    """PSD ratio (smoothed/raw) in the diabetes-signal band (cycles per 1–6 h).
    Returns dimensionless gain (1.0 = no attenuation, <1 = signal dampened)."""
    valid = np.isfinite(raw) & np.isfinite(smoothed)
    if valid.sum() < 64:
        return float("nan")
    raw = raw[valid]
    smo = smoothed[valid]
    n = len(raw)
    fs = 1.0 / GRID_S
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    raw_fft = np.fft.rfft(raw - raw.mean())
    smo_fft = np.fft.rfft(smo - smo.mean())
    raw_psd = np.abs(raw_fft) ** 2
    smo_psd = np.abs(smo_fft) ** 2
    band = (freqs >= band_hz[0]) & (freqs <= band_hz[1])
    if not band.any() or raw_psd[band].sum() == 0:
        return float("nan")
    return float(smo_psd[band].sum() / raw_psd[band].sum())


def hypo_event_preservation(
    raw: np.ndarray,
    smoothed: np.ndarray,
    *,
    threshold: float = 70.0,
    min_event_separation_min: float = 60.0,
) -> dict:
    """For each raw nadir <70 mg/dL, did the smoothed series also dip <70?
    Reports nadir Δ (mg/dL) and time-to-nadir Δ (minutes)."""
    return _event_preservation(raw, smoothed, threshold, "below",
                               int(min_event_separation_min / GRID_MIN))


def peak_preservation(
    raw: np.ndarray,
    smoothed: np.ndarray,
    *,
    threshold: float = 180.0,
    min_event_separation_min: float = 60.0,
) -> dict:
    return _event_preservation(raw, smoothed, threshold, "above",
                               int(min_event_separation_min / GRID_MIN))


def _event_preservation(
    raw: np.ndarray, smoothed: np.ndarray, threshold: float,
    direction: str, min_sep_samples: int,
) -> dict:
    n = len(raw)
    if n != len(smoothed) or n < 4:
        return {"n_events": 0, "preserved_pct": float("nan"),
                "median_amp_delta": float("nan"), "median_time_delta_min": float("nan")}

    pred = (raw < threshold) if direction == "below" else (raw > threshold)
    in_ev = False
    events: list[tuple[int, int]] = []
    start = -1
    for i, p in enumerate(pred):
        if p and not in_ev:
            in_ev = True
            start = i
        elif not p and in_ev:
            events.append((start, i))
            in_ev = False
    if in_ev:
        events.append((start, n))

    merged: list[tuple[int, int]] = []
    for s, e in events:
        if merged and s - merged[-1][1] < min_sep_samples:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    n_pres = 0
    amp_deltas = []
    time_deltas_min = []
    for s, e in merged:
        raw_extreme_idx = (np.argmin(raw[s:e]) if direction == "below" else np.argmax(raw[s:e])) + s
        raw_extreme = raw[raw_extreme_idx]
        smo_window = smoothed[s:e]
        if direction == "below":
            crossed = (smo_window < threshold).any()
            smo_extreme_idx = int(np.argmin(smo_window)) + s
        else:
            crossed = (smo_window > threshold).any()
            smo_extreme_idx = int(np.argmax(smo_window)) + s
        smo_extreme = smoothed[smo_extreme_idx]
        if crossed:
            n_pres += 1
            amp_deltas.append(float(smo_extreme - raw_extreme))
            time_deltas_min.append(float((smo_extreme_idx - raw_extreme_idx) * GRID_MIN))

    return {
        "n_events": len(merged),
        "preserved_pct": (100.0 * n_pres / len(merged)) if merged else float("nan"),
        "median_amp_delta": float(np.median(amp_deltas)) if amp_deltas else float("nan"),
        "median_time_delta_min": float(np.median(time_deltas_min)) if time_deltas_min else float("nan"),
    }


def outlier_behaviour(
    raw: np.ndarray,
    smoothed: np.ndarray,
    *,
    step_threshold: float = 40.0,
) -> dict:
    """Count and magnitude of raw single-step changes ≥40 mg/dL retained vs absorbed."""
    if len(raw) < 2:
        return {"n_raw_steps": 0, "n_smoothed_steps": 0, "absorbed_pct": float("nan"),
                "median_absorbed_amp": float("nan")}
    raw_steps = np.where(np.abs(np.diff(raw)) >= step_threshold)[0]
    smo_steps = np.where(np.abs(np.diff(smoothed)) >= step_threshold)[0]
    n_absorbed = len(raw_steps) - len(np.intersect1d(raw_steps, smo_steps))
    if len(raw_steps) > 0:
        absorbed_amps = []
        for i in raw_steps:
            if i not in smo_steps:
                absorbed_amps.append(abs(raw[i + 1] - raw[i]))
        median_abs = float(np.median(absorbed_amps)) if absorbed_amps else float("nan")
    else:
        median_abs = float("nan")
    return {
        "n_raw_steps": int(len(raw_steps)),
        "n_smoothed_steps": int(len(smo_steps)),
        "absorbed_pct": (100.0 * n_absorbed / len(raw_steps)) if len(raw_steps) > 0 else float("nan"),
        "median_absorbed_amp": median_abs,
    }


# ----------------------------------------------------------------------
# Aggregator: returns a flat dict ready to land in a per-user CSV row
# ----------------------------------------------------------------------

@dataclass
class PerUserMetrics:
    user_id: str
    table: str
    algorithm: str
    n_readings: int
    xcorr_lag_min: float
    step_response_median_delay_min: float
    step_response_n_events: int
    phase_shift_delay_min: float
    noise_reduction_ratio: float
    attenuation_signal_band: float
    hypo_n: int
    hypo_preserved_pct: float
    hypo_amp_delta: float
    hypo_time_delta_min: float
    peak_n: int
    peak_preserved_pct: float
    peak_amp_delta: float
    peak_time_delta_min: float
    outlier_n_raw: int
    outlier_absorbed_pct: float
    outlier_median_absorbed_amp: float


def compute_per_user(
    raw: np.ndarray, smoothed: np.ndarray,
    *, user_id: str, table: str, algorithm: str,
) -> PerUserMetrics:
    nr = noise_reduction(raw, smoothed)
    sr = step_response_delay_min(raw, smoothed)
    hp = hypo_event_preservation(raw, smoothed)
    pk = peak_preservation(raw, smoothed)
    ob = outlier_behaviour(raw, smoothed)
    return PerUserMetrics(
        user_id=user_id, table=table, algorithm=algorithm,
        n_readings=int(len(raw)),
        xcorr_lag_min=xcorr_lag_min(raw, smoothed),
        step_response_median_delay_min=sr["median_delay_min"],
        step_response_n_events=int(sr["n_events"]),
        phase_shift_delay_min=phase_shift_delay_min(raw, smoothed),
        noise_reduction_ratio=nr["ratio"],
        attenuation_signal_band=attenuation_at_signal_freq(raw, smoothed),
        hypo_n=int(hp["n_events"]),
        hypo_preserved_pct=hp["preserved_pct"],
        hypo_amp_delta=hp["median_amp_delta"],
        hypo_time_delta_min=hp["median_time_delta_min"],
        peak_n=int(pk["n_events"]),
        peak_preserved_pct=pk["preserved_pct"],
        peak_amp_delta=pk["median_amp_delta"],
        peak_time_delta_min=pk["median_time_delta_min"],
        outlier_n_raw=int(ob["n_raw_steps"]),
        outlier_absorbed_pct=ob["absorbed_pct"],
        outlier_median_absorbed_amp=ob["median_absorbed_amp"],
    )


def to_dict(m: PerUserMetrics) -> dict:
    return asdict(m)
