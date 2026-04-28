# Per-sensor smoother behaviour: Dexcom G6 vs G7

*A Phase 2 sub-analysis using sensor-tagged Nightscout data, with three within-user G7→G6 transitions providing a paired comparison that holds physiology and behaviour constant. This version reflects two corrections from the audit: (i) the Trio Savitzky-Golay loader bug, fixed; (ii) deduplication of dual-upload-path entries, which collapsed the dataset to its true sensor partition.*

---

## 1. What this paper does

The Phase 3 main cohort had no sensor-type information in the database tables, so the smoother comparison there was sensor-agnostic. The Phase 2 source data — JSON pulled directly from 13 anonymised Nightscout sites, used in the prior multi-user paper — does carry per-entry device strings. We extracted those, classified each entry into Dexcom G6, Dexcom G7, or unknown, deduplicated entries that were the same physical reading uploaded via two paths simultaneously (a real phenomenon affecting two users in this cohort), and re-ran the four smoothers on the cleaned partition.

The resulting analysis answers: **does smoother behaviour transfer cleanly from G6 to G7?** And specifically: **is the noise / curvature / hypo-preservation profile of each smoother stable across sensor generations, or does the sensor change make a measurable difference to which smoother is the right pick?**

## 2. Cohort

After deduplication and per-entry classification, the Phase 2 cohort partitions as follows:

**Three users with sequential within-user G7 → G6 transitions** (paired comparison possible):
- **User_D**: G7 Oct–Dec 2025 (22,414 entries, 80 days), G6 Dec 2025–Apr 2026 (23,720 entries, 100 days), 3-day gap between sessions.
- **User_I**: G7 Oct 2024–Jan 2025 (28,833 entries, 105 days), G6 Jan–Apr 2025 (12,677 entries, 72 days), 6-day gap between sessions.
- **User_J**: G7 Oct 2024–Jan 2025 (28,993 entries, 109 days), G6 Jan–Apr 2025 (18,449 entries, 72 days), no gap between sessions.

**Two users with G7-only data:**
- User_E (182 days), User_L (182 days).

**Five users with G6-only data:**
- User_B (158 days), User_C (141 days), User_F (74 days), User_G (64 days), User_K (70 days).

**One user with mixed sources (User_M)**: 182 days of G6 plus 82 days of sparse G7 data running concurrently. Timestamp-pair check shows User_M's G6 and G7 entries are NOT the same readings (median Δsgv 4 mg/dL, none identical), so they appear to be genuinely different streams — possibly a third-party sensor share, or unusual dual-sensor wear. We include both partitions for User_M but flag it.

User_H (51,594 entries) and the unknown portions of other sites had insufficient sensor evidence and are excluded from the per-sensor analysis.

The deduplication pass is documented in detail in the audit note. Two users (User_D and User_L) had what appeared to be a within-user G7↔G6 split, but timestamp-pair analysis showed those splits were dual-upload-path artefacts — the same physical sensor data uploaded via two distinct paths to Nightscout, with my classifier assigning different sensor labels to the two streams. After deduplication, User_D is correctly partitioned as a real G7→G6 transition, and User_L is correctly recognised as G7-only across the full window. The companion upload-path effects paper analyses what those duplicate streams reveal about Nightscout upload pipelines.

## 3. Per-sensor cohort medians

Across the cohort, with each (user, sensor) treated as one observation, the medians per smoother are:

**Table 1.** Per-sensor cohort medians.

| sensor | smoother | noise ratio | hypo preserved % | outlier absorbed % | peak event accel retention |
|---|---|---|---|---|---|
| G6 | AAPS Average | 0.66 | 87.3 | 54.5 | 41% |
| G6 | AAPS Exponential | 0.79 | 90.0 | 50.0 | 47% |
| G6 | Trio Savitzky-Golay | 0.53 | 82.1 | 100.0 | 29% |
| G6 | Adaptive UKF | 0.57 | 82.6 | 68.8 | 28% |
| G7 | AAPS Average | 0.59 | 83.3 | 32.3 | 37% |
| G7 | AAPS Exponential | 0.73 | 86.6 | 55.0 | 44% |
| G7 | Trio Savitzky-Golay | 0.47 | 76.5 | 93.8 | 24% |
| G7 | Adaptive UKF | 0.46 | 76.8 | 60.1 | 22% |

Three patterns emerge across all four smoothers:

1. **G7 noise ratios are lower than G6** by ~0.05–0.11 — every smoother removes a larger fraction of variance on G7. The interpretation is that G7 raw data has more high-frequency content for the smoother to remove, consistent with G7's less-aggressive on-sensor smoothing algorithm (see §6). Note that these cohort-median noise ratios are dominated by what each smoother does during glucose movement: the companion event-aligned deviation analysis (in the diabettech paper) shows that during calm windows of 70–180 mg/dL with `|rate| < 0.3 mg/dL/min`, all four smoothers track raw glucose to within ≤0.33 mg/dL of median deviation regardless of sensor. The G7-vs-G6 noise difference is therefore concentrated in the moving regime — meal rises, hypo descents, post-meal recoveries — not as a global "G7 noise floor".

2. **G7 hypo preservation is lower** by ~3–6 percentage points across all four smoothers. The mechanism is twofold and reinforces what the event-aligned deviation analysis shows. First, G7's less-aggressive on-sensor smoothing means raw nadirs are sharper and shorter than they would be on G6. Second, the deviation visualisations in the diabettech companion show the aggressive smoothers (UKF, Trio SG) damp glucose by ~2 mg/dL below raw during steep movements — and a brief nadir that crosses 70 mg/dL in raw might not cross 70 in the smoothed series after that damping. Combined, the sharper raw nadirs and the smoother damping cost the UKF and Trio Savitzky-Golay the largest hits (down to ~76% on G7); AAPS Exponential preserves the most lows on either sensor (~86% on G7) because its anticipator behaviour pulls the smoothed signal *toward* upcoming changes rather than absorbing them.

3. **G7 peak event acceleration retention is lower** by ~3–5 percentage points across all four smoothers. The same on-sensor algorithm difference plays out at the curvature level — sharper raw events get more absorbed by the smoother on G7 than on G6. The cohort medians for peak event acceleration retention on G7 are: AAPS Exponential 43%, AAPS Average 36%, Trio Savitzky-Golay 24%, Adaptive UKF 22%. Trio SG and the UKF are tied at the bottom on either sensor; **the relative ranking of smoothers on curvature retention does not change between G6 and G7, only the absolute retention level shifts down on G7**.

## 4. Within-user paired comparison (3 users with sequential G7 → G6)

The within-user comparison removes the inter-user confound. For each of the three users with a sequential G7 → G6 transition, the same physiology and behavioural patterns persist across the sensor change; only the sensor-and-stack changes.

**Table 2.** Within-user G6 minus G7 deltas (per user × algorithm). Positive = better on G6.

| user | algorithm | noise ratio Δ | hypo preserved Δ |
|---|---|---|---|
| User_D | AAPS Avg | +0.07 | +2.0 pp |
| User_D | AAPS Exp | +0.05 | +3.0 pp |
| User_D | Trio SG | −0.01 | +4.5 pp |
| User_D | UKF | +0.08 | +8.1 pp |
| User_I | AAPS Avg | +0.05 | +6.7 pp |
| User_I | AAPS Exp | +0.06 | +4.6 pp |
| User_I | Trio SG | +0.02 | +8.2 pp |
| User_I | UKF | +0.05 | +9.7 pp |
| User_J | AAPS Avg | −0.02 | −7.3 pp |
| User_J | AAPS Exp | −0.03 | −11.7 pp |
| User_J | Trio SG | −0.01 | −12.9 pp |
| User_J | UKF | −0.03 | −9.9 pp |

For **User_D and User_I**, the cohort-level pattern holds: G6 has a higher (less aggressive) noise ratio and better hypo preservation. The UKF in particular shows the largest hypo-preservation hit when going to G7 (~8–10 pp on these two users).

**User_J shows the opposite pattern**: G7 was the cleaner sensor (lower noise ratio) and preserved more hypos than G6 for this user. The hypo-preservation difference is large (10–13 pp better on G7 across all smoothers).

The cohort effect is therefore real but not universal: 2 of 3 within-user transitions show G7 noisier and worse-for-hypos; 1 shows the reverse. This is a small-n result and should be reported as a tendency, not a law.

## 5. SID re-detection per sensor

Re-running SID v6 on each smoother's output, broken down by sensor, gives:

**Table 3.** Median SID cluster reduction per (sensor, smoother).

| sensor | AAPS Avg | AAPS Exp | Trio SG | UKF |
|---|---|---|---|---|
| G6 | 70% | 70% | 95% | 93% |
| G7 | 70% | 70% | 96% | 95% |

SID cluster reduction is essentially sensor-independent. Trio SG and the UKF eliminate over 93% on either sensor; the AAPS smoothers eliminate around 70% on either sensor. The mechanism that removes structural noise (the chi-squared test for UKF; the polynomial fit for Trio SG; the 3-point average for AAPS Avg) does not depend on the sensor's internal smoothing algorithm.

## 6. The G6 vs G7 difference at the on-sensor algorithm level

Why is G7 systematically noisier at the 5-minute step level than G6 in our cohort? The audit established three things by direct inspection of the source JSON:

1. **xDrip+ on G6 is a passthrough.** Across six of the seven G6 sites in our Phase 2 cohort, the `sgv`, `filtered`, and `unfiltered` columns carry identical values for 100% of entries. xDrip writes the Dexcom transmitter's value into all three Nightscout fields without modification. There is no upstream pre-Nightscout smoothing on the G6 path that could explain the difference.

2. **Both sensors broadcast at 5-minute cadence end-to-end.** Neither G6 nor G7 delivers a finer-grained stream that gets aliased at upload. There is no cadence-presentation effect.

3. **The G6/G7 difference is at the on-sensor EGV pipeline.** The most plausible explanation is that G7's on-sensor algorithm applies less internal smoothing than G6's. Dexcom emphasises lower lag and faster response for G7; both are achievable by reducing the on-sensor smoothing window. The predictable side-effect is more visible step-to-step variation in the broadcast EGV stream — exactly what we observe.

A useful framing: **on G7, more of the smoothing burden has been moved out of the transmitter and into whatever software smoother sits downstream**. The total noise-removal budget is divided differently between sensor-side and software-side. Picking an aggressive software smoother on G7 (UKF, Trio SG) re-creates roughly the G6-equivalent total smoothing window. Picking a gentle software smoother on G7 (AAPS Average) lets more of G7's deliberately-low-smoothed character pass through to the dose engine.

The event-aligned deviation analysis in the diabettech companion paper localises this finding further: during calm periods (raw stable in 70–180 mg/dL with `|rate| < 0.3 mg/dL/min`), all four smoothers track raw glucose to within ≤0.33 mg/dL of median deviation regardless of sensor. The G6/G7 difference therefore is not a "G7 noise floor" that the smoother has to fight all the time — it is specifically a difference in how much each sensor's on-board algorithm has dampened the curvature at meal-rise and hypo-descent inflections by the time the EGV reaches the user's device. The downstream software smoother sees that difference only when the underlying signal is moving fast.

## 7. Implications for AID stack choice

For users moving from G6 to G7 on AAPS or Trio:

- **Hypo-low alerting will fire slightly later** because the smoother is more likely to absorb a brief nadir on G7 than on G6. The within-user evidence is mixed in direction but consistent across smoothers within a user — i.e. for users where G7 is noisier, all four smoothers will absorb a few more lows. A 3 to 5 mg/dL tightening of the alert threshold or a shorter persistence window is a reasonable compensatory response if the user finds they are getting less timely low warnings post-G7-switch.

- **For acceleration-aware AID logic**: the UKF + G7 combination sits at the most aggressive end of the noise-vs-curvature tradeoff (peak event acceleration retention 22%, vs AAPS Average + G6's 41% — a factor of ~2 difference). Same acceleration threshold on both stacks will trigger meaningfully less aggressively on UKF + G7. **Per-(smoother, sensor) calibration of acceleration thresholds is necessary if acceleration is to be used as a dosing input.** AAPS Exponential preserves the most curvature on either sensor (50% on G6, 43% on G7); on a live Nightscout cohort of 45 meal events analysed in the diabettech companion paper, AAPS Exp's median peak rate retention vs raw was 91% — substantially higher than the UKF's 75% or Trio SG's 77% on the same meals. Combined with G7's sharper raw curvature, **AAPS Exp + G7 is the strongest stack for an AID dose engine that explicitly reads delta acceleration**, because the more raw curvature the sensor delivers, the more AAPS Exp's amplification mechanism has to work with while the more the aggressive smoothers have to dampen.

- **For users with frequent compression artefacts**: the UKF and Trio Savitzky-Golay both eliminate over 93% of SID-flagged structural-noise clusters on either sensor, vs the AAPS smoothers' approximately 70%. This is the strongest argument for either UKF or Trio SG over the AAPS smoothers regardless of sensor type.

## 8. Limitations

- **Within-user n = 3.** The paired comparison rests on three users with sequential G7→G6 transitions. The direction of the cohort effect is not unanimous (User_J reverses the trend). A cohort-level finding from n = 3 should be treated as suggestive, not conclusive.

- **G7 cohort is small.** 3 G7-only or G7-mostly users in the per-sensor analysis (E, L, plus the G7 era of D/I/J). Cohort-level G7 statistics are influenced by individual user characteristics.

- **No Libre 2/3 in the cohort.** All sensor data is Dexcom.

- **No direct measurement of Dexcom's on-sensor algorithm.** The "G7 has less internal smoothing than G6" interpretation rests on indirect evidence (step-rate at the broadcast layer, plus Dexcom's published characteristics). We do not have access to the on-sensor algorithm to verify this directly.

- **The deduplication pass is heuristic.** We dropped entries that paired by timestamp ±150s with identical sgv. This catches the dual-upload-path duplicates we directly verified but may miss subtler duplicates with slight timestamp drift. The audit note has details.

## 9. What would strengthen this finding

Three follow-ups would be valuable:

1. **Add the device field to the Phase 3 ingest pipeline.** The Phase 3 oref tables have no sensor information, so the larger 19-user cohort cannot be split by sensor without ingest changes. One column in the ingest SQL would close this gap.

2. **Look at additional within-user G6 → G7 transitions across more sources.** Three is small; ten would be enough to establish the cohort effect with confidence.

3. **Run the AID decision engine end-to-end with each smoother on the same per-sensor cohort.** This converts the offline characterisation into a clinical-impact estimate (does the loop dose differently? how often?).

## 10. Reproducibility

```
# Re-ingest Phase 2 with deduplication and per-entry classification
python3 -m backtest.cli.ingest_phase2_dedup --truncate

# Run all four smoothers per (user, sensor) pair
python3 -m backtest.cli.phase2_run --out runs/phase2

# Per-sensor metrics + within-user paired comparison
python3 -m backtest.cli.phase2_analysis --runs runs/phase2 --out reports/phase2
```

End-to-end wall time approximately 90 seconds. Outputs in `runs/phase2/` (per-(user, sensor, smoother) Parquet traces) and `reports/phase2/` (CSVs + figures).
