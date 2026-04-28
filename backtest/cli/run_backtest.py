"""Run all four smoothers on every cohort user, write per-(user, algorithm) traces."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from backtest import cohort, io, trace
from backtest.smoothers import ALL_SMOOTHERS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run smoother backtest pipeline")
    p.add_argument("--cohort", default="backtest/cohort.json", help="cohort.json path")
    p.add_argument("--refresh-cohort", action="store_true",
                   help="Re-select cohort from DB and rewrite cohort.json")
    p.add_argument("--tables", default="oref_v5,oref_v6,oref_v7",
                   help="Comma-separated list of source tables (when refreshing cohort)")
    p.add_argument("--days", type=int, default=90, help="Days per user to pull")
    p.add_argument("--out", default="runs", help="Output directory for parquets")
    p.add_argument("--users", default=None,
                   help="Comma-separated user_ids to limit to (smoke testing)")
    p.add_argument("--force", action="store_true",
                   help="Re-run even if Parquet is up-to-date with cohort hash")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    cohort_path = Path(args.cohort)
    if args.refresh_cohort or not cohort_path.exists():
        tables = tuple(args.tables.split(","))
        co = cohort.select_cohort(tables=tables)
        cohort.write_cohort(co, cohort_path)
        print(f"Selected {len(co)} cohort members → {cohort_path}", flush=True)
    else:
        co = cohort.read_cohort(cohort_path)
        print(f"Loaded {len(co)} cohort members from {cohort_path}", flush=True)

    if args.users:
        wanted = set(args.users.split(","))
        co = [m for m in co if m.user_id in wanted]
        print(f"Filtered to {len(co)} users: {[m.user_id for m in co]}", flush=True)

    cohort_hash_str = trace.cohort_hash(cohort_path)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_start = time.time()
    for member in co:
        print(f"\n=== {member.table}/{member.user_id} ===", flush=True)
        load_start = time.time()
        try:
            us = io.load_user(member.table, member.user_id, days=args.days)
        except Exception as e:
            print(f"  LOAD FAILED: {e}", flush=True)
            continue

        present = np.isfinite(us.glucose)
        g = us.glucose[present]
        ts = us.ts_sec[present]
        print(f"  loaded {us.n_raw} raw → {us.n_present} grid points "
              f"({us.n_present/us.n_grid:.1%}) in {time.time()-load_start:.1f}s",
              flush=True)

        for cls in ALL_SMOOTHERS:
            algo = cls.name
            if not args.force and trace.is_up_to_date(
                out_dir, user_id=member.user_id, algorithm=algo,
                cohort_hash_str=cohort_hash_str,
            ):
                print(f"  {algo:18s} SKIP (up-to-date)", flush=True)
                continue

            t0 = time.time()
            try:
                result = cls().process(g, ts, instrument=True)
            except Exception as e:
                print(f"  {algo:18s} FAILED: {e}", flush=True)
                continue

            trace_df = result.trace
            if trace_df.empty:
                print(f"  {algo:18s} EMPTY trace, skipping write", flush=True)
                continue

            path = trace.write_trace(
                trace_df, out_dir,
                user_id=member.user_id, table=member.table,
                algorithm=algo, cohort_hash_str=cohort_hash_str,
            )
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {algo:18s} {time.time()-t0:5.2f}s  rows={len(trace_df):>6d}  "
                  f"{size_mb:.2f} MB  → {path.relative_to(out_dir.parent)}",
                  flush=True)

    print(f"\nTotal wall time: {(time.time()-total_start)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
