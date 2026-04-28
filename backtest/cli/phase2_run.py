"""Phase 2 backtest — sensor-identified data only.

For each (user, sensor_type) where we have an explicit Dexcom G6 or G7 label,
load the readings, resample to a strict 5-min grid, run all 4 smoothers,
and write per-(user, sensor, algorithm) Parquet traces.

Excludes rows where sensor_type ∈ {'Dexcom_unspecified', 'unknown'}.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

from backtest import io as bt_io, trace as bt_trace
from backtest.smoothers import ALL_SMOOTHERS

DEFAULT_DSN = "dbname=oref"
SENSORS_KEPT = ("G6", "G7", "Libre2")
MIN_DAYS = 14   # require ≥14 days within a (user, sensor) segment to be useful
MIN_DENSITY = 0.50


def list_user_sensor_pairs(dsn: str, *, table: str = "oref_phase2_sites_v2") -> pd.DataFrame:
    sql = f"""
    SELECT user_id, sensor_type,
           COUNT(*) AS rows,
           MIN(ts_utc_ms) AS first_ms,
           MAX(ts_utc_ms) AS last_ms
    FROM {table}
    WHERE sensor_type = ANY(%(s)s)
    GROUP BY user_id, sensor_type
    ORDER BY user_id, MIN(ts_utc_ms);
    """
    with psycopg2.connect(dsn) as conn:
        df = pd.read_sql(sql, conn, params={"s": list(SENSORS_KEPT)})
    df["span_days"] = (df["last_ms"] - df["first_ms"]) / 86_400_000.0
    return df


def fetch_user_sensor_window(
    user_id: str, sensor_type: str, *, dsn: str = DEFAULT_DSN,
    table: str = "oref_phase2_sites_v2",
) -> pd.DataFrame:
    sql = f"""
    SELECT user_id, sensor_type, ts_utc_ms, cgm_mgdl, direction
    FROM {table}
    WHERE user_id = %(u)s AND sensor_type = %(s)s
    ORDER BY ts_utc_ms
    """
    with psycopg2.connect(dsn) as conn:
        return pd.read_sql(sql, conn, params={"u": user_id, "s": sensor_type})


def to_grid(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Resample (ts_utc_ms, cgm_mgdl) onto a 5-minute grid relative to first reading.

    Returns (ts_sec_relative, glucose) with NaN where readings are missing
    beyond the 1-step forward-fill limit. Mirrors backtest.io.load_user.
    """
    if df.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    df = df.dropna(subset=["cgm_mgdl"]).copy()
    df["ts_s"] = (df["ts_utc_ms"] // 1000).astype("int64")
    t0 = int(df["ts_s"].iloc[0])
    df["ts_grid"] = ((df["ts_s"] - t0 + bt_io.GRID_S // 2) // bt_io.GRID_S) * bt_io.GRID_S + t0
    df = df.drop_duplicates(subset="ts_grid", keep="last")
    grid_end = int(df["ts_s"].iloc[-1]) + bt_io.GRID_S
    grid = np.arange(t0, grid_end, bt_io.GRID_S, dtype="int64")
    g = pd.DataFrame({"ts_grid": grid}).merge(
        df[["ts_grid", "cgm_mgdl"]], on="ts_grid", how="left"
    )
    raw_idx = g["cgm_mgdl"].notna().to_numpy()
    glucose = np.asarray(g["cgm_mgdl"].ffill(limit=bt_io.FORWARD_FILL_LIMIT)
                         .to_numpy(dtype="float64")).copy()
    if raw_idx.any():
        gap_mask = bt_io._step_gap_mask(raw_idx)
        glucose[gap_mask] = np.nan
    ts_rel = g["ts_grid"].to_numpy(dtype="int64") - t0
    return ts_rel, glucose


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="runs/phase2", help="Output Parquet directory")
    p.add_argument("--dsn", default=DEFAULT_DSN)
    args = p.parse_args(argv)

    pairs = list_user_sensor_pairs(args.dsn)
    pairs = pairs[pairs.span_days >= MIN_DAYS]
    print(f"\n{len(pairs)} (user, sensor) pairs with ≥{MIN_DAYS}d span:\n")
    print(pairs[["user_id", "sensor_type", "rows", "span_days"]].to_string(index=False))

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    cohort_hash_str = "phase2_v1"  # version tag used for idempotency keying

    total_start = time.time()
    for row in pairs.itertuples(index=False):
        user_id = row.user_id
        sensor = row.sensor_type
        tag = f"{user_id}__{sensor}"
        print(f"\n=== {tag} ===", flush=True)
        df = fetch_user_sensor_window(user_id, sensor)
        ts_rel, glucose = to_grid(df)
        if len(glucose) == 0 or not np.isfinite(glucose).any():
            print("  empty, skipping", flush=True)
            continue
        present = np.isfinite(glucose)
        density = float(present.mean())
        n_present = int(present.sum())
        print(f"  raw rows={len(df):,}  grid={len(glucose):,}  present={n_present:,} "
              f"({density:.1%})", flush=True)
        if density < MIN_DENSITY:
            print(f"  density {density:.1%} < {MIN_DENSITY:.0%}, skipping", flush=True)
            continue

        g_in = glucose[present]
        ts_in = ts_rel[present]

        for cls in ALL_SMOOTHERS:
            algo = cls.name
            t0 = time.time()
            res = cls().process(g_in, ts_in, instrument=True)
            trace = res.trace
            if trace.empty:
                print(f"  {algo:18s} EMPTY", flush=True)
                continue
            # tag the trace with sensor info
            trace = trace.copy()
            trace.insert(0, "sensor_type", sensor)
            path = bt_trace.write_trace(
                trace, out_root, user_id=tag, table=f"phase2_{sensor}",
                algorithm=algo, cohort_hash_str=cohort_hash_str,
            )
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {algo:18s} {time.time()-t0:5.2f}s  rows={len(trace):>6,d}  "
                  f"{size_mb:.2f} MB → {path.relative_to(out_root.parent)}",
                  flush=True)

    print(f"\nTotal wall time: {(time.time() - total_start)/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
