# At-scale confirmation — all 183 users

This directory is a **separate at-scale re-run** of the smoother backtest, kept
distinct from the four primary papers. The papers report the curated
**19-user, 90-day** density cohort (`backtest/cohort.json`); the numbers here
report **every user in the database, over full per-user history**. The papers
are *not* rewritten to these figures — this is an independent confirmation that
the paper's conclusion holds at population scale.

## Method

- Cohort: `backtest/cohort_full.json` — all **183 users** across `oref_v5`
  (29), `oref_v6` (44), `oref_v7` (110), built by `backtest/build_full_cohort.py`
  (no density/cadence/span filtering; skips only users with < ~1 day of data).
  **10.9M SGV rows.**
- Run: `python -m backtest.cli.run_backtest --cohort backtest/cohort_full.json
  --days 2600 --out runs_full --workers 10` (full history; `--days 2600` covers
  the ~6.9-year max span). Online sliding-window mode, all three smoothers.
  ~38 min wall, 549 traces, zero failures. Traces (`runs_full/`, ~724 MB) are
  gitignored and reproducible from the command above.
- Analysis: `per_step_modify`, `compare`, `cross_smoother` → CSVs + figs here.

## Scale (analysed data points)

10.9M raw DB rows reduce to **8,071,627 present readings** after resampling to the
strict 5-minute grid and gap handling — these are the points actually run
through each smoother.

| Source | n users | n data points |
|---|---:|---:|
| oref_v5 (Trio) | 29 | 2,205,663 |
| oref_v6 (AndroidAPS) | 44 | 1,265,074 |
| oref_v7 (OpenAPS/oref0) | 110 | 4,600,890 |
| **Total** | **183** | **8,071,627** |

Sensor cohort (`oref_phase2_sites_v2`, after ≥14d / ≥50% density filter):
G6 = 9 users / 238,491 pts · G7 = 4 users / 133,917 pts (6 G7 tagged, 2 dropped).

## Headline — median per user, all 183 users

| Metric | AAPS Average | AAPS Exponential | Adaptive UKF |
|---|---|---|---|
| Noise reduction | 0% (no-op) | 15.6% | **17.0%** |
| Phase delay | 0.00 min | 1.70 min | **0.81 min** |
| Hypo events preserved | 100% | 94.4% | **96.9%** |
| Outlier absorbed | 0% | 27.0% | **38.2%** |
| Peak preserved | 100% | 98.1% | **99.4%** |

The UKF Pareto-dominates AAPS Exponential at scale — the same result the paper
reached on 19 users (18% / 0.85 min / 96.6% / 38%). Consistent across all three
source platforms (see `figs/table2_ukf_by_platform.jpg`).

## Sensor stratification

The main tables (`oref_v5/v6/v7`) carry **no sensor field** (only `platform` =
AID system), so the 183-user run cannot be split by sensor. Sensor identity
exists only in `oref_phase2_sites_v2` (9 G6 + 6 G7 users — the same cohort as
paper 02). Re-running `phase2_run` + `phase2_analysis` on it
(`reports_full/phase2/`) reproduces paper 02: G7 is the noisier sensor, and it
is the one place AAPS Exponential edges the UKF on raw noise reduction
(26.9% vs 26.1%), but the UKF keeps ~half the lag and better hypo/peak fidelity
on both sensors. Sensor type is a second-order modulator; algorithm choice
dominates. See `figs/table3_sensor_g6_g7.jpg`.

## Still open (unchanged by this run)

- **SID re-detection** was not reproduced: `backtest.cli.sid_redetect` imports an
  external `cgm_cluster_detector_v5` module that is not vendored in this repo.
- **Dose-engine downstream comparison** (the paper's stated "direct test") is
  still not done.
