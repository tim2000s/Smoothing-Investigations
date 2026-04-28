"""Layer 6.2: unsupervised user phenotype clustering on the per-user metric profile.

Tests whether user phenotypes exist where different smoothers win.
- Pivot per-user metrics to one row per user (averaged across algorithms or per-algo cols).
- KMeans with k=2..4, pick best by silhouette.
- 2D PCA scatter coloured by best-performing smoother per user.
- Output: user_phenotypes.csv + figs/user_phenotypes.png.

Per the plan: report cluster results only when silhouette ≥ 0.4; otherwise note
that the smoother ranking is robust across user phenotypes.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ALGOS = ("aaps_average", "aaps_exponential", "trio_sgolay", "ukf")
RANKED_FOR_BEST = "noise_reduction_ratio"  # lower = better
SILHOUETTE_THRESHOLD = 0.40


def _user_feature_matrix(per_user: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Build one row per user with per-algorithm metric columns."""
    metrics = [c for c in per_user.columns if c not in
               ("user_id", "table", "algorithm", "n_readings")]
    pivoted = per_user.pivot_table(
        index=["user_id", "table"], columns="algorithm", values=metrics,
    )
    pivoted.columns = [f"{m}__{a}" for m, a in pivoted.columns]
    pivoted = pivoted.dropna(how="any")
    return pivoted.reset_index(), list(pivoted.columns)


def _best_algo_per_user(per_user: pd.DataFrame) -> pd.Series:
    """Per user, the algorithm with the lowest noise_reduction_ratio (most aggressive smoothing)."""
    pivot = per_user.pivot_table(index="user_id", columns="algorithm",
                                 values=RANKED_FOR_BEST)
    return pivot.idxmin(axis=1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--per-user-metrics", default="reports/per_user_metrics.csv")
    p.add_argument("--out", default="reports")
    args = p.parse_args(argv)

    per_user = pd.read_csv(args.per_user_metrics)
    out_dir = Path(args.out)
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    feat_df, feat_cols = _user_feature_matrix(per_user)
    if len(feat_df) < 6:
        print(f"Only {len(feat_df)} users with full metrics — skipping clustering")
        return 0

    X = feat_df[[c for c in feat_df.columns if c not in ("user_id", "table")]].to_numpy()
    X = StandardScaler().fit_transform(np.nan_to_num(X, nan=0.0))

    best_k = 2
    best_score = -1.0
    best_labels = None
    for k in (2, 3, 4):
        if len(X) <= k:
            continue
        km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X)
        score = silhouette_score(X, km.labels_)
        if score > best_score:
            best_score = score
            best_k = k
            best_labels = km.labels_

    pca = PCA(n_components=2)
    coords = pca.fit_transform(X)
    best_algo = _best_algo_per_user(per_user)
    feat_df["best_algo"] = feat_df.user_id.map(best_algo)
    feat_df["cluster"] = best_labels
    feat_df["pc1"] = coords[:, 0]
    feat_df["pc2"] = coords[:, 1]

    feat_df[["user_id", "table", "cluster", "best_algo", "pc1", "pc2"]].to_csv(
        out_dir / "user_phenotypes.csv", index=False,
    )
    print(f"Wrote {out_dir/'user_phenotypes.csv'}  silhouette={best_score:.3f} (k={best_k})")

    fig, ax = plt.subplots(figsize=(9, 7))
    palette = {"aaps_average": "tab:blue", "aaps_exponential": "tab:orange",
               "trio_sgolay": "tab:green", "ukf": "tab:red"}
    markers = {0: "o", 1: "s", 2: "^", 3: "D"}
    for _, row in feat_df.iterrows():
        ax.scatter(row.pc1, row.pc2,
                   color=palette.get(row.best_algo, "gray"),
                   marker=markers.get(int(row.cluster), "x"),
                   s=120, alpha=0.7, edgecolor="black", linewidth=0.5)
        ax.annotate(row.user_id, (row.pc1, row.pc2),
                    fontsize=8, xytext=(4, 4), textcoords="offset points")
    title = (f"User phenotypes (PCA, k={best_k}, silhouette={best_score:.2f})\n"
             f"Color = best-smoother (lowest {RANKED_FOR_BEST}); marker = cluster")
    ax.set_title(title)
    ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]*100:.0f}% var)")
    ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]*100:.0f}% var)")
    legend_handles = [plt.scatter([], [], color=c, label=a, s=80)
                      for a, c in palette.items()]
    ax.legend(handles=legend_handles, title="best smoother", loc="best", fontsize=9)
    fig.tight_layout()
    fig.savefig(fig_dir / "user_phenotypes.png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {fig_dir/'user_phenotypes.png'}")

    if best_score < SILHOUETTE_THRESHOLD:
        print(f"\nNote: silhouette {best_score:.2f} < {SILHOUETTE_THRESHOLD} — "
              "no clear phenotype separation. Smoother ranking is robust across "
              "user types; the per-user 'best smoother' will not be reported as a "
              "regime-dependent recommendation in the paper.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
