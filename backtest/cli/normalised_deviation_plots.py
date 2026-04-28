"""Median per-smoother deviation from raw, aligned at a common event point.

Generates three-panel figures showing each smoother's median deviation from raw
across glucose (Δsgv), rate (Δdelta) and acceleration (Δaccel), with the IQR
band, for two event-aligned views:

  --target meal       align at every meal-tagged event in `data/nstest3/`
                      (window: meal − pre_min … meal + post_min)
  --target rate_rise  align at the first sample of each sustained rate-rise
                      event (raw rate ≥ 0.5 mg/dL/min for ≥ 15 min) found in
                      every Phase 3 trace under `runs/`
                      (window: event − 30 min … event + 60 min)

For each (smoother, time-offset, axis) the script computes the median and the
IQR of the deviation across all aligned events. Every event contributes one
sample at every time-offset; a time-offset's reported median is robust to
individual-event outliers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backtest.smoothers import AapsAverage, AapsExponential, TrioSGolay, UKF

GRID_S = 300

ALGOS = [
    ("aaps_average", AapsAverage, "tab:blue"),
    ("aaps_exponential", AapsExponential, "tab:orange"),
    ("trio_sgolay", TrioSGolay, "tab:green"),
    ("ukf", UKF, "tab:red"),
]


def rate_seq(g: np.ndarray) -> np.ndarray:
    out = np.full(len(g), np.nan)
    out[1:] = np.diff(g) / 5.0
    return out


def accel_seq(rate: np.ndarray) -> np.ndarray:
    out = np.full(len(rate), np.nan)
    out[1:] = np.diff(rate) / 5.0
    return out


# ----------------------------------------------------------------------
# Event extraction
# ----------------------------------------------------------------------

def collect_meal_aligned(data_dir: Path, *, pre_min: int, post_min: int):
    """Yield (raw_g, ts_rel_sec, event_idx) per meal in data/nstest3/."""
    treatments = json.loads((data_dir / "treatments_meals.json").read_text())
    entries = pd.DataFrame(json.loads((data_dir / "entries.json").read_text()))
    if "type" in entries.columns:
        entries = entries[entries["type"] == "sgv"].copy()
    entries["date"] = entries["date"].astype("int64")
    entries["sgv"] = pd.to_numeric(entries["sgv"], errors="coerce")
    entries = entries.dropna(subset=["sgv"]).sort_values("date").reset_index(drop=True)

    for t in treatments:
        ts_ms = t.get("date") or 0
        if not ts_ms:
            continue
        lo = int(ts_ms) - pre_min * 60_000
        hi = int(ts_ms) + post_min * 60_000
        sub = entries[(entries["date"] >= lo) & (entries["date"] <= hi)].copy()
        if len(sub) < 12:
            continue
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
        run = 0
        for i, present in enumerate(raw_idx):
            if present:
                run = 0
            else:
                run += 1
                if run > 1:
                    glucose[i] = np.nan
        if not np.isfinite(glucose).any():
            continue
        ts_rel = g["ts_grid"].to_numpy(dtype="int64") - t0
        event_offset_s = (int(ts_ms) // 1000) - t0
        event_idx = int(np.argmin(np.abs(ts_rel - event_offset_s)))
        yield glucose, ts_rel, event_idx


def collect_calm_aligned(runs_dir: Path, *, max_per_user: int = 10,
                         calm_min: int = 60,
                         pre_min: int = 30,
                         post_min: int = 60,
                         glucose_lo: float = 70.0,
                         glucose_hi: float = 180.0,
                         max_abs_rate: float = 0.3):
    """Yield calm windows: raw glucose stays in [70, 180] and |raw rate| stays
    below 0.3 mg/dL/min for ≥ calm_min minutes. Window: pre_min before the
    calm start through post_min after."""
    n_calm = calm_min // 5
    n_pre = pre_min // 5
    n_post = post_min // 5
    for user_dir in sorted(runs_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        pq = user_dir / "aaps_average.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq).sort_values("reading_idx")
        raw = df.input_glucose.to_numpy()
        ts = df.ts_sec.to_numpy()
        rate = rate_seq(raw)
        events_found = 0
        i = n_pre + 1
        while i < len(raw) - n_calm and events_found < max_per_user:
            window = slice(i, i + n_calm)
            if (np.isfinite(raw[window]).all()
                    and (raw[window] >= glucose_lo).all()
                    and (raw[window] <= glucose_hi).all()
                    and np.isfinite(rate[window]).all()
                    and (np.abs(rate[window]) < max_abs_rate).all()):
                lo = max(0, i - n_pre)
                hi = min(len(raw), i + n_post + 1)
                if hi - lo < 8:
                    i += n_calm
                    continue
                window_raw = raw[lo:hi].copy()
                window_ts = (ts[lo:hi] - ts[lo]).astype("int64")
                event_idx = i - lo
                yield window_raw, window_ts, event_idx
                events_found += 1
                i += n_calm + n_pre  # skip past this window
            else:
                i += 1


def collect_rate_rise_aligned(runs_dir: Path, *, max_per_user: int = 20,
                              rate_thresh: float = 0.5,
                              min_sustained_samples: int = 3):
    """For each user, scan the AAPS Average trace's input_glucose (= raw) for
    sustained rate-rise events. Yield (raw_g, ts_rel_sec, event_idx) windows.

    A "rate-rise event" starts at sample i if rate[i] ≥ rate_thresh and the
    next (min_sustained_samples - 1) samples also have rate ≥ rate_thresh.
    Window: 30 min before through 60 min after the event start.
    """
    for user_dir in sorted(runs_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        pq = user_dir / "aaps_average.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq).sort_values("reading_idx")
        raw = df.input_glucose.to_numpy()
        ts = df.ts_sec.to_numpy()
        if len(raw) < 30:
            continue
        rate = rate_seq(raw)
        events_found = 0
        i = 0
        while i < len(rate) - min_sustained_samples and events_found < max_per_user:
            if all(np.isfinite(rate[i:i + min_sustained_samples])) and \
               (rate[i:i + min_sustained_samples] >= rate_thresh).all():
                # Window: 30 min before, 60 min after
                lo = max(0, i - 6)   # 30 min = 6 samples
                hi = min(len(raw), i + 13)  # 60 min = 12 samples + 1 inclusive
                if hi - lo < 12:
                    i += min_sustained_samples
                    continue
                window_raw = raw[lo:hi].copy()
                window_ts = (ts[lo:hi] - ts[lo]).astype("int64")
                event_idx = i - lo
                yield window_raw, window_ts, event_idx
                events_found += 1
                i += 12  # skip ahead 60 min to avoid re-detecting the same event
            else:
                i += 1


# ----------------------------------------------------------------------
# Deviation accumulation
# ----------------------------------------------------------------------

def deviations_for_event(raw_g: np.ndarray, ts_rel: np.ndarray, event_idx: int,
                         *, before_min: int, after_min: int):
    """Return per-smoother arrays of (Δsgv, Δdelta, Δaccel) at each time-offset.

    Indexed by time offset in samples relative to event_idx, ranging from
    -before_min/5 to +after_min/5 inclusive.
    """
    present = np.isfinite(raw_g)
    g_in = raw_g[present]
    ts_in = ts_rel[present]
    if len(g_in) < 6:
        return None

    raw_rate = rate_seq(raw_g)
    raw_accel = accel_seq(raw_rate)

    n_before = before_min // 5
    n_after = after_min // 5
    offsets = np.arange(-n_before, n_after + 1)

    out_per_algo = {}
    for name, cls, _ in ALGOS:
        smo = np.full(len(raw_g), np.nan)
        try:
            res = cls().process(g_in, ts_in)
            out_idx = np.where(present)[0]
            if len(res.smoothed) == len(g_in):
                smo[out_idx] = res.smoothed
            else:
                missing = len(g_in) - len(res.smoothed)
                smo[out_idx[missing:]] = res.smoothed
        except Exception:
            continue
        smo_rate = rate_seq(smo)
        smo_accel = accel_seq(smo_rate)
        d_sgv = smo - raw_g
        d_delta = smo_rate - raw_rate
        d_accel = smo_accel - raw_accel

        per_offset = []
        for o in offsets:
            i = event_idx + o
            if 0 <= i < len(raw_g):
                per_offset.append((d_sgv[i], d_delta[i], d_accel[i]))
            else:
                per_offset.append((np.nan, np.nan, np.nan))
        out_per_algo[name] = (offsets, per_offset)
    return out_per_algo, offsets


def aggregate(events: list, label: str, *, before_min: int, after_min: int):
    """Run deviations_for_event over every event, stack per (algo, offset),
    return per-algo dicts of {offset: (median_sgv, p25, p75, median_delta, ...)}.
    """
    by_algo = {name: {"sgv": [], "delta": [], "accel": []} for name, _, _ in ALGOS}
    n_used = 0
    expected_offsets = None
    for raw_g, ts_rel, event_idx in events:
        out = deviations_for_event(raw_g, ts_rel, event_idx,
                                   before_min=before_min, after_min=after_min)
        if out is None:
            continue
        per_algo, offsets = out
        if expected_offsets is None:
            expected_offsets = offsets
        for name, _, _ in ALGOS:
            if name not in per_algo:
                continue
            _, per_offset = per_algo[name]
            d_sgv = np.array([p[0] for p in per_offset])
            d_delta = np.array([p[1] for p in per_offset])
            d_accel = np.array([p[2] for p in per_offset])
            by_algo[name]["sgv"].append(d_sgv)
            by_algo[name]["delta"].append(d_delta)
            by_algo[name]["accel"].append(d_accel)
        n_used += 1

    summarised = {}
    for name, _, _ in ALGOS:
        if not by_algo[name]["sgv"]:
            continue
        sgv = np.array(by_algo[name]["sgv"])
        delta = np.array(by_algo[name]["delta"])
        accel = np.array(by_algo[name]["accel"])
        with np.errstate(all="ignore"):
            summarised[name] = {
                "sgv_med": np.nanmedian(sgv, axis=0),
                "sgv_p25": np.nanpercentile(sgv, 25, axis=0),
                "sgv_p75": np.nanpercentile(sgv, 75, axis=0),
                "delta_med": np.nanmedian(delta, axis=0),
                "delta_p25": np.nanpercentile(delta, 25, axis=0),
                "delta_p75": np.nanpercentile(delta, 75, axis=0),
                "accel_med": np.nanmedian(accel, axis=0),
                "accel_p25": np.nanpercentile(accel, 25, axis=0),
                "accel_p75": np.nanpercentile(accel, 75, axis=0),
            }
    return summarised, expected_offsets, n_used


def plot_deviations(summarised, offsets, n_events, label, out_path):
    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    x_min = offsets * 5  # offsets in samples → minutes

    panels = [
        ("sgv_med", "sgv_p25", "sgv_p75", "Δ glucose (smoothed − raw)", "mg/dL"),
        ("delta_med", "delta_p25", "delta_p75", "Δ delta (smoothed_delta − raw_delta)", "mg/dL/min"),
        ("accel_med", "accel_p25", "accel_p75", "Δ acceleration (smoothed_accel − raw_accel)", "mg/dL/min²"),
    ]

    for ax, (med_key, p25_key, p75_key, title, unit) in zip(axes, panels):
        for name, _, color in ALGOS:
            if name not in summarised: continue
            s = summarised[name]
            ax.plot(x_min, s[med_key], color=color, lw=1.6, label=name)
            ax.fill_between(x_min, s[p25_key], s[p75_key], color=color, alpha=0.15)
        ax.axhline(0, color="black", lw=0.5, linestyle=":")
        ax.axvline(0, color="black", lw=0.5, linestyle=":")
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel(f"minutes from event")
    axes[0].legend(loc="best", fontsize=9)
    fig.suptitle(f"{label} — median deviation per smoother (line), IQR (band), n={n_events} events",
                 y=1.0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_calm_examples(events: list, out_path: Path, n_examples: int = 6) -> None:
    """Plot a 2×3 grid of example calm windows from different users, each panel
    showing raw + 4 smoothers overlaid on a zoomed glucose axis."""
    if len(events) < n_examples:
        n_examples = len(events)
    if n_examples == 0:
        return
    rows, cols = 2, 3
    fig, axes = plt.subplots(rows, cols, figsize=(15, 9))
    indices = np.linspace(0, len(events) - 1, n_examples, dtype=int)
    for ax, k in zip(axes.flatten(), indices):
        raw_g, ts_rel, event_idx = events[k]
        present = np.isfinite(raw_g)
        g_in = raw_g[present]
        ts_in = ts_rel[present]
        x_min = (ts_rel - ts_rel[event_idx]) / 60.0  # minutes from calm start
        ax.plot(x_min, raw_g, color="black", lw=1.2, alpha=0.55, label="raw")
        for name, cls, color in ALGOS:
            try:
                res = cls().process(g_in, ts_in)
                smo = np.full(len(raw_g), np.nan)
                out_idx = np.where(present)[0]
                if len(res.smoothed) == len(g_in):
                    smo[out_idx] = res.smoothed
                else:
                    missing = len(g_in) - len(res.smoothed)
                    smo[out_idx[missing:]] = res.smoothed
                ax.plot(x_min, smo, color=color, lw=1.5, label=name)
            except Exception:
                pass
        ax.axvline(0, color="black", lw=0.5, linestyle=":")
        finite = raw_g[np.isfinite(raw_g)]
        if len(finite):
            mid = float(np.median(finite))
            ax.set_ylim(mid - 12, mid + 12)
        ax.set_xlabel("minutes from calm window start")
        ax.set_ylabel("glucose (mg/dL)")
        ax.grid(True, alpha=0.3)
    axes[0, 0].legend(loc="best", fontsize=8)
    fig.suptitle(
        f"Calm-period example windows — raw + 4 smoothers, zoomed (±12 mg/dL around median)\n"
        f"Calm definition: 60+ min where raw is 70–180 mg/dL and |rate| < 0.3 mg/dL/min throughout",
        y=1.0,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["meal", "rate_rise", "calm", "all"], default="all")
    p.add_argument("--data-dir", default="data/nstest3", help="Meal data root")
    p.add_argument("--runs-dir", default="runs", help="Phase 3 traces root")
    p.add_argument("--out-dir", default="reports/deviation_plots")
    p.add_argument("--meal-pre-min", type=int, default=60)
    p.add_argument("--meal-post-min", type=int, default=180)
    p.add_argument("--rate-rise-before-min", type=int, default=30)
    p.add_argument("--rate-rise-after-min", type=int, default=60)
    p.add_argument("--max-per-user", type=int, default=20,
                   help="Cap rate-rise events per user")
    p.add_argument("--max-per-user-calm", type=int, default=10,
                   help="Cap calm-period events per user")
    p.add_argument("--calm-min", type=int, default=60,
                   help="Min duration of a calm window in minutes")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.target in ("meal", "both"):
        events = list(collect_meal_aligned(
            Path(args.data_dir),
            pre_min=args.meal_pre_min, post_min=args.meal_post_min,
        ))
        print(f"Meal events: {len(events)}")
        summarised, offsets, n_used = aggregate(
            events, label="Meal-aligned",
            before_min=args.meal_pre_min, after_min=args.meal_post_min,
        )
        out_path = out_dir / "meal_aligned_deviations.png"
        plot_deviations(summarised, offsets, n_used,
                        label="Nightscout meal-tagged events", out_path=out_path)
        print(f"  → {out_path} ({n_used} valid events)")

    if args.target in ("rate_rise", "all", "both"):
        events = list(collect_rate_rise_aligned(
            Path(args.runs_dir), max_per_user=args.max_per_user,
        ))
        print(f"Generic rate-rise events: {len(events)}")
        summarised, offsets, n_used = aggregate(
            events, label="Rate-rise aligned",
            before_min=args.rate_rise_before_min, after_min=args.rate_rise_after_min,
        )
        out_path = out_dir / "rate_rise_aligned_deviations.png"
        plot_deviations(summarised, offsets, n_used,
                        label="Phase 3 cohort, sustained rate-rise events (≥0.5 mg/dL/min for ≥15 min)",
                        out_path=out_path)
        print(f"  → {out_path} ({n_used} valid events)")

    if args.target in ("calm", "all"):
        calm_pre = 30
        calm_post = 60
        events = list(collect_calm_aligned(
            Path(args.runs_dir),
            max_per_user=args.max_per_user_calm,
            calm_min=args.calm_min,
            pre_min=calm_pre, post_min=calm_post,
        ))
        print(f"Calm-period events: {len(events)}")
        summarised, offsets, n_used = aggregate(
            events, label="Calm-period aligned",
            before_min=calm_pre, after_min=calm_post,
        )
        out_path = out_dir / "calm_aligned_deviations.png"
        plot_deviations(
            summarised, offsets, n_used,
            label=(f"Phase 3 cohort, calm windows (raw 70–180 mg/dL, "
                   f"|rate| < 0.3 mg/dL/min for ≥{args.calm_min} min)"),
            out_path=out_path,
        )
        print(f"  → {out_path} ({n_used} valid events)")

        # Also plot a 2×3 grid of example calm windows from different users
        ex_path = out_dir / "calm_example_windows.png"
        plot_calm_examples(events, ex_path, n_examples=6)
        print(f"  → {ex_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
