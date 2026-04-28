"""Layer 6.1: per-smoother frequency-domain transfer function.

For each (user, algorithm) computes the magnitude of FFT(smoothed)/FFT(raw)
and aggregates across users to produce a transfer-function plot per smoother.
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

GRID_S = 300
ALGOS = ("aaps_average", "aaps_exponential", "trio_sgolay", "ukf")


def _gain(raw: np.ndarray, smo: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (freq_cph, gain) for one (user, algo) pair."""
    valid = np.isfinite(raw) & np.isfinite(smo)
    if valid.sum() < 256:
        return np.array([]), np.array([])
    raw = raw[valid] - raw[valid].mean()
    smo = smo[valid] - smo[valid].mean()
    n = len(raw)
    fs = 1.0 / GRID_S
    freqs_hz = np.fft.rfftfreq(n, d=1.0 / fs)
    raw_fft = np.fft.rfft(raw)
    smo_fft = np.fft.rfft(smo)
    gain = np.abs(smo_fft) / np.maximum(np.abs(raw_fft), 1e-9)
    freqs_cph = freqs_hz * 3600
    return freqs_cph, gain


def _aggregate(per_user_traces: list[tuple[np.ndarray, np.ndarray]],
               n_bins: int = 60) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Resample each user's gain curve onto a common log-spaced freq grid,
    return (freq, median, p25, p75)."""
    if not per_user_traces:
        return np.array([]), np.array([]), np.array([]), np.array([])
    f_min = max(1e-3, min(t[0][1] for t in per_user_traces if len(t[0]) > 1))
    f_max = max(t[0][-1] for t in per_user_traces if len(t[0]) > 1)
    grid = np.logspace(np.log10(f_min), np.log10(f_max), n_bins)
    interp = np.full((len(per_user_traces), n_bins), np.nan)
    for i, (fr, g) in enumerate(per_user_traces):
        if len(fr) < 2:
            continue
        interp[i] = np.interp(grid, fr, g, left=np.nan, right=np.nan)
    return (grid,
            np.nanmedian(interp, axis=0),
            np.nanpercentile(interp, 25, axis=0),
            np.nanpercentile(interp, 75, axis=0))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", default="runs")
    p.add_argument("--out", default="reports")
    p.add_argument("--cohort", default="backtest/cohort.json")
    args = p.parse_args(argv)

    runs_dir = Path(args.runs)
    out_dir = Path(args.out)
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    cohort = json.loads(Path(args.cohort).read_text())["members"]
    by_algo: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {a: [] for a in ALGOS}
    for member in cohort:
        for algo in ALGOS:
            pq = runs_dir / member["user_id"] / f"{algo}.parquet"
            if not pq.exists():
                continue
            df = pd.read_parquet(pq)
            if algo == "trio_sgolay":
                # Pass-3 input_glucose = pass-2 output (already smoothed). Use
                # pass-1.input (raw) and pass-3.output (final) to compute the
                # cumulative three-pass transfer function.
                pass1 = df[df.step_name == "pass1"].sort_values("reading_idx")
                pass3 = df[df.step_name == "pass3"].sort_values("reading_idx")
                raw = pass1.input_glucose.to_numpy()
                smo = pass3.output_glucose.to_numpy()
            else:
                df = df.sort_values("reading_idx")
                raw = df.input_glucose.to_numpy()
                smo = df.output_glucose.to_numpy()
            by_algo[algo].append(_gain(raw, smo))

    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True, sharey=True)
    rows_for_csv: list[dict] = []
    for ax, algo in zip(axes.flatten(), ALGOS):
        grid, med, p25, p75 = _aggregate(by_algo[algo])
        if len(grid) == 0:
            ax.set_title(f"{algo} (no data)")
            continue
        ax.fill_between(grid, p25, p75, alpha=0.3, color="steelblue", label="IQR")
        ax.plot(grid, med, color="steelblue", linewidth=2, label="median")
        ax.axhline(1.0, color="black", linewidth=0.5, linestyle=":")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_ylim(0.05, 2.5)
        ax.set_title(f"{algo}")
        ax.set_xlabel("frequency (cycles/hour)")
        ax.set_ylabel("|H(f)|")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(loc="lower left", fontsize=9)
        for f, g in zip(grid, med):
            rows_for_csv.append({"algorithm": algo, "freq_cph": float(f), "median_gain": float(g)})

    fig.suptitle("Per-smoother transfer function (median ± IQR across users)", y=1.02)
    fig.tight_layout()
    fig.savefig(fig_dir / "transfer_function.png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {fig_dir/'transfer_function.png'}")

    pd.DataFrame(rows_for_csv).to_csv(out_dir / "transfer_function.csv", index=False)
    print(f"Wrote {out_dir/'transfer_function.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
