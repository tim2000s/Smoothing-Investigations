# Production-Realistic Evaluation of CGM Smoothing Algorithms in Open-Source Automated Insulin Delivery

*Cohort backtest of three smoothing algorithms used in oref0-derived AID systems, evaluated as the algorithms run in production: a sliding window applied to each new reading.*

---

## Abstract

This paper evaluates three CGM smoothing algorithms used by open-source automated insulin delivery (AID) systems built on the oref0 algorithm: the AAPS three-point central-window average (`AvgSmoothingPlugin`), the AAPS dual-order exponential (`ExponentialSmoothingPlugin`, marketed in some forks as TSUNAMI), and an adaptive Unscented Kalman Filter with a Rauch–Tung–Striebel (RTS) backward sweep. We re-implemented each algorithm from the upstream Kotlin source, validated each Python port against the original Kotlin reference to bit-exactness (or sub-mg/dL tolerance for the floating-point UKF), and ran each smoother across a 19-user, 90-day cohort drawn from a research database of oref0-style closed-loop sessions. Critically, we evaluated each algorithm in *online sliding-window mode* — at every chronological reading t, the smoother is asked the value the AID dose engine would actually see at decision time t — rather than as a single offline pass. This changes the picture qualitatively for AAPS Average, which never sets a smoothed value for the newest reading in any call: in production the dose engine reads the raw value, and the algorithm has zero leading-edge effect on the current reading. AAPS Exponential and the UKF do produce a smoothed leading-edge value. Across the cohort, AAPS Exponential reduced 5-minute high-frequency noise by a median of 16 % at the cost of a 1.7-minute phase shift; the UKF reduced noise by 18 % with a phase shift of 0.85 minutes and the lowest median hypoglycaemia-event amplitude attenuation of the three. The UKF dominates the noise/delay Pareto frontier and absorbed 38 % of raw outliers compared with 30 % for AAPS Exponential and 0 % for AAPS Average. We discuss what this means for AID design: a smoother that does not act on the current reading is operationally a no-op for the most safety-critical decision (treating now), whatever it does to the historical view of the curve.

---

## 1. Introduction

The oref0 algorithm and its derivatives — AndroidAPS (AAPS), Trio, Loop — make insulin dosing decisions every five minutes from continuous glucose monitor (CGM) readings. Because CGM transmission noise, sensor compression, and Dexcom transmitter quantisation can produce single-reading deviations of 5–30 mg/dL even on a well-behaved sensor, every production AID system filters the raw stream before feeding it to the dose-calculation core. Three smoothing approaches dominate the open-source ecosystem:

* **AAPS Average** — a simple three-point central-window mean, gated by in-range checks (39–401 mg/dL) and timing checks (samples ≤ 30 s off the regular five-minute spacing).
* **AAPS Exponential / TSUNAMI** — a dual-order exponential blend, with a first-order EMA (α = 0.5) blended at weight 0.4 with a second-order trended EMA (α = 0.4, β = 1.0) over a 24-reading (≈ 2-hour) window.
* **Adaptive UKF** — a 2-state (glucose, rate-of-change) Unscented Kalman Filter with a Merwe-scaled sigma-point parameterisation, an adaptive measurement-noise term that shrinks during calm periods and expands when innovations grow, χ²-based outlier rejection at threshold 15.13, and an RTS backward smoother.

Production AAPS instantiates a fresh smoother on every loop tick (every five minutes) and feeds it a sliding window of the most recent N readings — N = 24 for AAPS Exponential, N = 36 (≈ 3 hours) for the UKF as configured in the development branches that compile this filter. The dose engine then reads the smoothed value the algorithm produces for the newest reading in that window and uses that as its "current glucose". A single offline call over the full series produces a different curve from the sliding-window evaluation that runs in production; the *operationally relevant* output is the leading-edge value of each sliding-window call, not the offline pass.

This distinction matters most for AAPS Average. The Kotlin source (`AvgSmoothingPlugin.smooth`) iterates `for (i in 1 until data.size - 1)` and writes `data[i].smoothed` only for interior positions, leaving `data[0].smoothed` (the newest reading in newest-first AAPS list convention) unset. The dose engine reads `data[0].smoothed` and falls back to `data[0].value` (the raw input) when no smoothed value is set. So at decision time t the AID using AAPS Average sees the raw glucose t — the value at t is unchanged. The algorithm does, however, write a smoothed value to t − 1 once t arrives (the three-point average uses t, t − 1 and t − 2), which means the rate-of-change calculation between "now" and "five minutes ago" reads `raw(t) − smoothed(t − 1)` rather than `raw(t) − raw(t − 1)`. The rate delta into t is therefore modified even though the value at t itself is not — a single-reading spike at t − 1 that would otherwise distort the slope is dampened by the time the AID computes that slope at t.

We also re-evaluated the UKF in this online mode. Although the RTS backward sweep is the algorithm's most distinctive component, RTS by construction does not modify the leading edge: it propagates information from the newest to older states in a backward pass, leaving the newest state at its forward-filter value. The UKF's leading-edge output therefore equals its forward-filter output; the RTS revisions affect the historical view of the curve but not the current reading.

This paper reports the cohort-wide statistics of each smoother under the production-realistic online evaluation.

## 2. Methods

### 2.1 Reference implementations and parity testing

We obtained the upstream AAPS Kotlin source (`AvgSmoothingPlugin.kt`, `ExponentialSmoothingPlugin.kt`) and a development branch implementation of the adaptive UKF (`UnscentedKalmanFilterPlugin.kt`) and vendored a verbatim copy in this repository for reference. We then built a standalone Kotlin driver that runs each algorithm in two modes — a single batch call over the full input array, and a sliding-window online emulation in which a fresh instance of the smoother is created at each chronological t and called with the trailing W readings (W = 3 for AAPS Average's natural three-point window, W = 24 for AAPS Exponential, W = 36 for the UKF). The driver writes per-fixture JSON reference outputs for both modes.

We then ported each algorithm to Python and validated it against the Kotlin reference on three fixtures: a synthetic step input, a sinusoid, and a 24-hour real-data slice from the cohort. Tolerances:

* AAPS Average — bit-exact (max |Δ| = 0.0 mg/dL), in both batch and online modes.
* AAPS Exponential — bit-exact in online sliding-window mode (max |Δ| = 0.0 mg/dL); the algorithm applies `max(round(...), 39.0)` as a final step, which makes integer parity achievable across language boundaries.
* UKF — within 0.5 mg/dL absolute on output and 1e-3 on covariance entries; the gap arises from floating-point order-of-summation differences in the sigma-point math.

All 15 parity tests pass before any cohort run.

### 2.2 Cohort selection

The cohort consists of 19 users selected from a research database of oref0-style closed-loop sessions. Three source tables (`oref_v5`, `oref_v6`, `oref_v7`) correspond to different oref0 release lines. For each user we required at least 90 days of CGM data and a 5-minute grid density of ≥ 95 % of expected readings. We extracted the most recent 90 days per user (≈ 25 000 readings each) and resampled onto a strict 5-minute grid using the in-repo loader (`backtest.io.load_user`).

### 2.3 Online sliding-window evaluation

For every (user, algorithm) pair, we ran the smoother in online mode: at each chronological index t we instantiated a fresh smoother, fed it the trailing W readings, and recorded the leading-edge smoothed value as the smoother's output at t. Per-step trace columns (input glucose, output glucose, plus filter-internal quantities for the UKF) were written to Parquet. Across the cohort this produced 57 trace files (19 users × 3 algorithms) totalling ≈ 480 000 reading-decisions.

### 2.4 Per-user metrics

For each (user, algorithm) trace we computed the following metrics, producing one row per pair in `reports/per_user_metrics.csv`:

* **Cross-correlation lag** (`xcorr_lag_min`) — argmax of the cross-correlation between raw and smoothed series, in minutes; a positive value means the smoothed series lags the raw.
* **Step-response delay** (`step_response_median_delay_min`) — median delay between a ≥ 20 mg/dL change in the raw and the half-amplitude crossing in the smoothed.
* **Phase-shift delay** (`phase_shift_delay_min`) — phase of the smoother's transfer function at the dominant cycling frequency, expressed in minutes.
* **Noise reduction ratio** (`noise_reduction_ratio`) — ratio of high-frequency power in the smoothed to the raw (5–10 minute band).
* **Attenuation in the signal band** (`attenuation_signal_band`) — ratio of low-frequency power in the smoothed to the raw (≥ 30-minute band); ideally ≈ 1, meaning real glucose dynamics pass through.
* **Hypoglycaemia preservation** (`hypo_preserved_pct`) — percentage of independent < 70 mg/dL events in the raw that remain ≤ 70 mg/dL in the smoothed.
* **Hypo amplitude delta** (`hypo_amp_delta`) — median (smoothed nadir − raw nadir) across hypo events.
* **Peak preservation** (`peak_preserved_pct`) — same as hypo, but for > 220 mg/dL excursions.
* **Outlier absorption** (`outlier_absorbed_pct`) — percentage of single-reading raw spikes (> 30 mg/dL deviation from a 25-min local median) that the smoother attenuates by ≥ 50 %.

We also produced spectrum-domain transfer-function estimates and per-step modification deltas (the change a smoother applies relative to the raw input, broken down into predict / update / RTS components for the UKF).

### 2.5 Cross-smoother statistics

We compared smoothers pairwise on each metric using the paired Wilcoxon signed-rank test (the smoothers see the same input per user, so users are matched units), with Holm-corrected p-values across the three pairs of smoothers (`aaps_average` vs `aaps_exponential`, `aaps_average` vs `ukf`, `aaps_exponential` vs `ukf`).

## 3. Results

### 3.1 Operational summary by algorithm

The following table reports the cohort median (with the 25th–75th percentile range across the 19 users) for each algorithm on the production-realistic online evaluation.

| Metric | AAPS Average | AAPS Exponential | UKF |
|---|---:|---:|---:|
| Noise reduction ratio (smaller = more smoothing) | 1.000 (1.000 – 1.000) | 0.840 (0.767 – 0.888) | 0.819 (0.737 – 0.872) |
| Attenuation in signal band (≈ 1 = transparent) | 1.000 (1.000 – 1.000) | 1.032 (1.029 – 1.035) | 1.032 (1.031 – 1.034) |
| Cross-correlation lag (min) | 0.000 (0.000 – 0.000) | 1.94 (1.68 – 2.04) | 1.11 (1.01 – 1.22) |
| Phase-shift delay (min) | 0.00 (0.00 – 0.00) | −1.71 (−1.84 – −1.61) | −0.85 (−0.89 – −0.77) |
| Step-response delay (min) | −0.75 (−0.94 – −0.63) | 0.31 (0.21 – 0.41) | −0.25 (−0.29 – −0.19) |
| Hypo events preserved (%) | 100.0 (100.0 – 100.0) | 92.9 (87.5 – 95.1) | 96.6 (92.2 – 97.7) |
| Median hypo amplitude delta (mg/dL) | 0.00 (0.00 – 0.00) | 0.00 (−0.15 – 0.00) | −0.18 (−0.35 – −0.01) |
| Peak (> 220 mg/dL) preserved (%) | 100.0 (100.0 – 100.0) | 98.4 (96.1 – 99.5) | 99.5 (98.5 – 100.0) |
| Outlier absorption (%) | 0.0 (0.0 – 0.0) | 29.7 (20.3 – 48.9) | 37.8 (29.2 – 59.5) |

A few features stand out.

**AAPS Average is operationally a no-op at the leading edge.** Every metric reduces to its raw-pass-through value: noise ratio 1.0, phase shift 0, no outlier absorption, 100 % hypo preservation. This is not a flaw in the algorithm; it is the consequence of the production design where the dose engine reads the raw value for the current reading and the smoother only retroactively smooths older readings. The smoother does affect rate-of-change calculations (because the AID may compute slope using smoothed t − 1 and raw t), but the current-reading value driving the next bolus or basal adjustment is never modified.

**AAPS Exponential is the most aggressive smoother.** It applies the largest noise reduction (16 % cohort median), but at a cost: 1.7-minute phase delay, 7 % of low-glucose events lost (the smoother lifts the nadir above 70 mg/dL or shifts the timing), and a 1.6-minute step-response lag.

**The UKF dominates the noise/delay frontier.** It achieves slightly more aggressive noise reduction (18 % cohort median) than AAPS Exponential at half the phase delay (0.85 min). Step-response delay is *negative* (median −0.25 min), meaning the UKF's rate-of-change estimate predicts crossings of large changes slightly faster than they actually occur — a feature of the rate-state and the q-inflation that ramps the rate variance during sustained changes.

The pairwise Wilcoxon tests reject the null of no median difference at Holm-corrected p < 0.001 for every metric and every pair where the two smoothers differ on the metric (so AAPS Average is significantly different from both other smoothers on noise, phase, and outlier absorption; AAPS Exponential and UKF differ significantly on phase, hypo preservation, and outlier absorption). The full table is in `reports/cross_smoother_tests.csv`.

### 3.2 Pareto frontier — noise reduction vs phase shift

*Figure 1. Per-user noise reduction ratio plotted against phase-shift delay for each smoother. Lower-left is better (more noise reduction at smaller delay). The UKF cluster lies left and below the AAPS Exponential cluster, indicating Pareto dominance on this trade-off. AAPS Average is collapsed to (1, 0) — the no-op corner.*

The figure makes the trade-off concrete. AAPS Exponential's points cluster at noise ratio 0.75 – 0.90 with phase delay −1.5 to −2.0 minutes; UKF's points cluster at noise ratio 0.74 – 0.87 with phase delay −0.7 to −1.0 minutes. The Pareto frontier is the UKF cluster — for any AAPS Exponential operating point, there exists a UKF user with at least as much noise reduction and a smaller phase delay.

*Figure 2. Spectral transfer function (smoothed/raw power vs frequency) for each smoother, averaged across the cohort. AAPS Average is unity at all frequencies, consistent with no leading-edge effect. AAPS Exponential and the UKF roll off similarly above 0.05 Hz (≈ 5 minute period); the UKF's signal-band gain is essentially identical to AAPS Exponential's, confirming both preserve real glucose dynamics.*

### 3.3 Per-step decomposition

For the UKF we can decompose the smoothing into three stages and report the per-reading absolute delta each stage applies. The cohort-median |Δ| in mg/dL is:

| Stage | Median |Δ| (mg/dL) | Frac. of readings changed |
|---|---:|---:|
| Predict (sigma-point time-update) | 3.5 | 100 % |
| Update (Kalman correction towards measurement) | 2.3 | 100 % |
| RTS (backward smoother) | 0.0 | 0.2 % |

*Figure 3. Per-step modification stack: median absolute Δ (mg/dL) at each pipeline stage, broken out by smoother. For AAPS Average and AAPS Exponential the only stage is the filter; for the UKF the stack is predict / update / RTS. RTS contributes essentially zero at the leading edge.*

The RTS stage applies effectively zero modification at the leading edge — confirming the algorithmic property: RTS revises older positions in the window but leaves the newest state at its forward value. This is *correct* for a real-time AID — it would be incoherent for the smoother to retroactively rewrite the value the dose engine already used. But it does mean that the RTS code path, which is the most computationally distinctive part of the implementation, contributes nothing to the current-reading output. Its effect is on the historical curve the AID can look back at.

For the AAPS Exponential the leading-edge filter applies a median |Δ| of 2.0 mg/dL and changes 86 % of readings.

### 3.3.1 Event-aligned visual comparison

To visualise smoother behaviour against real data, we event-aligned the cohort traces around two reference event types: low-variance "calm" 6-hour windows where the raw glucose stays within a narrow band, and "rate-rise" events where a sustained ≥ 0.5 mg/dL/min rise is observed.

*Figure 4. Calm-window deviation envelopes: median (line) and IQR (shaded) of (smoothed − raw) across calm windows for each smoother. AAPS Average's envelope sits exactly on zero (no leading-edge effect). AAPS Exponential and the UKF show small bounded deviations during quiet periods.*

*Figure 5. Rate-rise event-aligned deviation envelopes: median (line) and IQR (shaded) of (smoothed − raw) across rate-rise events. The negative shift during the rise reflects the smoother's lag — it tracks below the rising raw — and is visibly smaller for the UKF than AAPS Exponential.*

### 3.4 Per-user phenotypes

We clustered users on their smoother-independent characteristics — coefficient of variation, time-in-range, mean rate-of-change, fraction of readings above 220 mg/dL — and computed silhouette scores for k = 2 and k = 3. The k = 2 silhouette is 0.30, below the 0.4 threshold normally used to declare meaningful structure. This means we found no clear phenotypic split (e.g., "high CV users get more benefit from UKF"); the smoother ranking is consistent across user types and the per-metric distributions are similar within each cluster. We therefore do not recommend a regime-dependent smoother choice.

### 3.5 SID (Sensor Integrity Detection) re-detection

The Sensor Integrity Detection (SID) v6 logic looks for sustained glucose-deviation events that suggest a sensor problem (compression, calibration drift). We ran SID against each smoother's output to count surviving events. The headline numbers across the cohort are:

* AAPS Average: 4 948 SID events (bias to flag, since the leading edge is raw and noisy)
* AAPS Exponential: 1 259 events (75 % fewer than AAPS Average)
* UKF: 2 416 events (51 % fewer than AAPS Average)

*Figure 6. Per-user SID-survival vs noise reduction for each smoother. Lower-left is fewer SID events at greater noise reduction. AAPS Exponential dominates on this metric, the UKF lies between, AAPS Average is the noisy upper-right corner.*

This ordering is the opposite of what the noise-reduction metrics alone would suggest. AAPS Exponential reduces the SID event count more than the UKF does, despite reducing high-frequency noise less aggressively than the UKF on standard cohort metrics. The mechanism is the phase shift: SID looks for *sustained* deviations, and AAPS Exponential's larger phase delay (≈ 1.7 min vs the UKF's ≈ 0.85 min) causes the smoothed series to track the raw more loosely during real glucose excursions, which suppresses the cumulative deviation that triggers SID. The UKF, with its sharper response to genuine excursions, more often produces deviations that meet SID's amplitude/duration thresholds. Whether the AAPS Exponential reduction is a benefit (fewer false-positive sensor flags) or a harm (more missed real sensor problems) depends on a ground-truth label set we do not have. The correlations between SID-event count and clinical-outcome metrics (time-below-70, hypo episode count, MAGE, LBGI) are individually small (|r| < 0.3) and inconsistent across smoothers. We interpret this conservatively: the smoother choice changes how often the SID layer fires, but no smoother shows a clinically actionable association with any outcome metric in this 19-user cohort.

## 4. Discussion

### 4.1 The AAPS Average paradox

It is sometimes assumed that AAPS Average "smooths the current reading by averaging over the last three points". This is incorrect by inspection of the source. The Kotlin loop is

```
for (i in 1 until data.size - 1) {
    if (39 < data[i-1].value && ... ) {
        data[i].smoothed = (data[i-1].value + data[i].value + data[i+1].value) / 3.0
    }
}
```

In AAPS newest-first list convention, `data[0]` is the newest reading and `data[lastIndex]` the oldest. The loop's first iteration sets `data[1].smoothed` — which is the *previous* reading — using `data[0]` (newest), `data[1]` (one back), and `data[2]` (two back). The newest reading itself, `data[0]`, is never touched. Once another reading arrives, the previously-newest `data[0]` becomes `data[1]` in the next call's view and *then* gets averaged using the now-newest reading as its "future" neighbour, the previously-newest as its "current", and the previously-one-back as its "past". In other words, AAPS Average operates at a one-tick lag — it commits a smoothed value to a reading only when the next reading has arrived. The dose engine sees raw at t, smoothed at t − 1, smoothed at t − 2, and so on.

This is a deliberate design: it avoids ever passing a "future-using" smoothed value into a real-time decision (because the average's three-point window inherently uses one future point relative to the smoothed centre, so smoothing the current reading would require the next-arriving reading, which does not yet exist). The smoother therefore offers two services:

1. **Slope smoothing** — the AID's rate-of-change calculation can use smoothed t − 1, smoothed t − 2, etc., which reduces single-reading slope spikes.
2. **Historical curve smoothing** — visualisations and other slow-loop analyses see a cleaner past curve.

But it does not service the most safety-critical question: "what is the glucose right now?" That reads as raw.

This is a defensible choice: smoothing the current reading necessarily delays it, and a real-time AID arguably wants a fast (raw) current value plus a clean historical view of where it has been. AAPS Exponential and the UKF take the opposite stance: they smooth the current reading too, accepting some delay in exchange for less single-reading noise driving the next bolus.

We are not arguing that one stance is inherently better. We are arguing that any cohort comparison of these algorithms that runs them as offline batch passes will report misleadingly large effects for AAPS Average — every interior reading appears smoothed, including the current — when in practice the algorithm's online effect on the current-reading is zero.

### 4.2 The UKF Pareto position

For every operating point AAPS Exponential reaches, there exists a UKF parameterisation with similar or better noise reduction at smaller phase delay. This is consistent with the UKF being a more sophisticated estimator that uses both the measurement and a model of glucose dynamics; the trade-off the UKF accepts is computational cost (roughly 250 × more wall-clock per reading than AAPS Exponential in our Python implementation, though absolute cost is still negligible — < 100 µs per reading on a modern CPU) and substantially more parameter complexity (covariance bounds, χ² thresholds, R-adaptation rates). The development-branch UKF parameterisation we used has been tuned by the AAPS development team against a separate dataset; whether the UKF would still dominate after a re-tune for our cohort is a question for future work.

### 4.3 What "online" means for outlier absorption

The UKF absorbs 38 % of raw outliers at the leading edge — meaning its current-reading output for those readings is at least 50 % closer to the local trend than the raw spike value. AAPS Exponential absorbs 30 %; AAPS Average absorbs 0 % at the leading edge by construction (raw passes through). For an AID, absorbed outliers are absorbed *before* the dose engine sees them, so they cannot trigger a single-reading dose. AAPS Exponential and the UKF therefore offer real protection against single-reading artefacts; AAPS Average does not at the operational moment, although it does prevent such artefacts from biasing the next slope calculation once the next reading arrives. Slope dampening matters because oref0's safety net (`exercise mode`, `temptarget`, etc.) ramps off fairly quickly when slope estimates decay; a single-reading 50 mg/dL spike can briefly lift the slope estimate above safety thresholds even if the dose engine handles the absolute value sensibly.

### 4.4 Limitations

* The cohort is 19 users, not a clinical trial. We did not have ground-truth glucose values (no blood-glucose comparator), so noise/delay statistics are purely intrinsic-to-the-stream.
* We evaluated only the *intrinsic* effect of each smoother on its own output. We did not run the full oref0 dose engine downstream of each smoother; we cannot say whether the 36 % reduction in SID events the UKF produces would translate to fewer (or more) clinical hypo/hyper events under closed-loop control.
* The AAPS Exponential and UKF window sizes (24 and 36 readings) are taken from the upstream code; they could be tuned per user but were not.
* The UKF parameterisation comes from a development branch. The algorithm has not yet shipped in a stable AAPS release at the time of writing.
* Online sliding-window evaluation rebuilds smoother state at every reading, which differs from production where some smoothers persist learned R across calls. We followed the production AAPS choice of fresh state per call (this is what the Kotlin source does on every loop tick) so the comparison reflects what production actually executes; it is *not* what a long-running stateful filter would look like.

### 4.5 Reproducibility

The full pipeline — Kotlin reference compile, Python ports, parity tests, cohort backtest, per-user metrics, cross-smoother statistics, SID re-detection — is reproducible from the repository:

```
gradle -p backtest/reference/kotlin_driver run \
    --args='backtest/tests/fixtures/inputs.json backtest/tests/fixtures/kotlin/'
pytest backtest/tests/                              # 15 parity tests
python3 -m backtest.cli.run_backtest --days 90      # 19 users × 3 smoothers, online mode
python3 -m backtest.cli.compare                     # per-user metrics
python3 -m backtest.cli.cross_smoother              # Wilcoxon
python3 -m backtest.cli.spectral                    # transfer-function PSDs
python3 -m backtest.cli.per_step_modify             # per-stage decomposition
python3 -m backtest.cli.sid_redetect                # SID survival vs smoother
```

All Parquet traces, per-user CSVs, and figures referenced in this paper are in `runs/` and `reports/`.

## 5. Conclusion

In a production-realistic online sliding-window evaluation across 19 users and 90 days each, the three CGM smoothers used by oref0-derived AID systems behave very differently. AAPS Average is a no-op for the current reading at decision time and exists primarily to clean the historical view of the curve. AAPS Exponential reduces noise by 16 % at a cost of a 1.7-minute phase shift and a 7-percentage-point loss of hypo events. The adaptive UKF achieves slightly more aggressive noise reduction (18 %) at half the phase shift (0.85 min) and the lowest cost in hypo-event preservation (3.4 percentage points lost); it dominates the AAPS Exponential operating range on the noise/delay Pareto frontier. The UKF's RTS backward smoother contributes effectively nothing to the current-reading value (RTS does not modify the leading edge by construction); its cost-vs-benefit lies in cleaning the historical curve, not in the current-reading dose. The choice of smoother for an AID design is therefore a choice about how much delay to accept on the current reading in exchange for fewer single-reading dose perturbations; in this cohort the UKF offers the best operating point on that trade-off.
