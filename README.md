# Smoothing Investigations

Production-realistic backtest of three CGM smoothing algorithms used in open-source AID systems built on the oref0 algorithm — AAPS Average, AAPS Exponential (TSUNAMI), and the Adaptive Unscented Kalman Filter — with full per-step instrumentation, Kotlin-reference parity verification on every algorithm, online sliding-window evaluation across a 19-user 90-day cohort, sensor-tagged G6/G7 sub-cohort analysis, real-meal sub-study using a live Nightscout instance, and same-sensor dual-upload path characterisation.

## Papers

The four primary papers live under `papers/`. Each is a standalone publication.

| paper | scope |
|---|---|
| `papers/01_cohort_backtest.md` (`.docx`) | Production-realistic online sliding-window evaluation of all three smoothers across the 19-user, 90-day cohort. Cohort medians, Pareto frontiers, per-step decomposition, SID re-detection, transfer functions, event-aligned deviations. |
| `papers/02_sensor_g6_vs_g7.md` (`.docx`) | Per-sensor sub-analysis on a Phase 2 cohort with explicit Dexcom G6 vs G7 sensor labels and three within-user G6→G7 transitions. Concludes sensor type is a second-order modulator; algorithm choice dominates. |
| `papers/03_meal_event_impact.md` (`.docx`) | 45 carbohydrate-tagged meal events from a live Nightscout instance. Per-meal latency, peak retention and acceleration retention by smoother. |
| `papers/04_upload_path_disagreement.md` (`.docx`) | Two case studies on a live Nightscout instance where the same physical Dexcom transmitter is uploaded via two paths concurrently (User_D, User_L). Documents 99.9–100 % agreement and the case for pre-database deduplication. |

## Code layout

```
backtest/
  smoothers/                   # Python ports (parity-tested vs Kotlin reference)
    aaps_average.py            # 3-point central-window mean
    aaps_exponential.py        # Dual-order EMA (TSUNAMI)
    ukf.py                     # Adaptive UKF + RTS backward smoother
  reference/                   # Vendored upstream Kotlin sources
    AvgSmoothingPlugin.kt
    ExponentialSmoothingPlugin.kt
    UnscentedKalmanFilterPlugin.kt
    kotlin_driver/             # Standalone JVM driver running each algorithm
                               # in batch and online sliding-window modes.
  cli/
    run_backtest.py            # Phase 3 main pipeline (online mode, parallel)
    compare.py                 # Per-user metrics + drill-down windows
    cross_smoother.py          # Paired Wilcoxon + Pareto + ranking
    spectral.py                # Per-smoother frequency response
    phenotypes.py              # User-phenotype clustering
    sid_redetect.py            # SID v6 re-detection on each smoother's output
    per_step_modify.py         # Predict / update / RTS decomposition
    ingest_phase2_dedup.py     # Phase 2 sensor-tagged ingest with dedup
    phase2_run.py              # Smoothers per (user, sensor) on Phase 2 (parallel)
    phase2_analysis.py         # Per-sensor metrics + within-user paired
    upload_path_study.py       # Dual-upload-path comparison
    fetch_nightscout_meals.py  # Pull meal-tagged treatments + CGM from Nightscout
    meal_event_smoother_impact.py  # Per-meal smoother behaviour
    normalised_deviation_plots.py  # Event-aligned median-deviation visualisations
    render_md_to_docx.py       # Markdown → DOCX renderer
  tests/                       # Kotlin-reference parity tests
    test_aaps_average_kotlin_reference.py        # batch
    test_aaps_average_online_kotlin_reference.py # online sliding window
    test_aaps_exponential_kotlin_reference.py    # online sliding window
    test_ukf_reference.py                        # batch
    test_ukf_online_kotlin_reference.py          # online sliding window
  metrics.py                   # All metric definitions
  io.py                        # CGM resampling onto strict 5-min grid
  cohort.py                    # Cohort selection
  db.py                        # PostgreSQL access
  trace.py                     # Per-(user, smoother) Parquet schema
papers/                        # Four primary publications (.md and .docx)
data/
  nstest3/                     # Live Nightscout cache (treatments + CGM)
reports/
  *.csv                        # Per-user, cohort, SID, meal, deviation tables
  figs/                        # Cohort-level figures
  deviation_plots/             # Event-aligned deviation figures
  phase2/                      # Per-sensor sub-analysis outputs
  upload_path/                 # Upload-path study outputs
  meal_events/                 # Per-meal traces and figures
runs/                          # Per-(user, algorithm) Parquet traces (gitignored)
```

## Online mode is the primary evaluation

All three smoothers are evaluated in *online sliding-window* mode: at every chronological reading t the smoother is asked the value the AID dose engine would actually see at decision time t (= leading-edge value of a fresh smoother instance fed the trailing W readings). This matches what production AAPS does on every loop tick and uncovers a critical property of AAPS Average — its production loop never sets `data[0].smoothed`, so the dose engine reads the raw value at the leading edge, and the algorithm has zero effect on the current reading. Earlier batch-mode evaluations of the same algorithms reported very different numbers for AAPS Average; the online evaluation here is the operationally accurate one.

## Reproducibility

Build the Kotlin reference fixtures once:

```bash
cd backtest/reference/kotlin_driver
PATH=/opt/homebrew/opt/openjdk@21/bin:$PATH gradle --no-daemon -q run \
    --args='../../tests/fixtures/inputs.json ../../tests/fixtures/kotlin/'
cd -
```

Then run the parity-test gate (15 tests, ~1 second):

```bash
pytest backtest/tests/
```

Then the cohort backtest pipeline:

```bash
python3 -m backtest.cli.run_backtest --days 90    # 19 users × 3 smoothers, online mode, parallel
python3 -m backtest.cli.compare                   # per-user metrics
python3 -m backtest.cli.cross_smoother            # Wilcoxon + Pareto + ranking
python3 -m backtest.cli.spectral                  # transfer-function PSDs
python3 -m backtest.cli.per_step_modify           # per-stage decomposition
python3 -m backtest.cli.sid_redetect              # SID re-detection vs smoother
python3 -m backtest.cli.normalised_deviation_plots --target all
python3 -m backtest.cli.phenotypes                # user phenotypes
```

Phase 2 sensor-tagged sub-cohort:

```bash
python3 -m backtest.cli.ingest_phase2_dedup --truncate
python3 -m backtest.cli.phase2_run --out runs/phase2     # parallel
python3 -m backtest.cli.phase2_analysis --runs runs/phase2 --out reports/phase2
```

Live Nightscout meal-event analysis:

```bash
NS_API_SECRET='your-secret' python3 -m backtest.cli.fetch_nightscout_meals --out data/nstest3
python3 -m backtest.cli.meal_event_smoother_impact --data-dir data/nstest3 --out reports/meal_events
```

Upload-path study:

```bash
python3 -m backtest.cli.upload_path_study --out reports/upload_path
```

Render the four papers from markdown:

```bash
python3 -m backtest.cli.render_md_to_docx --input papers/01_cohort_backtest.md --output papers/01_cohort_backtest.docx \
    --figure "reports/figs/pareto_noise_vs_delay.png:Pareto" \
    --figure "reports/figs/transfer_function.png:Transfer function" \
    --figure "reports/figs/per_step_modification.png:Per-step modification" \
    --figure "reports/deviation_plots/calm_aligned_deviations.png:Calm-window deviations" \
    --figure "reports/deviation_plots/rate_rise_aligned_deviations.png:Rate-rise deviations" \
    --figure "reports/figs/sid_pareto.png:SID pareto"
# (and the corresponding render commands for papers 02, 03, 04 — see Makefile)
```

## Dependencies

Python 3.13+, pandas, numpy, scipy, sklearn, pyarrow, python-docx, matplotlib, psycopg2 (for Phase 3 from a local TimescaleDB `oref` database). The Kotlin parity driver requires JDK 21 and Gradle.

## What lives where

- **Code**: `backtest/`
- **Datasets**: `data/nstest3/` (live Nightscout cache), `multi_user/data/site_*.json` (Phase 2 source — anonymised), `backtest/cohort.json` (Phase 3 cohort manifest), `backtest/tests/fixtures/` (parity-test fixtures including Kotlin reference outputs)
- **Papers**: `papers/*.md` (sources) and `papers/*.docx` (rendered)
- **Tables and figures**: `reports/`
- **Per-user trace data**: `runs/` (Parquet, gitignored — reproducible via the pipeline above)

## Note on Nightscout data

`data/nstest3/` contains real CGM and treatment data from a live Nightscout instance (`nstest3.crabdance.com`) whose owner has published it deliberately for use in this study. It does not contain anyone else's data.

The Phase 2 cohort source data lives in `multi_user/data/site_*.json` — anonymised data from 13 sites used in the prior multi-user paper.

The Phase 3 cohort metrics are derived from a local TimescaleDB extract of an `oref` database; the database itself is not in this repository.

## License

This is a research codebase. Use at your own discretion. The Kotlin sources `backtest/reference/AvgSmoothingPlugin.kt`, `ExponentialSmoothingPlugin.kt`, and `UnscentedKalmanFilterPlugin.kt` are vendored from the upstream AndroidAPS plugins; consult that project for its license.
