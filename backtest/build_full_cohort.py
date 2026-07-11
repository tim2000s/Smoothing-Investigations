"""Build a FULL cohort manifest: every user across oref_v5/v6/v7, no
density/cadence/span filtering and no per-table top-N cap.

This is the "all users, full history" manifest used to run the smoother
backtest over the entire TimescaleDB SGV set rather than the curated
19-user density cohort in cohort.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from backtest import db
from backtest.cohort import EXPECTED_5MIN_PER_DAY

TABLES = ("oref_v5", "oref_v6", "oref_v7")
WINDOW_DAYS = 90  # only used to compute the (informational) density field
OUT = Path("backtest/cohort_full.json")
MIN_ROWS = 288  # skip degenerate users with < ~1 day of readings


def main() -> int:
    members = []
    per_table = {}
    for table in TABLES:
        users = db.list_users(table)
        expected = WINDOW_DAYS * EXPECTED_5MIN_PER_DAY
        kept = 0
        for row in users.itertuples(index=False):
            rows = int(row.rows)
            if rows < MIN_ROWS:
                continue
            density = min(rows, expected) / expected
            mode_gap = int(row.mode_gap_s) if row.mode_gap_s == row.mode_gap_s else 0
            members.append({
                "table": table,
                "user_id": str(row.user_id),
                "rows": rows,
                "span_days": float(row.span_days),
                "mode_gap_s": mode_gap,
                "density": float(density),
            })
            kept += 1
        per_table[table] = (len(users), kept)

    payload = {"schema_version": 1, "members": members}
    OUT.write_text(json.dumps(payload, indent=2))
    total = len(members)
    print(f"Wrote {total} members -> {OUT}")
    for t, (n_all, n_kept) in per_table.items():
        print(f"  {t}: {n_kept}/{n_all} users kept (>= {MIN_ROWS} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
