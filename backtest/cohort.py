"""Density/cadence-based cohort selection across oref_v5/v6/v7."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from . import db

EXPECTED_5MIN_PER_DAY = 288  # 86400 / 300
DEFAULT_DENSITY_MIN = 0.70
DEFAULT_CADENCE_RANGE = (299, 301)  # seconds
DEFAULT_MIN_SPAN_DAYS = 90

DEFAULT_PER_TABLE_N = {
    "oref_v5": 3,
    "oref_v6": 5,
    "oref_v7": 11,
}


@dataclass(frozen=True)
class CohortMember:
    table: str
    user_id: str
    rows: int
    span_days: float
    mode_gap_s: int
    density: float  # rows / expected_5min_rows_in_first_window


def _eligible(
    table: str,
    *,
    density_min: float,
    cadence_range: tuple[int, int],
    min_span_days: float,
    window_days: int,
) -> pd.DataFrame:
    users = db.list_users(table)
    expected = window_days * EXPECTED_5MIN_PER_DAY
    users["density"] = (users["rows"].clip(upper=expected) / expected).astype(float)
    keep = (
        (users["span_days"] >= min_span_days)
        & users["mode_gap_s"].between(*cadence_range)
        & (users["density"] >= density_min)
    )
    return users.loc[keep].copy()


def select_cohort(
    *,
    tables: Iterable[str] = ("oref_v5", "oref_v6", "oref_v7"),
    per_table_n: dict[str, int] | None = None,
    density_min: float = DEFAULT_DENSITY_MIN,
    cadence_range: tuple[int, int] = DEFAULT_CADENCE_RANGE,
    min_span_days: float = DEFAULT_MIN_SPAN_DAYS,
    window_days: int = 90,
) -> list[CohortMember]:
    """Pick the top-density users per table, returning a flat cohort list."""
    per_table_n = per_table_n or DEFAULT_PER_TABLE_N
    cohort: list[CohortMember] = []
    for table in tables:
        eligible = _eligible(
            table,
            density_min=density_min,
            cadence_range=cadence_range,
            min_span_days=min_span_days,
            window_days=window_days,
        )
        eligible = eligible.sort_values("density", ascending=False).head(per_table_n[table])
        for row in eligible.itertuples(index=False):
            cohort.append(
                CohortMember(
                    table=table,
                    user_id=row.user_id,
                    rows=int(row.rows),
                    span_days=float(row.span_days),
                    mode_gap_s=int(row.mode_gap_s),
                    density=float(row.density),
                )
            )
    return cohort


def write_cohort(cohort: list[CohortMember], path: str | Path) -> None:
    payload = {
        "schema_version": 1,
        "members": [asdict(m) for m in cohort],
    }
    Path(path).write_text(json.dumps(payload, indent=2))


def read_cohort(path: str | Path) -> list[CohortMember]:
    payload = json.loads(Path(path).read_text())
    return [CohortMember(**m) for m in payload["members"]]
