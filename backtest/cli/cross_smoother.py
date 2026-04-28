"""Layer 3 + Layer 4: cross-smoother stats + Pareto figure + cohort distributions.

Outputs:
- reports/cross_smoother_tests.csv  (paired Wilcoxon, Holm-corrected)
- reports/smoother_ranking.md  (median + IQR per metric per smoother)
- reports/figs/pareto_noise_vs_delay.png
- reports/figs/dist_<metric>.png  (boxplots, full cohort + by-table split)
"""
from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ALGOS = ("aaps_average", "aaps_exponential", "trio_sgolay", "ukf")
RANKED_METRICS = (
    "xcorr_lag_min",
    "step_response_median_delay_min",
    "phase_shift_delay_min",
    "noise_reduction_ratio",
    "attenuation_signal_band",
    "hypo_preserved_pct",
    "hypo_amp_delta",
    "hypo_time_delta_min",
    "peak_preserved_pct",
    "outlier_absorbed_pct",
)


def _holm_correct(pvals: list[float]) -> list[float]:
    n = len(pvals)
    order = np.argsort(pvals)
    corrected = [0.0] * n
    running_max = 0.0
    for rank, i in enumerate(order):
        adj = pvals[i] * (n - rank)
        running_max = max(running_max, adj)
        corrected[i] = min(running_max, 1.0)
    return corrected


def _paired_tests(per_user: pd.DataFrame, metric: str) -> list[dict]:
    rows = []
    pivot = per_user.pivot_table(index="user_id", columns="algorithm", values=metric)
    pivot = pivot.dropna(how="any")
    if pivot.empty:
        return rows
    raw_pvals = []
    pairs = []
    for a, b in combinations(ALGOS, 2):
        if a not in pivot.columns or b not in pivot.columns:
            continue
        diff = pivot[a] - pivot[b]
        if diff.std() == 0 or len(diff) < 5:
            p = float("nan")
            stat = float("nan")
        else:
            try:
                stat, p = stats.wilcoxon(pivot[a], pivot[b], zero_method="zsplit")
            except ValueError:
                stat, p = float("nan"), float("nan")
        pairs.append((a, b, stat, p, len(pivot), pivot[a].median(), pivot[b].median()))
        raw_pvals.append(p)
    holm = _holm_correct([p if np.isfinite(p) else 1.0 for p in raw_pvals])
    for (a, b, stat, p, n, med_a, med_b), p_holm in zip(pairs, holm):
        rows.append({
            "metric": metric,
            "algo_a": a, "algo_b": b,
            "n_users": int(n),
            "median_a": float(med_a), "median_b": float(med_b),
            "wilcoxon_stat": float(stat) if np.isfinite(stat) else None,
            "p_value": float(p) if np.isfinite(p) else None,
            "p_holm": float(p_holm),
        })
    return rows


def _pareto_figure(per_user: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = {"aaps_average": "tab:blue", "aaps_exponential": "tab:orange",
              "trio_sgolay": "tab:green", "ukf": "tab:red"}
    markers = {"oref_v5": "o", "oref_v6": "s", "oref_v7": "^"}
    for algo in ALGOS:
        for table in ("oref_v5", "oref_v6", "oref_v7"):
            sub = per_user[(per_user.algorithm == algo) & (per_user.table == table)]
            if sub.empty:
                continue
            ax.scatter(
                sub.xcorr_lag_min, sub.noise_reduction_ratio,
                color=colors[algo], marker=markers[table], s=70, alpha=0.7,
                edgecolor="black", linewidth=0.4,
                label=f"{algo} ({table})",
            )
    ax.set_xlabel("xcorr lag (min) — lower is faster")
    ax.set_ylabel("noise reduction ratio (smoothed/raw, lower = more aggressive)")
    ax.set_title("Pareto: noise removal vs delay (one point per (user, algorithm))")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=8, ncol=1)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _dist_figures(per_user: pd.DataFrame, fig_dir: Path) -> None:
    for metric in RANKED_METRICS:
        if metric not in per_user.columns:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(13, 5))

        order = list(ALGOS)
        full_data = [per_user[per_user.algorithm == a][metric].dropna().to_numpy() for a in order]
        axes[0].boxplot(full_data, tick_labels=order, showmeans=True)
        axes[0].set_title(f"{metric} — full cohort")
        axes[0].grid(True, axis="y", alpha=0.3)
        for tick in axes[0].get_xticklabels():
            tick.set_rotation(20)

        by_table = []
        labels = []
        for table in ("oref_v5", "oref_v6", "oref_v7"):
            for a in order:
                vals = per_user[(per_user.algorithm == a) & (per_user.table == table)][metric].dropna().to_numpy()
                by_table.append(vals)
                labels.append(f"{a[:6]}\n{table[-2:]}")
        axes[1].boxplot(by_table, tick_labels=labels, showmeans=True)
        axes[1].set_title(f"{metric} — split by source table")
        axes[1].grid(True, axis="y", alpha=0.3)
        for tick in axes[1].get_xticklabels():
            tick.set_rotation(45)
            tick.set_fontsize(8)
        fig.tight_layout()
        fig.savefig(fig_dir / f"dist_{metric}.png", dpi=110, bbox_inches="tight")
        plt.close(fig)


def _ranking_md(per_user: pd.DataFrame) -> str:
    lines = ["# Smoother ranking (median ± IQR across cohort)\n"]
    for metric in RANKED_METRICS:
        if metric not in per_user.columns:
            continue
        agg = per_user.groupby("algorithm")[metric].agg(
            median="median",
            p25=lambda s: np.nanpercentile(s, 25),
            p75=lambda s: np.nanpercentile(s, 75),
        ).reindex(ALGOS)
        lines.append(f"\n## {metric}\n")
        lines.append("| algorithm | median | p25 | p75 |")
        lines.append("|---|---:|---:|---:|")
        for a in ALGOS:
            if a not in agg.index or pd.isna(agg.loc[a, "median"]):
                continue
            lines.append(
                f"| {a} | {agg.loc[a,'median']:.3f} | "
                f"{agg.loc[a,'p25']:.3f} | {agg.loc[a,'p75']:.3f} |"
            )
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--per-user-metrics", default="reports/per_user_metrics.csv")
    p.add_argument("--out", default="reports")
    args = p.parse_args(argv)

    per_user = pd.read_csv(args.per_user_metrics)
    out_dir = Path(args.out)
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    test_rows = []
    for metric in RANKED_METRICS:
        if metric in per_user.columns:
            test_rows.extend(_paired_tests(per_user, metric))
    pd.DataFrame(test_rows).to_csv(out_dir / "cross_smoother_tests.csv", index=False)
    print(f"Wrote {out_dir/'cross_smoother_tests.csv'} ({len(test_rows)} pairs)")

    md = _ranking_md(per_user)
    (out_dir / "smoother_ranking.md").write_text(md)
    print(f"Wrote {out_dir/'smoother_ranking.md'}")

    _pareto_figure(per_user, fig_dir / "pareto_noise_vs_delay.png")
    print(f"Wrote {fig_dir/'pareto_noise_vs_delay.png'}")

    _dist_figures(per_user, fig_dir)
    print(f"Wrote distribution figures to {fig_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
