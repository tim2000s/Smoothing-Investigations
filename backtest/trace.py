"""Per-step trace schema + Parquet writer."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

import pandas as pd

SCHEMA_VERSION = 1

# Columns common to every smoother trace.
COMMON_COLS = (
    "user_id",
    "algorithm",
    "reading_idx",
    "ts_sec",
    "input_glucose",
    "output_glucose",
    "step_name",
)

# Algorithm-specific extra columns (for documentation / sanity checks).
ALGO_COLS = {
    "aaps_average": (
        "prev_g", "curr_g", "next_g", "dt_prev_s", "dt_next_s",
        "in_range", "evenly_spaced", "window_avg",
    ),
    "aaps_exponential": (
        "window_size", "gap_to_prev_min",
        "o1_anchor", "o1_final",
        "o2_anchor", "o2_final",
        "o2_d_anchor", "o2_d_final",
        "blended",
    ),
    "ukf": (
        "segment_idx", "dt_min",
        "x_pred_g", "x_pred_r", "P_pred_gg", "P_pred_rr", "P_pred_gr",
        "innov", "innov_var", "chi2", "is_outlier",
        "R_live", "R_eff", "K_g", "K_r",
        "x_upd_g", "x_upd_r", "P_upd_gg", "P_upd_rr",
        "R_after", "is_rts", "output_rate",
        "session_id", "session_meas_n", "session_outlier_n",
    ),
}


@dataclass
class TraceMetadata:
    user_id: str
    table: str
    algorithm: str
    n_readings: int
    n_trace_rows: int
    cohort_hash: str
    schema_version: int = SCHEMA_VERSION


def cohort_hash(cohort_path: str | Path) -> str:
    """Stable hash of cohort.json so we can detect cohort changes."""
    return hashlib.sha256(Path(cohort_path).read_bytes()).hexdigest()[:16]


def write_trace(
    trace: pd.DataFrame,
    out_dir: str | Path,
    *,
    user_id: str,
    table: str,
    algorithm: str,
    cohort_hash_str: str,
) -> Path:
    """Write `trace` to `<out_dir>/<user_id>/<algorithm>.parquet` with metadata."""
    out_dir = Path(out_dir)
    user_dir = out_dir / user_id
    user_dir.mkdir(parents=True, exist_ok=True)

    df = trace.copy()
    df.insert(0, "user_id", user_id)
    df.insert(1, "algorithm", algorithm)

    parquet_path = user_dir / f"{algorithm}.parquet"
    df.to_parquet(parquet_path, engine="pyarrow", compression="zstd", index=False)

    meta = TraceMetadata(
        user_id=user_id,
        table=table,
        algorithm=algorithm,
        n_readings=int(trace["reading_idx"].nunique()) if "reading_idx" in trace.columns else 0,
        n_trace_rows=len(trace),
        cohort_hash=cohort_hash_str,
    )
    meta_path = user_dir / f"{algorithm}.meta.json"
    meta_path.write_text(json.dumps(meta.__dict__, indent=2))

    return parquet_path


def read_trace(
    out_dir: str | Path,
    *,
    user_id: str,
    algorithm: str,
) -> pd.DataFrame:
    return pd.read_parquet(Path(out_dir) / user_id / f"{algorithm}.parquet")


def read_metadata(
    out_dir: str | Path,
    *,
    user_id: str,
    algorithm: str,
) -> TraceMetadata | None:
    p = Path(out_dir) / user_id / f"{algorithm}.meta.json"
    if not p.exists():
        return None
    return TraceMetadata(**json.loads(p.read_text()))


def is_up_to_date(
    out_dir: str | Path,
    *,
    user_id: str,
    algorithm: str,
    cohort_hash_str: str,
) -> bool:
    meta = read_metadata(out_dir, user_id=user_id, algorithm=algorithm)
    return (
        meta is not None
        and meta.schema_version == SCHEMA_VERSION
        and meta.cohort_hash == cohort_hash_str
    )
