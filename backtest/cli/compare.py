"""Layer 2 + Layer 5: per-(user,smoother) characterization metrics + drill-down windows.

Outputs:
- reports/per_user_metrics.csv (one row per (user, algorithm))
- reports/figs/<user>/window_{calm,hypo,postmeal}.png  (3 windows × user × overlay)
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

from backtest import metrics


def _load_smoothed(user: str, algo: str, runs_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    pq = runs_dir / user / f"{algo}.parquet"
    if not pq.exists():
        return None
    df = pd.read_parquet(pq)
    if algo == "trio_sgolay":
        # Pass-3's input_glucose is pass-2's output, NOT the original raw.
        # End-to-end metrics need raw = pass1.input and smoothed = pass3.output.
        pass1 = df[df.step_name == "pass1"].sort_values("reading_idx")
        pass3 = df[df.step_name == "pass3"].sort_values("reading_idx")
        return (
            pass1.ts_sec.to_numpy(),
            pass1.input_glucose.to_numpy(),
            pass3.output_glucose.to_numpy(),
        )
    df = df.sort_values("reading_idx")
    return (
        df.ts_sec.to_numpy(),
        df.input_glucose.to_numpy(),
        df.output_glucose.to_numpy(),
    )


def _pick_windows(ts: np.ndarray, raw: np.ndarray, hours: int = 6) -> dict[str, slice]:
    n = len(raw)
    win = int(hours * 60 / 5)
    if n < win:
        return {}
    nadirs = np.full(n, np.nan)
    peaks = np.full(n, np.nan)
    rolling_std = pd.Series(raw).rolling(win, min_periods=win // 2).std().to_numpy()
    rolling_min = pd.Series(raw).rolling(win, min_periods=win // 2).min().to_numpy()
    rolling_max = pd.Series(raw).rolling(win, min_periods=win // 2).max().to_numpy()

    calm_idx = int(np.nanargmin(rolling_std)) if np.isfinite(rolling_std).any() else win
    hypo_candidates = np.where(rolling_min < 70)[0]
    hypo_idx = int(hypo_candidates[len(hypo_candidates) // 2]) if len(hypo_candidates) > 0 else int(np.nanargmin(rolling_min))
    peak_candidates = np.where(rolling_max > 220)[0]
    postmeal_idx = int(peak_candidates[len(peak_candidates) // 2]) if len(peak_candidates) > 0 else int(np.nanargmax(rolling_max))

    return {
        "calm": slice(max(0, calm_idx - win), min(n, calm_idx)),
        "hypo": slice(max(0, hypo_idx - win // 2), min(n, hypo_idx + win // 2)),
        "postmeal": slice(max(0, postmeal_idx - win // 2), min(n, postmeal_idx + win // 2)),
    }


def _plot_windows(user: str, runs_dir: Path, fig_dir: Path) -> None:
    base = _load_smoothed(user, "aaps_average", runs_dir)
    if base is None:
        return
    ts, raw, _ = base
    windows = _pick_windows(ts, raw)
    if not windows:
        return

    smoothed_by_algo: dict[str, np.ndarray] = {}
    for algo in ("aaps_average", "aaps_exponential", "trio_sgolay", "ukf"):
        loaded = _load_smoothed(user, algo, runs_dir)
        if loaded is not None:
            smoothed_by_algo[algo] = loaded[2]

    user_dir = fig_dir / user
    user_dir.mkdir(parents=True, exist_ok=True)
    for label, sl in windows.items():
        fig, ax = plt.subplots(figsize=(11, 5))
        x = (ts[sl] - ts[sl][0]) / 60.0
        ax.plot(x, raw[sl], color="black", alpha=0.5, label="raw", linewidth=1.0)
        for algo, color in (
            ("aaps_average", "tab:blue"),
            ("aaps_exponential", "tab:orange"),
            ("trio_sgolay", "tab:green"),
            ("ukf", "tab:red"),
        ):
            if algo in smoothed_by_algo and len(smoothed_by_algo[algo]) >= sl.stop:
                ax.plot(x, smoothed_by_algo[algo][sl], color=color, label=algo, linewidth=1.5)
        ax.set_title(f"{user} — {label} window")
        ax.set_xlabel("minutes")
        ax.set_ylabel("mg/dL")
        ax.legend(loc="best", fontsize=9)
        fig.tight_layout()
        fig.savefig(user_dir / f"window_{label}.png", dpi=110, bbox_inches="tight")
        plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", default="runs")
    p.add_argument("--out", default="reports")
    p.add_argument("--cohort", default="backtest/cohort.json")
    p.add_argument("--no-figs", action="store_true", help="Skip drill-down window figures")
    args = p.parse_args(argv)

    runs_dir = Path(args.runs)
    out_dir = Path(args.out)
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    cohort_payload = json.loads(Path(args.cohort).read_text())
    rows = []
    for member in cohort_payload["members"]:
        for algo in ("aaps_average", "aaps_exponential", "trio_sgolay", "ukf"):
            loaded = _load_smoothed(member["user_id"], algo, runs_dir)
            if loaded is None:
                continue
            ts, raw, smo = loaded
            m = metrics.compute_per_user(raw, smo,
                user_id=member["user_id"], table=member["table"], algorithm=algo)
            rows.append(metrics.to_dict(m))
        if not args.no_figs:
            _plot_windows(member["user_id"], runs_dir, fig_dir)

    if not rows:
        print("No traces in", runs_dir)
        return 1

    df = pd.DataFrame(rows)
    csv_path = out_dir / "per_user_metrics.csv"
    df.to_csv(csv_path, index=False)
    print(f"Wrote {csv_path} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
