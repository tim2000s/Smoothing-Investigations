"""Run all three smoothers in production-realistic online mode on every
cohort user, write per-(user, algorithm) traces.

Online mode = sliding window evaluation: at each chronological reading t the
smoother is asked what the AID would see at decision time t (leading-edge
output). This matches how AAPS calls the smoothing plugins in production.

Parallelised across users with ProcessPoolExecutor — one user is one
worker job, so we can saturate the 19-user cohort across many cores.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from backtest import cohort, io, trace
from backtest.smoothers import ALL_SMOOTHERS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run smoother backtest pipeline (online mode)")
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
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2),
                   help="Parallel workers for per-user processing")
    p.add_argument("--serial", action="store_true",
                   help="Disable parallelism (useful for debugging)")
    return p.parse_args(argv)


def _process_user(
    table: str,
    user_id: str,
    days: int,
    out_dir: str,
    cohort_hash_str: str,
    force: bool,
) -> dict:
    """Worker: load one user, run all three smoothers in online mode, write
    per-algo Parquet traces, return a per-algo timing dict."""
    out = Path(out_dir)
    timings: dict = {"user_id": user_id, "table": table, "algos": {}}
    t_load = time.time()
    try:
        us = io.load_user(table, user_id, days=days)
    except Exception as e:
        timings["load_error"] = repr(e)
        return timings

    present = np.isfinite(us.glucose)
    g = us.glucose[present]
    ts = us.ts_sec[present]
    timings["load_s"] = time.time() - t_load
    timings["n_present"] = int(us.n_present)
    timings["n_grid"] = int(us.n_grid)

    for cls in ALL_SMOOTHERS:
        algo = cls.name
        if not force and trace.is_up_to_date(
            out, user_id=user_id, algorithm=algo,
            cohort_hash_str=cohort_hash_str,
        ):
            timings["algos"][algo] = {"status": "skip"}
            continue

        t0 = time.time()
        smoother = cls()
        try:
            if hasattr(smoother, "online_process"):
                result = smoother.online_process(g, ts, instrument=True)
            else:
                result = smoother.process(g, ts, instrument=True)
        except Exception as e:
            timings["algos"][algo] = {"status": "error", "error": repr(e)}
            continue

        trace_df = result.trace
        if trace_df is None or trace_df.empty:
            timings["algos"][algo] = {"status": "empty"}
            continue

        path = trace.write_trace(
            trace_df, out,
            user_id=user_id, table=table,
            algorithm=algo, cohort_hash_str=cohort_hash_str,
        )
        timings["algos"][algo] = {
            "status": "ok",
            "rows": int(len(trace_df)),
            "elapsed_s": time.time() - t0,
            "path": str(path),
        }
    return timings


def _print_user_summary(timings: dict) -> None:
    user_id = timings["user_id"]
    table = timings["table"]
    if "load_error" in timings:
        print(f"  {table}/{user_id}  LOAD FAILED: {timings['load_error']}", flush=True)
        return
    print(
        f"  {table}/{user_id}  loaded {timings['n_present']} pts in "
        f"{timings['load_s']:.1f}s",
        flush=True,
    )
    for algo, info in timings["algos"].items():
        st = info["status"]
        if st == "skip":
            print(f"    {algo:18s} SKIP", flush=True)
        elif st == "error":
            print(f"    {algo:18s} FAILED: {info['error']}", flush=True)
        elif st == "empty":
            print(f"    {algo:18s} EMPTY", flush=True)
        else:
            print(
                f"    {algo:18s} {info['elapsed_s']:6.2f}s  rows={info['rows']:>6d}",
                flush=True,
            )


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

    if args.serial or args.workers <= 1 or len(co) <= 1:
        print(f"\n=== Running {len(co)} users serially ===\n", flush=True)
        for member in co:
            timings = _process_user(
                member.table, member.user_id,
                args.days, str(out_dir), cohort_hash_str, args.force,
            )
            _print_user_summary(timings)
    else:
        n_workers = min(args.workers, len(co))
        print(
            f"\n=== Running {len(co)} users across {n_workers} workers ===\n",
            flush=True,
        )
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futures = {
                ex.submit(
                    _process_user,
                    m.table, m.user_id,
                    args.days, str(out_dir), cohort_hash_str, args.force,
                ): m
                for m in co
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                try:
                    timings = fut.result()
                except Exception as e:
                    m = futures[fut]
                    print(f"  {m.table}/{m.user_id}  WORKER CRASH: {e!r}", flush=True)
                    continue
                _print_user_summary(timings)
                print(f"  ({done}/{len(co)} done)", flush=True)

    print(f"\nTotal wall time: {(time.time()-total_start)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
