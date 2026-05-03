"""
smoother_comparison_figure.py

Four-panel visual comparison of the three CGM smoothers against raw data.
Each panel shows a real CGM segment illustrating one key behaviour:
  1. Flat/stable glucose  — jitter reduction
  2. Post-meal rise       — tracking lag
  3. Transmission spike   — artefact absorption
  4. Hypoglycaemia dip    — low-glucose event visibility

Usage:
    python -m backtest.cli.smoother_comparison_figure --out reports/figs/smoother_comparison.png
"""

import argparse
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

# ── colour palette ────────────────────────────────────────────────────────────
C_RAW  = "#555555"   # dark grey
C_AVG  = "#2176AE"   # blue   — AAPS Average
C_EXP  = "#E07B39"   # orange — AAPS Exponential
C_UKF  = "#3AA66A"   # green  — Adaptive UKF
C_LOW  = "#E03333"   # red    — 70 mg/dL threshold
C_RISE = "#9B59B6"   # purple — threshold line on rise panel

LW_RAW = 1.4
LW_SM  = 2.0
ALPHA_RAW = 0.55

RUNS = "runs"


def load_user(user: str) -> pd.DataFrame:
    avg = (pd.read_parquet(f"{RUNS}/{user}/aaps_average.parquet")
           [["ts_sec", "input_glucose", "output_glucose"]]
           .rename(columns={"output_glucose": "avg"}))
    exp = (pd.read_parquet(f"{RUNS}/{user}/aaps_exponential.parquet")
           [["ts_sec", "output_glucose"]]
           .rename(columns={"output_glucose": "exp"}))
    ukf = (pd.read_parquet(f"{RUNS}/{user}/ukf.parquet")
           [["ts_sec", "output_glucose"]]
           .rename(columns={"output_glucose": "ukf"}))
    df = avg.merge(exp, on="ts_sec").merge(ukf, on="ts_sec")
    return df.sort_values("ts_sec").reset_index(drop=True)


def mins(df_slice: pd.DataFrame) -> np.ndarray:
    """Return elapsed minutes from start of slice."""
    ts = df_slice["ts_sec"].values
    return (ts - ts[0]) / 60.0


def add_deviation_strip(ax_dev, x, raw_vals, avg_vals, exp_vals, ukf_vals):
    """Fill ±deviation-from-raw strips for each smoother."""
    ax_dev.axhline(0, color=C_RAW, lw=0.8, ls="--", alpha=0.6)
    ax_dev.fill_between(x, 0, avg_vals - raw_vals, color=C_AVG, alpha=0.25)
    ax_dev.fill_between(x, 0, exp_vals - raw_vals, color=C_EXP, alpha=0.25)
    ax_dev.fill_between(x, 0, ukf_vals - raw_vals, color=C_UKF, alpha=0.25)
    ax_dev.plot(x, avg_vals - raw_vals, color=C_AVG, lw=1.0)
    ax_dev.plot(x, exp_vals - raw_vals, color=C_EXP, lw=1.0)
    ax_dev.plot(x, ukf_vals - raw_vals, color=C_UKF, lw=1.0)
    ax_dev.set_ylabel("vs raw\n(mg/dL)", fontsize=7, labelpad=2)
    ax_dev.tick_params(labelsize=7)


def style_main(ax, title: str, ylabel="Glucose (mg/dL)"):
    ax.set_title(title, fontsize=10, fontweight="bold", pad=6)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.tick_params(labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def style_dev(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlabel("Time (minutes)", fontsize=8)


def plot_traces(ax, x, raw, avg, exp, ukf, show_raw_label=True):
    label = "Raw CGM" if show_raw_label else None
    ax.plot(x, raw, color=C_RAW,  lw=LW_RAW, alpha=ALPHA_RAW, ls="--", label=label)
    ax.plot(x, avg, color=C_AVG,  lw=LW_SM, label="AAPS Average")
    ax.plot(x, exp, color=C_EXP,  lw=LW_SM, label="AAPS Exponential")
    ax.plot(x, ukf, color=C_UKF,  lw=LW_SM, label="Adaptive UKF")


# ─────────────────────────────────────────────────────────────────────────────
def build_figure(out_path: str):
    df0   = load_user("U000")
    df122 = load_user("U122")

    # ── Panel 1: stable glucose, jitter visible ──────────────────────────────
    # U000 readings 19519–19542  (2 h, glucose 90–126, clear reading-to-reading noise)
    sl1 = df0.iloc[19519:19543]

    # ── Panel 2: post-meal rise from euglycaemia ─────────────────────────────
    # U000 readings 24810–24840  (glucose 84 → 313, fast sustained rise)
    sl2 = df0.iloc[24810:24841]

    # ── Panel 3: transmission spike ──────────────────────────────────────────
    # U122 readings 19105–19120  (39 sentinel then 170 rebound)
    sl3 = df122.iloc[19105:19121]

    # ── Panel 4: hypoglycaemia dip ───────────────────────────────────────────
    # U000 readings 6290–6355  (approach from ~120, dip to 42, recovery)
    sl4 = df0.iloc[6290:6356]

    # ── Figure layout ────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 11))
    fig.patch.set_facecolor("white")

    # 4 columns, each column = 2 rows (main + deviation), using height_ratios
    gs = GridSpec(
        4, 2,
        figure=fig,
        hspace=0.55,
        wspace=0.35,
        height_ratios=[3.5, 1.2, 3.5, 1.2],
    )

    axes = [
        (fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[1, 0])),   # panel 1
        (fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 1])),   # panel 2
        (fig.add_subplot(gs[2, 0]), fig.add_subplot(gs[3, 0])),   # panel 3
        (fig.add_subplot(gs[2, 1]), fig.add_subplot(gs[3, 1])),   # panel 4
    ]

    panels = [
        (sl1, "1.  Stable glucose — reading-to-reading noise",           False),
        (sl2, "2.  Fast-rising glucose — how quickly smoothers respond", False),
        (sl3, "3.  Transmission artefact — spike absorption",            True),
        (sl4, "4.  Hypoglycaemia — low-glucose event detection",         False),
    ]

    for idx, ((ax_main, ax_dev), (sl, title, _)) in enumerate(zip(axes, panels)):
        x    = mins(sl)
        raw  = sl["input_glucose"].values
        avg  = sl["avg"].values
        exp  = sl["exp"].values
        ukf  = sl["ukf"].values

        plot_traces(ax_main, x, raw, avg, exp, ukf, show_raw_label=(idx == 0))
        style_main(ax_main, title)

        # Panel-specific annotations
        if idx == 0:  # stable
            ax_main.set_ylim(72, 148)
            # Annotate a clear jitter peak where raw and avg diverge most from ukf
            peak_noise_i = int(np.argmax(np.abs(raw - ukf)))
            ax_main.annotate(
                "Average = raw\n(no smoothing applied)",
                xy=(x[peak_noise_i], raw[peak_noise_i]),
                xytext=(x[peak_noise_i] + 10, raw[peak_noise_i] + 16),
                arrowprops=dict(arrowstyle="->", color=C_AVG, lw=1.2),
                fontsize=7.5, color=C_AVG,
            )
            trough_i = int(np.argmin(ukf))
            ax_main.annotate(
                "UKF: noticeably\ncalmer than raw",
                xy=(x[trough_i], ukf[trough_i]),
                xytext=(x[trough_i] + 8, ukf[trough_i] - 18),
                arrowprops=dict(arrowstyle="->", color=C_UKF, lw=1.2),
                fontsize=7.5, color=C_UKF,
            )

        elif idx == 1:  # rise
            THRESHOLD = 1.0  # mg/dL/min rate trigger
            ax_main.set_ylim(70, 330)

            def first_cross(vals, threshold_mgdl_per_min=THRESHOLD):
                rates = np.diff(vals) / 5.0
                hits = np.where(rates >= threshold_mgdl_per_min)[0]
                return hits[0] + 1 if len(hits) else None

            cross_raw = first_cross(raw)
            cross_exp = first_cross(exp)
            cross_ukf = first_cross(ukf)

            if cross_raw is not None:
                ax_main.axvline(x[cross_raw], color=C_RAW, ls=":", lw=1.5, alpha=0.8)
                ax_main.text(x[cross_raw] - 1.5, 82,
                             "Raw & Average\nsee rising glucose",
                             fontsize=7, color=C_RAW, ha="right")
            if cross_exp is not None and cross_exp != cross_raw:
                ax_main.axvline(x[cross_exp], color=C_EXP, ls=":", lw=1.5, alpha=0.8)
            if cross_ukf is not None:
                later_i = max(cross_exp or 0, cross_ukf or 0)
                ax_main.axvline(x[later_i], color="#888888", ls=":", lw=1.5, alpha=0.6)
                lag_min = int(x[later_i] - x[cross_raw]) if cross_raw is not None else "?"
                ax_main.text(x[later_i] + 1.5, 82,
                             f"Exp & UKF detect\n({lag_min} min later)",
                             fontsize=7, color="#555555", ha="left")

            # Annotate the lag at a steeper part of the rise
            mid_i = (cross_raw or 6) + 9
            mid_i = min(mid_i, len(x) - 2)
            lag_exp = raw[mid_i] - exp[mid_i]
            lag_ukf = raw[mid_i] - ukf[mid_i]
            lo, hi = int(min(lag_ukf, lag_exp)), int(max(lag_ukf, lag_exp))
            ax_main.annotate(
                f"Smoothers trail raw\nby {lo}–{hi} mg/dL here",
                xy=(x[mid_i], exp[mid_i]),
                xytext=(x[mid_i] + 8, exp[mid_i] - 60),
                arrowprops=dict(arrowstyle="->", color=C_EXP, lw=1.2),
                fontsize=7.5, color=C_EXP,
            )

        elif idx == 2:  # spike
            ax_main.set_ylim(20, 225)
            dip_i = np.argmin(raw)     # 39 sentinel reading
            spike_i = dip_i + 1        # 170 rebound reading
            # Absorption: how much of the raw swing does each smoother show?
            pre_baseline = raw[dip_i - 2]  # stable glucose just before
            exp_swing  = exp[spike_i]  - exp[dip_i]
            ukf_swing  = ukf[spike_i]  - ukf[dip_i]
            raw_swing  = raw[spike_i]  - raw[dip_i]
            exp_abs_pct = int((1 - exp_swing / raw_swing) * 100)
            ukf_abs_pct = int((1 - ukf_swing / raw_swing) * 100)
            ax_main.annotate(
                f"Raw & Average:\nfull artefact visible\n({int(raw[dip_i])}→{int(raw[spike_i])} mg/dL)",
                xy=(x[spike_i], raw[spike_i]),
                xytext=(x[spike_i] + 5, raw[spike_i] - 15),
                arrowprops=dict(arrowstyle="->", color=C_AVG, lw=1.2),
                fontsize=7.5, color=C_AVG, ha="left",
            )
            ax_main.annotate(
                f"Exp: ~{exp_abs_pct}% absorbed\n({int(exp[spike_i])} mg/dL)",
                xy=(x[spike_i], exp[spike_i]),
                xytext=(x[spike_i] - 22, exp[spike_i] + 30),
                arrowprops=dict(arrowstyle="->", color=C_EXP, lw=1.2),
                fontsize=7.5, color=C_EXP,
            )
            ax_main.annotate(
                f"UKF: ~{ukf_abs_pct}% absorbed\n({int(ukf[spike_i])} mg/dL)",
                xy=(x[spike_i], ukf[spike_i]),
                xytext=(x[spike_i] - 22, ukf[spike_i] - 30),
                arrowprops=dict(arrowstyle="->", color=C_UKF, lw=1.2),
                fontsize=7.5, color=C_UKF,
            )

        elif idx == 3:  # hypo
            ax_main.axhline(70, color=C_LOW, ls="--", lw=1.6, alpha=0.85,
                            zorder=5, label="70 mg/dL — low-glucose threshold")
            ax_main.set_ylim(30, 145)
            # Count sub-70 readings
            n_raw = (raw < 70).sum()
            n_exp = (exp < 70).sum()
            n_ukf = (ukf < 70).sum()
            ax_main.text(
                0.03, 0.05,
                (f"Readings below 70 mg/dL threshold:\n"
                 f"  Raw = {n_raw}    Exp = {n_exp}    UKF = {n_ukf}"),
                transform=ax_main.transAxes,
                fontsize=7.5, color="#333333",
                bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#cccccc", alpha=0.92),
            )
            ax_main.text(x[-1] * 0.68, 73, "70 mg/dL", fontsize=7,
                         color=C_LOW, va="bottom")

        add_deviation_strip(ax_dev, x, raw, avg, exp, ukf)
        style_dev(ax_dev)

        if idx in (0, 2):
            ax_main.set_xlabel("")
            ax_dev.set_xlabel("Time (minutes)", fontsize=8)

    # ── Shared legend ─────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color=C_RAW, alpha=0.6, label="Raw CGM (what the sensor transmits)"),
        mpatches.Patch(color=C_AVG, label="AAPS Average  (passes raw through unchanged)"),
        mpatches.Patch(color=C_EXP, label="AAPS Exponential  (weighted average, 24-reading window)"),
        mpatches.Patch(color=C_UKF, label="Adaptive UKF  (Kalman filter, 36-reading window)"),
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        ncol=2,
        fontsize=8.5,
        frameon=True,
        framealpha=0.95,
        edgecolor="#cccccc",
        bbox_to_anchor=(0.5, 1.01),
    )

    fig.suptitle(
        "Three CGM smoothers compared against raw glucose data\n"
        "Lower strip = smoothed minus raw (mg/dL); flat strip = no difference from raw",
        fontsize=11,
        y=1.065,
        fontweight="normal",
        color="#333333",
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"Saved {out_path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/figs/smoother_comparison.png")
    args = parser.parse_args()
    build_figure(args.out)


if __name__ == "__main__":
    main()
