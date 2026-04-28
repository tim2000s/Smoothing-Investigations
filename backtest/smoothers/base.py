"""Common smoother interface + result type."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class SmootherResult:
    """Output of a smoother run.

    `smoothed` is always populated. `trace` is populated only when the smoother
    was called with `instrument=True`; one row per (reading, step) — a single
    row per reading for AAPS Avg/Exp and UKF, three rows per reading for Trio SG.
    """
    smoothed: np.ndarray
    trace: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())


class Smoother(Protocol):
    name: str

    def process(
        self,
        glucose: np.ndarray,
        ts_sec: np.ndarray,
        *,
        instrument: bool = False,
    ) -> SmootherResult: ...
