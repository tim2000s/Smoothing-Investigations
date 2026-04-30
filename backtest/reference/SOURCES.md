# Vendored upstream sources

Each file in this directory is a verbatim copy of the upstream production source. The Kotlin/Swift drivers below compile these directly and emit JSON fixtures consumed by `backtest/tests/test_*_reference.py`. Re-run the drivers when an upstream file is updated.

| File | Upstream | Pinned commit | Used by |
|---|---|---|---|
| `AvgSmoothingPlugin.kt` | `nightscout/AndroidAPS` — `plugins/aps/src/main/java/app/aaps/plugins/aps/openAPS/utils/cgm/AvgSmoothingPlugin.kt` | TBD | `tests/test_aaps_average.py` |
| `ExponentialSmoothingPlugin.kt` | `nightscout/AndroidAPS` — `plugins/aps/src/main/java/app/aaps/plugins/aps/openAPS/utils/cgm/ExponentialSmoothingPlugin.kt` | TBD | `tests/test_aaps_exponential.py` |
| `UnscentedKalmanFilterPlugin.kt` | local — copied from `/Users/timstreet/SID-evaluation/UnscentedKalmanFilterPlugin.kt` | local 2026-04-26 | `tests/test_ukf_reference.py` |

## Driver workflow

```
cd kotlin_driver && gradle run --args="../../tests/fixtures/inputs.json ../../tests/fixtures/kotlin/"
```

Each driver reads the same `inputs.json` (3 fixture series: synthetic step, sinusoid, real 24-h slice) and writes one `<algorithm>.json` per smoother containing per-reading output and intermediate state.
