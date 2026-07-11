"""Render the smoother-comparison tables as clean JPEG images."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import os
# Write JPEGs next to this script, under reports_full/figs/
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figs")
os.makedirs(OUT, exist_ok=True)

INK = "#1a1a1a"
BEST = "#0b6b3a"
BESTBG = "#e3f3ea"
HDRBG = "#243b53"
ALTBG = "#f4f6f8"


def render(fname, title, subtitle, col_labels, rows, best_cells, label_w=0.30):
    ncol = len(col_labels)
    nrows = len(rows)
    fig_w = 2.0 + 2.35 * (ncol - 1) + 1.2
    fig_h = 1.9 + 0.52 * nrows
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=200)
    # header band height as a fraction of the figure
    head_frac = 1.35 / fig_h
    ax = fig.add_axes([0.015, 0.03, 0.97, 1 - head_frac - 0.06])
    ax.axis("off")
    tbl = ax.table(cellText=rows, colLabels=col_labels,
                   cellLoc="center", loc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(12.5)
    tbl.scale(1, 1.9)
    multiline_hdr = any("\n" in str(lbl) for lbl in col_labels)
    val_w = (1.0 - label_w) / (ncol - 1)
    widths = [label_w] + [val_w] * (ncol - 1)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#d0d7de")
        cell.set_linewidth(0.8)
        cell.set_width(widths[c])
        if r == 0:
            cell.set_facecolor(HDRBG)
            cell.set_text_props(color="white", fontweight="bold")
            if multiline_hdr:
                cell.set_height(cell.get_height() * 1.75)
                cell.set_fontsize(11.5)
        else:
            data_r = r - 1
            base = "white" if data_r % 2 == 0 else ALTBG
            if c == 0:
                cell.set_text_props(ha="left", color=INK, fontweight="bold")
                cell.PAD = 0.03
                cell.set_facecolor(base)
            elif (data_r, c) in best_cells:
                cell.set_facecolor(BESTBG)
                cell.set_text_props(color=BEST, fontweight="bold")
            else:
                cell.set_facecolor(base)
                cell.set_text_props(color=INK)
    # title + subtitle, manually placed in the header band (top of figure)
    fig.text(0.02, 1 - 0.42 / fig_h, title, fontsize=15.5, fontweight="bold",
             color=INK, ha="left", va="top")
    fig.text(0.02, 1 - 0.92 / fig_h, subtitle, fontsize=9.5, color="#52606d",
             ha="left", va="top")
    path = f"{OUT}/{fname}"
    fig.savefig(path, format="jpg", facecolor="white")
    plt.close(fig)
    print("wrote", path)


# ---- Table 1: full 183-user headline ----
render(
    "table1_full_cohort.jpg",
    "Smoother comparison  ·  all 183 users, full history",
    "n = 8,071,627 analysed readings · 183 users · online sliding-window · median per user.  "
    "Green = best (excl. AAPS Average, a no-op).",
    ["Metric", "AAPS Average", "AAPS Exponential", "Adaptive UKF"],
    [
        ["Noise reduction",       "0%  (no-op)", "15.6%",    "17.0%"],
        ["Phase delay",           "0.00 min",    "1.70 min", "0.81 min"],
        ["Hypo events preserved", "100%",        "94.4%",    "96.9%"],
        ["Outlier absorbed",      "0%",          "27.0%",    "38.2%"],
        ["Peak preserved",        "100%",        "98.1%",    "99.4%"],
    ],
    best_cells={(0, 3), (1, 3), (2, 3), (3, 3), (4, 3)},
    label_w=0.30,
)

# ---- Table 2: per-platform UKF breakdown ----
render(
    "table2_ukf_by_platform.jpg",
    "Adaptive UKF  ·  consistency across AID systems",
    "Median UKF metrics per AID system.  AndroidAPS cohort is the pre-DynISF era.",
    ["Metric", "Trio\n29 users · 2.21M pts", "AndroidAPS\n44 users · 1.27M pts",
     "OpenAPS\n110 users · 4.60M pts"],
    [
        ["Noise reduction",       "23.0%",    "12.9%",    "16.6%"],
        ["Phase delay",           "1.08 min", "0.85 min", "0.76 min"],
        ["Hypo events preserved", "95.6%",    "99.4%",    "97.5%"],
        ["Outlier absorbed",      "49.3%",    "36.5%",    "32.3%"],
    ],
    best_cells=set(),
    label_w=0.28,
)

# ---- Table 3: sensor stratified G6 vs G7 ----
render(
    "table3_sensor_g6_g7.jpg",
    "Sensor-stratified  ·  Dexcom G6 vs G7",
    "Sensor-tagged cohort · median per user · n per column · "
    "Green = UKF beats AAPS Exponential.",
    ["Metric", "G6 · Exp\n9 users", "G6 · UKF\n238k pts", "G7 · Exp\n4 users", "G7 · UKF\n134k pts"],
    [
        ["Noise reduction",      "20.6%",    "21.7%",    "26.9%",    "26.1%"],
        ["Phase delay",          "2.02 min", "1.04 min", "2.04 min", "1.09 min"],
        ["Hypo preserved",       "90.0%",    "95.6%",    "86.6%",    "93.6%"],
        ["Outlier absorbed",     "50.0%",    "61.1%",    "55.0%",    "64.8%"],
        ["Peak-accel retention", "0.47",     "0.62",     "0.44",     "0.59"],
    ],
    # UKF-better cells: G6 UKF (col2) all; G7 UKF (col4) all except noise reduction (col4 row0 Exp wins)
    best_cells={(0, 2), (1, 2), (2, 2), (3, 2), (4, 2),
                (1, 4), (2, 4), (3, 4), (4, 4)},
    label_w=0.24,
)
