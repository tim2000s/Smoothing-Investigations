"""Phase 2 re-ingest with explicit per-timestamp deduplication.

The original ingest accepted every Nightscout entry and classified it by device
string + column signature. The audit (28 Apr) showed that several users have the
same physical sensor data uploaded via two paths simultaneously, with the
classifier assigning different sensor labels to the two streams. To fix the
sensor partition we deduplicate at the timestamp + sgv level, keeping only the
entry with the most-informative device string.

Specificity ranking (lower = more specific, kept):
  0  Dexcom G7 DXCM<id>      (explicit sensor + serial)
  1  G6 Native, G7, Libre2   (explicit sensor model)
  2  org.nightscout.*.trio   (explicit AID stack — sensor inferable from columns)
  3  share2, dexcom-*        (explicit upload API — sensor not in marker)
  4  go-away-please          (scrubbed but device field present)
  5  <missing> / absent      (no device string at all)

Within a 150-second window of an existing kept entry with matching sgv, drop
the new entry. Across a longer window or with differing sgv, keep both.

Output goes to a new table `oref_phase2_sites_v2` so the original ingest is
preserved for the upload-path study.
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
PAIR_TOL_MS = 150_000
DEXCOM_SERIAL_RE = re.compile(r"DXCM\w{2}", re.IGNORECASE)


def specificity_rank(device: str | None) -> int:
    """Lower rank = more specific. Used for tie-breaking duplicates."""
    if not device:
        return 5
    d = device.lower()
    if "dxcm" in d:
        return 0
    if "g7" in d or ("g6" in d and "native" in d) or "libre" in d:
        return 1
    if "org.nightscout" in d or "trio" in d:
        return 2
    if "share2" in d or "dexcom" in d:
        return 3
    if "go-away" in d or "dont-own" in d:
        return 4
    return 5


def classify_sensor(device: str | None, columns: set[str]) -> tuple[str, str | None]:
    """Map device + column signature to (sensor_type, sensor_id).

    Conservative: returns 'unknown' if there is no explicit sensor evidence.
    Specifically `share2` and `org.nightscout.*` are NOT mapped to any sensor by
    themselves — they are upload markers, not sensor markers — and require
    column-signature corroboration.
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
    # `share2` / `org.nightscout.*.trio` / `dexcom-dont-own-my-data` carry no
    # sensor information by themselves — fall through to the column signature
    # to disambiguate.
    if "trendrate" in [c.lower() for c in columns] or "transmitterid" in [c.lower() for c in columns] \
       or "sessionstartdate" in [c.lower() for c in columns]:
        return "G7", None
    if "unfiltered" in columns and "filtered" in columns:
        return "G6", None
    if "unfiltered" in columns or "filtered" in columns:
        # one but not both — partial G6 signal, treat as unknown
        return "unknown", None
    return "unknown", None


def ingest_site_dedup(conn, site_path: Path, user_id: str, *, batch_size: int = 5000) -> dict:
    entries = json.loads(site_path.read_text())
    if not entries:
        return {"user_id": user_id, "n_after_dedup": 0, "n_dropped_dup": 0, "by_sensor": {}}

    # Per-entry column set; base set fallback for sparse entries.
    sample = [e for e in entries[:200] if isinstance(e, dict)]
    base_set = set().union(*(e.keys() for e in sample))

    # First pass: collect all valid entries with timestamp + sgv + device + per-entry cols
    raw = []
    for e in entries:
        if not isinstance(e, dict): continue
        if "date" not in e or not e["date"]: continue
        sgv = e.get("sgv")
        if sgv is None: continue
        ts_ms = int(e["date"])
        device = e.get("device")
        # Use per-entry columns ONLY — the previous version unioned with
        # base_set, which propagated G7 markers from G7-era entries to
        # G6-era entries within the same site.
        per_entry_cols = set(e.keys())
        rank = specificity_rank(device)
        raw.append({
            "ts_ms": ts_ms, "sgv": float(sgv), "device": device, "rank": rank,
            "direction": e.get("direction"),
            "filtered": float(e["filtered"]) if e.get("filtered") is not None else None,
            "unfiltered": float(e["unfiltered"]) if e.get("unfiltered") is not None else None,
            "per_entry_cols": per_entry_cols,
        })

    # Sort by ts_ms ascending
    raw.sort(key=lambda r: r["ts_ms"])

    # Dedup pass: walk through entries; for each, check whether any earlier
    # KEPT entry within ±150 ms has matching sgv. If yes, treat as duplicate;
    # keep whichever has lower (more-specific) rank.
    kept: list[dict] = []
    n_dropped = 0
    last_idx = 0  # leftmost still-relevant kept index
    for r in raw:
        # advance last_idx to first kept whose ts is within window
        while last_idx < len(kept) and kept[last_idx]["ts_ms"] < r["ts_ms"] - PAIR_TOL_MS:
            last_idx += 1
        # check candidates
        dup_idx = None
        for k in range(last_idx, len(kept)):
            if kept[k]["ts_ms"] > r["ts_ms"] + PAIR_TOL_MS:
                break
            if kept[k]["sgv"] == r["sgv"]:
                dup_idx = k
                break
        if dup_idx is None:
            kept.append(r)
        else:
            # both candidates exist for the same physical reading; keep the more specific one
            if r["rank"] < kept[dup_idx]["rank"]:
                kept[dup_idx] = r
            n_dropped += 1

    # Now classify each kept entry, find per-user epoch, build rows
    user_epoch_ms = min(k["ts_ms"] for k in kept) if kept else 0
    by_sensor: dict[str, int] = {}
    rows = []
    for r in kept:
        sensor_type, sensor_id = classify_sensor(r["device"], r["per_entry_cols"])
        by_sensor[sensor_type] = by_sensor.get(sensor_type, 0) + 1
        rows.append((
            user_id,
            int((r["ts_ms"] - user_epoch_ms) // 1000),
            r["ts_ms"],
            r["sgv"],
            r["direction"],
            sensor_type,
            sensor_id,
            r["device"],
            r["filtered"],
            r["unfiltered"],
            site_path.name,
        ))

    sql = """
    INSERT INTO oref_phase2_sites_v2 (
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
        execute_batch(cur, sql, rows, page_size=batch_size)
    conn.commit()
    return {
        "user_id": user_id,
        "n_after_dedup": len(rows),
        "n_dropped_dup": n_dropped,
        "by_sensor": by_sensor,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--dsn", default=DEFAULT_DSN)
    p.add_argument("--truncate", action="store_true")
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)
    sites = sorted(s for s in data_dir.glob("site_*.json") if s.stat().st_size > 100)

    conn = psycopg2.connect(args.dsn)
    if args.truncate:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE oref_phase2_sites_v2;")
        conn.commit()
        print("Truncated oref_phase2_sites_v2")

    print(f"\nDedup-ingesting {len(sites)} site files → oref_phase2_sites_v2\n")
    li = 1
    grand_total = 0
    grand_dropped = 0
    for site in sites:
        user_id = f"User_{string.ascii_uppercase[li]}" if li < 26 else f"User_{li}"
        li += 1
        info = ingest_site_dedup(conn, site, user_id)
        grand_total += info["n_after_dedup"]
        grand_dropped += info["n_dropped_dup"]
        sensor_str = ", ".join(f"{k}={v}" for k, v in sorted(info["by_sensor"].items()))
        print(f"  {user_id:6s} ← {site.name:14s}  kept={info['n_after_dedup']:>7,d}  "
              f"dropped_dup={info['n_dropped_dup']:>6,d}  [{sensor_str}]")

    print(f"\nTotal kept: {grand_total:,}  total dropped as duplicate: {grand_dropped:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
