# A step-by-step characterisation of four CGM smoothing algorithms used in open-source AID systems, including the new Adaptive Unscented Kalman Filter

*Phase 3 backtest of the SID evaluation programme, with full per-step instrumentation, fixture-based parity verification against Kotlin reference, event-aligned deviation analysis on calm vs. meal-rise vs. sustained-rise periods, and a real-meal sub-study using Nightscout-tagged meal events from a live AID user. Companion to the technical paper (`SID_Smoother_Backtest_with_UKF.docx`), the per-sensor paper (`SID_Per_Sensor_G6_vs_G7.docx`), and the upload-path effects study (`SID_Upload_Path_Effects.docx`).*

---

## Abstract

**Background.** Open-source automated insulin-delivery (AID) systems built on the **oref0** algorithm — AndroidAPS, Trio, and earlier OpenAPS — depend on a smoothed CGM signal as the input to every dose decision. Three smoothing algorithms have been in operational use for several years (AAPS Average, AAPS Exponential / TSUNAMI, Trio Savitzky-Golay). A fourth, an **Adaptive Unscented Kalman Filter** (UKF) with chi-squared-based outlier rejection and a backward Rauch-Tung-Striebel smoothing pass, has recently been added to AndroidAPS. The next generation of AID logic increasingly uses **delta acceleration** — the second derivative of glucose — as a dosing input, either explicitly (in proposed unannounced-meal detection upgrades) or implicitly (through dynamic-ISF derivatives that compare short-window and long-window deltas). The behaviour of each smoother on the acceleration signal is therefore directly load-bearing for AID dose decisions.

**Methods.** We instrumented every internal step of all four smoothers in Python, validated each port against a reference implementation (Kotlin standalone driver for the UKF, with tolerances ≤0.5 mg/dL output, ≤1e-3 covariance, ≤1e-2 χ²/innovation, and exact outlier-flag match; bit-exact comparison against the published Python ports for the other three), and ran the full pipeline on 19 anonymised users from a local TimescaleDB extract of oref data (~14,000 reading-days). Every reading produced one row in a per-(user, smoother) trace table. We computed cohort-level metrics (noise reduction, three independent effective-delay estimators, real-movement preservation, hypo and peak event preservation, single-step outlier absorption, and peak event acceleration retention) and then constructed **event-aligned deviation visualisations** that show how each smoother's smoothed glucose, smoothed delta, and smoothed acceleration deviate from raw at every minute relative to a common event point. Three event types are studied: **calm periods** (60+ minutes of raw glucose 70–180 mg/dL with rate <0.3 mg/dL/min; 158 windows), **sustained rate-rise events** (raw rate ≥0.5 mg/dL/min for ≥15 minutes; 380 events), and **meal-tagged Nightscout treatments** from a live AID user's site (45 meal events labelled "meal" or "fast carbs" since 2026-01-01). SID v6 was re-run on each smoother's output to test cluster reduction and surviving cluster severity.

**Results.** During calm periods, all four smoothers track raw glucose to within ≤0.33 mg/dL of median deviation; AAPS Exponential and Trio Savitzky-Golay deviate by exactly 0.00 mg/dL on the median. The differences between smoothers become measurable only during glucose movement. Across both the synthetic rate-rise events and the real meal events, **AAPS Exponential's smoothed glucose runs up to 4 mg/dL above raw at meal-rise peaks**, with smoothed delta running 0.4–0.5 mg/dL/min above raw — the dual-EMA blend functions as a low-effort extrapolator that anticipates where the curve is heading. Trio Savitzky-Golay and the UKF are the most aggressive smoothers (median noise reduction ratio 0.45 and 0.63 respectively) and absorb the largest fraction of single-step ≥40 mg/dL outliers. On peak event acceleration retention — how much of the *bend* at a meal-rise onset survives the smoother, the metric that matters most for AID logic that uses delta acceleration as a dosing input — AAPS Exponential preserves the most curvature (50%), AAPS Average is second (42%), and Trio SG and the UKF are tied at the bottom (33% and 32%). On the live Nightscout cohort of 45 meal events: AAPS Exp's median time to cross the 1 mg/dL/min rate threshold is 5 minutes later than the other three smoothers (25 vs 20 minutes after the meal note) but its peak rate during the rise is the highest (3.4 mg/dL/min vs 2.55–2.73 for the others) — a 91% retention of raw's peak rate vs 75–87% for the others.

**Conclusions.** The four smoothers are essentially indistinguishable during calm glucose periods; the differences between them appear only during meal rises, hypo descents, and post-meal recoveries. The two more-aggressive smoothers (Trio Savitzky-Golay, UKF) eliminate the most SID-flagged structural noise and absorb the most outliers, at the cost of the worst peak event curvature retention. AAPS Exponential is the strongest curvature preserver despite peaking ~1.7 minutes ahead of raw, and is therefore the strongest pick for any AID logic that uses delta acceleration as a dosing input — provided that downstream thresholds are calibrated for its mild over-amplification of real meal-rise and hypo-descent magnitudes. AAPS Average is the gentlest noise remover and the second-best curvature preserver, with the cleanest peak-time alignment.

---

## 1. Introduction

A continuous glucose monitor (CGM) reports an interstitial glucose value approximately every five minutes, sitting on top of a measurement-noise floor of roughly four to eight milligrams per decilitre. Some of that noise is inherent to electrochemical sensing of interstitial fluid; some is short-lived sensor weirdness — pressure compression, hydration shifts, the early-life jumps familiar to anyone in the first 24 hours of a new sensor session. Because a closed-loop AID system reacts to changes in glucose at five-minute intervals, the way each loop chooses to filter that noise has direct consequences for how often it doses, how aggressively, and how reliably it avoids being misled by sensor artefacts.

The **oref0** algorithm — the open-source closed-loop algorithm that powers AndroidAPS (AAPS), Trio, and earlier OpenAPS implementations — does not consume raw CGM directly. It reads three smoothed glucose-derivative numbers as inputs to its dose computation: **delta** (the change from the most recent reading versus 5 minutes ago), **short_avgdelta** (average rate over the last ~15 minutes), and **long_avgdelta** (average rate over the last ~45 minutes). These three numbers feed the predicted glucose curves (`predBGs`), drive the decision of whether a Super Micro Bolus (SMB) fires, modulate temp-basal magnitude, and, in **Unannounced Meals (UAM)** mode, trigger the loop to correct for an unbolused meal.

Three smoothers have been in operational use across the open-source AID world for several years. **AAPS Average** is the AndroidAPS default — it averages each reading with the one before and the one after when the timestamps line up. **AAPS Exponential**, also known as **TSUNAMI**, is a more elaborate dual-EMA blend that AndroidAPS introduced to track real glucose excursions a bit faster. **Trio Savitzky-Golay** is Trio's three-pass polynomial-fit smoother, a standard signal-processing tool repurposed for glucose. A fourth smoother has recently been added to AndroidAPS: an **Adaptive Unscented Kalman Filter** (UKF) — about 1,300 lines of Kotlin — with adaptive measurement-noise estimation, chi-squared-based outlier rejection, and a backward Rauch-Tung-Striebel smoothing pass that revisits past estimates as new data arrives.

The obvious comparative question — *is the UKF actually better?* — is the question this paper answers. A second question, sharper and increasingly relevant, follows on from it: *does the smoother preserve the parts of the signal that next-generation AID logic depends on?* The answer matters because the next direction of work in this space — what we will call **acceleration-aware AID** — increasingly looks not just at the rate of change but at how the rate of change is itself changing. A meal that is about to spike shows up in acceleration first, before the rate becomes large enough to cross any threshold. A hypoglycaemic descent starts as a flat-to-slightly-falling line that suddenly bends downward, and the bend (acceleration) is what tells UAM-style logic to correct early. Modern dynamic-ISF derivatives in both Trio and AAPS, recent SMB-on-rise logic, and several proposed UAM upgrades all rely either explicitly or implicitly on this second derivative; the comparison of `short_avgdelta` against `long_avgdelta` is mathematically a discrete second derivative.

So when a new smoother is evaluated for AID use, removing noise without lag is necessary but no longer sufficient — what also needs to be preserved is the curvature signal at meal-rise and hypo-descent inflections that the dose engine is going to read. This paper presents a step-by-step backtest in which all four smoothers were instrumented to log every internal step on a 19-user cohort, plus three event-aligned visualisations that show how each smoother's deviation from raw evolves minute-by-minute during calm periods, during sustained rate-rise events, and during real meal events tagged in a live Nightscout site. Every claim made here is reproducible from the released code; every numerical headline traces to a CSV in the released `reports/` tree.

A note on terminology. This paper occasionally uses "frequency" or "acceleration" or "curvature" to describe glucose dynamics — these come from the engineering vocabulary used to compute the metrics, not from how glucose physically behaves (it does not actually oscillate; meal rises happen once). Where possible the Results and Discussion describe findings in glucose-event terms — meal rises, hypo descents, peak alignment — rather than in pure signal-processing language. The Methods section retains the engineering terms because they are what makes the metrics computable.

## 2. Methods

### 2.1 Cohort and data

**Phase 3 cohort.** Nineteen users were selected from three TimescaleDB tables (`oref_v5`, `oref_v6`, `oref_v7`) containing disjoint user populations across approximately 183 distinct subjects. Selection was codified in `cohort.py`: each user required at least 90 days of total span, a 5-minute modal sampling cadence, and (at the cohort filter level) a high enough total-data ratio. Each user's series was trimmed to the first 90 days from their first available reading and resampled to a strict 5-minute grid; gaps greater than five minutes were marked as missing rather than interpolated.

**Phase 2 sub-cohort.** A separate sub-cohort drew on the original Nightscout source data underlying the prior multi-user paper, providing per-entry sensor labels for the per-sensor sub-analysis (covered in the companion paper `SID_Per_Sensor_G6_vs_G7.docx`).

**nstest3 live Nightscout cohort.** For the real-meal sub-study, we pulled all treatments and CGM entries from `https://nstest3.crabdance.com` (a live AID user's Nightscout instance) covering 2026-01-01 through the present. The fetcher walks the Nightscout REST API in date-descending pages, filtering treatments to those whose notes contain the strings "meal" or "fast carbs" (case-insensitive). The resulting sub-cohort has 45 meal-tagged treatments and 33,683 CGM entries.

### 2.2 Smoother instrumentation

Each smoother was reimplemented in Python so that every intermediate value computed during a smoothing step could be recorded. For AAPS Average and Exponential this meant logging the window of recent readings the algorithm sees and, for Exponential, the partial EMAs it computes. For Trio Savitzky-Golay it meant emitting one trace row per pass, with the input glucose, the post-filter pre-clamp value, and the post-clamp output for each of the three sequential filter passes. For the UKF it meant emitting per-reading values for the predicted state (glucose and rate), the innovation (measured value minus prediction), the chi-squared test statistic, the adaptive measurement-noise variance R, the Kalman gain, the post-update state, and a flag indicating whether the backward Rauch-Tung-Striebel smoother modified the final output. Every reading produced one row in a per-user, per-smoother trace table; with 19 users and four smoothers this yielded 76 trace files totalling approximately 100 MB of zstd-compressed Parquet data.

### 2.3 Parity verification

Each smoother is checked against an independent reference on three fixture inputs: a synthetic step probe, a noisy sinusoid, and a real 24-hour cohort slice. The UKF Python port is validated against a standalone Kotlin compile of `UnscentedKalmanFilterPlugin.kt` with AAPS framework dependencies stripped (logger, persistence layer, RxBus, sensor-event listener); the algorithmic core is preserved verbatim. Tolerances are 0.5 mg/dL on output, 1e-3 on covariance entries, 1e-2 on chi-squared and innovation, and exact match on the outlier flag. The other three smoothers are validated bit-for-bit against the published Python ports in `multi_user/full_analysis.py`, supplemented by a sympy-derived first-principles check on the AAPS Average rule for the first ten readings of the step fixture, and a cross-check of Trio Savitzky-Golay's first pass against a direct invocation of `scipy.signal.savgol_filter` over the central interior of the window. For Trio Savitzky-Golay, end-to-end comparisons (raw vs final smoothed) take pass-1's input as the raw reference and pass-3's output as the final smoothed value, so the metrics describe the cumulative three-pass effect rather than any single pass's marginal contribution. All 15 parity tests pass.

### 2.4 Cohort-level metrics

Six families of metrics are computed per (user, smoother) and reported as cohort medians. The metric definitions below use signal-processing language for precision; the Results section translates the numbers into glucose-event terms.

**Effective delay** is estimated three ways. The cross-correlation lag finds the time shift that best aligns the smoothed series with the raw series across the entire 90-day window. The step-response delay isolates real rate-change events (raw rate ≥ 0.5 mg/dL/min sustained for ≥ 15 minutes — typically meal rises and hypo descents) and measures how long the smoothed signal takes to register the same event using a relaxed crossing threshold. The third estimator works on glucose movements that take 1 to 6 hours to complete (the duration range that contains meal rises, post-meal recoveries, hypo descents, hypo recoveries, and overnight drift) and measures whether the smoothed peak arrives earlier or later than the raw peak during such events; we report the result in minutes.

**Noise reduction ratio** is the variance of step-to-step differences in the smoothed series divided by the same quantity for raw. Lower means more aggressive smoothing.

**Real-movement preservation** measures, for events lasting 1 to 6 hours, whether the smoother passes the real glucose movement through unchanged (factor = 1.00), dampens it (< 1.00), or amplifies it (> 1.00).

**Hypo and peak event preservation** measures the fraction of raw nadirs below 70 mg/dL and peaks above 180 mg/dL that the smoother retains, with the amplitude shift and time-to-extremum shift recorded for each preserved event.

**Outlier behaviour** is reported in two parts: the raw spike count per 1000 readings (a sensor-quality metric, measuring how often the sensor produces single-step changes ≥ 40 mg/dL) and the smoother's absorption percentage of those spikes (a smoother-behaviour metric, measuring how many of the produced spikes the smoother dampens to under-threshold in its output).

**Peak event acceleration retention** measures how much of the *bend* at a meal-rise onset or a hypo-descent inflection survives the smoother — formally, the median ratio of smoothed-to-raw peak acceleration during sustained rate-change events, where acceleration is the second discrete derivative of glucose. This is the metric that matters most for any AID logic that uses delta acceleration as a dosing input.

In addition, **SID v6** was re-run on each smoother's output for each user, producing per-(user, smoother) cluster counts, surviving cluster severity statistics, and a per-user Random Forest classifier trained to predict, from the features of each raw cluster, whether that cluster would survive each smoother.

### 2.5 Event-aligned deviation analysis

The cohort-level metrics summarise each smoother's behaviour across an entire 90-day window. To see *when* and *how* the smoothers actually diverge from raw, we constructed event-aligned deviation visualisations — three-panel plots showing the median deviation (smoothed minus raw) for glucose, delta, and acceleration at every minute relative to a common event point. Three event types are studied:

**Calm periods.** A calm window is defined as 60+ contiguous minutes where raw glucose stays in the 70–180 mg/dL range and `|raw rate|` stays below 0.3 mg/dL/min throughout. We collected up to 10 such windows per user, yielding 158 calm events. Each event is aligned at the start of the calm region; the deviation profile is plotted from −30 minutes (pre-buffer for smoother warmup) to +60 minutes.

**Sustained rate-rise events.** A rate-rise event starts at the first sample where raw rate ≥ 0.5 mg/dL/min and the next two samples also have rate ≥ 0.5 mg/dL/min (i.e., rate stays above threshold for at least 15 minutes). We collected up to 20 events per user, yielding 380 events. Each event is aligned at the start of the sustained rise; the deviation profile is plotted from −30 minutes through +60 minutes.

**Meal-tagged Nightscout treatments.** All 45 treatments tagged "meal" or "fast carbs" in the nstest3 dataset since 2026-01-01. Each is aligned at the meal note's timestamp; the deviation profile is plotted from −60 minutes through +180 minutes.

For each event, we compute Δsgv(t) = smoothed(t) − raw(t), Δdelta(t) = smoothed_rate(t) − raw_rate(t), and Δaccel(t) = smoothed_accel(t) − raw_accel(t) at every grid point. Across all events of a given type, we report the median and the inter-quartile range (25th–75th percentile band) at each time offset.

### 2.6 Real meal-event analysis on nstest3

For each of the 45 meal-tagged treatments, we slice CGM ±60 / +180 minutes around the meal time, resample to a strict 5-minute grid, and run all four smoothers on the windowed raw glucose. For each smoother and for raw, we then compute decision-relevant quantities at every post-event sample: `delta` (5-minute change), `short_avgdelta` (15-minute average rate), `long_avgdelta` (45-minute average rate), and acceleration. We record per smoother the time at which `delta` first crosses 1 mg/dL/min after the meal note (a proxy for SMB-on-rise being eligible), the time at which acceleration first crosses +0.2 mg/dL/min² (a proxy for UAM-style acceleration logic detecting the rise), the peak `delta` and acceleration during the post-event window, and the retention of each smoother's peak relative to raw's peak.

## 3. Results

### 3.1 Where each smoother actually does its work

The per-step instrumentation makes it possible to attribute each smoother's behaviour to specific stages. The median absolute change per reading at each internal stage, aggregated across all 19 users, is shown in Table 1.

**Table 1.** Median absolute change (mg/dL) per internal step, aggregated across users.

| smoother | step | median \|Δ\| reading (mg/dL) |
|---|---|---|
| AAPS Average | filter | 0.67 |
| AAPS Exponential | filter | 1.00 |
| Trio Savitzky-Golay | pass 1 | 1.00 |
| Trio Savitzky-Golay | pass 2 | 0.00 |
| Trio Savitzky-Golay | pass 3 | 0.00 |
| Adaptive UKF | predict | 2.34 |
| Adaptive UKF | update (routine) | 1.60 |
| Adaptive UKF | update (chi-squared outlier) | 23.69 |
| Adaptive UKF | RTS backward smooth | 0.84 |

Two findings stand out. First, **the second and third passes of Trio Savitzky-Golay change essentially nothing on the typical reading** (median |Δ| of 0.00 mg/dL on each). All useful smoothing happens in pass one; passes 2 and 3 are computationally redundant on top of pass 1 for the typical sample. The cumulative three-pass effect across the full series is nevertheless substantial because of how pass 1 absorbs outliers and bends.

Second, **the UKF's smoothing is bimodal**. Routine readings are touched by a median 1.6 mg/dL on the update step — broadly comparable to AAPS Average's 0.67 mg/dL or AAPS Exponential's 1.0 mg/dL. Chi-squared-flagged outlier readings are touched by a median 23.7 mg/dL on the same step — an order of magnitude difference. **No other smoother exhibits this bimodality.** AAPS Average, Exponential, and Trio Savitzky-Golay all apply a roughly uniform smoothing operation to every reading. The UKF applies almost no smoothing on most readings and very strong smoothing on a few.

### 3.2 Effective delay and noise removal

The headline tradeoff for any smoother is between removing noise and adding lag. Table 2 reports all three delay estimators side by side, plus the noise reduction ratio and the real-movement preservation factor.

**Table 2.** Effective delay and noise removal across the 19-user cohort (medians).

| smoother | overall lag (min) | step-event lag (min) | peak-time offset on 1–6 h events (min) | noise ratio | real-movement preservation |
|---|---|---|---|---|---|
| AAPS Average | 0.01 | −2.08 | −0.01 | 0.71 | 0.98 |
| AAPS Exponential | 1.94 | 0.31 | **−1.71** (peaks early) | 0.84 | **1.03** (amplifies) |
| Trio Savitzky-Golay | 0.00 | −2.29 | 0.00 | **0.45** | 0.99 |
| Adaptive UKF | 0.08 | −1.69 | −0.02 | 0.63 | 0.99 |

Two findings stand out. First, **AAPS Exponential / TSUNAMI's smoothed peaks arrive approximately 1.7 minutes BEFORE the raw peaks** during 1- to 6-hour glucose events (which is the duration range covering meal rises, post-meal recoveries, hypo descents and recoveries). The smoother also produces peaks that are about 3% taller than the raw peaks during such events. The dual-EMA blend in TSUNAMI carries a forward-looking trend term that pulls the current value past the raw value when the curve is rising or falling steadily. The other three smoothers' peak times track the raw peak times to within ±1 second on the same events, and their peak heights match the raw peak heights to within 1–2%.

Second, **Trio Savitzky-Golay is the most aggressive noise-reducer of the four**, with a median noise ratio of 0.45 — it removes 55% of the step-to-step jitter present in the raw series. The UKF is second (0.63), AAPS Average third (0.71), AAPS Exponential gentlest (0.84).

### 3.3 What each smoother does in calm periods

The cohort-level numbers above are summary statistics across an entire 90-day window per user. They average over both the boring stretches of stable glucose and the dramatic stretches of meal rises and hypo descents. To see what each smoother actually does *during* calm periods, Figure 1 shows the median smoother-vs-raw deviation at every minute relative to the start of 158 calm windows drawn from the Phase 3 cohort (raw glucose stable in 70–180 mg/dL with rate < 0.3 mg/dL/min for ≥ 60 minutes).

*Figure 1. Calm-period aligned deviations across 158 calm windows. Three panels (top to bottom): Δ glucose, Δ delta, Δ acceleration. Lines are the median deviation for each smoother at each minute; bands are the 25th–75th percentile spread.*

The headline of the calm-period view is that **the four smoothers are essentially indistinguishable from raw and from each other during stable glucose**. Peak median |Δ glucose| during the entire calm window is 0.33 mg/dL (AAPS Average, UKF) or 0.00 mg/dL (AAPS Exponential, Trio Savitzky-Golay). Peak median |Δ delta| is at most 0.086 mg/dL/min. Peak median |Δ acceleration| is at most 0.028 mg/dL/min². For comparison, the raw glucose itself wobbles by ±2–4 mg/dL within a typical calm window from sensor noise alone — more than ten times larger than any smoother's deviation from it.

The two smoothers that *do* leave a small residual deviation during calm periods are AAPS Average and the UKF. AAPS Average's residual comes from its unconditional 3-point central averaging — even on flat data, the running average produces fractional-mg/dL drifts that don't quite match the raw integer-mg/dL values. The UKF's residual comes from its persistent state estimator: even when nothing exciting happens, the predict-and-update loop is constantly nudging the state estimate by small amounts, and the RTS backward smoother adds a further per-sample nudge. AAPS Exponential and Trio Savitzky-Golay are stateless filters that only do work on locally-changing data; on a stable signal they emit exactly the raw value.

Figure 2 makes the same point visually with six representative calm windows from six different users, showing all four smoothers overlaid on the raw glucose with the y-axis zoomed to ±12 mg/dL around the median.

*Figure 2. Six example calm windows from six different users. Each panel: raw (black) plus the four smoothers (coloured), zoomed to ±12 mg/dL around the median. The smoother lines overlap raw closely; the differences between smoothers are smaller than the sensor-noise envelope of the raw signal itself.*

### 3.4 What each smoother does during glucose movement

The differences between smoothers become measurable only when glucose starts moving. Figure 3 shows the same three-panel deviation view but for 380 sustained rate-rise events from the Phase 3 cohort (raw rate ≥ 0.5 mg/dL/min for ≥ 15 minutes), aligned at the first sample of each event.

*Figure 3. Sustained rate-rise events (n=380), aligned at event start. Three panels: Δ glucose, Δ delta, Δ acceleration. The smoother differences that were invisible during calm become large during rises.*

Three patterns are visible:

**1. AAPS Exponential's deviation pattern is qualitatively different from the other three.** Its smoothed glucose runs up to 4 mg/dL above raw at peak rise, and its delta runs +0.47 mg/dL/min above raw delta. This is the anticipator behaviour from §3.2 made visual: AAPS Exp doesn't lag the raw signal during the rise — it leads it, reporting "where the curve is heading" rather than "where the sensor currently is".

**2. The aggressive smoothers (Trio SG, UKF) deviate ~2 mg/dL on glucose during the rise.** Their deviation has the opposite sign to AAPS Exp's: smoothed glucose runs *below* raw during the rise (the smoother is dampening). On acceleration, both deviate by ~0.10–0.12 mg/dL/min² from raw — an order of magnitude more than during calm periods.

**3. AAPS Average is closest to raw on glucose and rate** (peak deviations 1.3 mg/dL and 0.23 mg/dL/min) but shows the second-highest acceleration deviation because its 3-point central window noticeably bends the second derivative.

Figure 4 shows the same view for 44 real meal events from the live nstest3 Nightscout site, aligned at each meal note's timestamp.

*Figure 4. Meal-tagged events from the nstest3 Nightscout site (n=44), aligned at each meal note's timestamp. Window: −60 to +180 minutes. The pattern matches the synthetic rate-rise events in Figure 3, confirming the smoother behaviours hold in live AID use.*

The meal-event pattern matches the synthetic rate-rise pattern: AAPS Exp peaks 4 mg/dL above raw in glucose; UKF and Trio SG damp glucose by ~1 mg/dL during the rise; AAPS Average stays closest to raw. The minute-by-minute alignment makes clear that the AAPS Exp deviation is concentrated in the 30-to-60-minute post-meal window, exactly when the rise is steepest and the dose engine is most actively making decisions.

### 3.5 Real meal events from a live Nightscout site

Beyond the deviation curves, the 45 meal events also let us measure *when* each smoother would actually trigger oref0's decision-relevant thresholds during a real meal. Table 3 reports the median timing across the cohort.

**Table 3.** Median timing to threshold during real meal events on nstest3 (n=45 meals).

| smoother | median time to delta ≥ 1 mg/dL/min (min) | median time to accel ≥ 0.2 mg/dL/min² (min) | median peak delta (mg/dL/min) | peak retention vs raw |
|---|---|---|---|---|
| AAPS Average | 20 | 22.5 | 2.73 | 87% |
| **AAPS Exponential** | **25** | 20 | **3.40** | **91%** |
| Trio Savitzky-Golay | 20 | 20 | 2.60 | 77% |
| Adaptive UKF | 20 | 20 | 2.55 | 75% |

Two findings stand out. First, **AAPS Exp's median time to cross the SMB-on-rise threshold is 5 minutes later than the other three smoothers** (25 minutes after the meal note vs 20 minutes for AAPS Avg, Trio SG, UKF). The dual-EMA window is wide enough that the rate signal takes longer to cross 1 mg/dL/min, even though the eventual peak rate it reports is higher. So AAPS Exp delivers a stronger signal once it triggers, but it triggers later than the alternatives.

Second, **AAPS Exp's peak rate retention vs raw is highest at 91%** — its peak delta during meal rises is a median 3.4 mg/dL/min compared to raw's median 3.7 mg/dL/min. UKF and Trio SG peaks are 75–77% of raw's peak. AAPS Average sits in the middle at 87%. For UAM-style logic that compares peak rate against a calibrated threshold, this means **the same threshold will trigger UAM ~17 percentage points more often on AAPS Exp than on UKF or Trio SG, holding the underlying physiology constant**. Per-(smoother) calibration of acceleration thresholds is therefore demonstrably necessary.

Out of 45 meal events, 41 had AAPS Average and the UKF cross the SMB-on-rise threshold within 60 minutes of the meal note; 42 had AAPS Exp and Trio SG cross. The four smoothers agree on whether a meal triggered the threshold in roughly 90% of cases — but they disagree on *when* by 5 minutes on the median, and on *peak intensity* by 17 percentage points.

### 3.6 SID re-detection

Sensor Integrity Detection v6 was re-run on each smoother's output. SID is an independent algorithm that flags clusters of physiologically implausible CGM readings — sudden directional reversals that suggest sensor compression or hardware noise. It serves as a useful independent yardstick for whether each smoother actually cleaned up structural noise or merely dampened everything equally. Results in Table 4.

**Table 4.** SID v6 results per smoother across the 19-user cohort.

| smoother | total clusters left | median reduction vs raw | median surviving amplitude (mg/dL) | RF cluster-survival F1 |
|---|---|---|---|---|
| AAPS Average | 1,505 | 69.3% | 34 | 0.55 |
| AAPS Exponential | 1,711 | 69.6% | 33 | 0.51 |
| Trio Savitzky-Golay | 264 | **94.6%** | 40 | **0.23** |
| Adaptive UKF | 354 | **93.1%** | 36 | **0.19** |

The UKF and Trio Savitzky-Golay are in a different category from the two AAPS smoothers on cluster reduction, eliminating over 93% of what SID flags on raw data versus the AAPS smoothers' approximately 70%. The Random Forest cluster-survival F1 collapses to ~0.2 for Trio SG and the UKF (survival is essentially random) and stays around 0.5 for the AAPS smoothers (survival is predictable from cluster features), implying that aggressive smoothing leaves a sparse, individually-severe survivor population that defies prediction.

### 3.7 Acceleration retention

The metric that matters most for acceleration-aware AID is **peak event acceleration retention** — how much of the raw curvature peak survives the smoother during sustained rate-change events.

**Table 5.** Acceleration retention across the 19-user cohort.

| smoother | total acceleration retained | acceleration retained on 1–6 h events | peak event acceleration retention |
|---|---|---|---|
| AAPS Average | 50% | 93% | 42% |
| AAPS Exponential | 53% | **115%** (amplified) | **50%** |
| Trio Savitzky-Golay | 80% | 98% | 33% |
| Adaptive UKF | **35%** | 94% | 32% |

This table is the one that should govern any AID-design conversation about smoother choice for acceleration-aware logic.

The first column is dominated by step-to-step noise. The UKF removes 65% of total acceleration content, almost all of which is noise that has no business influencing dose decisions. The second column measures how much of the *real* acceleration during meal-rise-and-recovery-scale events the smoother preserves; AAPS Average, Trio Savitzky-Golay, and the UKF all keep approximately 93–98%. AAPS Exponential's 115% confirms what the peak-time offset already showed: it amplifies real glucose dynamics on this timescale.

The peak event retention is what matters for AID. **AAPS Exponential preserves the most curvature at the bend** (50%), AAPS Average is second (42%), and **Trio Savitzky-Golay and the UKF are tied at the bottom (33% and 32%)**. The smoother that preserves the most curvature at meal-rise inflections and hypo-descent inflections is therefore AAPS Exponential, despite (or arguably because of) its anticipation behaviour during those events.

## 4. Discussion

### 4.1 The two regimes: calm versus moving

The most useful framing that emerges from this study is that there are **two regimes** in which a CGM smoother operates, and the four smoothers behave very differently in each.

**Regime 1: calm glucose.** Stable glucose between 70 and 180 mg/dL with `|rate| < 0.3 mg/dL/min`. The four smoothers are essentially indistinguishable here. Median deviations from raw are 0–0.33 mg/dL on glucose, 0–0.09 mg/dL/min on rate, 0–0.03 mg/dL/min² on acceleration — all an order of magnitude smaller than the underlying sensor noise envelope. AAPS Exponential and Trio Savitzky-Golay collapse to exactly 0 mg/dL median deviation; AAPS Average and the UKF leave a small residual because they are always-on filters. From an AID dose-decision standpoint, **smoother choice is irrelevant during calm periods** — the dose engine sees essentially the same signal regardless of which smoother is upstream.

**Regime 2: glucose moving.** Meal rises, hypo descents, post-meal recoveries. This is where the smoother differences appear and where smoother choice matters. The aggressive smoothers (Trio SG, UKF) damp glucose by 1–2 mg/dL during the rise and absorb 30–60% of real curvature. AAPS Exponential goes the other way — it amplifies the rise by 4 mg/dL and 0.4–0.5 mg/dL/min, peaking 1.7 minutes earlier than raw. AAPS Average sits in the middle.

A useful corollary: **debates about smoother choice based on calm-window observation are unresolvable**. If you compare two smoothers by looking at a flat 6-hour stretch overnight, you will see almost no difference. The differences only appear during the events that actually drive AID dose decisions — and those events are minutes long, scattered through the day. This is why per-event aligned visualisation, as used in §3.3 to §3.5, is the right tool for the comparison.

### 4.2 What the UKF does differently

The Adaptive UKF is qualitatively different from the three other smoothers in this cohort. AAPS Average, AAPS Exponential, and Trio Savitzky-Golay all apply a roughly uniform smoothing operation to every reading. The UKF applies almost no smoothing to most readings and very strong smoothing to a few — those flagged by the chi-squared outlier mechanism (median 0.13% of readings). On the rejected readings the UKF dampens the input by approximately a factor of 15 relative to its routine behaviour. No other smoother in current AID use does this.

This choice has three measurable consequences. Outlier rejection (rate of single-step ≥40 mg/dL absorption) is high. SID cluster reduction matches Trio Savitzky-Golay's at over 93%. The Random Forest cluster-survival F1 drops to 0.19, indicating that surviving clusters are individually severe and not predictable from a small set of cluster features.

The cost is real: the UKF preserves only 32% of raw curvature peaks during sustained rate-change events. That places it tied for worst with Trio Savitzky-Golay on this metric. The mechanism is the combination of the chi-squared rejector and the backward RTS smoothing pass, which together spread inflection-point curvature across more samples than the other smoothers do.

### 4.3 What Trio Savitzky-Golay actually does

Trio Savitzky-Golay is the most aggressive smoother of the four. It removes 55% of step-to-step variance (noise ratio 0.45), absorbs 93% of single-step ≥40 mg/dL outliers, and preserves only 33% of peak event acceleration. The per-step finding that "passes 2 and 3 do nothing on the typical reading" sits comfortably alongside this: pass 1 alone is doing aggressive smoothing on every reading, and the cumulative three-pass effect across the full series is substantial because of how pass 1 absorbs outliers and bends.

The implication: **Trio Savitzky-Golay and the UKF are similarly aggressive smoothers**, with comparable noise removal, comparable SID cluster reduction, and similarly poor peak event acceleration retention. Trio SG achieves this with a deterministic, time-uniform polynomial fit; the UKF achieves it through a bimodal routine-vs-outlier mechanism. Both end up at roughly the same operating point on the noise-vs-curvature tradeoff.

### 4.4 What AAPS Exponential's anticipation behaviour means

AAPS Exponential / TSUNAMI is the only smoother whose smoothed peaks during meal rises, hypo descents, and post-meal recoveries arrive **before** the raw peaks (median 1.7 minutes earlier), and the only smoother whose smoothed peak heights exceed the raw peak heights during such events (median 3% taller). Both phenomena follow from the same mechanism: the dual-EMA blend, with its first-order and second-order anchors, carries a forward-looking trend term that pulls the current value past the raw value when the curve is moving steadily in one direction. In effect it is a low-effort extrapolator: when glucose is rising and has been rising for a while, AAPS Exp reports a slightly-higher value than where the sensor actually is, on the assumption that the rise will continue. The further the sensor is into a sustained move, the more the anticipation accumulates.

For oref0 in its classical form this is largely benign because the dose engine reads `delta` and `avgdelta` values, not the absolute glucose level. But for any AID logic that reads the smoothed glucose as a proxy for "where the user is right now" — in particular, anything that uses absolute thresholds or expects the smoothed value to lag raw — this is a quirk worth knowing about.

The corollary for acceleration-aware logic: because AAPS Exponential preserves the most curvature at meal-rise and hypo-descent inflections (50% peak event acceleration retention), and because it slightly amplifies the magnitude of those events, **it presents the strongest acceleration signal of the four to a downstream UAM or dynISF derivative**. The price is calibration. Acceleration thresholds that work for the other three smoothers will be slightly over-triggered on AAPS Exponential because the values it reports during a real meal rise are 3% taller and arrive 1.7 minutes earlier than the values the other smoothers report at the same moment in the same physiology.

The 5-minute median delay in AAPS Exp's threshold-crossing time on real meal events (§3.5) is a counterweight to the anticipation: the longer EMA window means the rate signal takes longer to cross any fixed threshold, so even though AAPS Exp eventually delivers a higher peak rate, it does so later. Whether this is net better or net worse for an SMB-on-rise trigger depends on which threshold is set and how the dose engine penalises false starts versus late starts.

### 4.5 Implications for AID smoother choice

For classical oref0 — SMB and UAM with no explicit acceleration input — the choice between the four smoothers is largely a wash during calm periods (§3.3) and a matter of well-understood tradeoffs during glucose movement (§3.4–3.5). Existing oref0 thresholds were tuned with AAPS Average in mind; substituting Trio SG or the UKF without retuning will change behaviour in ways that need empirical validation before they should be claimed as improvements.

For acceleration-aware AID logic, the picture is sharper. A dose engine that explicitly reads delta acceleration as an input will see:
- **AAPS Exponential**: 50% of the raw curvature at meal-rise inflections, with a small additive amplification of the underlying movement. Calibrate thresholds to it. Note the 5-minute median delay in threshold-crossing on real meals.
- **AAPS Average**: 42% of the raw curvature at meal-rise inflections, with peak-time alignment to the second. The most "honest" smoother in terms of where the user actually is, but the least aggressive at removing step-to-step noise.
- **Trio Savitzky-Golay**: 33% of the raw curvature at meal-rise inflections. Aggressive noise removal at the cost of the curvature signal. **Retune acceleration thresholds downward by approximately 25% from an AAPS Average baseline.**
- **Adaptive UKF**: 32% of the raw curvature at meal-rise inflections, plus the bimodal outlier mechanism. **Retune acceleration thresholds downward by approximately 25% from an AAPS Average baseline.**

For users who experience frequent compression artefacts: the UKF and Trio Savitzky-Golay both eliminate over 93% of SID-flagged structural-noise clusters versus the AAPS smoothers' approximately 70%. That translates to fewer false hypoalarms and fewer spurious zero-temp-basal events triggered by sensor compression. This is the strongest case for either UKF or Trio SG over the AAPS smoothers in everyday loop use.

## 5. Limitations

**Cohort size.** Phase 3 used 19 users from a single Nightscout-style oref database; the meal-event sub-study used 45 events from one live AID user. Phenotype clustering on the per-user metric profile produced no robust regimes (silhouette 0.25, below the 0.40 threshold), so per-user "best smoother" recommendations are not warranted from this dataset.

**Window length.** Per-user data was trimmed to the first 90 days of each user's available history. Long-term sensor-quality drift over a year or more is not characterised.

**Sensor coverage.** No Libre 2 or Libre 3 data is in the main cohort. The per-sensor findings in the companion paper are Dexcom-only.

**Offline characterisation.** Every numerical claim in this paper is computed from offline traces of smoothed glucose. The closed-loop impact — whether different smoother choices change SMB or temp-basal decisions in clinically meaningful ways — is not measured here. That is the natural follow-up study.

**Threshold choice for the meal-event analysis.** The 1 mg/dL/min `delta` threshold and 0.2 mg/dL/min² acceleration threshold used to characterise smoother responsiveness on real meals (§3.5) are reasonable defaults but are not the precise oref0 SMB-on-rise threshold (which is configurable per-user). The directional ranking of smoothers should be robust to threshold choice within plausible ranges, but the absolute timings will shift if the threshold is moved.

## 6. Conclusions

The four smoothers behave the same during calm glucose periods and differently during glucose movement. The differences during movement reduce to a single tradeoff: noise removal versus curvature preservation. Trio Savitzky-Golay and the Adaptive UKF sit at the aggressive end of that tradeoff (most noise removed, most curvature lost at meal-rise inflections); AAPS Average is the gentlest noise remover and the second-best curvature preserver; AAPS Exponential is the unique anticipator that peaks slightly ahead of raw with the best preservation of curvature at meal-rise and hypo-descent inflections. There is no universal winner.

For the next generation of AID systems that use delta acceleration as a dosing input, the smoother choice has measurable consequences. AAPS Exponential preserves the most curvature signal (50%) but with a calibration quirk — its smoothed peaks arrive 1.7 minutes early and are 3% taller than the corresponding raw peaks. AAPS Average is the safe second choice (42% curvature retention, peak-time alignment to the second). The UKF and Trio Savitzky-Golay both deliver only ~32% of raw curvature at the bend and require their downstream acceleration thresholds retuned to match. The 45-meal sub-study on a live Nightscout site confirms these patterns hold under real-world AID use.

For classical oref0 use, the case for migrating to the UKF or Trio SG is strongest for users dealing with sensor noise or compression — both eliminate over 93% of SID-flagged structural noise clusters versus the AAPS smoothers' approximately 70%. The case for the UKF specifically is that its bimodal outlier-rejection mechanism is the most principled response to compression-style sensor errors: routine readings sail through unchanged, while the rare physically-implausible spikes are decisively absorbed. The case against migrating, for users without significant sensor noise, requires acknowledging some history.

**oref0 was first built for Dexcom G4 in the early days of DIY closed-loop AID**, and the SMB-on-rise, temp-basal, and UAM thresholds got their initial calibration against G4-era CGM data. AAPS Average came later — its 3-point central averaging was specifically designed to approximate the on-sensor smoothing that the Dexcom G6 transmitter applies internally, so that users running G6 plus AAPS Average would land in roughly the same total-smoothing regime that the G4 era had already produced. The combination "G6 + AAPS Average" therefore approximates the original threshold-calibration assumption by historical accident more than by design: G6's on-board filter plus AAPS Average's 3-point average roughly equals G4's raw stream, which is what the thresholds were tuned against.

That history matters because it changes what "the conservative default" is. The thing that needs to be preserved for oref0's existing thresholds to behave as intended is **the total smoothing budget across the sensor + software-smoother chain**, not any particular smoother. The data identifies three specific ways that budget changes when components are substituted. First, **peak rate magnitude differs by 17 percentage points across the four software smoothers** (AAPS Average 87%, AAPS Exponential 91%, Trio Savitzky-Golay 77%, Adaptive UKF 75% peak-rate retention vs raw on real meal events). Same SMB-on-rise threshold therefore fires more easily on AAPS Exp and less easily on UKF or Trio SG. Second, **timing differs by 5 minutes**: AAPS Exp's median time to cross the SMB-on-rise rate threshold is 25 minutes after a meal note vs 20 minutes for the other three. Third, **outlier behaviour differs dramatically**: AAPS Average absorbs 26% of single-step ≥ 40 mg/dL spikes, the UKF 46%, Trio Savitzky-Golay 93%. The UKF and Trio SG hide spike events that AAPS Average would have shown.

Switching the **sensor** does the same kind of thing on a different axis. G7 applies less on-sensor smoothing than G6 (§3 of the per-sensor companion paper), so "G7 + AAPS Average" already produces a sharper signal than "G6 + AAPS Average" — already a behaviour change relative to the historical tuning, before any software-smoother substitution is considered. This is consistent with the per-sensor finding that hypo preservation drops 3–6 percentage points on G7 across all four smoothers.

The honest framing is therefore that **everything except the historical G4-era / G6 + AAPS Average envelope is empirically unvalidated against the oref0 thresholds**. Substituting software smoothers, switching from G6 to G7, or both, all shift the total smoothing budget that downstream oref0 thresholds see. Any of those shifts may turn out to be net beneficial, but none have been validated against actual closed-loop dose decisions. The conservative position is not "AAPS Average is the right baseline" — it is "**whatever combination of sensor and software smoother has produced acceptable looping for you in the past is the only combination with empirical evidence behind it for your specific physiology**, because the original threshold calibration assumed a particular total-smoothing envelope and no other combination has been measured against it." The natural follow-up study — running the full closed-loop oref0 decision engine end-to-end with each (smoother, sensor) combination — is what would replace this path-dependent default with a principled one.

## 7. Reproducibility

The Phase 3 pipeline is reproducible from `/Users/timstreet/SID-evaluation/backtest/` via `make all`, which runs end-to-end in approximately 90 seconds on an M-series Mac. The Phase 2 sensor-tagged ingest, dedup, smoother run, and per-sensor analysis run from `python3 -m backtest.cli.ingest_phase2_dedup --truncate && python3 -m backtest.cli.phase2_run --out runs/phase2 && python3 -m backtest.cli.phase2_analysis --runs runs/phase2 --out reports/phase2`. The upload-path study runs from `python3 -m backtest.cli.upload_path_study --out reports/upload_path`. The Nightscout meal-event study runs from `NS_API_SECRET='...' python3 -m backtest.cli.fetch_nightscout_meals --out data/nstest3 && python3 -m backtest.cli.meal_event_smoother_impact --data-dir data/nstest3 --out reports/meal_events`. The event-aligned deviation visualisations run from `python3 -m backtest.cli.normalised_deviation_plots --target all`.

Output artefacts: `reports/per_user_metrics.csv` (76 rows), `reports/per_step_modification.csv`, `reports/sid_*.csv`, `reports/phase2/*.csv`, `reports/upload_path/*.csv`, `reports/meal_events/per_meal_smoother_metrics.csv`, `reports/deviation_plots/*.png`. Trace files are gitignored but reproducible. Parity tests run via `make test` and complete in under a second; all 15 currently pass. The standalone Kotlin UKF driver is at `backtest/reference/kotlin_driver/`; rebuilding requires JDK 21 and Gradle.
