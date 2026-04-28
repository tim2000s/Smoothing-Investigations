"""Upload-path effects study (separate from per-sensor analysis).

Reads source Nightscout JSON directly (not the deduplicated database table) so
both upload paths are visible.

Two users in the Phase 2 cohort have the same physical Dexcom G7 sensor
uploaded to Nightscout via two distinct paths simultaneously:

  User_D (site_04): the same sensor uploaded both via xDrip+ (no device
    string, with `unfiltered`/`filtered` columns) and via Trio's iOS app
    (`org.nightscout.K9BZJ3BP33.trio` device string).

  User_L (site_19): the same sensor uploaded both via the Dexcom Share2
    legacy API (`share2` device string) and via Trio / Loop with explicit
    `Dexcom G7 DXCM<id>` device strings.

This script characterises:
  1. Time-pair density per user.
  2. sgv agreement at each tolerance (exact, ≤1, ≤5, ≤10 mg/dL).
  3. Where the two paths disagree — by glucose range, by sgv magnitude, by
     time-of-day, and by error-code encoding (Dexcom 39 vs 40 sentinel).
  4. Timestamp jitter distribution between paired entries.

Output: reports/upload_path/{user}_pair_report.csv + figures.
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

DEFAULT_DATA_DIR = Path("/Users/timstreet/SID-evaluation/multi_user/data")
PAIR_TOL_MS = 150_000


# Predicates that select entries belonging to each upload path. Reading from
# the source JSON (not the deduplicated DB table) preserves both paths.
def _is_xdrip(e):
    return e.get("device") is None and "filtered" in e and "unfiltered" in e

def _is_trio(e):
    return (e.get("device") or "").startswith("org.nightscout")

def _is_share2(e):
    return e.get("device") == "share2"

def _is_dexcom_g7_native(e):
    return (e.get("device") or "").startswith("Dexcom G7 ")


USER_PAIRS = {
    "User_D": {
        "site_file": "site_04.json",
        "label_a": "xDrip+ (no device string, filtered/unfiltered cols)",
        "predicate_a": _is_xdrip,
        "label_b": "Trio iOS (org.nightscout.K9BZJ3BP33.trio)",
        "predicate_b": _is_trio,
    },
    "User_L": {
        "site_file": "site_19.json",
        "label_a": "Dexcom Share2 (legacy API)",
        "predicate_a": _is_share2,
        "label_b": "Dexcom G7 native (Trio / Loop)",
        "predicate_b": _is_dexcom_g7_native,
    },
}


def load_path(site_path: Path, predicate) -> pd.DataFrame:
    entries = json.loads(site_path.read_text())
    rows = []
    for e in entries:
        if not isinstance(e, dict): continue
        if "date" not in e or e["date"] is None: continue
        if e.get("sgv") is None: continue
        if not predicate(e): continue
        rows.append({"ts_utc_ms": int(e["date"]), "cgm_mgdl": float(e["sgv"])})
    return pd.DataFrame(rows).sort_values("ts_utc_ms").reset_index(drop=True)


def pair_by_timestamp(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    a_ts = a["ts_utc_ms"].to_numpy(); a_sgv = a["cgm_mgdl"].to_numpy()
    b_ts = b["ts_utc_ms"].to_numpy(); b_sgv = b["cgm_mgdl"].to_numpy()
    rows = []
    j = 0
    for i in range(len(a_ts)):
        while j < len(b_ts) and b_ts[j] < a_ts[i] - PAIR_TOL_MS:
            j += 1
        if j >= len(b_ts):
            break
        cands = []
        if j < len(b_ts) and abs(b_ts[j] - a_ts[i]) <= PAIR_TOL_MS:
            cands.append(j)
        if j > 0 and abs(b_ts[j-1] - a_ts[i]) <= PAIR_TOL_MS:
            cands.append(j-1)
        if not cands:
            continue
        best = min(cands, key=lambda jj: abs(b_ts[jj] - a_ts[i]))
        rows.append({
            "a_ts": int(a_ts[i]), "a_sgv": float(a_sgv[i]),
            "b_ts": int(b_ts[best]), "b_sgv": float(b_sgv[best]),
            "delta_sgv": float(a_sgv[i] - b_sgv[best]),
            "delta_t_s": (int(b_ts[best]) - int(a_ts[i])) / 1000.0,
        })
    return pd.DataFrame(rows)


def summarise(pair: pd.DataFrame, label_a: str, label_b: str) -> dict:
    n = len(pair)
    if n == 0:
        return {"n_pairs": 0}
    abs_diff = pair["delta_sgv"].abs()
    return {
        "n_pairs": n,
        "n_unique_a": pair.a_ts.nunique(),
        "n_unique_b": pair.b_ts.nunique(),
        "label_a": label_a,
        "label_b": label_b,
        "exact_agreement_pct": float(100 * (abs_diff == 0).mean()),
        "within_1_pct": float(100 * (abs_diff <= 1).mean()),
        "within_5_pct": float(100 * (abs_diff <= 5).mean()),
        "within_10_pct": float(100 * (abs_diff <= 10).mean()),
        "median_abs_delta": float(abs_diff.median()),
        "mean_abs_delta": float(abs_diff.mean()),
        "max_abs_delta": float(abs_diff.max()),
        "median_dt_s": float(pair["delta_t_s"].abs().median()),
        "mean_dt_s": float(pair["delta_t_s"].abs().mean()),
        "max_dt_s": float(pair["delta_t_s"].abs().max()),
        "n_a_only_39_b_40": int(((pair.a_sgv == 39) & (pair.b_sgv == 40)).sum()),
        "n_b_only_39_a_40": int(((pair.b_sgv == 39) & (pair.a_sgv == 40)).sum()),
        "n_disagree_above_5": int((abs_diff > 5).sum()),
    }


def figures(pair: pd.DataFrame, user: str, fig_dir: Path,
            label_a: str, label_b: str) -> None:
    if pair.empty: return
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))

    # 1. Δsgv distribution (excluding the 39/40 sentinel pairs)
    real = pair[~((pair.a_sgv.isin([39, 40])) & (pair.b_sgv.isin([39, 40])))]
    axes[0, 0].hist(real.delta_sgv.clip(-50, 50), bins=51, color="steelblue", edgecolor="black")
    axes[0, 0].set_title(f"{user}: Δsgv (a − b) — excluding 39/40 sentinel pairs (n={len(real):,})")
    axes[0, 0].set_xlabel("a − b (mg/dL)")
    axes[0, 0].set_ylabel("count")
    axes[0, 0].axvline(0, color="black", linewidth=0.5)
    axes[0, 0].grid(True, alpha=0.3)

    # 2. Δt distribution
    axes[0, 1].hist(pair.delta_t_s, bins=41, color="seagreen", edgecolor="black")
    axes[0, 1].set_title(f"{user}: timestamp jitter b − a (seconds)")
    axes[0, 1].set_xlabel("seconds")
    axes[0, 1].set_ylabel("count")
    axes[0, 1].axvline(0, color="black", linewidth=0.5)
    axes[0, 1].grid(True, alpha=0.3)

    # 3. Disagreement rate as a function of glucose range
    bins = [(0, 70), (70, 180), (180, 250), (250, 1000)]
    rates = []; labels = []
    for lo, hi in bins:
        sub = pair[(pair.a_sgv >= lo) & (pair.a_sgv < hi)]
        if len(sub) == 0:
            rates.append(0); labels.append(f"{lo}-{hi}\n(n=0)")
            continue
        disagree_pct = 100 * (sub.delta_sgv.abs() > 0).mean()
        rates.append(disagree_pct)
        labels.append(f"{lo}-{hi}\n(n={len(sub):,})")
    axes[1, 0].bar(labels, rates, color="indianred", edgecolor="black")
    axes[1, 0].set_title(f"{user}: disagreement rate by glucose range")
    axes[1, 0].set_ylabel("% pairs with Δsgv ≠ 0")
    axes[1, 0].grid(True, axis="y", alpha=0.3)
    for i, r in enumerate(rates):
        axes[1, 0].text(i, r, f"{r:.1f}%", ha="center", va="bottom", fontsize=9)

    # 4. Disagreement rate by hour of day (UTC)
    hours = pd.to_datetime(pair.a_ts, unit="ms", utc=True).dt.hour
    by_hr = pair.assign(hr=hours, abs_diff=pair.delta_sgv.abs()).groupby("hr").apply(
        lambda d: 100 * (d.abs_diff > 0).mean(), include_groups=False
    )
    axes[1, 1].bar(by_hr.index.astype(int), by_hr.values, color="purple", edgecolor="black")
    axes[1, 1].set_title(f"{user}: disagreement rate by hour-of-day (UTC)")
    axes[1, 1].set_xlabel("hour of day"); axes[1, 1].set_ylabel("% pairs")
    axes[1, 1].set_xticks(range(0, 24, 2))
    axes[1, 1].grid(True, axis="y", alpha=0.3)

    fig.suptitle(f"{user}: {label_a}  vs  {label_b}", y=1.02)
    fig.tight_layout()
    fig.savefig(fig_dir / f"{user}_upload_path_diagnostic.png", dpi=120, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="reports/upload_path")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)
    out_root = Path(args.out)
    fig_dir = out_root / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for user, cfg in USER_PAIRS.items():
        site_path = data_dir / cfg["site_file"]
        a = load_path(site_path, cfg["predicate_a"])
        b = load_path(site_path, cfg["predicate_b"])
        if a.empty or b.empty:
            print(f"  {user}: missing data for one path (a={len(a)}, b={len(b)})")
            continue
        pair = pair_by_timestamp(a, b)
        pair.to_csv(out_root / f"{user}_pairs.csv", index=False)
        summary = summarise(pair, cfg["label_a"], cfg["label_b"])
        summary["user_id"] = user
        summaries.append(summary)
        figures(pair, user, fig_dir, cfg["label_a"], cfg["label_b"])
        print(f"\n=== {user}: {cfg['label_a']}  vs  {cfg['label_b']} ===")
        for k, v in summary.items():
            if k in ("user_id", "label_a", "label_b"): continue
            print(f"  {k:30s}  {v}")

    pd.DataFrame(summaries).to_csv(out_root / "summary.csv", index=False)
    print(f"\nWrote {out_root}/summary.csv and per-user pair files + figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
