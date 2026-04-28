"""Ingest Nightscout site_*.json into oref_phase2_sites with per-entry sensor labels.

Each entry's sensor_type is derived from the Nightscout `device` string (where
present) and a column-presence fallback (where the device string was scrubbed
during the Phase-2 export). Sensor IDs (e.g. DXCMlh) are extracted from device
strings of the form "Dexcom G7 DXCM<2chars>".

Site → user_id mapping mirrors `multi_user/full_analysis.py` so the labels match
the prior multi-user paper:
  User_A = Phase 1 single user (not loaded by this script)
  User_B = first non-empty site in alphabetical order
  User_C = second non-empty site
  ...
"""
from __future__ import annotations

import argparse
import json
import re
import string
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_batch

DEFAULT_DSN = "dbname=oref"
DEFAULT_DATA_DIR = Path("/Users/timstreet/SID-evaluation/multi_user/data")

DEXCOM_SERIAL_RE = re.compile(r"DXCM\w{2}", re.IGNORECASE)


def classify_sensor(device: str | None, columns: set[str]) -> tuple[str, str | None]:
    """Return (sensor_type, sensor_id_or_None) for a single entry.

    sensor_type ∈ {'G6', 'G7', 'Libre2', 'Dexcom_unspecified', 'unknown'}.
    """
    dev = (device or "").lower()
    sensor_id = None

    if "g7" in dev:
        m = DEXCOM_SERIAL_RE.search(device or "")
        if m:
            sensor_id = m.group(0)
        return "G7", sensor_id
    if "g6" in dev:
        m = DEXCOM_SERIAL_RE.search(device or "")
        if m:
            sensor_id = m.group(0)
        return "G6", sensor_id
    if "libre" in dev:
        return "Libre2", None
    if "share2" in dev:
        # Dexcom Share2 = G6 era
        return "G6", None
    if "dexcom" in dev:
        return "Dexcom_unspecified", None

    # Column-signature fallback: G6/xDrip path is the only one that consistently
    # populates both `unfiltered` and `filtered` per entry.
    if "unfiltered" in columns and "filtered" in columns:
        return "G6", None
    if "trendRate" in columns or "transmitterID" in columns or "sessionStartDate" in columns:
        return "G7", None

    return "unknown", None


def ingest_site(
    conn,
    site_path: Path,
    user_id: str,
    *,
    batch_size: int = 5000,
) -> dict:
    """Ingest one site, returning per-sensor counts for reporting."""
    entries = json.loads(site_path.read_text())
    if not entries:
        return {"user_id": user_id, "n_total": 0, "by_sensor": {}}

    # Establish per-entry columns set (used by the column-signature fallback)
    sample = [e for e in entries[:200] if isinstance(e, dict)]
    base_columns = sorted(set().union(*(e.keys() for e in sample))) if sample else []
    base_set = set(base_columns)

    # Determine per-user epoch (min `date` across all entries with a valid `date`)
    epochs = [int(e["date"]) for e in entries if isinstance(e, dict) and "date" in e and e["date"]]
    if not epochs:
        return {"user_id": user_id, "n_total": 0, "by_sensor": {}}
    user_epoch_ms = min(epochs)

    rows = []
    by_sensor: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict): continue
        if "date" not in e or not e["date"]: continue
        sgv = e.get("sgv")
        if sgv is None: continue
        ts_ms = int(e["date"])
        ts_rel_sec = (ts_ms - user_epoch_ms) // 1000
        device = e.get("device")
        # Per-entry column set — same as base for most files, but a few sites
        # have keys that vary across entries; favor the per-entry set.
        cols = set(e.keys()) | base_set
        sensor_type, sensor_id = classify_sensor(device, cols)
        by_sensor[sensor_type] = by_sensor.get(sensor_type, 0) + 1

        rows.append((
            user_id,
            int(ts_rel_sec),
            ts_ms,
            float(sgv),
            e.get("direction"),
            sensor_type,
            sensor_id,
            device,
            float(e["filtered"]) if "filtered" in e and e["filtered"] is not None else None,
            float(e["unfiltered"]) if "unfiltered" in e and e["unfiltered"] is not None else None,
            site_path.name,
        ))

    # Dedup on (user_id, ts_relative_sec) — keep last (Nightscout occasionally
    # produces multiple entries at the same timestamp due to backfill races).
    seen: dict[int, tuple] = {}
    for r in rows:
        seen[r[1]] = r
    deduped = list(seen.values())

    sql = """
    INSERT INTO oref_phase2_sites (
        user_id, ts_relative_sec, ts_utc_ms, cgm_mgdl, direction,
        sensor_type, sensor_id, raw_device, filtered, unfiltered, site_file
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (user_id, ts_relative_sec) DO UPDATE SET
        cgm_mgdl    = EXCLUDED.cgm_mgdl,
        direction   = EXCLUDED.direction,
        sensor_type = EXCLUDED.sensor_type,
        sensor_id   = EXCLUDED.sensor_id,
        raw_device  = EXCLUDED.raw_device,
        filtered    = EXCLUDED.filtered,
        unfiltered  = EXCLUDED.unfiltered;
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, deduped, page_size=batch_size)
    conn.commit()
    return {"user_id": user_id, "n_total": len(deduped), "by_sensor": by_sensor}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--dsn", default=DEFAULT_DSN)
    p.add_argument("--truncate", action="store_true",
                   help="Empty the table before ingest")
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)
    sites = sorted(data_dir.glob("site_*.json"))
    sites = [s for s in sites if s.stat().st_size > 100]

    conn = psycopg2.connect(args.dsn)
    if args.truncate:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE oref_phase2_sites;")
        conn.commit()
        print("Truncated oref_phase2_sites")

    print(f"\nIngesting {len(sites)} site files → oref_phase2_sites\n")
    li = 1
    grand_total = 0
    grand_by_sensor: dict[str, int] = {}
    for site in sites:
        user_id = f"User_{string.ascii_uppercase[li]}" if li < 26 else f"User_{li}"
        li += 1
        info = ingest_site(conn, site, user_id)
        grand_total += info["n_total"]
        for k, v in info["by_sensor"].items():
            grand_by_sensor[k] = grand_by_sensor.get(k, 0) + v
        sensor_str = ", ".join(f"{k}={v}" for k, v in sorted(info["by_sensor"].items()))
        print(f"  {user_id:6s} ← {site.name:14s}  {info['n_total']:>7,d} rows  [{sensor_str}]")

    print(f"\nTotal rows: {grand_total:,}")
    print("By sensor:")
    for k, v in sorted(grand_by_sensor.items(), key=lambda kv: -kv[1]):
        print(f"  {k:24s} {v:>10,d}  ({100*v/grand_total:.1f}%)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
