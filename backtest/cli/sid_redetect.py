"""Layer 7: re-run SID v6 on raw + each smoother's output, per user.

Extends the prior multi-user paper's 3-smoother SID analysis to include the UKF.
Outputs:
- reports/sid_cluster_counts.csv      (per (user, smoother): total + low/med/high)
- reports/sid_surviving.csv           (per surviving cluster: amplitude, duration, etc.)
- reports/sid_outcome_correlations.csv (Pearson r per outcome × smoother, 3 conditions)
- reports/sid_rf_survival.csv         (RF cluster-survival F1 per smoother)
- reports/figs/sid_pareto.png         (cluster reduction % vs surviving severity)
- reports/figs/sid_outcome_correlations.png (forest plot)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[2]
SID_PKG = REPO_ROOT / "cgm-sensor-integrity" / "cgm_sensor_integrity_v6" / "cgm_ai_assistant" / "ai_assistant"
sys.path.insert(0, str(SID_PKG))
import cgm_cluster_detector_v5 as detector  # noqa: E402

ALGOS = ("aaps_average", "aaps_exponential", "trio_sgolay", "ukf")
RF_FEATS = [
    "duration_min", "amplitude", "min_glucose", "max_glucose", "mean_glucose",
    "peak_reversals", "peak_incoh_ratio", "step_max", "chain_size", "hour_of_day",
    "is_nocturnal", "has_hypo_event", "spike_bumped", "chain_promoted",
]
OUTCOMES = ["tbr", "tbr54", "hypo_episodes", "nadir", "cv", "mage", "lbgi", "tir"]


def _build_df(ts_sec: np.ndarray, glucose: np.ndarray) -> pd.DataFrame:
    """Build a (timestamp, glucose, date_str) frame for SID + outcome routines."""
    ts = pd.to_datetime(ts_sec.astype("int64"), unit="s", utc=True).tz_localize(None)
    df = pd.DataFrame({"timestamp": ts, "glucose": glucose.astype("float64")})
    df["date_str"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    return df


def _run_sid(df: pd.DataFrame, glucose_vals: np.ndarray) -> tuple[list, list]:
    clusters, events = [], []
    for day_str in sorted(df["date_str"].unique()):
        day = pd.Timestamp(day_str)
        de = day + pd.Timedelta(days=1)
        ae = de + pd.Timedelta(hours=3)
        md = (df["timestamp"] >= day) & (df["timestamp"] < de)
        me = (df["timestamp"] >= day) & (df["timestamp"] < ae)
        idx_d = df.index[md]
        idx_e = df.index[me]
        if len(idx_d) < 20:
            continue
        cl = detector.detect_clusters(
            df.loc[idx_d, "timestamp"],
            pd.Series(glucose_vals[idx_d], index=idx_d),
        )
        ev = detector.find_hypo_events(
            df.loc[idx_e, "timestamp"],
            pd.Series(glucose_vals[idx_e], index=idx_e),
            hypo_threshold=70.0, window_hours=3.0, min_confidence="low",
        )
        for c in cl:
            c["date"] = day_str
        clusters.extend(cl)
        events.extend(ev)
    return clusters, events


def _daily_outcomes(df: pd.DataFrame, glucose_vals: np.ndarray) -> pd.DataFrame:
    rows = []
    for day_str, grp in df.groupby("date_str"):
        idx = grp.index
        g = glucose_vals[idx]
        n = len(g)
        if n < 20:
            continue
        tir = float(np.mean((g >= 70) & (g <= 180)) * 100)
        tbr = float(np.mean(g < 70) * 100)
        tbr54 = float(np.mean(g < 54) * 100)
        tar = float(np.mean(g > 180) * 100)
        nadir = float(np.min(g))
        mg = float(np.mean(g))
        sg = float(np.std(g))
        cv = (sg / mg * 100) if mg > 0 else 0.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fbg = 1.509 * (np.log(np.clip(g, 1, None)) ** 1.084 - 5.381)
        lbgi = float(np.mean(10 * np.clip(fbg, None, 0) ** 2))
        he = 0
        ih = False
        for v in (g < 70).astype(int):
            if v == 1 and not ih:
                he += 1
                ih = True
            elif v == 0:
                ih = False
        diffs = np.abs(np.diff(g))
        exc = diffs[diffs > sg]
        mage = float(np.mean(exc)) if len(exc) > 0 else 0.0
        rows.append(dict(date=day_str, tir=tir, tbr=tbr, tbr54=tbr54, tar=tar,
                         nadir=nadir, mean_glucose=mg, cv=cv, lbgi=lbgi,
                         hypo_episodes=he, mage=mage))
    return pd.DataFrame(rows)


def _daily_clusters(cl_list: list) -> pd.DataFrame:
    dm = defaultdict(lambda: {"n_clusters": 0, "n_low": 0, "n_medium": 0, "n_high": 0,
                              "severity": 0, "total_dur": 0,
                              "mean_amp": [], "mean_incoh": [], "mean_step": []})
    for c in cl_list:
        d = c.get("date", "")
        dm[d]["n_clusters"] += 1
        dm[d][f"n_{c['confidence']}"] += 1
        dm[d]["severity"] += {"low": 1, "medium": 2, "high": 3}.get(c["confidence"], 1)
        dm[d]["total_dur"] += c["duration_min"]
        dm[d]["mean_amp"].append(c["max"] - c["min"])
        dm[d]["mean_incoh"].append(c["debug"]["peak_incoh_ratio"])
        dm[d]["mean_step"].append(c["debug"]["step_max"])
    rows = []
    for d, v in dm.items():
        rows.append({
            "date": d, "n_clusters": v["n_clusters"],
            "n_low": v["n_low"], "n_medium": v["n_medium"], "n_high": v["n_high"],
            "severity": v["severity"], "total_dur": v["total_dur"],
            "mean_amp": float(np.mean(v["mean_amp"])) if v["mean_amp"] else 0.0,
            "mean_incoh": float(np.mean(v["mean_incoh"])) if v["mean_incoh"] else 0.0,
            "mean_step": float(np.mean(v["mean_step"])) if v["mean_step"] else 0.0,
        })
    return pd.DataFrame(rows)


def _overlap(s1, e1, s2, e2) -> bool:
    return s1 < e2 and e1 > s2


def _match_survival(raw_cl: list, smooth_cl: list) -> list[bool]:
    out = []
    smoothed_intervals = [(pd.Timestamp(sc["start"]), pd.Timestamp(sc["end"])) for sc in smooth_cl]
    for c in raw_cl:
        cs, ce = pd.Timestamp(c["start"]), pd.Timestamp(c["end"])
        out.append(any(_overlap(cs, ce, ss, se) for ss, se in smoothed_intervals))
    return out


def _cluster_feats(cl_list: list, ev_list: list) -> pd.DataFrame:
    ev_map = {(pd.Timestamp(e["cluster"]["start"]), pd.Timestamp(e["cluster"]["end"])): e
              for e in ev_list}
    rows = []
    for c in cl_list:
        d = c["debug"]
        st = pd.Timestamp(c["start"])
        en = pd.Timestamp(c["end"])
        rows.append({
            "duration_min": c["duration_min"],
            "amplitude": c["max"] - c["min"],
            "min_glucose": c["min"], "max_glucose": c["max"],
            "mean_glucose": (c["min"] + c["max"]) / 2,
            "peak_reversals": d.get("peak_reversals_window", 0),
            "peak_incoh_ratio": d.get("peak_incoh_ratio", 0),
            "step_max": d.get("step_max", 0),
            "chain_size": d.get("chain_size", 1),
            "hour_of_day": st.hour,
            "is_nocturnal": int(st.hour >= 22 or st.hour < 7),
            "has_hypo_event": int((st, en) in ev_map),
            "spike_bumped": int(d.get("bumped", False)),
            "chain_promoted": int(d.get("chain_promoted", False)),
            "confidence": c["confidence"],
        })
    return pd.DataFrame(rows)


def _surviving_cluster_rows(user: str, smoother: str, raw_cl: list,
                            survives: list[bool]) -> list[dict]:
    out = []
    for c, alive in zip(raw_cl, survives):
        if not alive:
            continue
        out.append({
            "user_id": user, "smoother": smoother,
            "amplitude": c["max"] - c["min"],
            "duration_min": c["duration_min"],
            "peak_incoh_ratio": c["debug"]["peak_incoh_ratio"],
            "step_max": c["debug"]["step_max"],
            "confidence": c["confidence"],
        })
    return out


def _load_raw_for_user(user: str, runs_dir: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Pull raw input from any one trace.

    For trio_sgolay we must take pass-1's input_glucose (the original raw); for
    other smoothers, input_glucose is the raw directly.
    """
    for algo in ALGOS:
        pq = runs_dir / user / f"{algo}.parquet"
        if pq.exists():
            df = pd.read_parquet(pq)
            if algo == "trio_sgolay":
                df = df[df.step_name == "pass1"].sort_values("reading_idx")
            else:
                df = df.sort_values("reading_idx")
            return df.ts_sec.to_numpy(), df.input_glucose.to_numpy()
    return None


def _load_smoothed(user: str, algo: str, runs_dir: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Returns (reading_idx, output_glucose) for the smoother."""
    pq = runs_dir / user / f"{algo}.parquet"
    if not pq.exists():
        return None
    df = pd.read_parquet(pq)
    if algo == "trio_sgolay":
        # Pass-3 output is the cumulative final smoothed value.
        df = df[df.step_name == "pass3"]
    df = df.sort_values("reading_idx")
    return df.reading_idx.to_numpy(), df.output_glucose.to_numpy()


def _process_user(user: str, table: str, runs_dir: Path, *,
                  count_rows, surviving_rows, corr_rows, rf_rows) -> None:
    raw_load = _load_raw_for_user(user, runs_dir)
    if raw_load is None:
        return
    ts_sec, raw_g = raw_load
    df = _build_df(ts_sec, raw_g)

    raw_cl, raw_ev = _run_sid(df, raw_g)
    n_raw = len(raw_cl)
    if n_raw == 0:
        print(f"  {user:6s} no raw clusters detected — skipping")
        return

    raw_feat = _cluster_feats(raw_cl, raw_ev)
    raw_outcomes = _daily_outcomes(df, raw_g)
    raw_daily = _daily_clusters(raw_cl)
    raw_merged = raw_outcomes.merge(raw_daily, on="date", how="left").fillna(0)

    count_rows.append(_count_row(user, table, "raw", raw_cl))

    for algo in ALGOS:
        loaded = _load_smoothed(user, algo, runs_dir)
        if loaded is None:
            continue
        idx_smo, smo_out = loaded
        # Build a smoothed series aligned to the canonical raw length;
        # any reading_idx the smoother skipped (e.g. UKF segment-init) keeps the raw value.
        smo_g = raw_g.copy()
        smo_g[idx_smo] = smo_out
        smo_cl, _ = _run_sid(df, smo_g)
        count_rows.append(_count_row(user, table, algo, smo_cl, n_raw=n_raw))

        survives = _match_survival(raw_cl, smo_cl)
        surviving_rows.extend(_surviving_cluster_rows(user, algo, raw_cl, survives))

        # Outcome correlation: smoothed clusters vs raw outcomes (clusters were re-detected
        # on smoothed glucose; outcomes are computed on raw to keep the clinical reference fixed).
        smo_daily = _daily_clusters(smo_cl)
        if smo_daily.empty:
            smo_daily = pd.DataFrame({"date": pd.Series(dtype=str), "n_clusters": pd.Series(dtype=int)})
        merged = raw_outcomes.merge(smo_daily, on="date", how="left").fillna(0)
        if "n_clusters" not in merged.columns:
            merged["n_clusters"] = 0
        for o in OUTCOMES:
            if o not in merged.columns or merged.n_clusters.std() == 0:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                r, p = pearsonr(merged.n_clusters, merged[o])
            corr_rows.append({"user_id": user, "smoother": algo, "outcome": o,
                              "r": float(r), "p": float(p), "n_days": len(merged)})

        # RF survival classifier (only if class balance allows)
        n_pos = int(sum(survives))
        if len(raw_feat) >= 30 and 5 <= n_pos < len(survives) - 5:
            X = raw_feat[RF_FEATS].copy()
            y = np.array(survives, dtype=int)
            try:
                rf = RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=10,
                                            class_weight="balanced", random_state=42, n_jobs=-1)
                cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    scores = cross_val_score(rf, X, y, cv=cv, scoring="f1")
                rf_rows.append({"user_id": user, "smoother": algo,
                                "n_clusters": len(raw_feat), "n_survived": n_pos,
                                "f1_mean": float(scores.mean()),
                                "f1_std": float(scores.std())})
            except Exception:
                pass


def _count_row(user: str, table: str, smoother: str, cl: list, *,
               n_raw: int | None = None) -> dict:
    by_conf = {"low": 0, "medium": 0, "high": 0}
    for c in cl:
        by_conf[c["confidence"]] = by_conf.get(c["confidence"], 0) + 1
    row = {
        "user_id": user, "table": table, "smoother": smoother,
        "n_total": len(cl),
        "n_low": by_conf["low"], "n_medium": by_conf["medium"], "n_high": by_conf["high"],
    }
    if n_raw is not None and n_raw > 0:
        row["pct_reduction_vs_raw"] = float(100.0 * (1 - len(cl) / n_raw))
    return row


def _pareto_figure(counts_df: pd.DataFrame, surviving_df: pd.DataFrame, out_path: Path) -> None:
    smoothers = [a for a in ALGOS if a in counts_df.smoother.unique()]
    fig, ax = plt.subplots(figsize=(9, 7))
    colors = {"aaps_average": "tab:blue", "aaps_exponential": "tab:orange",
              "trio_sgolay": "tab:green", "ukf": "tab:red"}
    for algo in smoothers:
        per_user_red = counts_df[counts_df.smoother == algo].set_index("user_id")["pct_reduction_vs_raw"]
        per_user_amp = surviving_df[surviving_df.smoother == algo].groupby("user_id")["amplitude"].median()
        joined = pd.concat([per_user_red, per_user_amp], axis=1).dropna()
        ax.scatter(joined["pct_reduction_vs_raw"], joined["amplitude"],
                   color=colors[algo], s=80, alpha=0.7, edgecolor="black", linewidth=0.4,
                   label=algo)
        med_x = joined["pct_reduction_vs_raw"].median()
        med_y = joined["amplitude"].median()
        ax.scatter([med_x], [med_y], color=colors[algo], s=240, marker="X",
                   edgecolor="black", linewidth=1.2)
    ax.set_xlabel("SID cluster reduction % vs raw")
    ax.set_ylabel("Median surviving cluster amplitude (mg/dL)")
    ax.set_title("SID Pareto: cluster reduction vs surviving severity\n"
                 "(small dots = users; large X = smoother median)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _correlation_forest_figure(corr_df: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 7))
    smoothers = [a for a in ALGOS if a in corr_df.smoother.unique()]
    y_labels = []
    y_pos = []
    pos = 0
    colors = {"aaps_average": "tab:blue", "aaps_exponential": "tab:orange",
              "trio_sgolay": "tab:green", "ukf": "tab:red"}
    for outcome in OUTCOMES:
        for algo in smoothers:
            sub = corr_df[(corr_df.outcome == outcome) & (corr_df.smoother == algo)]
            if sub.empty:
                continue
            r_med = sub.r.median()
            r_lo, r_hi = sub.r.quantile(0.25), sub.r.quantile(0.75)
            ax.errorbar(r_med, pos, xerr=[[r_med - r_lo], [r_hi - r_med]],
                        fmt="o", color=colors[algo], capsize=3,
                        label=algo if outcome == OUTCOMES[0] else None)
            y_labels.append(f"{outcome} — {algo}")
            y_pos.append(pos)
            pos += 1
        pos += 0.6
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(y_labels, fontsize=8)
    ax.set_xlabel("Pearson r (cluster count vs daily outcome) — median ± IQR across users")
    ax.set_title("Outcome correlations per (outcome × smoother)")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", default="runs")
    p.add_argument("--out", default="reports")
    p.add_argument("--cohort", default="backtest/cohort.json")
    p.add_argument("--users", default=None, help="Comma-separated user_ids to limit to")
    args = p.parse_args(argv)

    runs_dir = Path(args.runs)
    out_dir = Path(args.out)
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)

    cohort = json.loads(Path(args.cohort).read_text())["members"]
    if args.users:
        wanted = set(args.users.split(","))
        cohort = [m for m in cohort if m["user_id"] in wanted]

    counts: list[dict] = []
    surviving: list[dict] = []
    corrs: list[dict] = []
    rfs: list[dict] = []

    for member in cohort:
        print(f"  {member['user_id']:6s} ({member['table']})")
        _process_user(
            member["user_id"], member["table"], runs_dir,
            count_rows=counts, surviving_rows=surviving,
            corr_rows=corrs, rf_rows=rfs,
        )

    counts_df = pd.DataFrame(counts)
    surviving_df = pd.DataFrame(surviving)
    corr_df = pd.DataFrame(corrs)
    rf_df = pd.DataFrame(rfs)

    counts_df.to_csv(out_dir / "sid_cluster_counts.csv", index=False)
    surviving_df.to_csv(out_dir / "sid_surviving.csv", index=False)
    corr_df.to_csv(out_dir / "sid_outcome_correlations.csv", index=False)
    rf_df.to_csv(out_dir / "sid_rf_survival.csv", index=False)

    if not counts_df.empty and not surviving_df.empty:
        _pareto_figure(counts_df, surviving_df, fig_dir / "sid_pareto.png")
        print(f"Wrote {fig_dir/'sid_pareto.png'}")
    if not corr_df.empty:
        _correlation_forest_figure(corr_df, fig_dir / "sid_outcome_correlations.png")
        print(f"Wrote {fig_dir/'sid_outcome_correlations.png'}")

    print(f"\nWrote SID outputs to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
