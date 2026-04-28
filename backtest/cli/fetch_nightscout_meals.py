"""Fetch Nightscout treatments tagged 'meal' or 'fast carbs' plus their CGM context.

Targets a Nightscout instance specified by --base-url (default `https://nstest3.crabdance.com`).
Pulls everything from --since (default `2026-01-01T00:00:00Z`) to --until (default now).

What it produces (under --out, default `data/nstest3/`):
  treatments_meals.json   — meal-tagged treatments matching notes ~ /meal|fast carbs/i
  entries.json            — every CGM entry (`type=sgv`) in the window
  fetch_meta.json         — base URL, time window, counts, fetch timestamp
  raw_treatments.json     — every treatment in the window (for audit; superset of treatments_meals)

Authentication: pass NS_API_SECRET (will be SHA1-hashed and sent as `api-secret` header)
or NS_API_TOKEN (sent as `Authorization: Bearer <token>`) via env. The default Nightscout
instance config uses API secret; this script tries unauth first and falls back to auth on 401.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import urllib.request
import urllib.error

DEFAULT_BASE_URL = "https://nstest3.crabdance.com"
DEFAULT_SINCE = "2026-01-01T00:00:00Z"
PAGE_SIZE = 1000


def _build_headers() -> dict:
    headers = {"accept": "application/json", "user-agent": "sid-eval/0.1"}
    if (token := os.environ.get("NS_API_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"
    elif (secret := os.environ.get("NS_API_SECRET")):
        headers["api-secret"] = hashlib.sha1(secret.encode("utf-8")).hexdigest()
    return headers


def _http_get(url: str, *, retries: int = 3, sleep_s: float = 1.5) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_build_headers())
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and attempt == 0:
                # If we got an auth failure, surface that immediately rather than retrying.
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        time.sleep(sleep_s * (attempt + 1))
    assert last_err is not None
    raise last_err


def fetch_paginated(base_url: str, endpoint: str, params: dict,
                    *, page_size: int = PAGE_SIZE,
                    sort_field: str = "created_at") -> list[dict]:
    """Walk a Nightscout collection backwards in time.

    Nightscout returns records in date-descending order by default. We page by
    moving the upper bound (`$lt`) past the oldest record returned in each
    batch. We stop when either: a partial page comes back (we've hit the lower
    bound), no records come back, or we've collected the entire window.
    """
    all_rows: list[dict] = []
    cur_params = dict(params)
    cur_params["count"] = page_size

    while True:
        url = f"{base_url.rstrip('/')}/api/v1/{endpoint}.json?{urlencode(cur_params, doseq=True)}"
        data = json.loads(_http_get(url))
        if not data:
            break
        all_rows.extend(data)
        if len(data) < page_size:
            break
        # data[-1] is the oldest record of this batch in default desc order.
        last = data[-1].get(sort_field)
        if last is None:
            # Fallback chain: try the alternate timestamp fields.
            last = data[-1].get("dateString") or data[-1].get("date")
        if last is None:
            break
        # Move the upper bound just past the oldest record we just got.
        if isinstance(last, (int, float)):
            cur_params[f"find[{sort_field}][$lt]"] = last
        else:
            cur_params[f"find[{sort_field}][$lt]"] = str(last)
        # Drop any pre-existing $lte so it doesn't conflict.
        cur_params.pop(f"find[{sort_field}][$lte]", None)
        if len(all_rows) > 500_000:
            print("  WARNING: bailing at 500k rows", file=sys.stderr)
            break
    return all_rows


def fetch_treatments(base_url: str, since_iso: str, until_iso: str | None) -> list[dict]:
    params = {
        "find[created_at][$gte]": since_iso,
    }
    if until_iso:
        params["find[created_at][$lte]"] = until_iso
    return fetch_paginated(base_url, "treatments", params, sort_field="created_at")


def fetch_entries(base_url: str, since_ms: int, until_ms: int | None) -> list[dict]:
    """Pull CGM entries (`type=sgv`) using the millisecond `date` field."""
    params = {
        "find[type]": "sgv",
        "find[date][$gte]": since_ms,
    }
    if until_ms:
        params["find[date][$lte]"] = until_ms
    return fetch_paginated(base_url, "entries", params, sort_field="date")


def filter_meal_treatments(treatments: list[dict]) -> list[dict]:
    out = []
    for t in treatments:
        notes = (t.get("notes") or "").lower()
        if "meal" in notes or "fast carbs" in notes:
            out.append(t)
    return out


def to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str) -> int:
    """Parse ISO-8601 to ms-since-epoch (UTC)."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return int(datetime.fromisoformat(s).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help="ISO-8601 lower bound (UTC). Default 2026-01-01T00:00:00Z")
    p.add_argument("--until", default=None,
                   help="ISO-8601 upper bound (UTC). Default = now")
    p.add_argument("--out", default="data/nstest3", help="Output directory")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    since_iso = args.since
    since_ms = from_iso(since_iso)
    until_iso = args.until
    until_ms = from_iso(until_iso) if until_iso else None

    print(f"Fetching from {args.base_url}")
    print(f"Window: {since_iso} → {until_iso or 'now'}")

    print("\n[1/2] Treatments…")
    treatments = fetch_treatments(args.base_url, since_iso, until_iso)
    print(f"  fetched {len(treatments):,} treatments")

    meal_treatments = filter_meal_treatments(treatments)
    print(f"  {len(meal_treatments):,} have notes ~ 'meal' or 'fast carbs'")

    print("\n[2/2] CGM entries…")
    entries = fetch_entries(args.base_url, since_ms, until_ms)
    print(f"  fetched {len(entries):,} entries")

    (out_dir / "raw_treatments.json").write_text(json.dumps(treatments, indent=2))
    (out_dir / "treatments_meals.json").write_text(json.dumps(meal_treatments, indent=2))
    (out_dir / "entries.json").write_text(json.dumps(entries, indent=2))

    meta = {
        "base_url": args.base_url,
        "since": since_iso,
        "until": until_iso,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_treatments_total": len(treatments),
        "n_treatments_meal_tagged": len(meal_treatments),
        "n_entries_sgv": len(entries),
    }
    (out_dir / "fetch_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nWrote {out_dir}/{{raw_treatments,treatments_meals,entries,fetch_meta}}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
