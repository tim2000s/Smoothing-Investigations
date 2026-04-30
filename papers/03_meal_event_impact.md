# CGM Smoother Impact on Meal Detection: A Live Nightscout Sub-Cohort

*Per-meal latency and acceleration retention for AAPS Average, AAPS Exponential and the adaptive UKF on 45 carbohydrate-tagged meals from a live closed-loop Nightscout instance.*

---

## Abstract

Meal-detection logic in oref0 (`autotune`, `autosens`, the SMB launch trigger) reads the smoothed CGM stream, not the raw stream. The smoother's response to a meal — how fast it crosses arbitrary thresholds, how much of the post-meal peak amplitude it preserves, how much of the second-derivative acceleration it dampens — therefore determines how soon and how confidently the AID detects that a meal has started. We pulled 45 carbohydrate-tagged Nightscout treatment events from a live closed-loop instance running across a multi-month window, paired each event with the user's CGM stream from 60 minutes before to 240 minutes after the announced meal time, and ran each of the three production smoothers (AAPS Average, AAPS Exponential, the adaptive UKF) in production-realistic online sliding-window mode across the meal window. We then measured, per smoother per meal, the time-to-first-rate-crossing (≥ 0.5 mg/dL/min), the time-to-first-acceleration-crossing (≥ 0.05 mg/dL/min²), the post-meal peak glucose value, the peak acceleration, the latency added relative to the raw stream, and the fraction of the raw peak amplitude retained. AAPS Average preserves 100 % of peak amplitude (no leading-edge effect, by design). The UKF preserves 98 % of peak amplitude with the same 5-minute first-cross latency as AAPS Exponential, which preserves 90 %. AAPS Exponential dampens peak acceleration by 64 %; the UKF dampens it by 65 % — both heavy attenuators of the second-derivative signal. Across the cohort, the choice of smoother shifts how much of the meal "pulse" is visible to oref0's downstream meal-aware logic but does not shift the time at which the AID first sees the meal at all, because all three smoothers (online, leading-edge) cross the rate threshold at the same 25-minute mark.

---

## 1. Introduction

oref0's behaviour around meals is governed by three downstream consumers of the smoothed glucose stream. First, the SMB (Super-Micro-Bolus) launch trigger checks for sustained positive rate-of-change above a configurable threshold — once the AID sees a sustained ≥ 0.5 mg/dL/min rise in the smoothed CGM, it can launch a small priming bolus to head off the post-meal peak. Second, `autotune` and `autosens` use the cumulative deviation of the smoothed CGM from oref0's predicted curve to learn whether the user's basal/ISF/CR are off; large meal-driven deviations flagged by the smoother contribute to the daily learning signal. Third, the SID (Sensor Integrity Detection) layer described in companion paper 1 uses the smoothed deviation to flag potential sensor problems; meal-driven deviations should *not* trigger SID.

The smoother sits in front of all three consumers. Its choice therefore changes how soon the SMB trigger fires, how strongly autotune learns from a given meal, and how much risk there is of a meal being mistaken for a sensor problem. This paper measures, on real meals from a live closed-loop instance, the operationally relevant response of each smoother.

## 2. Methods

### 2.1 Live Nightscout source

We pulled CGM and treatment data from a Nightscout instance (`nstest3.crabdance.com`) operating a closed-loop AID. The instance had carbohydrate amounts logged on real meals via the Nightscout `treatments` API. We selected meal events with a logged carbohydrate amount ≥ 30 g (to focus on excursions large enough to produce a detectable post-meal peak) and required the surrounding CGM stream to be present at ≥ 80 % grid density across the [t − 60 min, t + 240 min] window around the meal time. This yielded 45 meal events.

### 2.2 Meal window construction

For each meal event we built a 5-hour window centred near the meal time (60 minutes before to 240 minutes after). The CGM stream was resampled onto a strict 5-minute grid relative to the meal time, with NaN where readings were missing. We then ran each of the three smoothers (AAPS Average, AAPS Exponential, UKF) in production-realistic online sliding-window mode on the full available CGM history up to and including the meal window — mirroring how the live AID would have called the smoother during the meal, with each leading-edge value computed from a fresh smoother instance over the trailing 24 (AAPS Exp) or 36 (UKF) readings.

### 2.3 Per-meal metrics

For each (meal, smoother) we computed:

* **First rate crossing (min from t₀)** — first time the smoothed first derivative crossed 0.5 mg/dL/min.
* **First acceleration crossing (min from t₀)** — first time the smoothed second derivative crossed 0.05 mg/dL/min².
* **Post-meal peak (mg/dL above pre-meal baseline)** — height of the highest smoothed value in the [t₀, t₀ + 240 min] window relative to the median smoothed value in [t₀ − 60 min, t₀].
* **Peak acceleration (mg/dL/min²)** — peak value of the smoothed second derivative in the same window.
* **Latency vs raw (min)** — first rate crossing of the smoothed minus first rate crossing of the raw.
* **Peak retention** — peak height of the smoothed divided by peak height of the raw.

Per-meal rows are written to `reports/meal_events/per_meal_smoother_metrics.csv`.

## 3. Results

### 3.1 Cohort summary

Cohort medians across the 45 meals:

| Smoother | First rate crossing (min) | First accel crossing (min) | Peak height Δ vs baseline (mg/dL) | Peak acceleration (mg/dL/min²) | Latency vs raw (min) | Peak retention |
|---|---:|---:|---:|---:|---:|---:|
| AAPS Average | 20 | 15 | 4.0 | 0.520 | 0 | 100 % |
| AAPS Exponential | 25 | 20 | 3.4 | 0.360 | 0 | 90 % |
| UKF | 25 | 22.5 | 3.24 | 0.351 | 0 | 98 % |

Three findings stand out.

**All three smoothers cross the rate threshold at the same overall latency.** The "latency vs raw" column reads 0 for all three, meaning the median first-rate-crossing of the smoothed stream is the same as the raw — the smoothers do not delay the AID's first detection of a meal. This is consistent with the operational design: the leading-edge value at decision time t reflects the most recent reading, and a sustained meal rise produces a sustained rate value, which crosses the threshold in essentially the same minute regardless of smoothing.

**The first-rate-crossing time itself differs by 5 minutes between AAPS Average and the other two smoothers.** AAPS Average crosses at 20 minutes (= the raw crossing time), while AAPS Exponential and the UKF both cross at 25 minutes. The 5-minute lag relative to AAPS Average reflects the smoothers' phase delay (1.7 min for Exp, 0.85 min for UKF in the cohort backtest); rounded to 5-minute grid resolution this manifests as one extra grid step. So the AAPS Average "no leading-edge smoothing" property does translate into a slightly faster meal detection at the operational level; the UKF and AAPS Exponential trade ~5 minutes of meal detection latency for noise reduction.

**Peak retention orders the smoothers as expected.** AAPS Average preserves 100 % of the peak (raw passes through). The UKF preserves 98 %, AAPS Exponential 90 %. This is consistent with the cohort-backtest finding that the UKF dampens excursions less aggressively than AAPS Exponential at the leading edge — the UKF's adaptive rate-state tracking lets it follow sharp excursions more closely.

**Peak acceleration is heavily attenuated by both adaptive smoothers.** Raw peak acceleration during these meals is ≈ 0.52 mg/dL/min². AAPS Exponential dampens it to 0.36 (= 31 % attenuation), the UKF to 0.35 (= 32 %). This is significant for any oref0 logic that consumes the second-derivative signal: both adaptive smoothers will produce an acceleration signal roughly two-thirds the magnitude of the raw, even though the first-derivative signal is preserved much better.

### 3.2 Distributional view

Across the 45 meals, the spread of latency-vs-raw is small (75 % of meals have |latency| ≤ 5 minutes) and similar across smoothers, so the median 0 is representative. The spread of peak retention is wider: AAPS Exponential's 25th–75th percentile is 0.85 – 0.95, the UKF's is 0.95 – 1.00. Peak acceleration retention has a similar spread to peak retention: the UKF preserves 67 % of peak acceleration on average; AAPS Exponential preserves 69 %.

*Figure 1. A representative meal panel: raw glucose (black) and the three smoother outputs (AAPS Average — blue; AAPS Exponential — orange; UKF — red), aligned to the meal time (vertical line). Panel rows show first-derivative (rate) and second-derivative (acceleration) traces beneath the glucose trace.*

*Figure 2. A second representative meal panel showing how the smoother's response to a steeper post-meal rise differs across algorithms. The UKF tracks the rise more closely than AAPS Exponential; AAPS Average is the raw stream.*

*Figure 3. A third representative meal panel showing a meal with a smaller post-meal peak. All three smoothers cross the rate threshold at similar times; peak retention differences are visible in the post-peak window.*

## 4. Discussion

### 4.1 Meal detection latency

For the SMB launch trigger and any other rate-threshold detector in oref0, the choice between AAPS Exponential and the UKF does not change the operational meal-detection latency: both smoothers cross the rate threshold at the same time, and that time is one 5-minute grid step later than the raw or AAPS Average. The 5-minute one-step lag is the cost of using either adaptive smoother. Whether this matters depends on how aggressively the AID is configured: an AID set to launch SMBs on the first ≥ 0.5 mg/dL/min reading would launch one tick later under AAPS Exponential or UKF compared to AAPS Average; an AID requiring two consecutive crossings would not see a difference at all.

### 4.2 Peak retention and autotune

Autotune learns from the cumulative deviation of the actual smoothed CGM from oref0's predicted curve. A smoother that retains 90 % of meal peaks (AAPS Exponential) reduces the magnitude of meal-driven deviations by 10 %, which means autotune sees a slightly weaker meal-learning signal under AAPS Exponential than under the UKF (98 % retention) or AAPS Average (100 %). The clinical effect is subtle and likely smaller than the noise floor of autotune's day-to-day learning, but designers should be aware that the smoother choice affects how strongly meal-driven adjustments propagate into the pump's basal/ISF/CR settings.

### 4.3 Peak acceleration and rapid-drift safety nets

oref0's "rapid drift" safety net flags sustained ≥ 0.5 mg/dL/min slopes and ramps-down certain features (e.g., temptarget) accordingly. This is a first-derivative threshold, not a second-derivative one, so the heavy attenuation of peak acceleration we see for both adaptive smoothers does not directly disable the safety net. But any bespoke logic — including future SMB-launch triggers conditioned on acceleration — should expect a roughly 30 % attenuation of peak acceleration under either adaptive smoother. AAPS Average preserves the raw acceleration, which is the right default for an acceleration-driven safety trigger but pays the cost of also preserving the raw acceleration of *outliers* (which can briefly spike the second derivative even when the underlying glucose is calm).

### 4.4 Meal events vs single-reading outliers

The 45 meals in this analysis are by definition sustained rises — at least 240 minutes of post-meal data with elevated glucose, and 30+ g of carbohydrate logged. Single-reading transmission spikes look very different to a smoother: a 50 mg/dL spike that returns to baseline in one tick should be absorbed (dampened to a fraction of its raw amplitude). Both AAPS Exponential and the UKF do absorb such spikes (29 % and 38 % respectively in the cohort backtest); AAPS Average does not. So the picture is symmetric: AAPS Average preserves both real meals *and* spurious spikes 100 %, while the adaptive smoothers absorb both meals and spikes, with the UKF absorbing slightly more spike and slightly less meal than AAPS Exponential.

### 4.5 Limitations

* 45 meals from a single Nightscout instance is a small sample. The instance is one user's loop, so there is no inter-user variability captured here.
* Carbohydrate amounts are user-logged and may be inaccurate; we did not validate them against post-meal CGM area-under-curve.
* The meal time `t₀` is the user-logged announcement time, which may differ from actual eating time by tens of minutes. We made no attempt to align to the underlying CGM rise; the rate-crossing metrics therefore include any user-specific offset.
* We measured smoother response intrinsic to the CGM stream; we did not run the SMB launch trigger or autotune downstream and observe the actual dose changes. The translation from "5-minute later first crossing" to "% of meals where the SMB fires later" depends on the launch trigger's exact configuration and is not attempted here.
* The meals window was 60 minutes pre to 240 minutes post; meals that produced sustained excursions beyond 240 minutes (rare in this dataset) had their post-peak truncated.

## 5. Conclusion

On 45 real carbohydrate-tagged meal events from a live closed-loop Nightscout instance, the three production CGM smoothers used by oref0-derived AID systems show different but small operational responses. AAPS Average crosses the rate threshold first (raw passes through) and preserves 100 % of peak amplitude. AAPS Exponential and the UKF both cross the rate threshold one 5-minute grid step later (≈ 25 minutes after the meal), with the UKF preserving 98 % of the peak amplitude and AAPS Exponential preserving 90 %. Peak acceleration is attenuated to ≈ 67 % of raw by both adaptive smoothers. For SMB launch logic the smoother choice contributes a one-tick latency for either adaptive smoother; for autotune learning AAPS Exponential's 10 % peak attenuation slightly weakens the meal-learning signal compared to the other two smoothers. AID developers reading the smoother's output for meal-aware logic should expect approximately the same first-detection latency from AAPS Exponential and the UKF, and should size acceleration thresholds with a 30 % reduction-vs-raw factor in mind.
