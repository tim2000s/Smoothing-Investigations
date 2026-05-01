# CGM Smoother Impact on Meal Detection: A Live Nightscout Sub-Cohort

*Per-meal latency, peak-rate retention and peak-acceleration retention for AAPS Average, AAPS Exponential and the adaptive UKF on 45 carbohydrate-tagged meals from a live closed-loop Nightscout instance.*

---

## Abstract

Meal-detection logic in oref0 (`autotune`, `autosens`, the SMB launch trigger) reads the smoothed CGM stream, not the raw stream. The smoother's response to a meal — how fast it crosses rate-of-rise thresholds, how much of the peak rate of rise it preserves, how much of the second-derivative acceleration it dampens — therefore determines how soon and how confidently the AID detects that a meal has started. We pulled 45 carbohydrate-tagged Nightscout treatment events from a live closed-loop instance running across a multi-month window, paired each event with the user's CGM stream from 60 minutes before to 180 minutes after the announced meal time, and ran each of the three production smoothers (AAPS Average, AAPS Exponential, the adaptive UKF) in production-realistic online sliding-window mode across the meal window. We then measured, per smoother per meal, the time-to-first-rate-crossing (≥ 1.0 mg/dL/min), the time-to-first-acceleration-crossing (≥ 0.2 mg/dL/min²), the peak rate of rise, the peak acceleration, the latency added relative to the raw stream, and the per-meal retention of the raw peak rate of rise and the raw peak acceleration. AAPS Average passes through unchanged at the leading edge (100 % retention of both peak rate of rise and peak acceleration). The UKF retains 98 % of the raw peak rate of rise and 69 % of the raw peak acceleration. AAPS Exponential retains 90 % of the peak rate of rise and 62 % of the peak acceleration. The first-rate-crossing latency relative to raw is 0 minutes for all three smoothers (median across meals). Across the cohort, the choice of smoother shifts how much of the meal "pulse" — particularly the second-derivative signal — is visible to oref0's downstream meal-aware logic but does not shift the median first-detection time of the rate-of-rise threshold relative to the raw stream.

---

## 1. Introduction

oref0's behaviour around meals is shaped by two downstream consumers of the smoothed glucose stream. First, the SMB (Super-Micro-Bolus) launch trigger checks for sustained positive rate-of-change above a configurable threshold — once the AID sees a sustained rise in the smoothed CGM (in this analysis the threshold is 1.0 mg/dL/min on the smoothed first derivative), it can launch a small priming bolus to head off the post-meal peak. Second, `autotune` and `autosens` use the cumulative deviation of the smoothed CGM from oref0's predicted curve to learn whether the user's basal/ISF/CR are off; large meal-driven deviations flagged by the smoother contribute to the daily learning signal.

The smoother sits in front of both consumers. Its choice therefore changes how soon the SMB trigger fires and how strongly autotune learns from a given meal. This paper measures, on real meals from a live closed-loop instance, the operationally relevant response of each smoother.

## 2. Methods

### 2.1 Live Nightscout source

We pulled CGM and treatment data from a Nightscout instance (`nstest3.crabdance.com`) operating a closed-loop AID. The instance had carbohydrate amounts logged on real meals via the Nightscout `treatments` API. We classified treatments as meal events by matching the notes against `meal|fast carbs` (case-insensitive) and required the surrounding CGM stream to be present across the [t − 60 min, t + 180 min] window around the meal time. This yielded 45 meal events.

### 2.2 Meal window construction

For each meal event we built a 4-hour window (60 minutes before to 180 minutes after). The CGM stream was resampled onto a strict 5-minute grid relative to the meal time, with NaN where readings were missing. We then ran each of the three smoothers (AAPS Average, AAPS Exponential, UKF) in production-realistic online sliding-window mode on the full available CGM history up to and including the meal window — mirroring how the live AID would have called the smoother during the meal, with each leading-edge value computed from a fresh smoother instance over the trailing 24 readings (AAPS Exp) or the configured UKF window (24–48 readings; 36 in this analysis).

### 2.3 Per-meal metrics

For each (meal, smoother) we computed:

* **First rate crossing (min from t₀)** — first time the smoothed first derivative crossed `DELTA_THRESHOLD = 1.0 mg/dL/min` after the event.
* **First acceleration crossing (min from t₀)** — first time the smoothed second derivative crossed `ACCEL_THRESHOLD = 0.2 mg/dL/min²` after the event.
* **Peak rate of rise (mg/dL/min)** — peak value of the smoothed first derivative in the [t₀, t₀ + 180 min] window.
* **Peak acceleration (mg/dL/min²)** — peak value of the smoothed second derivative in the same window.
* **Latency vs raw (min)** — first rate crossing of the smoothed minus first rate crossing of the raw.
* **Peak rate retention** — peak rate of rise of the smoothed divided by peak rate of rise of the raw.
* **Peak acceleration retention** — peak acceleration of the smoothed divided by peak acceleration of the raw.

Per-meal rows are written to `reports/meal_events/per_meal_smoother_metrics.csv`.

## 3. Results

### 3.1 Cohort summary

Cohort medians across the 45 meals:

| Smoother | First rate cross (min) | First accel cross (min) | Peak rate of rise (mg/dL/min) | Peak acceleration (mg/dL/min²) | Latency vs raw (min) | Peak rate retention | Peak accel retention |
|---|---:|---:|---:|---:|---:|---:|---:|
| AAPS Average | 20 | 15 | 4.0 | 0.520 | 0 | 100 % | 100 % |
| AAPS Exponential | 25 | 20 | 3.4 | 0.360 | 0 | 90 % | 62 % |
| UKF | 25 | 22.5 | 3.24 | 0.351 | 0 | 98 % | 69 % |

Note that the per-meal retention columns are the median of (smoothed peak ÷ raw peak) computed per meal, not the ratio of the median smoothed peak to the median raw peak; on this dataset the two summaries differ slightly because each meal has a different raw baseline.

Four observations follow from this table.

First, all three smoothers cross the rate threshold at the same median latency relative to the raw stream. The "latency vs raw" column is 0 for all three: the median first-rate-crossing of the smoothed stream is the same minute as the raw. A sustained meal rise produces a sustained rate value, which crosses the 1.0 mg/dL/min threshold in essentially the same minute regardless of smoothing.

Second, the first-rate-crossing time itself differs by one 5-minute grid step between AAPS Average and the other two smoothers. AAPS Average crosses at 20 minutes (= the raw crossing time, because the leading edge is raw); AAPS Exponential and the UKF cross at 25 minutes. The 5-minute lag relative to AAPS Average reflects the smoothers' phase delay (≈ 1.7 min for AAPS Exponential, ≈ 0.85 min for the UKF in this dataset), rounded to grid resolution.

Third, peak rate of rise retention orders the smoothers in line with leading-edge smoothing intensity: AAPS Average retains 100 % (raw passes through), the UKF 98 %, AAPS Exponential 90 %. The UKF's adaptive rate-state tracking lets it follow sharp first-derivative excursions more closely than the dual-EMA in AAPS Exponential.

Fourth, per-meal peak acceleration retention is much lower than peak rate retention for both adaptive smoothers. AAPS Average retains 100 % of the raw peak acceleration. The UKF retains 69 % (≈ 31 % attenuation), AAPS Exponential 62 % (≈ 38 % attenuation). The first-derivative signal is preserved much better than the second-derivative signal under either adaptive smoother.

### 3.2 Distributional view

Across the 45 meals, the spread of latency-vs-raw is small (75 % of meals have |latency| ≤ 5 minutes) and similar across smoothers, so the median 0 is representative. The spread of peak rate retention is wider: AAPS Exponential's 25th–75th percentile is 0.85 – 0.95, the UKF's is 0.95 – 1.00. Per-meal peak acceleration retention has a wider spread again: the UKF's median is 69 % and AAPS Exponential's is 62 %.

*Figure 1. A representative meal panel: raw glucose (black) and the three smoother outputs (AAPS Average — blue; AAPS Exponential — orange; UKF — red), aligned to the meal time (vertical line). Panel rows show first-derivative (rate) and second-derivative (acceleration) traces beneath the glucose trace.*

*Figure 2. A second representative meal panel showing how the smoother's response to a steeper post-meal rise differs across algorithms. The UKF tracks the rise more closely than AAPS Exponential; AAPS Average is the raw stream.*

*Figure 3. A third representative meal panel showing a meal with a smaller post-meal peak. All three smoothers cross the rate-of-rise threshold at similar times; peak rate retention differences are visible in the post-peak window.*

## 4. Discussion

### 4.1 Meal detection latency

For the SMB launch trigger and any other rate-threshold detector in oref0, the choice between AAPS Exponential and the UKF does not change the operational meal-detection latency: both smoothers cross the rate-of-rise threshold at the same time, and that time is one 5-minute grid step later than the raw stream or AAPS Average. The one-step lag is the cost of using either adaptive smoother. Whether this matters depends on the AID configuration: an AID set to launch SMBs on the first qualifying rate-of-rise reading would fire one tick later under AAPS Exponential or the UKF compared to AAPS Average; an AID requiring two consecutive qualifying readings would not see a difference.

### 4.2 Peak rate of rise and downstream learning

Autotune learns from the cumulative deviation of the actual smoothed CGM from oref0's predicted curve. The peak rate of rise observed during a meal is one driver of that deviation. AAPS Exponential's 10-percentage-point reduction in peak rate of rise (90 % retention vs the UKF's 98 % and AAPS Average's 100 %) modestly weakens the meal-learning signal compared with the other two smoothers; the effect is small relative to autotune's day-to-day learning noise, but it is a real consequence of the smoother choice for any logic that consumes the first-derivative meal envelope.

### 4.3 Peak acceleration and rapid-drift safety nets

oref0's rapid-drift safety net responds to sustained slopes of the smoothed first derivative. This is a first-derivative threshold, so the attenuation of peak acceleration observed under both adaptive smoothers does not directly disable the safety net. Bespoke logic that consumes the second derivative — for example, a future SMB-launch trigger conditioned on acceleration — should expect about 31 % attenuation of peak acceleration under the UKF and about 38 % under AAPS Exponential. AAPS Average preserves the raw acceleration in full, which is appropriate for an acceleration-driven safety trigger but also preserves the raw acceleration of single-reading outliers, which can briefly spike the second derivative even when the underlying glucose is calm.

### 4.4 Meal events vs single-reading outliers

The 45 meals in this analysis are sustained rises with notes consistent with a real eating event. Single-reading transmission spikes look very different to a smoother: a 50 mg/dL spike that returns to baseline in one tick is absorbed by a leading-edge smoother to a fraction of its raw amplitude. Both AAPS Exponential and the UKF absorb such spikes (about 30 % and about 38 % of the cohort backtest's outlier count, respectively); AAPS Average does not at the leading edge. The picture is therefore symmetric: AAPS Average preserves both real meals and single-reading spikes at 100 %, while the adaptive smoothers absorb both meals and spikes, with the UKF retaining more meal signal and absorbing more spike than AAPS Exponential.

### 4.5 Limitations

* 45 meals from a single Nightscout instance is a small sample. The instance is one user's loop, so there is no inter-user variability captured here.
* Meal events are identified from the treatment notes (`meal|fast carbs`); we did not validate that all such events represent a true post-meal rise of comparable magnitude.
* The meal time `t₀` is the user-logged announcement time, which may differ from actual eating time by tens of minutes. We made no attempt to align to the underlying CGM rise; the rate-crossing metrics therefore include any user-specific offset.
* We measured smoother response intrinsic to the CGM stream; we did not run the SMB launch trigger or autotune downstream and observe the actual dose changes.
* The meal window was 60 minutes before to 180 minutes after; meals whose post-meal peak fell outside that 3-hour post window had their post-peak truncated.

## 5. Conclusion

On 45 carbohydrate-tagged meal events from a live closed-loop Nightscout instance, the three production CGM smoothers used by oref0-derived AID systems show small differences in operational response. AAPS Average crosses the rate-of-rise threshold first (raw passes through) and retains 100 % of both the peak rate of rise and the peak acceleration. AAPS Exponential and the UKF both cross the rate-of-rise threshold one 5-minute grid step later (≈ 25 minutes after the meal); the UKF retains 98 % of the peak rate of rise and 69 % of the peak acceleration, while AAPS Exponential retains 90 % of the peak rate of rise and 62 % of the peak acceleration. The smoother choice therefore contributes a one-tick latency under either adaptive smoother for SMB launch logic that fires on the first qualifying rate-of-rise reading; modestly weakens any first-derivative-based meal-learning signal under AAPS Exponential relative to the other two smoothers; and roughly one-third attenuation of peak acceleration under either adaptive smoother (slightly more under AAPS Exponential than under the UKF). The smoother choice is again a trade-off; this paper does not pick a winner.
