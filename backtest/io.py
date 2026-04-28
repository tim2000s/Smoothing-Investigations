"""Load → clean → resample CGM data onto a strict 5-minute grid."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import db

GRID_S = 300  # 5 minutes
FORWARD_FILL_LIMIT = 1  # ≤1 step (5 min) of forward-fill across small gaps
GAP_THRESHOLD_S = GRID_S + 1  # anything strictly larger is treated as a gap


@dataclass
class UserSeries:
    user_id: str
    table: str
    ts_sec: np.ndarray  # int64, on the 5-min grid (relative-seconds, per-user)
    glucose: np.ndarray  # float64, NaN where missing
    direction: np.ndarray  # object, "" where missing
    n_raw: int  # count before resampling
    n_grid: int  # count of grid slots
    n_present: int  # grid slots with non-NaN glucose


def load_user(
    table: str,
    user_id: str,
    *,
    days: int = 90,
) -> UserSeries:
    """Pull a user's first `days` of readings and resample to the 5-min grid."""
    raw = db.fetch_user_window(table, user_id, days=days)
    if raw.empty:
        raise ValueError(f"No rows for {table}/{user_id}")

    raw = raw.dropna(subset=["cgm_mgdl"]).copy()
    raw["ts_relative_sec"] = raw["ts_relative_sec"].astype("int64")

    t0 = int(raw["ts_relative_sec"].iloc[0])
    raw["ts_grid"] = ((raw["ts_relative_sec"] - t0 + GRID_S // 2) // GRID_S) * GRID_S + t0
    raw = raw.drop_duplicates(subset="ts_grid", keep="last")

    grid_end = t0 + days * 86400
    grid = np.arange(t0, grid_end, GRID_S, dtype="int64")

    g = pd.DataFrame({"ts_grid": grid}).merge(
        raw[["ts_grid", "cgm_mgdl", "direction"]], on="ts_grid", how="left"
    )

    raw_idx = g["cgm_mgdl"].notna().to_numpy()
    glucose = np.asarray(g["cgm_mgdl"].ffill(limit=FORWARD_FILL_LIMIT).to_numpy(dtype="float64")).copy()
    direction = np.asarray(g["direction"].fillna("").astype(object).to_numpy()).copy()

    if raw_idx.any():
        gap_step = _step_gap_mask(raw_idx)
        glucose[gap_step] = np.nan
        direction[gap_step] = ""

    return UserSeries(
        user_id=user_id,
        table=table,
        ts_sec=g["ts_grid"].to_numpy(dtype="int64"),
        glucose=glucose,
        direction=direction,
        n_raw=len(raw),
        n_grid=len(grid),
        n_present=int(np.isfinite(glucose).sum()),
    )


def _step_gap_mask(raw_present: np.ndarray) -> np.ndarray:
    """Mask the *2nd-and-beyond* missing slot in each run of consecutive missing slots.
    The first missing slot of each run is reachable by 1-step ffill; later ones are
    not, so they must be re-NaN'd after the ffill above (which is unbounded by index).
    """
    out = np.zeros_like(raw_present, dtype=bool)
    missing_run = 0
    for i, present in enumerate(raw_present):
        if present:
            missing_run = 0
        else:
            missing_run += 1
            if missing_run > FORWARD_FILL_LIMIT:
                out[i] = True
    return out


def to_ms(ts_sec: np.ndarray) -> np.ndarray:
    """AAPS smoothers want millisecond timestamps."""
    return (ts_sec.astype("int64") * 1000)
