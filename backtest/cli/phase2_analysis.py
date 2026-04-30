"""Per-sensor metrics + within-user G6/G7 comparison from Phase 2 sensor-tagged traces.

Inputs: runs/phase2/<user>__<sensor>/<algo>.parquet (produced by phase2_run.py)

Outputs (under reports/phase2/):
  per_user_sensor_metrics.csv     — one row per (user, sensor, algorithm)
  per_sensor_summary.csv          — median across users, per (sensor, algorithm)
  within_user_g6_vs_g7.csv        — for users with both, per-algorithm Δ(G7 − G6)
  acceleration_per_sensor.csv     — peak-event acceleration retention per (sensor, algo)
  figs/per_sensor_<metric>.png    — boxplots of each metric, split by sensor
  figs/within_user_g6_vs_g7.png   — paired plot for the 3 transition users
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backtest import metrics

ALGOS = ("aaps_average", "aaps_exponential", "ukf")
ALGO_PRETTY = {
    "aaps_average": "AAPS Average",
    "aaps_exponential": "AAPS Exp",
    "ukf": "UKF",
}
SENSOR_COLORS = {"G6": "tab:blue", "G7": "tab:orange", "Libre2": "tab:green"}
ALGO_COLORS = {
    "aaps_average": "tab:blue",
    "aaps_exponential": "tab:orange",
    "ukf": "tab:red",
}
DT_MIN = 5.0


def _load_pair(runs_root: Path, tag: str, algo: str) -> tuple[np.ndarray, np.ndarray] | None:
    pq = runs_root / tag / f"{algo}.parquet"
    if not pq.exists():
        return None
    df = pd.read_parquet(pq)
    df = df.sort_values("reading_idx")
    return df.input_glucose.to_numpy(), df.output_glucose.to_numpy()


def _peak_event_accel_retention(raw: np.ndarray, smo: np.ndarray) -> tuple[float, int]:
    """Median peak-acceleration retention during sustained ≥0.5 mg/dL/min events ≥15 min."""
    if len(raw) < 6:
        return float("nan"), 0
    rate_raw = np.diff(raw) / DT_MIN
    raw_accel = (raw[2:] - 2 * raw[1:-1] + raw[:-2]) / (DT_MIN ** 2)
    smo_accel = (smo[2:] - 2 * smo[1:-1] + smo[:-2]) / (DT_MIN ** 2)
    sustained = np.abs(rate_raw) >= 0.5
    events: list[tuple[int, int]] = []
    i = 0
    while i < len(sustained):
        if sustained[i]:
            j = i
            while j < len(sustained) and sustained[j]:
                j += 1
            if j - i >= 3:
                events.append((i, j))
            i = j
        else:
            i += 1
    if not events:
        return float("nan"), 0
    ratios = []
    for s, e in events:
        a_s = max(0, s - 1)
        a_e = min(len(raw_accel), e)
        if a_e <= a_s:
            continue
        peak_raw = float(np.max(np.abs(raw_accel[a_s:a_e])))
        peak_smo = float(np.max(np.abs(smo_accel[a_s:a_e])))
        if peak_raw > 0:
            ratios.append(peak_smo / peak_raw)
    if not ratios:
        return float("nan"), 0
    return float(np.median(ratios)), len(ratios)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", default="runs/phase2")
    p.add_argument("--out", default="reports/phase2")
    args = p.parse_args(argv)

    runs_root = Path(args.runs)
    out_root = Path(args.out)
    fig_dir = out_root / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    pair_dirs = [d for d in runs_root.iterdir() if d.is_dir() and "__" in d.name]
    rows = []
    accel_rows = []
    for d in sorted(pair_dirs):
        user_id, sensor = d.name.split("__", 1)
        for algo in ALGOS:
            loaded = _load_pair(runs_root, d.name, algo)
            if loaded is None:
                continue
            raw, smo = loaded
            m = metrics.compute_per_user(
                raw, smo, user_id=user_id, table=f"phase2_{sensor}", algorithm=algo,
            )
            row = metrics.to_dict(m)
            row["sensor_type"] = sensor
            rows.append(row)
            peak_ret, n_ev = _peak_event_accel_retention(raw, smo)
            accel_rows.append({
                "user_id": user_id, "sensor_type": sensor, "algorithm": algo,
                "peak_event_accel_retention": peak_ret, "n_events": n_ev,
            })

    per_user = pd.DataFrame(rows)
    per_user.to_csv(out_root / "per_user_sensor_metrics.csv", index=False)
    print(f"Wrote {out_root}/per_user_sensor_metrics.csv ({len(per_user)} rows)")

    accel_df = pd.DataFrame(accel_rows)
    accel_df.to_csv(out_root / "acceleration_per_sensor.csv", index=False)
    print(f"Wrote {out_root}/acceleration_per_sensor.csv ({len(accel_df)} rows)")

    # ------- per-sensor summary across users -------
    summary_metrics = [
        "noise_reduction_ratio", "phase_shift_delay_min",
        "xcorr_lag_min", "step_response_median_delay_min",
        "attenuation_signal_band", "hypo_preserved_pct",
        "hypo_amp_delta", "outlier_absorbed_pct",
    ]
    avail = [c for c in summary_metrics if c in per_user.columns]
    summary = (per_user.groupby(["sensor_type", "algorithm"])[avail]
               .median().round(3).reset_index())
    accel_summary = (accel_df.groupby(["sensor_type", "algorithm"])
                     .peak_event_accel_retention.median().round(3)
                     .reset_index())
    summary = summary.merge(accel_summary, on=["sensor_type", "algorithm"], how="left")
    summary.to_csv(out_root / "per_sensor_summary.csv", index=False)
    print(f"Wrote {out_root}/per_sensor_summary.csv")

    # ------- within-user G6 vs G7 -------
    transition_users = sorted(set(per_user[per_user.sensor_type == "G6"].user_id) &
                              set(per_user[per_user.sensor_type == "G7"].user_id))
    print(f"\nWithin-user G6→G7 transitions: {transition_users}")
    pivot = per_user.pivot_table(
        index=["user_id", "algorithm"], columns="sensor_type",
        values=summary_metrics + ["hypo_preserved_pct"],
    ).reset_index()
    delta_rows = []
    for uid in transition_users:
        for algo in ALGOS:
            row_g6 = per_user[(per_user.user_id == uid) & (per_user.sensor_type == "G6")
                              & (per_user.algorithm == algo)]
            row_g7 = per_user[(per_user.user_id == uid) & (per_user.sensor_type == "G7")
                              & (per_user.algorithm == algo)]
            if row_g6.empty or row_g7.empty:
                continue
            r6 = row_g6.iloc[0]
            r7 = row_g7.iloc[0]
            delta_rows.append({
                "user_id": uid, "algorithm": algo,
                "noise_ratio_G6": r6.noise_reduction_ratio,
                "noise_ratio_G7": r7.noise_reduction_ratio,
                "noise_ratio_delta": r7.noise_reduction_ratio - r6.noise_reduction_ratio,
                "phase_shift_G6": r6.phase_shift_delay_min,
                "phase_shift_G7": r7.phase_shift_delay_min,
                "outlier_absorbed_G6": r6.outlier_absorbed_pct,
                "outlier_absorbed_G7": r7.outlier_absorbed_pct,
                "hypo_preserved_G6": r6.hypo_preserved_pct,
                "hypo_preserved_G7": r7.hypo_preserved_pct,
            })
    within = pd.DataFrame(delta_rows)
    within.to_csv(out_root / "within_user_g6_vs_g7.csv", index=False)
    print(f"Wrote {out_root}/within_user_g6_vs_g7.csv ({len(within)} rows)")

    # ------- figures -------
    # Per-sensor distributions (one figure per metric of interest)
    for metric in ["noise_reduction_ratio", "phase_shift_delay_min",
                   "outlier_absorbed_pct", "hypo_preserved_pct"]:
        if metric not in per_user.columns:
            continue
        fig, ax = plt.subplots(figsize=(11, 5))
        positions = []
        labels = []
        data = []
        colors = []
        x = 0
        for sensor in ("G6", "G7"):
            for algo in ALGOS:
                vals = per_user[(per_user.sensor_type == sensor) &
                                (per_user.algorithm == algo)][metric].dropna()
                if vals.empty:
                    continue
                data.append(vals.to_numpy())
                positions.append(x); colors.append(ALGO_COLORS[algo])
                labels.append(f"{ALGO_PRETTY[algo]}\n{sensor}")
                x += 1
            x += 0.7
        bp = ax.boxplot(data, positions=positions, tick_labels=labels,
                        showmeans=True, patch_artist=True, widths=0.6)
        for patch, c in zip(bp["boxes"], colors):
            patch.set_facecolor(c); patch.set_alpha(0.5)
        ax.set_title(f"{metric} — by sensor (n_users G6={sum(per_user[(per_user.algorithm=='aaps_average')&(per_user.sensor_type=='G6')].user_id.nunique() for _ in [0])}, "
                     f"G7={per_user[(per_user.algorithm=='aaps_average')&(per_user.sensor_type=='G7')].user_id.nunique()})")
        ax.grid(True, axis="y", alpha=0.3)
        for tick in ax.get_xticklabels():
            tick.set_rotation(30); tick.set_fontsize(8)
        fig.tight_layout()
        fig.savefig(fig_dir / f"per_sensor_{metric}.png", dpi=120, bbox_inches="tight")
        plt.close(fig)
    print(f"Wrote per-sensor distribution figures to {fig_dir}")

    # Within-user G6 vs G7 paired figure (one panel per metric)
    if not within.empty:
        fig, axes = plt.subplots(1, 4, figsize=(16, 5), sharey=False)
        plot_specs = [
            ("noise_ratio", "Noise reduction ratio (lower = more aggressive)"),
            ("outlier_absorbed", "Outlier absorption %"),
            ("hypo_preserved", "Hypo events preserved %"),
            ("phase_shift", "Phase shift (min)"),
        ]
        for ax, (key, title) in zip(axes, plot_specs):
            for algo in ALGOS:
                sub = within[within.algorithm == algo]
                if sub.empty: continue
                for _, r in sub.iterrows():
                    ax.plot([0, 1], [r[f"{key}_G6"], r[f"{key}_G7"]],
                            color=ALGO_COLORS[algo], alpha=0.45,
                            marker="o", linewidth=1.0)
                ax.scatter([0]*len(sub), sub[f"{key}_G6"],
                           color=ALGO_COLORS[algo], s=60, zorder=4,
                           label=ALGO_PRETTY[algo] if ax is axes[0] else None)
                ax.scatter([1]*len(sub), sub[f"{key}_G7"],
                           color=ALGO_COLORS[algo], s=60, zorder=4)
            ax.set_xticks([0, 1])
            ax.set_xticklabels(["G6", "G7"])
            ax.set_title(title, fontsize=10)
            ax.grid(True, axis="y", alpha=0.3)
            if ax is axes[0]:
                ax.legend(loc="best", fontsize=9)
        fig.suptitle(
            f"Within-user G6 → G7 transition (paired, n={within.user_id.nunique()} users)",
            y=1.02,
        )
        fig.tight_layout()
        fig.savefig(fig_dir / "within_user_g6_vs_g7.png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        print(f"Wrote {fig_dir}/within_user_g6_vs_g7.png")

    # ------- print headline tables -------
    print("\n=== Per-sensor median (across users) ===\n")
    print(summary.to_string(index=False))
    print("\n=== Within-user G6 → G7 deltas (per user × algorithm) ===\n")
    if not within.empty:
        print(within.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
