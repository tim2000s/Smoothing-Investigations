# Upload-path effects on Nightscout-uploaded CGM streams: a natural-experiment view

*A side-study of two Phase 2 cohort users (User_D and User_L) whose Dexcom G7 sensor data was uploaded to Nightscout via two distinct upload paths simultaneously, providing a clean natural experiment for measuring upload-path effects on the data layer.*

---

## 1. Background

The Phase 2 sub-analysis of the SID smoother evaluation used Nightscout source data carrying per-entry device strings to classify entries by sensor type (G6 vs G7). During the audit on 28 April we discovered that two users in the cohort had what initially looked like a within-user G6/G7 sensor mix, but on closer inspection turned out to be **the same physical Dexcom G7 sensor uploaded to Nightscout via two distinct paths simultaneously**. The duplicate-stream phenomenon was the cause of an erroneous "within-user G6→G7 transition" claim in the originally-published per-sensor paper, which has since been corrected.

The duplicate streams are themselves an interesting natural experiment. They allow us to ask: **when the same physical sensor signal is uploaded to Nightscout by two different upload paths, how do the resulting Nightscout entries differ?** Most users have only one upload path, so this question normally cannot be answered from real data. User_D and User_L's accidental dual upload gives us a direct measurement.

This paper documents:
- The two pairs of upload paths involved (xDrip+ vs Trio iOS for User_D; Dexcom Share2 legacy vs Dexcom G7 native API for User_L);
- The agreement rate between paired entries;
- The characteristic differences where they disagree (sentinel encoding, compression-event capture, timestamp jitter);
- Implications for downstream analysis pipelines and AID systems.

## 2. The two natural experiments

**User_D (site_04 in the Phase 2 source data).** Two upload paths active concurrently for ~60 days (Dec 2025 – Feb 2026):
- **Path A (xDrip+)**: Nightscout entries with no device string, but with `unfiltered` and `filtered` columns characteristic of xDrip+'s data layer for Dexcom G6/G7. 23,720 entries in this period.
- **Path B (Trio iOS)**: Nightscout entries with device string `org.nightscout.K9BZJ3BP33.trio` (the Trio iOS app's bundle identifier). 5,141 entries in this period.

**User_L (site_19).** Two upload paths active concurrently for the entire 182-day data window:
- **Path A (Dexcom Share2)**: Nightscout entries with device string `share2`, indicating they came via the Dexcom Share2 legacy API. 51,949 entries.
- **Path B (Dexcom G7 native)**: Nightscout entries with explicit `Dexcom G7 DXCM<id>` device strings, indicating native Dexcom G7 API uploads via Trio or Loop. 51,911 entries across 21 distinct G7 sensor sessions.

For each user, we paired Path A entries with Path B entries by timestamp (±150 seconds tolerance) and measured the agreement.

## 3. Findings

### 3.1 User_D (xDrip+ vs Trio iOS)

**Table 1.** User_D paired-entry summary.

| metric | value |
|---|---|
| pairs found | 4,875 |
| exact agreement (Δsgv = 0) | **100.0%** |
| within ±1 mg/dL | 100.0% |
| within ±5 mg/dL | 100.0% |
| within ±10 mg/dL | 100.0% |
| median |Δsgv| | 0.0 mg/dL |
| max |Δsgv| | 0 mg/dL |
| median timestamp gap | 0.0 s |
| max timestamp gap | 0.0 s |
| 39 vs 40 sentinel pairs | 0 |
| disagreements >5 mg/dL | 0 |

User_D's xDrip+ and Trio iOS paths emit **bit-identical sgv values at bit-identical timestamps** for all 4,875 paired entries. There is zero divergence at the data layer.

The interpretation is that both paths are reading from the same upstream data source — most likely a single Dexcom G7 transmitter — and applying no processing of their own beyond passing the value through. Trio iOS adds the device-string identifier; xDrip+ does not. Otherwise they are indistinguishable.

### 3.2 User_L (Dexcom Share2 vs Dexcom G7 native)

**Table 2.** User_L paired-entry summary.

| metric | value |
|---|---|
| pairs found | 51,856 |
| exact agreement (Δsgv = 0) | **99.9%** (51,802 pairs) |
| within ±1 mg/dL | 99.94% |
| within ±5 mg/dL | 99.95% |
| within ±10 mg/dL | 99.96% |
| median |Δsgv| | 0.0 mg/dL |
| mean |Δsgv| | 0.02 mg/dL |
| max |Δsgv| | 87 mg/dL |
| median timestamp gap | 7.0 s |
| max timestamp gap | 124 s |
| 39 vs 40 sentinel pairs | 21 |
| disagreements >5 mg/dL | 27 |

User_L's two paths agree on 99.9% of entries. The 0.1% that disagree fall into two categories:

**Category 1 — Sentinel-encoding differences (21 pairs).** Both Dexcom APIs encode "below sensible range" as a sentinel value, but with slightly different conventions:
- Share2 emits `39` mg/dL
- G7 native emits `40` mg/dL

These pairs are not real glucose differences — they are conventions for marking the same "below 40" condition.

**Category 2 — Compression and warmup events (27 pairs).** These pairs disagree by 5 mg/dL or more (max 87 mg/dL). Sample patterns from the trace:
- `2024-10-13 02:42  share2=122  G7=89  Δ=33` — a brief compression-low event captured at different magnitudes by the two APIs
- `2024-10-21 00:47  share2=215  G7=276  Δ=61` — a sharp rise where one path captured a higher peak than the other
- `2024-11-12 20:53  share2=153  G7=116  Δ=37` — another transient with different captured values

These look consistent with one path filtering or attenuating brief transients more than the other, possibly because the Share2 legacy API applies a small smoothing window before serving values that the native G7 API does not. The phenomenon is rare (27 events in 51,856 pairs) but real — it appears at moments when the underlying signal is moving fast.

**Timestamp jitter.** The two paths emit values at consistently offset timestamps — Share2's entries arrive a median of 7 seconds after the corresponding G7 native entry. This is a minor and consistent latency offset, almost certainly a function of the difference in API polling cadence between the two paths.

## 4. Implications

### 4.1 For data-pipeline correctness

The discovery that two upload paths can produce essentially the same data with different device-string labels is a real source of double-counting risk for any analysis pipeline that:
- Does not deduplicate at the timestamp + sgv level
- Treats device-string variations as evidence of different sensor models
- Aggregates across users without checking for dual-upload artefacts

Our original Phase 2 ingest fell into both of the second and third pitfalls — it took the device strings at face value and partitioned User_L's data into "G6 (share2)" and "G7" buckets that turned out to be the same readings. The corrected ingest deduplicates at the timestamp + sgv level; this paper documents what the corrected pipeline catches.

A defensive data-pipeline pattern is: **for every (user, timestamp_round_to_grid) combination, keep at most one entry, preferring the one with the most-specific device string**. That collapses the duplicate-upload artefact deterministically.

### 4.2 For sensor-comparison studies

Anyone doing per-sensor comparisons on Nightscout-derived data needs to verify that "G6" and "G7" entries within a user are actually different physical sensors, not the same sensor with different upload labels. The diagnostic is straightforward: pair entries by timestamp, check sgv agreement. If agreement is high (>90%) and timestamp gap is short (<30 seconds), the two streams are almost certainly one source; comparing smoother behaviour on them measures upload-path effects, not sensor effects.

In our cohort the verification revealed that two of three apparent within-user G7→G6 transitions were upload-path artefacts. The corrected per-sensor paper relies only on the one transition that is genuine plus paired G6/G7 cohort comparisons.

### 4.3 For closed-loop AID design

For a user running a closed-loop AID system that consumes Nightscout-relayed CGM data, dual upload of the same sensor through two paths is essentially harmless if the loop deduplicates by timestamp. It becomes a problem if:
- The loop reads only one path and that path lags or filters the other (Share2's 7-second delay is small but non-zero)
- The two paths disagree at compression-event boundaries, and the loop happens to read the "wrong" path at a critical moment

The latter is rare in our data (~0.05% of paired entries disagree by >5 mg/dL) but is plausible for users on noisy sensor sessions. A practical recommendation: **use the most direct upload path you can** (Dexcom G7 native API > Share2 legacy > intermediated paths) and prefer device strings that explicitly identify the sensor model.

## 5. Limitations

- **Two users, two pairings.** This is a sample of two natural experiments — User_D's xDrip+ vs Trio bundle and User_L's Share2 vs G7 native. Other upload-path combinations (Dexcom mobile app vs xDrip+, Loop vs Tidepool, etc.) are not represented.
- **Same-sensor assumption.** We infer that User_L's two streams come from the same physical sensor based on near-identical sgv values across 51,856 paired entries. This is overwhelmingly likely but cannot be ruled out as some other coincidence. The same applies to User_D.
- **The 87 mg/dL outlier in User_L.** A single pair where the two paths disagree by 87 mg/dL is striking. We did not investigate whether this represents a real sensor anomaly captured differently by the two APIs, a transmission corruption in one path, or something else. A spot-check of the source JSON entry would clarify.

## 6. Reproducibility

```
python3 -m backtest.cli.upload_path_study --out reports/upload_path
```

Reads from the original `multi_user/data/site_*.json` files (not the deduplicated database table — that intentionally collapses the duplicates and would not preserve both paths). Outputs:
- `reports/upload_path/User_D_pairs.csv` and `User_L_pairs.csv` — full per-pair detail
- `reports/upload_path/summary.csv` — the agreement statistics in this paper
- `reports/upload_path/figs/User_*_upload_path_diagnostic.png` — Δsgv distribution, timestamp jitter, by-glucose-range and by-hour-of-day disagreement rates

End-to-end wall time approximately 5 seconds.
