# Same-Sensor Dual-Upload Disagreement on Live Nightscout

*A characterisation of two cases where the same physical Dexcom G7 transmitter was uploaded to Nightscout via two distinct paths simultaneously, and what this means for sensor-tagged research datasets.*

---

## Abstract

Open-source automated insulin delivery (AID) systems often write CGM data to Nightscout for visualisation, sharing and downstream research. In some configurations a single physical Dexcom transmitter is bridged to Nightscout via two paths at once — for example, an xDrip+ collector and a Trio iOS uploader running on the same phone — producing duplicate `entries` rows for the same physical reading. For research that joins on `(user, timestamp)` and treats each row as an independent observation, this dual-path duplication can fabricate apparent within-sensor variability that does not exist in the underlying sensor data. This paper documents two such cases on a live Nightscout instance: User_D, who uploads the same G7 sensor via xDrip+ (no `device` string, raw `unfiltered`/`filtered` columns) and Trio iOS (`org.nightscout.K9BZJ3BP33.trio` device string); and User_L, whose same G7 sensor is uploaded via the Dexcom Share2 legacy bridge and a native Trio/Loop path that emits explicit `Dexcom G7 DXCM<id>` device strings. Pair-by-pair agreement between the two paths is 100 % exact for User_D (every paired pair of rows has identical `sgv`) and 99.90 % exact for User_L (small-amplitude differences attributable to clock jitter and a known Dexcom 39-vs-40 mg/dL low-end sentinel quirk). The headline conclusion is that pre-database deduplication of dual-uploaded entries is essential before any per-sensor or per-user CGM research; without it, the same physical reading can appear twice in a results aggregation, biasing within-user variance metrics by an amount that depends on what fraction of the user's record carries dual paths.

---

## 1. Introduction

A common configuration for closed-loop AID users is to run multiple CGM bridges on the same phone. xDrip+ is a popular open-source bridge that connects directly to Dexcom transmitters and exposes the resulting readings to Nightscout. Trio is a closed-loop iOS app that, in addition to closing the loop, can upload the readings it computes to Nightscout. Loop is another iOS closed-loop app with its own uploader. The Dexcom Share API also offers a path. A user running xDrip+ and Trio side-by-side will see *two* sets of CGM `entries` arrive at Nightscout for the same physical sensor reading — typically within a few seconds of each other.

For research that loads Nightscout `entries` and groups them by user and time, this duplication is a confound. Two rows for the same physical reading at nearby timestamps will:

* Inflate the per-user reading count (giving the user roughly twice as much weight in cohort statistics as they should have).
* Slightly perturb derived metrics (rate of change, smoother output) if the duplicates land in different 5-minute grid cells after rounding.
* Fabricate apparent within-user variability that does not exist in the underlying sensor data — because a non-zero `sgv` difference between the two upload paths' rounding/encoding choices can read as "noise" to a downstream variance metric.

This paper documents two specific cases on a live Nightscout instance to quantify the effect.

This deduplication work underpins the trustworthiness of the smoother comparisons in Papers 1–3 of this series. The per-sensor analysis in Paper 2 depends on sensor attribution being reliable: if the same physical reading appears under two device strings, it can be miscounted as G6 evidence, G7 evidence, or both, corrupting the per-sensor cohort. The cohort metrics in Paper 1 depend on each user's reading count being correct: a user with dual-upload paths and no dedup contributes roughly twice the statistical weight they should, biasing cohort medians and widening the apparent inter-user spread. Without the dedup protocol described here, neither the per-sensor comparison nor the per-user metric table can be trusted as a basis for the conclusion that the UKF is worth adding to AAPS and Trio.

## 2. Methods

### 2.1 Source

The two users analysed here come from the Phase 2 sensor-tagged cohort. The source data is per-user Nightscout `entries` JSON exported into `multi_user/data/site_*.json` from each user's own live AID-running Nightscout instance. We deliberately read the source JSON rather than a deduplicated database table so that both upload paths are visible.

### 2.2 Path classification

For User_D we classified each entry as either xDrip+ or Trio iOS based on Nightscout fields. xDrip+ entries have no `device` string and carry `filtered`/`unfiltered` columns from the Dexcom transmitter algorithm. Trio iOS entries carry a device string starting with `org.nightscout.<bundle-id>.trio`.

For User_L we classified each entry as either Dexcom Share2 or Dexcom-native based on the `device` string. Share2 entries have `device == "share2"`. Native entries have `device` strings of the form `Dexcom G7 DXCM<id>` (used by Trio and Loop's native G7 path).

For both users, we paired entries across paths whenever a row from path A and a row from path B fell within 150 seconds of each other (a tolerance generous enough to absorb the typical second-scale clock jitter between bridges but tight enough to ensure each pair refers to the same physical reading).

### 2.3 Pair statistics

For each (user, path-pair) we computed:

* `n_pairs` — number of paired entries.
* `n_unique_a`, `n_unique_b` — distinct entry counts on each side.
* Agreement at exact, ≤ 1, ≤ 5, ≤ 10 mg/dL tolerances on `sgv`.
* `median_abs_delta`, `mean_abs_delta`, `max_abs_delta` — absolute `sgv` difference statistics.
* `median_dt_s`, `mean_dt_s`, `max_dt_s` — timestamp jitter between the two paths in seconds.
* `n_a_only_39_b_40` and `n_b_only_39_a_40` — counts where one path reports the low-end sentinel as 39 and the other as 40 (a known Dexcom encoding quirk).
* `n_disagree_above_5` — number of pairs differing by > 5 mg/dL.

Reports written to `reports/upload_path/<user>_pairs.csv` and a per-user summary to `reports/upload_path/summary.csv`.

## 3. Results

### 3.1 User_D — xDrip+ vs Trio iOS

| Statistic | Value |
|---|---:|
| Paired entries | 4 875 |
| Unique entries on each side | 4 875 each |
| Exact `sgv` agreement | 100.00 % |
| Within ±1 / ±5 / ±10 mg/dL | 100.00 % / 100.00 % / 100.00 % |
| Median / mean `sgv` Δ | 0.0 / 0.00 mg/dL |
| Maximum `sgv` Δ | 0 mg/dL |
| Median / mean / max timestamp jitter | 0.0 / 0.0 / 0.0 s |
| Disagreements > 5 mg/dL | 0 |

User_D's two paths agree exactly on every paired entry. The `xDrip+` and `Trio iOS` rows for the same physical G7 reading carry the same `sgv` value, the same timestamp, and zero millisecond difference. This is consistent with both apps reading the same G7 transmitter algorithm output (Dexcom's smoothed value) and the upload paths simply re-publishing it. The duplication is therefore a faithful repetition: both rows describe the same reading, and the only research-relevant fact is that there are two of them rather than one.

### 3.2 User_L — Dexcom Share2 vs Dexcom-native

| Statistic | Value |
|---|---:|
| Paired entries | 51 856 |
| Unique entries (path A / path B) | 51 856 / 51 825 |
| Exact `sgv` agreement | 99.90 % |
| Within ±1 / ±5 / ±10 mg/dL | 99.94 % / 99.95 % / 99.96 % |
| Median / mean `sgv` Δ | 0.0 / 0.017 mg/dL |
| Maximum `sgv` Δ | 87 mg/dL |
| Median / mean / max timestamp jitter | 6.67 / 7.05 / 124.11 s |
| `n_a_only_39_b_40` (sentinel disagreement) | 21 |
| `n_b_only_39_a_40` | 0 |
| Disagreements > 5 mg/dL | 27 |

User_L's two paths agree exactly on 99.90 % of paired entries (51 802 of 51 856). Of the 54 disagreements:

* 21 are the Dexcom low-end sentinel quirk: the Share2 path reports `39` mg/dL where the native G7 path reports `40` mg/dL. Both indicate "below the LOW range"; this is an encoding choice by the bridge, not a different sensor reading.
* 6 differ by 1–5 mg/dL. These are pairs that fall within the ±5 mg/dL column in the table but not within the exact-agreement column; all have timestamp jitter in the 2–10 s range, consistent with minor rounding or encoding differences between upload paths.
* 27 differ by > 5 mg/dL. Inspecting the timestamps, these correspond to rows where the timestamp jitter is at the 60–120 s end of the range, suggesting the two paths captured the underlying sensor at slightly different sample times. The maximum `sgv` Δ of 87 mg/dL is one such row — a fast-rising glucose sampled 2 minutes apart at very different points in the trajectory.

The mean timestamp jitter is 7 seconds — well below the 5-minute grid spacing — so when the data is resampled onto a 5-minute grid both paths land in the same grid cell for 99.94 % of pairs.

*Figure 1. User_D upload-path diagnostic: paired-entry sgv-Δ histogram (top), timestamp-jitter histogram (middle), and per-time-of-day pair density (bottom). All-zero Δ across the entire 4 875-pair record.*

*Figure 2. User_L upload-path diagnostic: same panels, with the small-Δ tail (mostly 0–1 mg/dL), the 6.7-second-median timestamp jitter, and the 21 sentinel disagreements (39 vs 40) clustered at the low end.*

### 3.3 Implications for sensor-tagged research

User_L's record contains 51 856 paired entries. If the source Nightscout `entries` are loaded without dedup and used to compute "G7 noise reduction ratio for User_L", every pair of duplicate readings contributes both rows to the variance estimate. With 99.90 % exact agreement the contributed variance is small for User_L specifically, but the user weight in the cohort is doubled: User_L's 51 856 readings count as ≈ 100 000 readings if both paths are kept, distorting cohort averages.

Worse, in cases where the agreement is not 100 % (User_L's 27 disagreements above 5 mg/dL), a naive smoother run over the unioned stream — sorted by timestamp and processed reading-by-reading — would see the two paths' values for the same physical reading as a within-5-second oscillation, which is exactly the high-frequency noise the smoothers are trying to reject. The "noise reduction" metric would then misattribute the smoother's removal of the duplicate-induced oscillation to the smoother's intrinsic effect on real CGM noise.

For User_D the dedup is required only to correct the per-user weight; for User_L it is required both to correct the weight and to avoid the duplicate-induced oscillation artefact.

### 3.4 Pre-database dedup protocol

A pre-database deduplicated ingest keeps a single canonical row per `(user, rounded-timestamp)` pair according to a fixed precedence (Trio-iOS > Dexcom-native > xDrip+ > Share2 > unknown). After dedup, the User_D and User_L records contain 4 875 and 51 856 rows respectively (one row per physical reading), which is the correct per-user count for downstream sensor-tagged analysis.

## 4. Discussion

### 4.1 Why the agreement is so high

For User_D, the two upload paths read the same Dexcom G7 transmitter algorithm output. xDrip+ reads the unfiltered/filtered/sgv triplet from the transmitter's Bluetooth packets and writes them directly to Nightscout; Trio iOS does the same via the Dexcom SDK. Both paths see the same `sgv` value and republish it. There is no opportunity for the two paths to disagree on `sgv` because the Dexcom transmitter has already committed the value before either path reads it.

For User_L, the Share2 bridge reads from the cloud-side Dexcom Share API, which mirrors the values the transmitter published to Dexcom's servers. The native G7 path reads directly from the transmitter via Bluetooth. The two paths therefore observe the same underlying value, but with slightly different round-trip timing — the cloud-side path is delayed by Dexcom's server-side processing, and the values may have been processed by Dexcom's own server-side smoothing. The 6.7-second median jitter and the 21 sentinel disagreements are consistent with the two paths observing the same physical reading at slightly different processing stages.

### 4.2 What this means for in-the-wild Nightscout research

Researchers loading `entries` JSON from production Nightscout instances should:

1. **Identify upload paths explicitly** before joining or aggregating. The `device` field is the cleanest signal; absence of `device` plus presence of `filtered`/`unfiltered` columns indicates xDrip+; specific `org.nightscout.<bundle-id>.trio` strings indicate Trio iOS; `share2` indicates the Share API; explicit `Dexcom G6/G7 DXCM<id>` strings indicate native Bluetooth paths.
2. **Deduplicate at the (user, rounded-timestamp) level** before any per-user statistic is computed. The choice of precedence (which path wins when both are present) matters for sentinel-level encoding choices but not for the `sgv` value itself when paths agree.
3. **Quantify the dedup rate** per user: a user with a high dedup rate (many physical readings reported by two paths) contributes more weight to cohort statistics if not deduplicated.

Pre-database dedup, applied at ingest time, is the cleanest implementation; per-row dedup at query time risks recurring artefacts whenever the query path is not exhaustively reviewed.

### 4.3 Limitations

* Two users on one Nightscout instance is a very small sample. The 100 % agreement for User_D and 99.90 % for User_L are characteristic of these specific upload-path combinations; a different combination (e.g., Loop iOS vs xDrip+ on a G6) might show different agreement statistics.
* We did not test paths that involve calibration — for example, an xDrip+ instance that applies user calibration on top of the Dexcom value would disagree systematically with a Dexcom-native path that uses the factory calibration.
* The 150-second pairing tolerance was chosen pragmatically; a larger tolerance would pair more rows but at the cost of pairing readings across grid cells in fast-changing intervals, which would inflate the disagreement statistics by including comparisons of physically different sensor samples.

## 5. Conclusion

Two cases on a live Nightscout instance illustrate that same-sensor dual-upload duplication is real and substantial: User_D had 4 875 fully-paired entries with 100 % exact agreement; User_L had 51 856 paired entries with 99.90 % exact agreement and 27 disagreements above 5 mg/dL attributable to clock jitter and Dexcom sentinel encoding. For research that consumes Nightscout `entries` for per-user CGM analysis, pre-database deduplication on `(user, rounded-timestamp)` is essential — without it, per-user weights are inflated and, in the User_L case, duplicate-induced sub-grid oscillations can be misattributed to sensor noise. We recommend that anyone loading Nightscout `entries` for cohort research first identify the upload paths present in each user's record, then apply per-user dedup with an explicit precedence rule, then run downstream analysis on the deduplicated stream.
