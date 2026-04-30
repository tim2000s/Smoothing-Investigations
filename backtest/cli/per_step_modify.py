"""Layer 1: per-step modification analysis.

For every (algorithm, user, step), quantifies the Δ between consecutive pipeline
steps. Output: per_step_modification.csv with median, p25, p75, p95, frac-changed.
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


def _delta_stats(deltas: np.ndarray) -> dict:
    abs_d = np.abs(deltas)
    return {
        "median_abs_delta": float(np.median(abs_d)),
        "p25_abs_delta": float(np.percentile(abs_d, 25)),
        "p75_abs_delta": float(np.percentile(abs_d, 75)),
        "p95_abs_delta": float(np.percentile(abs_d, 95)),
        "frac_changed": float((abs_d > 0).mean()),
        "n": int(len(deltas)),
    }


def _stats_for_user_algo(user_id: str, table: str, algo: str, runs_dir: Path) -> list[dict]:
    pq = runs_dir / user_id / f"{algo}.parquet"
    if not pq.exists():
        return []
    df = pd.read_parquet(pq)
    rows: list[dict] = []

    if algo in ("aaps_average", "aaps_exponential"):
        deltas = df["output_glucose"].to_numpy() - df["input_glucose"].to_numpy()
        rows.append({
            "user_id": user_id, "table": table, "algorithm": algo, "step": "filter",
            **_delta_stats(deltas[np.isfinite(deltas)]),
        })

    elif algo == "ukf":
        d_predict = df["x_pred_g"].to_numpy() - df["input_glucose"].to_numpy()
        d_update = df["x_upd_g"].to_numpy() - df["x_pred_g"].to_numpy()
        d_rts = df["output_glucose"].to_numpy() - df["x_upd_g"].to_numpy()
        for step, deltas in [("predict", d_predict), ("update", d_update), ("rts", d_rts)]:
            rows.append({
                "user_id": user_id, "table": table, "algorithm": algo, "step": step,
                **_delta_stats(deltas[np.isfinite(deltas)]),
            })
        outlier_rows = df[df["is_outlier"]]
        if len(outlier_rows) > 0:
            d_out = outlier_rows["x_upd_g"].to_numpy() - outlier_rows["x_pred_g"].to_numpy()
            rows.append({
                "user_id": user_id, "table": table, "algorithm": algo,
                "step": "update@outlier",
                **_delta_stats(d_out[np.isfinite(d_out)]),
            })
    return rows


def _plot_stack(per_step_df: pd.DataFrame, out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, algo in zip(axes.flatten(), ("aaps_average", "aaps_exponential", "ukf")):
        sub = per_step_df[per_step_df.algorithm == algo]
        if sub.empty:
            ax.set_title(f"{algo} (no data)")
            continue
        agg = sub.groupby("step").median_abs_delta.median().reset_index()
        ax.bar(agg.step, agg.median_abs_delta, color="steelblue", edgecolor="black")
        ax.set_title(f"{algo}: median |Δ| per step (median across users)")
        ax.set_ylabel("mg/dL")
        ax.set_xlabel("pipeline step")
        for x, v in zip(agg.step, agg.median_abs_delta):
            ax.text(x, v, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


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

    cohort_payload = json.loads(Path(args.cohort).read_text())
    cohort_members = cohort_payload["members"]

    rows: list[dict] = []
    for member in cohort_members:
        for algo in ("aaps_average", "aaps_exponential", "ukf"):
            rows.extend(_stats_for_user_algo(
                member["user_id"], member["table"], algo, runs_dir,
            ))

    if not rows:
        print("No traces found in", runs_dir)
        return 1

    df = pd.DataFrame(rows)
    csv_path = out_dir / "per_step_modification.csv"
    df.to_csv(csv_path, index=False)
    print(f"Wrote {csv_path} ({len(df)} rows)")

    fig_path = fig_dir / "per_step_modification.png"
    _plot_stack(df, fig_path)
    print(f"Wrote {fig_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
