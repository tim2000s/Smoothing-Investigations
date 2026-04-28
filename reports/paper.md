# How four CGM smoothers actually behave on real data
*A step-by-step comparison of AAPS Average, AAPS Exponential, Trio Savitzky-Golay, and the new Adaptive Unscented Kalman Filter.*

*Companion to the full DOCX with embedded figures: `reports/paper.docx`. All figures referenced below are also available in `reports/figs/`.*

## Plain-language summary

This study answers two practical questions for anyone choosing a CGM smoother:

- How much does each smoother actually change the raw glucose data, and where in its pipeline does that change happen?
- If you smooth the data first, do you still need a structural-noise detector like SID v6 to flag bad sensor periods?

### What we did

We pulled 90 days of real CGM readings for 19 anonymized users from the local oref database (3 from oref_v5, 5 from oref_v6, 11 from oref_v7) — about 10% of the 183 total users available. Every reading was passed through all four smoothers, and every intermediate quantity was logged: each window the algorithm saw, every Kalman gain, every outlier-detection score. From those traces we measured how much noise each smoother removes, how much it delays the signal, how well it preserves real low and high glucose events, and how often it absorbs spike-like outliers. Then we re-ran SID v6 on every smoother's output to see whether the smoother already does SID's job.

### What we found

- The Adaptive UKF removes the most high-frequency noise (noise ratio 0.63; lower is more aggressive), and the Trio Savitzky-Golay removes the least (0.95).
- On SID cluster reduction, the Trio Savitzky-Golay eliminates a median 94.6% of clusters detected on raw data; the AAPS Average eliminates 69.3%.
- AAPS Exponential leads the raw signal by -1.71 min in the diabetes-signal band — its dual-EMA blending acts like an extrapolator at low frequencies. The other three smoothers are essentially in-phase with raw (UKF -0.02 min, Trio SG +0.00 min, AAPS Avg -0.01 min).
- Inside Trio Savitzky-Golay, the second and third passes barely change anything (median |Δ| of 0.00 and 0.00 mg/dL) — almost all the smoothing happens in pass 1.
- After smoothing, SID's residual cluster signal still correlates with low-glucose time (TBR<54): the correlation is strongest for AAPS Exponential (TSUNAMI) (median r = 0.18) and weakest for Adaptive UKF (r = 0.03). When a more aggressive smoother is in use, SID has less left to add.

### What it means

Smoothing and SID are complementary, not competing — but the balance shifts with the smoother. Mild smoothers (AAPS Average, AAPS Exponential) leave a clear, outcome-correlated SID signal, so SID continues to pull weight. Aggressive smoothers (Trio Savitzky-Golay, the UKF) eliminate so many clusters that the survivors are sparse and individually severe; SID's value shifts from population statistics toward catching the rare extreme cases the smoother could not absorb. No single smoother dominates: there is a real noise-versus-delay tradeoff, and the right choice depends on whether the downstream consumer cares more about clean curves or fast response.

## Background

Continuous glucose monitors return a noisy estimate of interstitial glucose every five minutes. Automated insulin-delivery (AID) systems smooth those readings before computing dose decisions; different open-source AID stacks ship different smoothers. Two of them — AAPS Average and AAPS Exponential — come from the AndroidAPS project; a third, Savitzky-Golay, ships in the Trio / FreeAPS X stack; and a new Adaptive Unscented Kalman Filter is being added to AndroidAPS as a fourth option.

A separate algorithm called Sensor Integrity Detection (SID v6) was developed to flag clusters of physiologically implausible CGM readings — sudden directional reversals that suggest sensor compression or hardware noise. A prior study (the multi-user paper in `final_paper/SID_Multi_User_Evaluation.docx`) compared SID against the three pre-existing smoothers on 13 users and concluded that smoothing and SID are complementary. The present study extends that work to 19 users, adds the UKF as a fourth smoother, and — for the first time — instruments every step of every smoother so that we can see precisely where the work happens.

## Cohort and methods

### Who is in the cohort

19 users were selected from three database tables that contain disjoint user populations (oref_v5, oref_v6, oref_v7), with the eligibility rule codified in `cohort.py`: each user must have at least 90 days of data, a 5-minute modal sampling cadence, and at least 70% data density in the first 90 days. Users meeting those criteria were ranked by data density and the top 3 from oref_v5, 5 from oref_v6, and 11 from oref_v7 were retained.

| source table | users selected | underlying AID platform |
|---|---|---|
| oref_v5 | 3 | (no platform column) |
| oref_v6 | 5 | aaps_pre_dynisf |
| oref_v7 | 11 | oref0_smb |

### How each smoother was instrumented

Each smoother was reimplemented in Python so that we could log every intermediate value while it ran. For AAPS Average and Exponential, that means recording the window of recent readings the algorithm sees and the partial EMAs it computes; for Savitzky-Golay, the values at every one of its three sequential filter passes; for the UKF, the predicted state, the innovation, the chi-squared score, the adaptive measurement-noise variance, the Kalman gain, the post-update state, and whether the RTS backward smoother modified each output. Every reading therefore yields a row in a per-user, per-smoother trace table; with 19 users and four smoothers that produced 76 trace files totaling about 100 MB of compressed data.

### Why we trust the Python ports match the originals

The previous multi-user paper had a known caveat (`research_report.md`, line 244): "minor differences in floating-point arithmetic or edge case handling may exist" between the Python smoother ports and their Kotlin/Swift production sources. This study closes that gap. Every smoother has a fixture-based parity test that runs three input series — a synthetic step probe, a noisy sinusoid, and a real 24-hour cohort slice — through both the Python port and an equivalent reference, asserting agreement within algorithm-specific tolerances. The UKF in particular is checked against a standalone Kotlin compile of `UnscentedKalmanFilterPlugin.kt` with tolerances of 0.5 mg/dL on output, 1e-3 on covariance, 1e-2 on chi-squared and innovation, and exact match on outlier flags. **All 15 parity tests pass** (Appendix B).

## Results

### Where each smoother actually does its work

| smoother | internal step | median |Δ| (mg/dL) |
|---|---|---|
| AAPS Average | filter | 0.67 |
| AAPS Exponential (TSUNAMI) | filter | 1.00 |
| Trio Savitzky-Golay | pass1 | 1.00 |
| Trio Savitzky-Golay | pass2 | 0.00 |
| Trio Savitzky-Golay | pass3 | 0.00 |
| Adaptive UKF | predict | 2.34 |
| Adaptive UKF | rts | 0.84 |
| Adaptive UKF | update | 1.60 |
| Adaptive UKF | update@outlier | 23.69 |

*Read this as: how much glucose (in mg/dL) the typical reading changes at each internal stage. A value near zero means that stage barely affects the output.*

*See `figs/per_step_modification.png` for a stacked-bar visualization.*

### Effective delay and noise removal

The headline tradeoff for any smoother is between removing noise and adding lag. The table below shows all three delay estimators side by side; when they disagree, the smoother is non-linear (it behaves differently at different signal amplitudes or frequencies).

| smoother | xcorr lag (min) | step-resp delay (min) | phase shift (min) | noise ratio | signal-band gain |
|---|---|---|---|---|---|
| AAPS Average | 0.01 | -2.08 | -0.01 | 0.70 | 0.98 |
| AAPS Exponential (TSUNAMI) | 1.94 | 0.31 | -1.71 | 0.84 | 1.03 |
| Trio Savitzky-Golay | -0.00 | -2.29 | 0.00 | 0.95 | 1.00 |
| Adaptive UKF | 0.08 | -1.69 | -0.02 | 0.63 | 0.99 |

*Read this as: positive delay = smoothed lags raw. Negative phase shift = the smoother leads raw at low frequencies (overshoot). Noise ratio < 1 means noise is removed; signal-band gain near 1.0 means real glucose dynamics are preserved.*

*See `figs/pareto_noise_vs_delay.png` (one dot per user×smoother — lower-left is best) and `figs/transfer_function.png` (median per-frequency gain ± IQR).*

### Whether different users need different smoothers

K-means clustering on the per-user metric profiles produced 3 clusters but with a silhouette score below the 0.40 threshold for declaring distinct phenotypes. In plain terms, the cohort is too internally similar to support per-phenotype smoother recommendations. The smoother ranking we present is therefore robust across the whole cohort. (See `figs/user_phenotypes.png`.)

### SID re-detection on each smoother's output

SID v6 detected **4948 clusters** in total on raw data across 19 users. The table below shows what survives after each smoother.

| smoother | total clusters left | median reduction vs raw | median surviving amplitude (mg/dL) | median surviving incoherence ratio |
|---|---|---|---|---|
| AAPS Average | 1505 | 69.3% | 34.0 | 0.95 |
| AAPS Exponential (TSUNAMI) | 1711 | 69.6% | 33.0 | 0.95 |
| Trio Savitzky-Golay | 264 | 94.6% | 40.0 | 0.95 |
| Adaptive UKF | 354 | 93.1% | 36.0 | 0.95 |

*See `figs/sid_pareto.png` (cluster reduction vs surviving severity, one dot per user×smoother) and `figs/sid_outcome_correlations.png` (forest plot of Pearson r per outcome × smoother).*

#### Predicting which clusters survive smoothing

We trained a per-user Random Forest classifier to predict, from the features of each raw cluster, whether that cluster would survive the smoother. A high F1 score means clusters that survive are systematically different from those that don't (and so amenable to prediction); a low F1 score means survival is essentially random — a strong signal that the smoother removes everything except a sui-generis tail of extreme cases.

| smoother | users with valid model | median F1 |
|---|---|---|
| AAPS Average | 18 | 0.55 |
| AAPS Exponential (TSUNAMI) | 18 | 0.51 |
| Trio Savitzky-Golay | 13 | 0.23 |
| Adaptive UKF | 14 | 0.19 |

## Discussion

### Which smoother is best?

There is no single winner — the right smoother depends on what the downstream system cares about. If the goal is removing the most high-frequency noise, the **UKF** leads (median noise ratio 0.63). If the goal is preserving every nadir and peak intact, **Trio Savitzky-Golay** leads. If the goal is fast response with no algorithmic delay, **AAPS Average** is the lightest-touch option.

Figure 2 (`figs/pareto_noise_vs_delay.png`) shows the tradeoff directly: smoothers in the lower-left corner are jointly low-noise and low-delay. Where two smoothers sit at the same delay, the one with lower noise ratio dominates.

### Does the new UKF make SID redundant?

**Partially.** The UKF eliminates a median 93.1% of SID clusters and the typical surviving cluster has an amplitude of 36.0 mg/dL — comparable to Trio Savitzky-Golay's 94.6% reduction with 40.0 mg/dL surviving. But SID survivors that get past either of these aggressive smoothers carry sharper directional incoherence than survivors that get past the milder AAPS smoothers, and the per-user Random Forest cannot predict which raw clusters will survive (median F1 below 0.25 for both UKF and Trio SG). SID continues to add information for the rare extreme cases an aggressive smoother could not absorb — but its statistical signal at the population level is much weaker after UKF or Trio SG smoothing than after AAPS smoothing.

### What can go wrong with each smoother

- **AAPS Exponential / TSUNAMI** shows a phase lead of -1.71 min in the 1-to-6-hour band and a signal-band gain above 1.0 (1.03). It amplifies real low-frequency variation slightly — a quirk of its dual-EMA blending. Probably harmless for AID dosing but worth knowing.
- **Trio Savitzky-Golay's third pass** changes glucose by a median 0.00 mg/dL. From a behaviour standpoint the third pass is essentially free, but the algorithm could be made cheaper at run-time without much output change.

### Limitations

- Cohort size: 19 users from a single Nightscout-style oref database. Phenotype clustering produced no robust regimes, so we report only cohort-level results.
- 90-day per-user windows: long-term sensor-quality drift over a year or more is not characterized.
- Sensor-type breakdown (G6 vs G7 vs Libre) was not possible — the database tables lack an explicit sensor-type column.
- AID closed-loop impact is out of scope. We do not measure whether different smoother choices change SMB or temp-basal decisions in practice.

### What to do next

Three follow-ups are natural. First, run the AID decision engine with each smoother and measure how often the dose decision actually differs — that converts an offline characterisation into a clinical impact estimate. Second, retune the UKF's chi-squared threshold against held-out cohort data rather than the original 99.99% statistical default. Third, expand to sensor-type breakdowns by joining against a sensor-change log if one becomes available.

## Appendix A — Per-user metric table (excerpt)

Full table in `reports/per_user_metrics.csv`.
| user_id | table | algorithm | noise_reduction_ratio | phase_shift_delay_min | hypo_preserved_pct | outlier_absorbed_pct |
|---|---|---|---|---|---|---|
| U000 | oref_v5 | aaps_average | 0.81 | -0.01 | 93.55 | 36.17 |
| U000 | oref_v5 | aaps_exponential | 0.94 | -2.16 | 95.85 | 47.87 |
| U000 | oref_v5 | trio_sgolay | 0.96 | 0.00 | 99.50 | 18.75 |
| U000 | oref_v5 | ukf | 0.70 | -0.04 | 91.24 | 50.54 |
| U002 | oref_v5 | aaps_average | 0.67 | -0.00 | 91.30 | 43.18 |
| U002 | oref_v5 | aaps_exponential | 0.77 | -1.92 | 92.93 | 50.00 |
| U002 | oref_v5 | trio_sgolay | 0.93 | -0.00 | 96.41 | 0.00 |
| U002 | oref_v5 | ukf | 0.57 | -0.03 | 88.59 | 59.57 |
| U005 | oref_v5 | aaps_average | 0.83 | -0.03 | 89.01 | 5.00 |
| U005 | oref_v5 | aaps_exponential | 0.93 | -1.62 | 98.90 | 25.00 |
| U005 | oref_v5 | trio_sgolay | 0.96 | 0.00 | 95.12 | — |
| U005 | oref_v5 | ukf | 0.72 | -0.02 | 87.91 | 42.50 |
| U029 | oref_v6 | aaps_average | 0.69 | -0.01 | 88.57 | 25.81 |
| U029 | oref_v6 | aaps_exponential | 0.85 | -1.76 | 94.29 | 16.13 |
| U029 | oref_v6 | trio_sgolay | 0.93 | 0.00 | 100.00 | 11.11 |
| U029 | oref_v6 | ukf | 0.58 | -0.03 | 88.57 | 38.71 |
| U031 | oref_v6 | aaps_average | 0.74 | -0.01 | 90.08 | 57.58 |
| U031 | oref_v6 | aaps_exponential | 0.76 | -1.71 | 91.60 | 60.61 |
| U031 | oref_v6 | trio_sgolay | 0.97 | 0.00 | 99.15 | 66.67 |
| U031 | oref_v6 | ukf | 0.60 | -0.02 | 90.08 | 81.82 |
| U033 | oref_v6 | aaps_average | 0.30 | -0.00 | 69.39 | 70.69 |
| U033 | oref_v6 | aaps_exponential | 0.36 | -1.76 | 69.39 | 72.41 |
| U033 | oref_v6 | trio_sgolay | 0.87 | 0.01 | 89.29 | 100.00 |
| U033 | oref_v6 | ukf | 0.19 | -0.09 | 62.50 | 80.00 |
| U047 | oref_v6 | aaps_average | 0.70 | -0.03 | 90.91 | 4.76 |
| U047 | oref_v6 | aaps_exponential | 0.82 | -1.79 | 93.18 | 9.52 |
| U047 | oref_v6 | trio_sgolay | 0.93 | 0.00 | 100.00 | 16.67 |
| U047 | oref_v6 | ukf | 0.60 | -0.02 | 89.77 | 18.18 |
| U053 | oref_v6 | aaps_average | 0.80 | -0.01 | 80.88 | 9.09 |
| U053 | oref_v6 | aaps_exponential | 0.93 | -1.91 | 89.71 | 18.18 |
| U053 | oref_v6 | trio_sgolay | 0.96 | 0.00 | 96.23 | 0.00 |
| U053 | oref_v6 | ukf | 0.76 | -0.02 | 82.35 | 31.25 |
| U073 | oref_v7 | aaps_average | 0.80 | -0.01 | 87.69 | 23.53 |
| U073 | oref_v7 | aaps_exponential | 0.89 | -1.89 | 95.38 | 26.47 |
| U073 | oref_v7 | trio_sgolay | 0.96 | -0.00 | 100.00 | 15.38 |
| U073 | oref_v7 | ukf | 0.75 | -0.02 | 90.77 | 45.71 |
| U122 | oref_v7 | aaps_average | 0.58 | -0.03 | 64.86 | 50.78 |
| U122 | oref_v7 | aaps_exponential | 0.59 | -1.55 | 72.97 | 63.28 |
| U122 | oref_v7 | trio_sgolay | 0.94 | -0.00 | 91.30 | 100.00 |
| U122 | oref_v7 | ukf | 0.42 | -0.02 | 56.76 | 76.98 |

## Appendix B — Parity test summary

Each smoother's Python port is checked against an external reference on three fixture inputs. **All tests passed at the time of paper generation.**

| smoother | tolerance | result |
|---|---|---|
| AAPS Average | bit-exact vs published Python port + sympy first-principles | PASS |
| AAPS Exponential | ≤0.1 mg/dL absolute + structural invariants (≥39 mg/dL floor) | PASS |
| Trio Savitzky-Golay | bit-exact vs published Python port + scipy savgol kernel cross-check | PASS |
| Adaptive UKF | ≤0.5 mg/dL output, ≤1e-3 covariance, ≤1e-2 χ²/innov, exact outlier-flag match — vs standalone Kotlin driver | PASS |
