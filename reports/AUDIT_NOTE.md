# Audit findings and corrections — 2026-04-28

After publishing the initial results, an end-to-end internal review of the analysis code uncovered one material bug and several methodological caveats that change or qualify the published findings. This document lists each finding, what it changes, and where it is corrected.

---

## Finding 1 — Material bug: Trio Savitzky-Golay raw/smoothed loading

**Where:** `compare.py`, `spectral.py`, `phase2_analysis.py` — every place where the Trio SG trace was loaded as `(raw, smoothed)` for end-to-end metrics.

**The bug:** Trio SG's trace stores three rows per reading (one per filter pass). Each row carries `input_glucose` (the input to *that pass*) and `output_glucose` (the output of *that pass*). The loader filtered `step_name == "pass3"` and used pass-3's `input_glucose` as the "raw" reference. Pass 3's input is pass 2's output — already smoothed twice. So all "raw vs smoothed" comparisons for Trio SG were actually measuring the marginal pass-3 effect, not the cumulative three-pass effect.

**The fix:** load Trio SG `(raw, smoothed)` as `(pass1.input_glucose, pass3.output_glucose)`. Other smoothers (AAPS Average, AAPS Exponential, UKF) were unaffected because their traces have one row per reading.

**What this changed for Trio SG cohort medians:**

| metric | published (buggy) | corrected | implication |
|---|---|---|---|
| noise reduction ratio | 0.95 | **0.45** | most aggressive smoother, not the gentlest |
| signal-band gain | 1.00 | 0.99 | unchanged |
| phase shift (min) | 0.00 | 0.00 | unchanged |
| hypo preserved (%) | 97.1% | **82.4%** | lowest of the four, not highest |
| outlier absorbed (%) | ~30% | **93.2%** | highest of the four, not low-mid |
| peak event acceleration retention | **100%** | **33%** | second-worst of the four |

**What this means for the published narrative:**

The strongest claim in the diabettech-readers paper — "Trio Savitzky-Golay keeps the full peak (100%) because its 7-point polynomial fit is specifically designed to preserve curvature" — is wrong. The 100% retention was an artefact of the bug; the actual retention against true raw is 33%, statistically indistinguishable from the UKF's 32%.

The recommendation that "Trio SG is the smoother whose behaviour transfers most cleanly across sensor generations" is also wrong; the corrected numbers show Trio SG is the most aggressive smoother on both G6 and G7 and preserves the fewest hypos.

The per-step finding "passes 2 and 3 do almost nothing" stands as a per-step measurement, but its interpretation needs to shift. Pass 1 alone IS the aggressive smoothing; passes 2 and 3 are computationally redundant on top of pass 1's already-strong filter.

**Status:** Corrected in compare.py, spectral.py, phase2_analysis.py, sid_redetect.py. All affected CSVs regenerated. All affected papers re-rendered.

---

## Finding 2 — Per-sensor "within-user G6 → G7 transitions" are not clean

**Where:** Per-sensor sub-analysis (`paper_per_sensor.md`) — the claim of "three within-user G6→G7 transitions providing paired comparison".

**The issue:** Of the three users I described as having a within-user G6→G7 transition, only one is sequential and only in the inverse direction:

| user | timing | overlap | actual pattern |
|---|---|---|---|
| User_D (site_04) | G7 then G6 then concurrent | 60 days | Two upload paths (Trio + xDrip) running concurrently for 60 days, then xDrip continues alone |
| User_I (site_16) | G7 → G6 sequential | 0 days | Real switch but in REVERSE direction (G7 first, then G6) |
| User_L (site_19) | concurrent | 182 days (100%) | `share2` (legacy Dexcom Share API) + G7 native uploads of probably the same physical sensor |

Specifically for User_L: the `share2` device string identifies the *upload API* (Dexcom Share2 endpoint), not the sensor model. Share2 can serve G6 or G7 sensor data. The 100% time overlap with the explicit `Dexcom G7 DXCM<id>` entries strongly suggests both streams are reading the same physical G7 sensor through different upload paths. The within-user "G6 vs G7" comparison for User_L is therefore likely measuring upload-path differences, not sensor differences.

**What this means for the published narrative:**

The "G7 produces approximately 3× more single-step ≥40 mg/dL jumps per 1000 readings than G6" finding still has cohort-median support, but the within-user paired evidence is weaker than I described:
- Only User_I provides a clean sequential G7→G6 sample, and even there the G7 era is identified mostly via column-presence signature rather than explicit device string
- User_D's G6 and G7 streams overlapping by 60 days makes per-event causal attribution difficult
- User_L's data should arguably be treated as a single sensor dual-uploaded, not a transition

The "every smoother loses 3-6 pp of hypo preservation on G7" finding is thus weakened — much of the within-user evidence comes from User_L, where the G6/G7 split may be artificial.

**Status:** Caveats added to `paper_per_sensor.md`. The cohort-median findings retained but flagged. The within-user paired-comparison framing softened.

---

## Finding 3 — Cohort selection uses total-span density, not first-90-day density

**Where:** `cohort.py` — the eligibility filter.

**The issue:** The SQL counts total rows per user (across their whole span) and divides by 90-day expected count. Users with long spans (e.g. 540 days) and any meaningful density appear to have density ≥ 1.0 in the cohort filter. Their first-90-day density may actually be much lower (we observed U029 with first-90-day density of 60.6% post-resample, despite passing the nominal "≥70%" filter).

**Severity:** Methodological, not a correctness bug. The cohort still represents 19 users with substantial data; some have lower-than-described first-window density. Smoother metrics tolerate gaps gracefully.

**Status:** Documented in this audit note. Not corrected in code (would require re-pulling cohort and re-running the entire pipeline; the change in selected users is unlikely to materially shift the cohort-level findings).

---

## Finding 4 — UKF trace omits one reading per segment

**Where:** `smoothers/ukf.py` — the UKF needs one initialisation reading per segment, so the trace has one fewer row per segment than the input array.

**Severity:** Negligible. For our cohort, UKF traces have ~15 fewer rows out of ~15,680 total per user. SID redetect alignment compensates by carrying through the raw value at those skipped indices.

**Status:** Documented; behaviour is correct by design.

---

## Finding 5 — `phase_shift_delay_min` uses a single centre frequency

**Where:** `metrics.py:phase_shift_delay_min`.

**The issue:** The Hilbert-transform phase shift is computed in the 1-to-6-hour cycle band, then converted from radians to minutes using a single `f_center = 1/3 hour^-1`. For smoothers with frequency-dependent phase response (AAPS Exponential's dual-EMA, the UKF's adaptive R), the actual group delay varies across the band and the single-`f_center` conversion underestimates phase at low frequencies and overestimates at high frequencies.

**Severity:** Methodological. Magnitude of the approximation is small (~0.05 rad at band edges) and does not change the qualitative ranking. The headline finding "AAPS Exponential leads raw by ~1.7 min in the slow band, the others are essentially in-phase" stands.

**Status:** Documented; not corrected.

---

## Finding 6 — `step_response_delay_min` shows negative values that are not real leads

**Where:** `metrics.py:step_response_delay_min`.

**The issue:** The metric uses an asymmetric threshold protocol — raw rate must exceed 0.5 mg/dL/min, but the smoother's rate only needs to exceed 0.35 mg/dL/min (`smoothed_threshold_frac = 0.7`). When the smoother dampens noise just before a real rise, it can cross 0.35 marginally before the raw exceeds 0.5. Combined with the anti-causal central averaging in AAPS Average and Trio SG, and the RTS backward smoother in UKF, the metric reports negative delays that do not correspond to real "leads".

**Severity:** The reported numbers (e.g. −2.08 min for AAPS Average) cannot be interpreted as real positional leads. They reflect threshold-asymmetry artefacts plus anti-causal smoother behaviour.

**Status:** Documented in this audit note. The published claim that "AAPS Average leads raw by 2 minutes on step responses" should be qualified as "AAPS Average's relaxed-threshold crossing happens approximately 2 minutes before raw exceeds the strict threshold, an artefact of the measurement protocol rather than a real lead".

---

## Finding 7 — `outlier_absorbed_pct` is a smoother-behaviour metric, not a sensor-quality metric

**Status:** Already corrected in earlier round of revisions to `paper_per_sensor.md` (raw spike rate now reported via `outlier_n_raw` / 1000 readings, with `outlier_absorbed_pct` reported separately as the smoother's effectiveness).

---

## Summary of what stands and what changes

**Findings that stand:**
- The UKF removes more high-frequency noise than AAPS Average (median noise ratio 0.63 vs 0.71). ✓
- The UKF's smoothing is bimodal: routine median |Δ| 1.6 mg/dL, χ²-flagged median 23.7 mg/dL. ✓
- AAPS Exponential / TSUNAMI leads raw by ~1.7 min in the slow band, with a signal-band gain >1.0. ✓
- The UKF and Trio Savitzky-Golay both eliminate >93% of SID-flagged clusters. ✓
- AAPS Avg / Exp eliminate ~70% of SID clusters; their RF cluster-survival F1 is ~0.5; UKF and Trio SG drop to F1 ~0.2. ✓
- Cohort-wide G7 raw spike rate is approximately 3× higher than G6's at the per-1000-reading level. ✓ (cohort median holds; within-user paired evidence is weaker than initially claimed)

**Findings that change:**
- ❌ Trio SG is *not* the gentlest smoother — it is the most aggressive (noise ratio 0.45 vs UKF 0.63 vs AAPS Avg 0.71)
- ❌ Trio SG does *not* preserve 100% of peak event acceleration — it preserves ~33%, statistically indistinguishable from UKF
- ❌ Trio SG does *not* preserve the most hypos — it preserves the fewest (82% vs UKF 88%, AAPS Avg 89%, AAPS Exp 93%)
- ❌ Trio SG does *not* absorb a moderate fraction of outliers — it absorbs the most (93% vs UKF 46%, AAPS Avg 26%, AAPS Exp 30%)
- ⚠ The "three within-user G6→G7 transitions" framing is too strong — only User_I provides a sequential transition, and that one is G7→G6, not G6→G7
- ⚠ The recommendation that "Trio SG is best for acceleration-aware AID logic" is invalidated; the corrected numbers show Trio SG and UKF are statistically tied for worst peak event acceleration retention

**The corrected smoother ranking:**

| metric | best | … | worst |
|---|---|---|---|
| noise removal (lowest ratio) | **Trio SG** (0.45) | UKF (0.63), AAPS Avg (0.71) | AAPS Exp (0.84) |
| hypo preservation | **AAPS Exp** (93%) | AAPS Avg (89%), UKF (88%) | Trio SG (82%) |
| outlier absorption | **Trio SG** (93%) | UKF (46%), AAPS Exp (30%) | AAPS Avg (26%) |
| peak event accel retention | **AAPS Exp** (50%) | AAPS Avg (42%) | Trio SG (33%) ≈ UKF (32%) |
| SID cluster reduction | Trio SG (95%) ≈ **UKF** (93%) | … | AAPS Avg (69%), AAPS Exp (70%) |

**The corrected practical guidance:**

- For an AID stack that uses delta acceleration as a dosing input, **AAPS Exponential is the best choice for curvature preservation** despite its phase-lead quirk in the slow band — it retains the largest fraction of peak event acceleration. Its signal-band amplification means thresholds should be calibrated to it rather than to a true smoother.
- **AAPS Average is the second choice** for curvature preservation (42% retention), with the cleanest phase response.
- **Trio SG and the UKF are tied at the bottom** for curvature preservation (~33%), and should not be used in front of acceleration-aware logic without retuning the acceleration thresholds downward by a factor of ~2-3.
- **The previous claim that Trio SG was the safest pick for acceleration-aware AID is wrong.**
