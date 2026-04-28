# Smoothing Investigations

Step-by-step backtest of four CGM smoothing algorithms used in open-source AID systems — AAPS Average, AAPS Exponential (TSUNAMI), Trio Savitzky-Golay, and the new Adaptive Unscented Kalman Filter — with full per-step instrumentation, fixture-based parity verification against Kotlin reference, event-aligned deviation analysis, and a real-meal sub-study using a live Nightscout instance.

## Papers

All four papers live under `reports/`:

| paper | what it is |
|---|---|
| `paper_diabettech.md` (rendered to `SID_UKF_for_Diabettech_readers.docx`) | Reader-facing paper aimed at open-source AID users. Contains the calm-vs-moving regime framing, event-aligned deviation visualisations, real meal-event evidence, and AID-design implications. The primary deliverable. |
| `paper.docx` (auto-generated) | Technical paper. Cohort-summary tables, full metric definitions, parity-test summary, event-aligned analysis, SID re-detection. |
| `phase2/paper_per_sensor.md` (rendered to `SID_Per_Sensor_G6_vs_G7.docx`) | Per-sensor sub-analysis on a Phase 2 cohort with explicit Dexcom G6 vs G7 sensor labels and three within-user G7→G6 transitions. |
| `upload_path/paper_upload_path.md` (rendered to `SID_Upload_Path_Effects.docx`) | Side-study on dual-upload-path Nightscout entries: Dexcom Share2 vs G7 native API, and xDrip+ vs Trio iOS. Documents that 99.9–100% of paired entries are bit-identical when the same physical sensor is uploaded by two clients. |
| `AUDIT_NOTE.md` | Internal-review findings and corrections — kept as historical record. |

## Code layout

```
backtest/
  smoothers/                   # Python ports (parity-tested vs Kotlin/Swift refs)
    aaps_average.py
    aaps_exponential.py
    trio_sgolay.py
    ukf.py
  reference/                   # Vendored upstream sources
    UnscentedKalmanFilterPlugin.kt
    kotlin_driver/             # Standalone JVM driver for UKF parity
  cli/
    run_backtest.py            # Phase 3 main pipeline
    compare.py                 # Per-user metrics + drill-down windows
    cross_smoother.py          # Paired Wilcoxon + Pareto + ranking
    spectral.py                # Per-smoother frequency response
    phenotypes.py              # User-phenotype clustering
    sid_redetect.py            # SID v6 on raw + each smoother's output
    paper.py                   # Auto-generates the technical DOCX
    ingest_phase2_dedup.py     # Phase 2 sensor-tagged ingest with dedup
    phase2_run.py              # Smoothers per (user, sensor) on Phase 2
    phase2_analysis.py         # Per-sensor metrics + within-user paired
    upload_path_study.py       # Dual-upload-path comparison
    fetch_nightscout_meals.py  # Pull meal-tagged treatments + CGM from Nightscout
    meal_event_smoother_impact.py  # Per-meal smoother behaviour
    normalised_deviation_plots.py  # Event-aligned median-deviation visualisations
    render_md_to_docx.py       # Markdown → DOCX renderer
  tests/                       # 15 fixture-based parity tests
  metrics.py                   # All metric definitions (delay, noise, accel, etc.)
  io.py                        # CGM resampling onto strict 5-min grid
  cohort.py                    # Cohort selection
  db.py                        # PostgreSQL access
  trace.py                     # Per-(user, smoother) Parquet schema
data/
  nstest3/                     # Live Nightscout cache (treatments + CGM)
reports/
  *.csv                        # Per-user, cohort, SID, meal, deviation tables
  figs/                        # Cohort-level figures
  deviation_plots/             # Event-aligned deviation figures
  phase2/                      # Per-sensor sub-analysis outputs
  upload_path/                 # Upload-path study outputs
  meal_events/                 # Per-meal traces and figures
```

## Reproducibility

All scripts are runnable from the repo root. The pipeline that produced the technical paper:

```bash
make all          # runs the full 19-user Phase 3 pipeline (~90 s on M-series Mac)
make test         # 15 fixture-based parity tests (~1 s)
```

Phase 2 sensor-tagged sub-cohort:

```bash
python3 -m backtest.cli.ingest_phase2_dedup --truncate
python3 -m backtest.cli.phase2_run --out runs/phase2
python3 -m backtest.cli.phase2_analysis --runs runs/phase2 --out reports/phase2
```

Upload-path study:

```bash
python3 -m backtest.cli.upload_path_study --out reports/upload_path
```

Live Nightscout meal-event analysis (requires API access):

```bash
NS_API_SECRET='your-secret' python3 -m backtest.cli.fetch_nightscout_meals --out data/nstest3
python3 -m backtest.cli.meal_event_smoother_impact --data-dir data/nstest3 --out reports/meal_events
```

Event-aligned deviation visualisations:

```bash
python3 -m backtest.cli.normalised_deviation_plots --target all
```

## Dependencies

Python 3.13+, pandas, numpy, scipy, sklearn, pyarrow, python-docx, matplotlib, psycopg2 (for Phase 3 from local TimescaleDB). The Kotlin parity driver requires JDK 21 and Gradle.

## What lives where

- **Scripts**: `backtest/`
- **Datasets**: `data/`, `multi_user/data/site_*.json` (Phase 2 source), `backtest/cohort.json` (Phase 3 cohort), `backtest/tests/fixtures/` (parity-test fixtures including the Kotlin reference outputs)
- **Documents**: `reports/*.md` (sources) and `reports/*.docx` (rendered)
- **Derived data**: `runs/` (Parquet traces, gitignored — reproducible via `make backtest`)

## Note on Nightscout data

`data/nstest3/` contains real CGM and treatment data from a live Nightscout instance (`nstest3.crabdance.com`). The owner of that instance has published it deliberately for use in this study. It does not contain any data from anyone else.

The Phase 2 cohort source data lives in `multi_user/data/site_*.json` — anonymised data from 13 sites used in the prior multi-user paper.

The Phase 3 cohort metrics are derived from a local TimescaleDB extract of the `oref` database; the database itself is not in this repo.

## License

This is a research codebase. Use at your own discretion. The Kotlin source `backtest/reference/UnscentedKalmanFilterPlugin.kt` is vendored from the upstream AndroidAPS plugin; consult that project for its license.
