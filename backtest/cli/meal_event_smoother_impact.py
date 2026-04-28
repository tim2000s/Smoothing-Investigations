"""Per-meal smoother-impact analysis.

For every Nightscout treatment whose notes match /meal|fast carbs/i, this script:

  1. Slices a window of CGM readings around the meal time
       (T_event − pre_min, T_event + post_min) and resamples to a strict
       5-minute grid.
  2. Runs all four smoothers (AAPS Average, AAPS Exponential, Trio Savitzky-Golay,
       Adaptive UKF) on the window's raw glucose.
  3. Computes the AID-relevant decision quantities at each post-event sample for
       both raw and each smoother:
         - delta            (5-min change, mg/dL/min)
         - short_avgdelta   (~15-min average rate, mg/dL/min)
         - long_avgdelta    (~45-min average rate, mg/dL/min)
         - acceleration     (rate-of-change of rate, mg/dL/min²)
       and records per smoother:
         - first time `delta` ≥ DELTA_THRESHOLD after the event (a proxy for
           SMB-on-rise being eligible)
         - first time `acceleration` ≥ ACCEL_THRESHOLD after the event (a proxy
           for UAM-style acceleration-aware logic detecting the rise)
         - peak `delta`, peak `acceleration` during the post-event window
         - time-to-peak for each
  4. Plots a per-meal overlay (raw + 4 smoothers, two panels: glucose and rate)
     with vertical line at the meal moment and threshold reference lines.
  5. Writes one row per (meal, smoother) to a CSV for cohort-level aggregation.

Inputs (from `backtest.cli.fetch_nightscout_meals`):
  data/nstest3/treatments_meals.json
  data/nstest3/entries.json

Outputs (under --out, default reports/meal_events/):
  per_meal_smoother_metrics.csv  — one row per (meal, smoother)
  cohort_summary.csv             — cohort medians per smoother
  figs/meal_<idx>_<ts>.png       — per-meal overlay plot (top N by data quality)
  meal_index.csv                 — list of all detected meal events
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backtest.smoothers import AapsAverage, AapsExponential, TrioSGolay, UKF

GRID_S = 300
DELTA_THRESHOLD = 1.0     # mg/dL/min — a typical "rate-rising" threshold
ACCEL_THRESHOLD = 0.2     # mg/dL/min² — a typical "real meal accelerating" threshold

# Window around the event for analysis
PRE_MIN_DEFAULT = 60     # 60 minutes of pre-event CGM (warmup for smoothers)
POST_MIN_DEFAULT = 180   # 3 hours of post-event CGM

ALGOS = (
    ("aaps_average", AapsAverage),
    ("aaps_exponential", AapsExponential),
    ("trio_sgolay", TrioSGolay),
    ("ukf", UKF),
)


def load_inputs(data_dir: Path) -> tuple[list[dict], pd.DataFrame]:
    treatments_path = data_dir / "treatments_meals.json"
    entries_path = data_dir / "entries.json"
    if not treatments_path.exists() or not entries_path.exists():
        raise FileNotFoundError(
            f"Run fetch_nightscout_meals first; expected {treatments_path} and {entries_path}"
        )
    treatments = json.loads(treatments_path.read_text())
    entries_raw = json.loads(entries_path.read_text())
    df = pd.DataFrame(entries_raw)
    df = df[df["type"] == "sgv"].copy() if "type" in df.columns else df
    df["date"] = df["date"].astype("int64")
    df["sgv"] = pd.to_numeric(df["sgv"], errors="coerce")
    df = df.dropna(subset=["sgv"]).sort_values("date").reset_index(drop=True)
    return treatments, df


def event_timestamp_ms(treatment: dict) -> int | None:
    """Treatment's `date` is ms since epoch; fallback to `created_at`."""
    if treatment.get("date"):
        return int(treatment["date"])
    if treatment.get("created_at"):
        s = treatment["created_at"]
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    return None


def slice_window_to_grid(
    entries_df: pd.DataFrame, t_event_ms: int,
    pre_min: int, post_min: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Return (ts_sec_relative_to_t0, glucose, t0_ms_aligned_to_grid).

    Resamples the window to a strict 5-min grid; uses ffill(limit=1) for short
    gaps and leaves longer gaps as NaN.
    """
    lo_ms = t_event_ms - pre_min * 60_000
    hi_ms = t_event_ms + post_min * 60_000
    sub = entries_df[(entries_df["date"] >= lo_ms) & (entries_df["date"] <= hi_ms)].copy()
    if sub.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64"), 0

    # Snap each entry to the nearest 5-min grid point (relative to lo_ms).
    sub["ts_s"] = sub["date"] // 1000
    t0 = int(sub["ts_s"].iloc[0])
    sub["ts_grid"] = ((sub["ts_s"] - t0 + GRID_S // 2) // GRID_S) * GRID_S + t0
    sub = sub.drop_duplicates(subset="ts_grid", keep="last")

    grid_end = int(sub["ts_s"].iloc[-1]) + GRID_S
    grid = np.arange(t0, grid_end, GRID_S, dtype="int64")
    g = pd.DataFrame({"ts_grid": grid}).merge(
        sub[["ts_grid", "sgv"]], on="ts_grid", how="left"
    )
    raw_idx = g["sgv"].notna().to_numpy()
    glucose = np.asarray(g["sgv"].ffill(limit=1).to_numpy(dtype="float64")).copy()
    # Re-NaN any slot that's beyond the 1-step ffill reach.
    missing_run = 0
    for i, present in enumerate(raw_idx):
        if present:
            missing_run = 0
        else:
            missing_run += 1
            if missing_run > 1:
                glucose[i] = np.nan
    return (g["ts_grid"].to_numpy(dtype="int64") - t0), glucose, t0 * 1000


def rate_seq(g: np.ndarray) -> np.ndarray:
    """5-minute first-difference rate in mg/dL/min."""
    return np.concatenate([[np.nan], np.diff(g) / 5.0])


def avgdelta(g: np.ndarray, window_min: int) -> np.ndarray:
    """`avgdelta` over `window_min` minutes, computed at every sample."""
    n_samples = window_min // 5
    out = np.full(len(g), np.nan)
    for i in range(n_samples, len(g)):
        if np.isfinite(g[i]) and np.isfinite(g[i - n_samples]):
            out[i] = (g[i] - g[i - n_samples]) / window_min
    return out


def acceleration_seq(rate: np.ndarray) -> np.ndarray:
    """Difference of rate per 5-min step (mg/dL/min²)."""
    out = np.full(len(rate), np.nan)
    out[1:] = np.diff(rate) / 5.0
    return out


def first_crossing(values: np.ndarray, threshold: float, *, after_idx: int) -> int | None:
    """First index ≥ after_idx where values ≥ threshold (NaN-safe)."""
    for i in range(after_idx, len(values)):
        v = values[i]
        if np.isfinite(v) and v >= threshold:
            return i
    return None


def analyse_event(event_ts_ms: int, raw_g: np.ndarray, ts_rel_sec: np.ndarray, t0_ms: int,
                  *, pre_min: int, post_min: int) -> dict:
    """Run all 4 smoothers on the window, return per-smoother metrics."""
    if len(raw_g) < 12 or not np.isfinite(raw_g).any():
        return {"valid": False}

    # Smoothers want contiguous finite arrays.
    present = np.isfinite(raw_g)
    g_in = raw_g[present]
    ts_in = ts_rel_sec[present]
    if len(g_in) < 12:
        return {"valid": False}

    # Index of the meal moment within the contiguous-grid representation.
    event_offset_ms = event_ts_ms - t0_ms
    event_idx_grid = int(np.argmin(np.abs(ts_rel_sec - event_offset_ms / 1000.0)))

    # Per-smoother smoothed series back on the original grid (with NaN where input was NaN)
    smoothed_per_algo: dict[str, np.ndarray] = {}
    for name, cls in ALGOS:
        out = np.full(len(raw_g), np.nan)
        try:
            res = cls().process(g_in, ts_in)
            # Need to align res.smoothed back to the contiguous-grid mask
            out_idx = np.where(present)[0]
            # UKF emits 1 fewer per segment, so we can't always index 1:1; trust
            # smoothers to emit one output per input on a single segment.
            if len(res.smoothed) == len(g_in):
                out[out_idx] = res.smoothed
            else:
                # UKF segment-init drop: pad first by raw, rest by output
                missing = len(g_in) - len(res.smoothed)
                out[out_idx[missing:]] = res.smoothed
        except Exception:
            out = raw_g.copy()
        smoothed_per_algo[name] = out

    metrics = []
    for name, _ in ALGOS:
        smo = smoothed_per_algo[name]
        rate = rate_seq(smo)
        accel = acceleration_seq(rate)
        sad = avgdelta(smo, 15)
        lad = avgdelta(smo, 45)
        cross_delta = first_crossing(rate, DELTA_THRESHOLD, after_idx=event_idx_grid)
        cross_accel = first_crossing(accel, ACCEL_THRESHOLD, after_idx=event_idx_grid)
        # peak rate / accel within the post-event window
        post = slice(event_idx_grid, None)
        peak_rate_idx = (np.nanargmax(rate[post]) if np.isfinite(rate[post]).any() else None)
        peak_accel_idx = (np.nanargmax(accel[post]) if np.isfinite(accel[post]).any() else None)

        def t_min(idx):  # idx into post slice or absolute
            if idx is None:
                return float("nan")
            return ((idx + event_idx_grid) - event_idx_grid) * 5.0 if idx < len(rate[post]) else float("nan")

        def t_min_abs(idx):  # absolute idx
            if idx is None:
                return float("nan")
            return (idx - event_idx_grid) * 5.0

        peak_rate_val = float(np.nanmax(rate[post])) if np.isfinite(rate[post]).any() else float("nan")
        peak_accel_val = float(np.nanmax(accel[post])) if np.isfinite(accel[post]).any() else float("nan")
        peak_rate_t = t_min(peak_rate_idx) if peak_rate_idx is not None else float("nan")
        peak_accel_t = t_min(peak_accel_idx) if peak_accel_idx is not None else float("nan")

        metrics.append({
            "smoother": name,
            "delta_first_cross_min_after_event": t_min_abs(cross_delta),
            "accel_first_cross_min_after_event": t_min_abs(cross_accel),
            "peak_delta_mgdl_per_min": peak_rate_val,
            "peak_delta_at_min_after_event": peak_rate_t,
            "peak_accel_mgdl_per_min2": peak_accel_val,
            "peak_accel_at_min_after_event": peak_accel_t,
        })

    # Also report raw equivalents for the "no smoothing" baseline.
    raw_rate = rate_seq(raw_g)
    raw_accel = acceleration_seq(raw_rate)
    raw_cross_delta = first_crossing(raw_rate, DELTA_THRESHOLD, after_idx=event_idx_grid)
    raw_cross_accel = first_crossing(raw_accel, ACCEL_THRESHOLD, after_idx=event_idx_grid)
    raw_peak_rate = float(np.nanmax(raw_rate[event_idx_grid:])) if np.isfinite(raw_rate[event_idx_grid:]).any() else float("nan")
    raw_peak_accel = float(np.nanmax(raw_accel[event_idx_grid:])) if np.isfinite(raw_accel[event_idx_grid:]).any() else float("nan")

    return {
        "valid": True,
        "event_idx_grid": event_idx_grid,
        "raw_g": raw_g,
        "ts_rel_sec": ts_rel_sec,
        "smoothed_per_algo": smoothed_per_algo,
        "metrics": metrics,
        "raw": {
            "delta_first_cross_min_after_event": (raw_cross_delta - event_idx_grid) * 5.0 if raw_cross_delta is not None else float("nan"),
            "accel_first_cross_min_after_event": (raw_cross_accel - event_idx_grid) * 5.0 if raw_cross_accel is not None else float("nan"),
            "peak_delta_mgdl_per_min": raw_peak_rate,
            "peak_accel_mgdl_per_min2": raw_peak_accel,
        },
    }


def plot_event(meal_idx: int, treatment: dict, info: dict, fig_dir: Path) -> None:
    raw_g = info["raw_g"]
    ts_min = info["ts_rel_sec"] / 60.0  # seconds → minutes
    event_idx = info["event_idx_grid"]
    event_t = ts_min[event_idx]
    smooth = info["smoothed_per_algo"]

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    colors = {"aaps_average": "tab:blue", "aaps_exponential": "tab:orange",
              "trio_sgolay": "tab:green", "ukf": "tab:red"}

    # Top: glucose
    axes[0].plot(ts_min - event_t, raw_g, color="black", lw=1.0, alpha=0.55, label="raw")
    for name, _ in ALGOS:
        axes[0].plot(ts_min - event_t, smooth[name], color=colors[name], lw=1.5, label=name)
    axes[0].axvline(0, color="black", lw=0.8, linestyle=":")
    note = (treatment.get("notes") or "")[:50]
    carbs = treatment.get("carbs")
    title_extra = f" — carbs={carbs}" if carbs is not None else ""
    axes[0].set_title(f"Meal {meal_idx}: '{note}'{title_extra}  @ {treatment.get('created_at', '?')}")
    axes[0].set_ylabel("glucose (mg/dL)")
    axes[0].legend(loc="best", fontsize=9)
    axes[0].grid(True, alpha=0.3)

    # Bottom: rate
    raw_rate = rate_seq(raw_g)
    axes[1].plot(ts_min - event_t, raw_rate, color="black", lw=1.0, alpha=0.55, label="raw")
    for name, _ in ALGOS:
        rate = rate_seq(smooth[name])
        axes[1].plot(ts_min - event_t, rate, color=colors[name], lw=1.5, label=name)
    axes[1].axvline(0, color="black", lw=0.8, linestyle=":")
    axes[1].axhline(DELTA_THRESHOLD, color="purple", lw=0.7, linestyle="--",
                    label=f"delta threshold = {DELTA_THRESHOLD} mg/dL/min")
    axes[1].set_xlabel("minutes from meal time")
    axes[1].set_ylabel("delta (mg/dL/min)")
    axes[1].legend(loc="best", fontsize=8)
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    out_path = fig_dir / f"meal_{meal_idx:04d}.png"
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data/nstest3")
    p.add_argument("--out", default="reports/meal_events")
    p.add_argument("--pre-min", type=int, default=PRE_MIN_DEFAULT)
    p.add_argument("--post-min", type=int, default=POST_MIN_DEFAULT)
    p.add_argument("--max-plots", type=int, default=30,
                   help="Generate per-meal plots for at most N events")
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out)
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    treatments, entries = load_inputs(data_dir)
    print(f"Loaded {len(treatments):,} meal-tagged treatments and {len(entries):,} CGM entries")

    meal_index_rows = []
    per_meal_metrics = []

    for idx, t in enumerate(treatments):
        ts_ms = event_timestamp_ms(t)
        if ts_ms is None:
            continue
        ts_rel, glucose, t0_ms = slice_window_to_grid(
            entries, ts_ms, pre_min=args.pre_min, post_min=args.post_min,
        )
        if len(glucose) < 12 or not np.isfinite(glucose).any():
            meal_index_rows.append({
                "meal_idx": idx, "ts_ms": ts_ms,
                "created_at": t.get("created_at"),
                "notes": t.get("notes"),
                "carbs": t.get("carbs"),
                "valid": False,
                "reason": "insufficient cgm coverage",
            })
            continue

        info = analyse_event(ts_ms, glucose, ts_rel, t0_ms,
                             pre_min=args.pre_min, post_min=args.post_min)
        if not info.get("valid"):
            meal_index_rows.append({
                "meal_idx": idx, "ts_ms": ts_ms,
                "created_at": t.get("created_at"),
                "notes": t.get("notes"),
                "carbs": t.get("carbs"),
                "valid": False,
                "reason": "smoother analysis returned invalid",
            })
            continue

        meal_index_rows.append({
            "meal_idx": idx, "ts_ms": ts_ms,
            "created_at": t.get("created_at"),
            "notes": t.get("notes"),
            "carbs": t.get("carbs"),
            "valid": True,
            "raw_delta_first_cross_min": info["raw"]["delta_first_cross_min_after_event"],
            "raw_accel_first_cross_min": info["raw"]["accel_first_cross_min_after_event"],
            "raw_peak_delta": info["raw"]["peak_delta_mgdl_per_min"],
            "raw_peak_accel": info["raw"]["peak_accel_mgdl_per_min2"],
        })

        for m in info["metrics"]:
            row = dict(m)
            row.update({
                "meal_idx": idx, "ts_ms": ts_ms,
                "created_at": t.get("created_at"),
                "notes": (t.get("notes") or "")[:80],
                "carbs": t.get("carbs"),
                "raw_delta_first_cross_min": info["raw"]["delta_first_cross_min_after_event"],
                "raw_accel_first_cross_min": info["raw"]["accel_first_cross_min_after_event"],
                "raw_peak_delta": info["raw"]["peak_delta_mgdl_per_min"],
                "raw_peak_accel": info["raw"]["peak_accel_mgdl_per_min2"],
                "delta_latency_vs_raw_min": (
                    row["delta_first_cross_min_after_event"] - info["raw"]["delta_first_cross_min_after_event"]
                    if np.isfinite(row["delta_first_cross_min_after_event"])
                    and np.isfinite(info["raw"]["delta_first_cross_min_after_event"])
                    else float("nan")
                ),
                "peak_delta_retention": (
                    row["peak_delta_mgdl_per_min"] / info["raw"]["peak_delta_mgdl_per_min"]
                    if info["raw"]["peak_delta_mgdl_per_min"] and info["raw"]["peak_delta_mgdl_per_min"] > 0
                    else float("nan")
                ),
            })
            per_meal_metrics.append(row)

        if idx < args.max_plots:
            plot_event(idx, t, info, fig_dir)

    pd.DataFrame(meal_index_rows).to_csv(out_dir / "meal_index.csv", index=False)
    pd.DataFrame(per_meal_metrics).to_csv(out_dir / "per_meal_smoother_metrics.csv", index=False)
    print(f"Wrote meal_index.csv ({len(meal_index_rows):,} rows) and "
          f"per_meal_smoother_metrics.csv ({len(per_meal_metrics):,} rows)")

    # Cohort-level summary across smoothers
    if per_meal_metrics:
        df = pd.DataFrame(per_meal_metrics)
        agg = df.groupby("smoother").agg(
            n_meals=("meal_idx", "count"),
            median_delta_first_cross_min=("delta_first_cross_min_after_event", "median"),
            median_accel_first_cross_min=("accel_first_cross_min_after_event", "median"),
            median_peak_delta=("peak_delta_mgdl_per_min", "median"),
            median_peak_accel=("peak_accel_mgdl_per_min2", "median"),
            median_delta_latency_vs_raw=("delta_latency_vs_raw_min", "median"),
            median_peak_delta_retention=("peak_delta_retention", "median"),
        ).round(3)
        agg.to_csv(out_dir / "cohort_summary.csv")
        print(f"\n=== Cohort summary across {df.meal_idx.nunique()} meal events ===")
        print(agg.to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
