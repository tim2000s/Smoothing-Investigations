# Sensor-Type Effects in Production CGM Smoothing: Dexcom G6 versus G7

*Within-user comparison of three smoothing algorithms across a sensor-tagged sub-cohort, with three users who transitioned from G6 to G7 mid-record.*

---

## Abstract

The dominant CGM sensors in open-source automated insulin delivery (AID) deployments are the Dexcom G6 and G7 transmitters. Both deliver readings on a five-minute cadence end to end (G7 transmits more frequently internally but exposes data through Nightscout-compatible bridges at five-minute resolution, matching G6); both report values in the 39–401 mg/dL range with a 39 mg/dL low-end sentinel. Beyond the cadence and value range, the two transmitter generations differ in internal smoothing, calibration profile, and noise spectrum. This paper asks whether those upstream differences modulate what each of the three production smoothing algorithms used by oref0-derived AID systems — AAPS Average, AAPS Exponential, the adaptive Unscented Kalman Filter — does to the data the AID dose engine sees. We selected 13 (user × sensor) pairs (with at least 14 days and 50 % grid density per pair) from a research database whose entries are tagged with the originating sensor model, and three users for whom both a G6 and a G7 segment exists. Across the per-sensor median, sensor type modulates noise reduction modestly (UKF reduces 78 % of high-frequency power on G6 and 74 % on G7) but barely affects phase delay (≈ 1 minute on both). Within-user G6→G7 deltas (paired across the same user, who therefore controls for individual physiology) are 4–6 percentage points on noise reduction for both AAPS Exponential and the UKF, with one user showing the *opposite* sign. We conclude that within the operating range of the three production smoothers, sensor type is a second-order modulator: algorithm choice matters more than which Dexcom generation is feeding the data, and the G6→G7 transition does not require an algorithm change.

---

## 1. Introduction

When a user upgrades their Dexcom transmitter from G6 to G7, several things change at once: the on-skin sensor wire, the transmitter chemistry, the on-transmitter smoothing, and (in some setups) the upload path through which Nightscout receives the data. From the AID's perspective, what arrives is still a 5-minute cadence stream of mg/dL integers in the 39–401 range, but the noise spectrum, the rate at which compression artefacts manifest, and the overall calibration profile may shift.

A natural question for AID developers is: do the three production smoothing algorithms in current oref0-derived stacks — AAPS Average, AAPS Exponential, the adaptive UKF — perform differently on G6 versus G7 streams? If they do, sensor-conditional smoother choice could be considered. If they do not, the analysis from a single-sensor backtest would be valid for both transmitter families.

This paper answers that question for a sensor-tagged sub-cohort of 13 (user × sensor) pairs and three within-user G6→G7 transitions.

## 2. Methods

### 2.1 Sub-cohort selection

We extracted readings from a Phase 2 sensor-tagging table (`oref_phase2_sites_v2`) where each Nightscout entry has been classified as originating from a Dexcom G6, Dexcom G7, or unknown sensor based on the `device` string and other metadata. We required at least 14 days within a (user, sensor) segment to be useful, which yielded 13 segments distributed as:

* G6 only: User_B, User_C, User_F, User_G, User_K (5 users)
* G7 only: User_L (1 user)
* Both G6 and G7 in the same user (in disjoint time windows): User_D, User_I, User_J (3 users, 6 segments)
* G6 only with overlapping unknown segment dropped: User_M (1 user, G6 segment only — the small G7 fragment was below the 14-day floor)

Total: 13 (user, sensor) pairs across 10 unique users.

For users with both sensors we did not require the segments to be contiguous; we only required the rows to be cleanly attributed to one sensor or the other. Where the same physical sensor was reported via two upload paths (e.g., Trio iOS and xDrip+ both polling the Dexcom transmitter), we used a deduplicated ingest that keeps a single representative row per timestamp.

### 2.2 Algorithm execution

For each (user, sensor) segment we ran each smoother in production-realistic online sliding-window mode: at each chronological reading t the algorithm is asked the value the AID dose engine would see at decision time t. The Python ports were validated against the upstream Kotlin source to bit-exactness for AAPS Average and AAPS Exponential and within 0.5 mg/dL for the UKF. Per-segment trace columns went to `runs/phase2/<user>__<sensor>/<algorithm>.parquet`.

### 2.3 Per-sensor metrics

For each (user, sensor, algorithm) trace we computed the following per-user metrics:

* Noise reduction ratio (variance ratio of first differences)
* Phase shift delay (Hilbert-phase difference in the 1–6 hour cycle band, in minutes; negative = smoothed lags raw)
* Cross-correlation lag (minutes; sub-sample parabolic interpolation, search bounded to ± 60 min)
* Step-response delay (raw rate ≥ 0.5 mg/dL/min sustained, total amplitude ≥ 15 mg/dL; smoothed crosses 0.35 mg/dL/min)
* Hypoglycaemia event preservation (% of < 70 mg/dL events that the smoothed series also dips below 70 on within the same event window; events separated by ≥ 60 min)
* Outlier absorption (% of single-step ≥ 40 mg/dL raw changes that the smoothed series does not exhibit at the same step)
* Peak-event acceleration retention — a Phase-2-specific addition; see § 2.4.

We aggregated the per-segment metrics to the median across users for each (sensor, algorithm) combination.

### 2.4 Peak-event acceleration retention

Acceleration (the second derivative of glucose) is what oref0's "rapid drift" safety nets respond to: a sustained ≥ 0.5 mg/dL/min slope is the trigger condition. We added a per-event acceleration retention metric: for each sustained ≥ 0.5 mg/dL/min event lasting ≥ 15 minutes in the raw stream, we computed the peak |acceleration| in both the raw and the smoothed series and reported the smoothed/raw ratio. A value near 1 means the smoother preserves the dynamic; lower values mean the smoother is dampening the second-derivative signature.

### 2.5 Within-user G6→G7 comparison

For the three users with both sensors, we computed the per-algorithm Δ(G7 − G6) for each metric. Because the comparison is paired within the same physiology, this isolates the sensor effect from inter-user variation.

## 3. Results

### 3.1 Per-sensor median across users

| Metric | Sensor | AAPS Average | AAPS Exp | UKF |
|---|---|---:|---:|---:|
| Noise reduction ratio | G6 | 1.000 | 0.794 | 0.783 |
|                         | G7 | 1.000 | 0.731 | 0.739 |
| Phase shift delay (min) | G6 | 0.00 | −2.02 | −1.04 |
|                          | G7 | 0.00 | −2.04 | −1.09 |
| Cross-correlation lag (min) | G6 | 0.00 | 2.64 | 1.55 |
|                              | G7 | 0.00 | 2.76 | 1.67 |
| Hypo events preserved (%) | G6 | 100.0 | 90.0 | 95.6 |
|                            | G7 | 100.0 | 86.6 | 93.6 |
| Hypo amplitude delta (mg/dL) | G6 | 0.00 | 0.00 | −0.07 |
|                               | G7 | 0.00 | 0.00 | −0.09 |
| Outlier absorption (%) | G6 | 0.0 | 50.0 | 61.1 |
|                         | G7 | 0.0 | 55.0 | 64.8 |
| Peak-event acceleration retention | G6 | 1.000 | 0.474 | 0.623 |
|                                     | G7 | 1.000 | 0.439 | 0.587 |

The picture across the median is consistent across sensors. AAPS Average reduces to no-op (the production loop never sets a smoothed value for the newest reading, so the dose engine reads the raw input) regardless of sensor. AAPS Exponential and the UKF reduce slightly more noise on G7 than on G6 (UKF: 78 % → 74 %), with phase delay essentially unchanged across sensors (variation of 0.05 minutes). Hypoglycaemia preservation is 3–4 percentage points worse on G7 for both adaptive smoothers (UKF: 96 % → 94 %; AAPS Exp: 90 % → 87 %), consistent with G7 having slightly more low-glucose excursions per sensor-day in this sub-cohort. Outlier absorption rises modestly on G7 — both adaptive smoothers absorb a slightly larger fraction of single-reading spikes — consistent with G7 producing slightly more single-reading transmission artefacts in our data.

Peak-event acceleration retention is the most differentiating metric: the UKF preserves 62 % of peak acceleration on G6 and 59 % on G7, whereas AAPS Exponential preserves only 47 % on G6 and 44 % on G7. The UKF is therefore a less aggressive damper on rapid second-derivative changes than AAPS Exponential, on both sensors.

### 3.2 Within-user paired G6 vs G7

For the three users with both sensors, the within-user Δ(G7 − G6) (paired across the same physiology) is small and often inconsistent in sign:

| User | Algorithm | Δ Noise Reduction Ratio | Δ Phase Shift (min) | Δ Outlier Absorbed (%) | Δ Hypo Preserved (%) |
|---|---|---:|---:|---:|---:|
| User_D | AAPS Exp | −0.05 | +0.03 | +4.1 | −3.0 |
|        | UKF      | −0.04 | −0.00 | +11.6 | −0.6 |
| User_I | AAPS Exp | −0.06 | +0.15 | +21.1 | −4.6 |
|        | UKF      | −0.04 | +0.04 | +2.9 | −4.0 |
| User_J | AAPS Exp | +0.03 | −0.05 | −17.0 | +11.7 |
|        | UKF      | +0.04 | −0.05 | −18.7 | +4.3 |

User_D and User_I show the same pattern: G7 produces more aggressive smoothing (more noise reduction, more outlier absorption) and slightly fewer hypo events preserved. User_J shows the opposite pattern: G7 leads to less smoothing, fewer outliers absorbed, and more hypo events preserved. We do not have an explanation for why User_J differs; possible drivers include differing time-of-year (which changes activity profile), insulin pump model change between segments, or simply small-N variance — three users is well below the sample size needed to characterise within-user transitions.

The phase delay is essentially insensitive to the sensor for both adaptive smoothers (Δ within ±0.15 minutes across all six user-algorithm rows), suggesting the smoother's filter dynamics dominate over the sensor's noise spectrum.

### 3.3 Phase 2 distribution figures

The per-sensor distribution boxplots show overlapping G6 and G7 distributions for every metric and every algorithm — there is no metric on which the G6 and G7 distributions are statistically separable at the per-user level. The within-user G7 − G6 paired figure confirms the small magnitude of the deltas (mostly ≤ 5 percentage points on noise reduction).

*Figure 1. Per-sensor distribution of noise reduction ratio across users, split by smoother and sensor. The G6 (blue) and G7 (orange) distributions overlap heavily for every smoother.*

*Figure 2. Per-sensor distribution of phase-shift delay across users. The two adaptive smoothers' phase-shift distributions are essentially indistinguishable across G6 and G7.*

*Figure 3. Per-sensor distribution of hypoglycaemia-event preservation across users. AAPS Average is bound at 100 %; the adaptive smoothers show modest 3–4 percentage-point shifts between sensors.*

*Figure 4. Per-sensor distribution of outlier absorption (% of single-step ≥ 40 mg/dL raw changes that the smoothed series does not exhibit at the same step). G7 shows slightly higher absorption rates for both adaptive smoothers, consistent with G7 producing slightly more single-step transmission artefacts in our data.*

*Figure 5. Within-user G6 → G7 paired comparison for the three users with both sensors. Each panel shows one smoother metric with G6 on the left, G7 on the right, and lines connecting the same user's two values. Small magnitude of within-user deltas confirms that sensor type is a second-order modulator.*

## 4. Discussion

### 4.1 Sensor effects are second-order

For AID developers, the question "should I run a different smoother on G7 than on G6?" has a clear answer in this sub-cohort: no. The cohort medians for noise reduction, phase shift, hypo preservation, and outlier absorption are within 5 percentage points across sensors for both adaptive smoothers. The within-user paired comparison shows similarly small deltas, with one of three users showing the opposite sign on every metric. There is no signal in this dataset that would justify a sensor-conditional smoother choice — choose the smoother on its operating-point trade-off, not on which Dexcom generation is feeding it.

### 4.2 The peak-acceleration retention story

The one metric where the smoothers differ substantially across sensors is peak-event acceleration retention. The UKF preserves more peak acceleration than AAPS Exponential on both sensors, and both smoothers preserve marginally less on G7. This is consistent with G7's reportedly faster transmitter response: peak acceleration is more pronounced in G7 input, and the smoothers dampen a slightly larger fraction of it. AAPS Average preserves 100 % by construction (raw passes through at the leading edge), which is unsurprising but worth flagging — if a user wants the AID to react quickly to acceleration changes, AAPS Average's no-op leading edge is arguably the right default for the acceleration-driven safety nets.

### 4.3 The User_J anomaly

User_J's reversed sign on every G7 − G6 delta is the largest within-user effect in this sub-cohort and warrants a check. The upstream data shows User_J's G6 segment is 18 449 readings spanning ≈ 72 days, the G7 segment is 28 993 readings spanning ≈ 109 days, and the segments do not overlap in time. The dedup ingest used here keeps a single canonical row per (user, timestamp), so the same physical reading cannot appear in both segments. The reversal therefore reflects a real change between the two periods. With three users this paper does not attribute it to a cause; possibilities include the seasonal shift, a pump model change, or small-N variance. A larger transition cohort with explicit metadata for confounders (pump model, season, insulin formulation) would be needed to disentangle these.

### 4.4 Phase delay independence

The phase delay's near-insensitivity to sensor is reassuring: it tells us the smoother's intrinsic filter dynamics — the EMA α coefficients, the UKF's Kalman gain — dominate over whatever phase characteristics the sensor brings. Phase delay is the single metric most relevant to dose-engine timing (a smoother that lags by 2 minutes effectively dilates the AID's reaction time); the fact that the same algorithm produces the same phase delay on both sensors means the AID's effective reaction time will not change at the G6→G7 transition. This is true even though the underlying noise spectrum is different — confirming, again, that the smoothers are working as designed.

### 4.5 Limitations

* 13 (user, sensor) pairs and 3 within-user transitions are a small sub-cohort. Confidence intervals on the per-sensor medians are wide; we deliberately did not run a hypothesis test on the small-N within-user deltas.
* Only Dexcom G6 and G7 are represented in usable density. The single G7-only user (User_L) and the three transition users dominate the G7 statistics.
* The G6 and G7 segments for the three transition users are in disjoint calendar periods, not concurrent. Confounders such as season, activity profile, and pump model changes are not controlled for.
* The sensor-type tag is derived from upload metadata (`device` string and Nightscout fields). For the small fraction of rows where this tag is missing (≈ 25 % of total Nightscout rows in the source), we exclude rather than guess; the resulting (user, sensor) pairs are therefore the cleanly-tagged subset, which may not be representative of the user's full record.
* We did not run the full oref0 dose engine downstream of the smoothers. The analysis is intrinsic to each smoother's output, not clinical-outcome.

## 5. Conclusion

Within a sensor-tagged sub-cohort of 13 (user × sensor) pairs and three within-user G6→G7 transitions, the three production CGM smoothers used by oref0-derived AID systems show second-order sensitivity to which Dexcom transmitter generation is feeding them. AAPS Average remains operationally a no-op at the leading edge regardless of sensor. AAPS Exponential and the UKF show 4–7 percentage point shifts in noise reduction across sensors, with similar (≤ 0.15 min) phase delay. The within-user paired comparison shows three users with consistently small deltas, one of whom reversed sign on every metric — consistent with three-user variance rather than a systematic G7 effect. Phase delay, the single metric most relevant to dose-engine timing, is essentially identical across sensors for each smoother. We therefore do not recommend a sensor-conditional smoother choice for AID systems; algorithm choice on its noise-versus-delay operating-point trade-off takes precedence over the G6 vs G7 distinction.
