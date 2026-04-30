# Production-Realistic Evaluation of CGM Smoothing Algorithms in Open-Source Automated Insulin Delivery

*Cohort backtest of three smoothing algorithms used in oref0-derived AID systems, evaluated as the algorithms run in production: a sliding window applied to each new reading.*

---

## Abstract

This paper evaluates three CGM smoothing algorithms used by open-source automated insulin delivery (AID) systems built on the oref0 algorithm: the AAPS three-point central-window average (`AvgSmoothingPlugin`), the AAPS dual-order exponential (`ExponentialSmoothingPlugin`, marketed in some forks as TSUNAMI) over a trailing 24-reading window, and an adaptive Unscented Kalman Filter with a Rauch–Tung–Striebel (RTS) backward sweep over a trailing 36-reading window. We re-implemented each algorithm from the upstream Kotlin source, validated each Python port against the original Kotlin reference to bit-exactness (or sub-mg/dL tolerance for the floating-point UKF), and ran each smoother across a 19-user, 90-day cohort drawn from a research database of oref0-style closed-loop sessions in *online sliding-window mode* — at every chronological reading t, the smoother is asked the value the AID dose engine would actually see at decision time t. The picture is qualitatively different for AAPS Average, which never sets a smoothed value for the newest reading in any call: in production the dose engine reads the raw value, and the algorithm has zero leading-edge effect on the current reading. AAPS Exponential and the UKF do produce a smoothed leading-edge value. Across the cohort, AAPS Exponential reduced step-to-step noise by a median of 16 % at the cost of a 1.7-minute phase shift, with 7.1 percentage points of < 70 mg/dL events not preserved at the leading edge; the UKF reduced noise by 18 % with a phase shift of 0.85 minutes and 3.4 percentage points of hypo events not preserved (AAPS Average preserves all events at the leading edge because it does not smooth the current reading). On a per-user paired noise-versus-phase plot, the UKF lies at smaller phase delay than AAPS Exponential with overlapping noise-reduction ranges, and achieves at least as much noise reduction at smaller phase delay as AAPS Exponential for 14 of 19 users. UKF absorbed 38 % of raw outliers vs 30 % for AAPS Exponential and 0 % for AAPS Average at the leading edge. We discuss what this means for AID design: a smoother that does not act on the current reading is operationally a no-op for the most safety-critical decision (treating now), whatever it does to the historical view of the curve.

---

## 1. Introduction

The oref0 algorithm and its derivatives — AndroidAPS (AAPS), Trio, Loop — make insulin dosing decisions every five minutes from continuous glucose monitor (CGM) readings. Because CGM transmission noise, sensor compression, and Dexcom transmitter quantisation can produce single-reading deviations of 5–30 mg/dL even on a well-behaved sensor, every production AID system filters the raw stream before feeding it to the dose-calculation core. Three smoothing approaches dominate the open-source ecosystem:

* **AAPS Average** — a simple three-point central-window mean, gated by in-range checks (39–401 mg/dL) and timing checks (samples ≤ 30 s off the regular five-minute spacing).
* **AAPS Exponential / TSUNAMI** — a dual-order exponential blend, with a first-order EMA (α = 0.5) blended at weight 0.4 with a second-order trended EMA (α = 0.4, β = 1.0) over a 24-reading (≈ 2-hour) window.
* **Adaptive UKF** — a 2-state (glucose, rate-of-change) Unscented Kalman Filter with a Merwe-scaled sigma-point parameterisation, an adaptive measurement-noise term that shrinks during calm periods and expands when innovations grow, χ²-based outlier rejection at threshold 15.13, and an RTS backward smoother.

Production AAPS instantiates a fresh smoother on every loop tick (every five minutes). AAPS Average is fed the trailing reading history and acts on a three-point window centred on each interior position; AAPS Exponential is fed the trailing 24 readings and runs its dual-EMA across them; the UKF is fed the trailing 36 readings (≈ 3 hours) as configured in the development branch that compiles this filter. The dose engine then reads the smoothed value the algorithm produces for the newest reading in that window and uses that as its "current glucose". The operationally relevant output is therefore the leading-edge value of each sliding-window call.

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

The cohort consists of 19 users selected from a research database of oref0-style closed-loop sessions. Three source tables (`oref_v5`, `oref_v6`, `oref_v7`) correspond to different oref0 release lines. The cohort selector requires at least 90 days of CGM data and a 5-minute grid density of ≥ 95 % of expected readings. We extracted up to the most recent 90 days per user (median 21 033 present readings per user, range 2 733 – 25 312) and resampled onto a strict 5-minute grid using the in-repo loader (`backtest.io.load_user`).

### 2.3 Online sliding-window evaluation

For every (user, algorithm) pair, we ran the smoother in online mode: at each chronological index t we instantiated a fresh smoother, fed it the trailing W readings, and recorded the leading-edge smoothed value as the smoother's output at t. Per-step trace columns (input glucose, output glucose, plus filter-internal quantities for the UKF) were written to Parquet. Across the cohort this produced 57 trace files (19 users × 3 algorithms) totalling ≈ 370 000 reading-decisions per algorithm (≈ 1.1 million in aggregate).

### 2.4 Per-user metrics

For each (user, algorithm) trace we computed the following metrics, producing one row per pair in `reports/per_user_metrics.csv`. Exact definitions are in `backtest/metrics.py`.

* **Cross-correlation lag** (`xcorr_lag_min`) — argmax of the cross-correlation between raw and smoothed (with sub-sample parabolic interpolation around the peak), in minutes; a positive value means the smoothed series lags the raw. Search bounded to ± 60 min.
* **Step-response delay** (`step_response_median_delay_min`) — median time between (a) the raw rate of change first crossing 0.5 mg/dL/min at the start of a sustained event whose total amplitude reaches ≥ 15 mg/dL, and (b) the smoothed rate of change crossing the same threshold scaled by 0.7 (= 0.35 mg/dL/min). The 0.7 factor is applied because smoothing dampens the rate; a strict same-threshold crossing under-counts smoother responses. A negative delay means the smoothed crosses 0.35 mg/dL/min before the raw crosses 0.5 mg/dL/min.
* **Phase-shift delay** (`phase_shift_delay_min`) — median Hilbert-phase difference between band-pass-filtered (1–6 hour cycle) raw and smoothed series, scaled at the band centre frequency (3-hour cycle) and expressed in minutes.
* **Noise reduction ratio** (`noise_reduction_ratio`) — variance ratio of the first differences of the two series: `var(diff(smoothed)) / var(diff(raw))`. First-difference variance is dominated by 5-minute step-to-step changes, which are the high-frequency content. A ratio < 1 means the smoother reduces step-to-step noise.
* **Attenuation in the signal band** (`attenuation_signal_band`) — power-spectral-density ratio in the 1–6 hour cycle band (where physiological glucose dynamics live). 1.0 = no attenuation, < 1 = real signal dampened.
* **Hypoglycaemia preservation** (`hypo_preserved_pct`) — percentage of independent < 70 mg/dL events in the raw (separated by ≥ 60 min of in-range glucose) that the smoothed series also dips below 70 mg/dL on within the same event window.
* **Hypo amplitude delta** (`hypo_amp_delta`) — median (smoothed nadir − raw nadir) across preserved hypo events.
* **Peak preservation** (`peak_preserved_pct`) — same as hypo, but for > 180 mg/dL excursions.
* **Outlier absorption** (`outlier_absorbed_pct`) — percentage of single-step (one-grid-step, i.e. 5-minute) raw changes ≥ 40 mg/dL that the smoothed series does not also exhibit at the same step (i.e. the smoothed first-difference at that step falls below 40 mg/dL).

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
| Peak (> 180 mg/dL) preserved (%) | 100.0 (100.0 – 100.0) | 98.4 (96.1 – 99.5) | 99.5 (98.5 – 100.0) |
| Outlier absorption (%) | 0.0 (0.0 – 0.0) | 29.7 (20.3 – 48.9) | 37.8 (29.2 – 59.5) |

A few features stand out.

**AAPS Average is operationally a no-op at the leading edge.** Every metric driven by the leading-edge value reduces to its raw-pass-through level: noise ratio 1.0, phase shift 0, no outlier absorption, 100 % hypo and peak preservation. The step-response value of −0.75 minutes is a metric artefact (the smoothed-rate threshold of 0.35 mg/dL/min is crossed before the raw-rate threshold of 0.5 mg/dL/min as a rising rate sweeps past 0.35 then 0.5, regardless of whether smoothing is applied) and is consistent with no leading-edge effect. This behaviour is the consequence of the production AAPS design — the dose engine reads the raw value for the current reading and the smoother only retroactively smooths older readings — not a flaw. The smoother does affect rate-of-change calculations (because the AID may compute slope using smoothed t − 1 and raw t), but the current-reading value driving the next bolus or basal adjustment is never modified.

**AAPS Exponential applies the larger phase delay.** It reduces step-to-step noise by 16 % (cohort median) at a cost of a 1.7-minute phase delay, with 7 % of low-glucose events not preserved (the smoother lifts the nadir above 70 mg/dL or shifts the timing) and a step-response that crosses the smoothed-rate threshold 0.31 minutes after the raw rate crossing — the only smoother in the cohort with a positive step-response value.

**The UKF achieves slightly more noise reduction at less than half the phase delay.** It reduces step-to-step noise by 18 % (cohort median) versus AAPS Exponential's 16 %, with phase delay 0.85 minutes versus AAPS Exponential's 1.7 minutes. The step-response value (median −0.25 min) is negative because the metric uses a smoothed-rate threshold of 0.35 mg/dL/min while measuring against a raw-rate threshold of 0.5 mg/dL/min: the smoothed rate, fed by the UKF's rate-state, crosses 0.35 mg/dL/min before the raw single-step rate crosses 0.5 mg/dL/min in 50 % of large events. AAPS Average shows a comparable −0.75 min on the same metric for the same threshold-asymmetry reason. This number should not be read as the UKF "predicting" the future of the raw stream.

Pairwise Wilcoxon signed-rank tests with Holm correction across the 30 (metric × pair) combinations: for the metrics on which AAPS Average is structurally distinct from the adaptive smoothers (noise reduction, phase shift, signal-band attenuation, cross-correlation lag, outlier absorption), AAPS Average vs each adaptive smoother rejects at p ≤ 1e-4. AAPS Exponential vs UKF rejects at p ≤ 1e-3 on cross-correlation lag, step-response delay, phase shift, hypo preservation, peak preservation and outlier absorption; rejects at p = 0.026 on noise reduction (significant at α = 0.05 but the Exp-vs-UKF noise difference is modest, ≈ 2 percentage points); and does not reject on signal-band attenuation, hypo amplitude delta, or hypo timing delta (p > 0.05 — the smoothed/raw signal-band gain and the small-amplitude shifts in surviving hypo events are not statistically separable between the two adaptive smoothers in this cohort). The full table is in `reports/cross_smoother_tests.csv`.

### 3.2 Pareto frontier — noise reduction vs phase shift

*Figure 1. Per-user noise reduction ratio plotted against phase-shift delay for each smoother. Lower-left is better (more noise reduction at smaller delay). The UKF cluster lies at smaller phase delay than AAPS Exponential, with overlapping noise-reduction ranges. AAPS Average is at (1, 0) — the no-op corner.*

The figure makes the trade-off concrete. AAPS Exponential's points cluster at noise ratio 0.36 – 0.94 with phase delay −1.2 to −2.3 minutes; UKF's points cluster at noise ratio 0.40 – 0.92 with phase delay −0.66 to −1.17 minutes. On a per-user paired comparison the UKF achieves at least as much noise reduction at smaller phase-delay magnitude than AAPS Exponential for 14 of 19 users; the remaining 5 users (U033, U047, U138, U142, U143) have AAPS Exponential with marginally lower noise ratios than the UKF on the same user but with larger phase delays. Across the cohort the existential claim holds for 18 of 19 AAPS Exponential operating points (some UKF user has at least as much noise reduction at smaller phase-delay magnitude); U033's AAPS Exponential point at noise ratio 0.359 is the one not matched by any UKF user.

*Figure 2. Spectral transfer function (smoothed/raw power vs frequency) for each smoother, averaged across the cohort. AAPS Average is unity at all frequencies, consistent with no leading-edge effect. AAPS Exponential and the UKF roll off similarly above 0.05 Hz (≈ 5 minute period); the UKF's signal-band gain is essentially identical to AAPS Exponential's, confirming both preserve real glucose dynamics.*

### 3.3 Per-step decomposition

For the UKF we can decompose the smoothing into three stages and report the per-reading absolute delta each stage applies. The cohort-median |Δ| in mg/dL is:

| Stage | Median |Δ| (mg/dL) | Frac. of readings changed |
|---|---:|---:|
| Predict (sigma-point time-update) | 2.4 | 100 % |
| Update (Kalman correction towards measurement) | 1.6 | 100 % |
| RTS (backward smoother) | 0.0 | 0.01 % |

*Figure 3. Per-step modification stack: median absolute Δ (mg/dL) at each pipeline stage, broken out by smoother. For AAPS Average and AAPS Exponential the only stage is the filter; for the UKF the stack is predict / update / RTS. RTS contributes essentially zero at the leading edge.*

The RTS stage applies effectively zero modification at the leading edge — confirming the algorithmic property: RTS revises older positions in the window but leaves the newest state at its forward value. This is *correct* for a real-time AID — it would be incoherent for the smoother to retroactively rewrite the value the dose engine already used. But it does mean that the RTS code path, which is the most computationally distinctive part of the implementation, contributes nothing to the current-reading output. Its effect is on the historical curve the AID can look back at.

For AAPS Exponential the leading-edge filter applies a median |Δ| of 1.0 mg/dL and changes 78 % of readings (cohort medians).

### 3.3.1 Event-aligned visual comparison

To visualise smoother behaviour against real data, we event-aligned the cohort traces around two reference event types: low-variance "calm" 60-minute windows (raw glucose in [70, 180] mg/dL with |raw rate| < 0.3 mg/dL/min sustained for the full window), and "rate-rise" events (raw rate ≥ 0.5 mg/dL/min sustained for ≥ 15 minutes). The deviation figures below have three panels — (smoothed − raw) glucose, (smoothed − raw) first derivative, and (smoothed − raw) second derivative — but only the glucose panel is described in the captions for brevity.

*Figure 4. Calm-window deviation envelopes: median (line) and IQR (shaded) of (smoothed − raw) across calm windows for each smoother. AAPS Average's envelope sits exactly on zero (no leading-edge effect). AAPS Exponential and the UKF show small bounded deviations during quiet periods.*

*Figure 5. Rate-rise event-aligned deviation envelopes: median (line) and IQR (shaded) of (smoothed − raw) across rate-rise events. The negative shift during the rise reflects the smoother's lag — it tracks below the rising raw — and is visibly smaller for the UKF than AAPS Exponential.*

### 3.4 Per-user phenotypes

We pivoted the per-user metrics into one row per user with a column for each (metric × algorithm) pair (so each user is described by their full multi-smoother profile), z-scored the columns, and ran KMeans clustering for k ∈ {2, 3, 4}. The best silhouette across k was 0.30, below the 0.4 threshold typically used to declare meaningful cluster structure. We therefore did not assign users to phenotypes for downstream analysis; the smoother ranking is consistent across the cohort and we do not recommend a regime-dependent smoother choice.

### 3.5 SID (Sensor Integrity Detection) re-detection

SID v6 is a research-grade Sensor Integrity Detection layer maintained separately from AAPS and oref0 (it is not part of either production stack). The logic looks for sustained glucose-deviation events that suggest a sensor problem (compression, calibration drift). We ran SID against each smoother's output to count surviving events. The headline numbers across the cohort are:

* AAPS Average: 4 948 SID events (bias to flag, since the leading edge is raw and noisy)
* AAPS Exponential: 1 259 events (75 % fewer than AAPS Average)
* UKF: 2 416 events (51 % fewer than AAPS Average)

*Figure 6. Per-user SID-survival vs noise reduction for each smoother. Lower-left is fewer SID events at greater noise reduction. AAPS Exponential lies lowest on event count, the UKF in between, AAPS Average highest (the noisy upper-right corner).*

This ordering is the opposite of what the noise-reduction metrics alone would suggest. AAPS Exponential reduces the SID event count more than the UKF does, despite reducing high-frequency noise less aggressively than the UKF on standard cohort metrics. The mechanism is the phase shift: SID looks for *sustained* deviations, and AAPS Exponential's larger phase delay (≈ 1.7 min vs the UKF's ≈ 0.85 min) causes the smoothed series to track the raw more loosely during real glucose excursions, which suppresses the cumulative deviation that triggers SID. The UKF, with its sharper response to genuine excursions, more often produces deviations that meet SID's amplitude/duration thresholds. Whether the AAPS Exponential reduction is a benefit (fewer false-positive sensor flags) or a harm (more missed real sensor problems) depends on a ground-truth label set we do not have. We record that the smoother choice changes how often the SID layer fires, without claiming a clinical interpretation in either direction.

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

We are not arguing that one stance is inherently better. The smoothed values committed to past readings still affect the AID's slope calculations and historical view of the curve; the only operational claim about AAPS Average is that the value at t itself is not modified.

### 4.2 The UKF Pareto position

For 18 of the 19 AAPS Exponential operating points reached in this cohort there is a UKF user with at least as much noise reduction at smaller phase-delay magnitude; on a per-user paired comparison this paired dominance holds for 14 of 19 users. The UKF therefore lies on the lower-delay side of the AAPS Exponential operating range, but the dominance is not strict per-user. This is consistent with the UKF being a more parameterised estimator that uses both the measurement and a model of glucose dynamics; the trade-off it accepts is computational cost (roughly 250 × more wall-clock per reading than AAPS Exponential in our Python implementation, though absolute cost is still small — < 100 µs per reading on a modern CPU) and substantially more parameter complexity (covariance bounds, χ² thresholds, R-adaptation rates). The development-branch UKF parameterisation we used has been tuned by the AAPS development team against a separate dataset; whether the same parameterisation would remain on the lower-delay side after a re-tune for this cohort is a question for future work.

### 4.3 What "online" means for outlier absorption

The UKF absorbs 38 % of raw outliers at the leading edge — meaning its current-reading output for those readings is at least 50 % closer to the local trend than the raw spike value. AAPS Exponential absorbs 30 %; AAPS Average absorbs 0 % at the leading edge by construction (raw passes through). For an AID, absorbed outliers are absorbed *before* the dose engine sees them, so they cannot trigger a single-reading dose. AAPS Exponential and the UKF therefore offer real protection against single-reading artefacts; AAPS Average does not at the operational moment, although it does prevent such artefacts from biasing the next slope calculation once the next reading arrives. Slope dampening matters because oref0's safety net (`exercise mode`, `temptarget`, etc.) ramps off fairly quickly when slope estimates decay; a single-reading 50 mg/dL spike can briefly lift the slope estimate above safety thresholds even if the dose engine handles the absolute value sensibly.

### 4.4 Limitations

* The cohort is 19 users, not a clinical trial. We did not have ground-truth glucose values (no blood-glucose comparator), so noise/delay statistics are purely intrinsic-to-the-stream.
* We evaluated only the *intrinsic* effect of each smoother on its own output. We did not run the full oref0 dose engine downstream of each smoother; we cannot say whether the SID-event-count reductions under the adaptive smoothers (≈ 75 % under AAPS Exponential, ≈ 51 % under the UKF, both relative to AAPS Average) would translate to fewer or more clinical hypo/hyper events under closed-loop control. SID v6 is itself a research-grade detection layer maintained separately from production AAPS / oref0.
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

In a production-realistic online sliding-window evaluation across 19 users and up to 90 days each, the three CGM smoothers used by oref0-derived AID systems show distinct operating points. AAPS Average is a no-op for the current reading at decision time and exists primarily to clean the historical view of the curve and dampen the slope into t. AAPS Exponential reduces step-to-step noise by 16 % at a cost of a 1.7-minute phase shift and 7.1 percentage points of < 70 mg/dL events not preserved at the leading edge. The adaptive UKF reduces noise by 18 % at half the phase shift (0.85 min), with 3.4 percentage points of hypo events not preserved (lower than AAPS Exponential; AAPS Average preserves all events because it does not smooth the leading edge). On a per-user paired noise-versus-phase comparison the UKF lies on the lower-delay side of AAPS Exponential, with at least as much noise reduction at smaller phase delay than AAPS Exponential for 14 of 19 users; for the remaining 5 users AAPS Exponential has marginally lower noise ratio. The UKF's RTS backward smoother contributes effectively nothing to the current-reading value (RTS does not modify the leading edge by construction); its effect is on the historical curve, not the dose-engine input. The choice of smoother for an AID design is a choice about how much delay to accept on the current reading in exchange for fewer single-reading dose perturbations; this paper does not argue that one smoother is clinically better than another.
