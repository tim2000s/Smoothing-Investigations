# Smoother ranking (median ± IQR across cohort)


## xcorr_lag_min

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 0.013 | 0.006 | 0.020 |
| aaps_exponential | 1.940 | 1.681 | 2.035 |
| trio_sgolay | -0.002 | -0.004 | -0.001 |
| ukf | 0.078 | 0.045 | 0.107 |

## step_response_median_delay_min

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | -2.083 | -2.210 | -1.599 |
| aaps_exponential | 0.312 | 0.208 | 0.409 |
| trio_sgolay | -2.708 | -3.097 | -2.500 |
| ukf | -1.695 | -1.967 | -1.574 |

## phase_shift_delay_min

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | -0.013 | -0.021 | -0.006 |
| aaps_exponential | -1.712 | -1.838 | -1.606 |
| trio_sgolay | 0.004 | -0.000 | 0.007 |
| ukf | -0.019 | -0.030 | -0.017 |

## noise_reduction_ratio

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 0.705 | 0.667 | 0.804 |
| aaps_exponential | 0.840 | 0.767 | 0.888 |
| trio_sgolay | 0.446 | 0.396 | 0.563 |
| ukf | 0.632 | 0.574 | 0.729 |

## attenuation_signal_band

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 0.980 | 0.978 | 0.982 |
| aaps_exponential | 1.032 | 1.029 | 1.035 |
| trio_sgolay | 0.993 | 0.992 | 0.994 |
| ukf | 0.992 | 0.989 | 0.994 |

## hypo_preserved_pct

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 89.011 | 82.546 | 90.616 |
| aaps_exponential | 92.935 | 87.539 | 95.061 |
| trio_sgolay | 82.353 | 72.872 | 87.639 |
| ukf | 87.912 | 82.353 | 90.038 |

## hypo_amp_delta

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 1.333 | 0.902 | 1.333 |
| aaps_exponential | 0.000 | -0.148 | 0.000 |
| trio_sgolay | 1.000 | 1.000 | 1.500 |
| ukf | 1.319 | 1.001 | 1.576 |

## hypo_time_delta_min

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 0.000 | 0.000 | 0.000 |
| aaps_exponential | 0.000 | 0.000 | 5.000 |
| trio_sgolay | 0.000 | 0.000 | 0.000 |
| ukf | 0.000 | 0.000 | 0.000 |

## peak_preserved_pct

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 96.296 | 94.929 | 98.261 |
| aaps_exponential | 98.374 | 96.148 | 99.478 |
| trio_sgolay | 95.349 | 93.339 | 96.707 |
| ukf | 95.161 | 94.074 | 97.561 |

## outlier_absorbed_pct

| algorithm | median | p25 | p75 |
|---|---:|---:|---:|
| aaps_average | 25.806 | 14.080 | 46.982 |
| aaps_exponential | 29.730 | 20.254 | 48.936 |
| trio_sgolay | 93.182 | 90.839 | 100.000 |
| ukf | 45.714 | 33.681 | 63.859 |