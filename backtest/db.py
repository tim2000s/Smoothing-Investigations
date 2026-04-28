"""PostgreSQL access to the local oref database."""
from __future__ import annotations

from contextlib import contextmanager
import os

import pandas as pd
import psycopg2

DEFAULT_DSN = "dbname=oref"
PSQL_BIN = "/opt/homebrew/opt/postgresql@17/bin/psql"


@contextmanager
def connect(dsn: str = DEFAULT_DSN):
    conn = psycopg2.connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


def list_users(table: str, *, dsn: str = DEFAULT_DSN) -> pd.DataFrame:
    """Per-user row count, span days, and modal inter-reading gap (seconds)."""
    sql = f"""
    WITH gaps AS (
      SELECT user_id,
             ts_relative_sec - LAG(ts_relative_sec) OVER (PARTITION BY user_id ORDER BY ts_relative_sec) AS dt
      FROM {table}
    ),
    mode_gap AS (
      SELECT user_id, dt AS mode_gap_s, COUNT(*) AS n
      FROM gaps
      WHERE dt IS NOT NULL AND dt BETWEEN 1 AND 3600
      GROUP BY user_id, dt
    ),
    ranked AS (
      SELECT user_id, mode_gap_s,
             ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY n DESC) AS rk
      FROM mode_gap
    ),
    spans AS (
      SELECT user_id,
             COUNT(*)::bigint AS rows,
             MIN(ts_relative_sec) AS min_ts,
             MAX(ts_relative_sec) AS max_ts,
             (MAX(ts_relative_sec) - MIN(ts_relative_sec))::float / 86400.0 AS span_days
      FROM {table}
      GROUP BY user_id
    )
    SELECT s.user_id, s.rows, s.min_ts, s.max_ts, s.span_days,
           r.mode_gap_s
    FROM spans s
    LEFT JOIN ranked r ON r.user_id = s.user_id AND r.rk = 1
    ORDER BY s.user_id;
    """
    with connect(dsn) as conn:
        return pd.read_sql(sql, conn)


def fetch_user_window(
    table: str,
    user_id: str,
    *,
    days: int = 90,
    dsn: str = DEFAULT_DSN,
) -> pd.DataFrame:
    """Pull the first `days` of CGM readings for one user.

    Returns columns: user_id, ts_relative_sec, cgm_mgdl, direction (if present).
    Rows are ordered by ts_relative_sec ascending. Window starts at the user's
    own min(ts_relative_sec).
    """
    cols = "user_id, ts_relative_sec, cgm_mgdl, direction"
    sql = f"""
    SELECT {cols}
    FROM {table}
    WHERE user_id = %(uid)s
      AND ts_relative_sec >= (SELECT MIN(ts_relative_sec) FROM {table} WHERE user_id = %(uid)s)
      AND ts_relative_sec <  (SELECT MIN(ts_relative_sec) FROM {table} WHERE user_id = %(uid)s)
                             + %(window_s)s
    ORDER BY ts_relative_sec
    """
    params = {"uid": user_id, "window_s": days * 86400}
    with connect(dsn) as conn:
        return pd.read_sql(sql, conn, params=params)
