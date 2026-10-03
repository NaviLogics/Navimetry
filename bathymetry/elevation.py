"""Absolute bottom elevations from a KoggerApp CSV: antenna altitude on every row, UTC, beam rejection.

The KoggerApp «GNSS Altitude MSL» is the altitude of the GNSS antenna (MAVLink GPS_RAW_INT.alt), filled on
about every other row: the sonar writes more often than GNSS comes in.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LATITUDE_FIELD = "Latitude"
LONGITUDE_FIELD = "Longitude"
BEAM_DISTANCE_FIELD = "Beam distance"
ALTITUDE_FIELD = "GNSS Altitude MSL"
UTC_DATE_FIELD = "GNSS UTC Date"
UTC_TIME_FIELD = "GNSS UTC Time"

# KoggerApp writes 1970-1-1 0:0:0 when it has no UTC (navigation over MAVLink)
MIN_VALID_UTC_YEAR = 2000
# UTC is the time axis when at least this share of the rows has it
UTC_AXIS_MIN_SHARE = 0.5

ALTITUDE_RANGE_M = (-500.0, 9000.0)
ALTITUDE_MEDIAN_WINDOW = 51
ALTITUDE_MAX_DEVIATION_M = 0.10
# Interpolation does not bridge a longer gap between two rows with an altitude
MAX_GAP_ROWS = 10
MAX_GAP_UTC_S = 2.0

MIN_BEAM_M = 0.3
BEAM_MEDIAN_WINDOW = 21
BEAM_MAX_DEVIATION_M = 0.2
BEAM_MAX_DEVIATION_SHARE = 0.10

REASON_ALTITUDE_RANGE = "Высота вне диапазона"
REASON_ALTITUDE_OUTLIER = "Выброс высоты"
REASON_NO_ALTITUDE = "Нет высоты"
REASON_SHORT_BEAM = "Малая дальность"
REASON_BEAM_OUTLIER = "Выброс дальности"

HEIGHT_SYSTEM = "MSL по приёмнику, система высот подлежит согласованию с заказчиком"


@dataclass
class TimeAxis:
    values: np.ndarray
    kind: str
    """«utc» or «number»."""
    max_gap: float


def detect_columns(columns: list[str]) -> dict[str, str | None]:
    """The KoggerApp columns found by name, whatever their order; None for those missing."""
    return {}


def parse_utc(dates: pd.Series, times: pd.Series) -> pd.Series:
    """UTC of every row in seconds since the epoch; NaN when empty or before 2000 (1970-1-1 0:0:0)."""
    return pd.Series(np.nan, index=dates.index)


def time_axis(utc_s: pd.Series, number: pd.Series) -> TimeAxis:
    """UTC when at least half the rows have it, otherwise the row Number."""
    return TimeAxis(number.to_numpy(dtype=np.float64), "number", MAX_GAP_ROWS)


def antenna_altitude(altitude: pd.Series, axis: TimeAxis) -> pd.DataFrame:
    """Antenna altitude on every row: measured, or interpolated along the time axis over a short gap.

    Returns the columns alt_antenna_m, alt_source («measured», «interpolated» or empty) and alt_reason.
    """
    return pd.DataFrame(
        {"alt_antenna_m": np.nan, "alt_source": "", "alt_reason": ""},
        index=altitude.index,
    )


def beam_rejects(beam: pd.Series) -> pd.Series:
    """Why a beam distance is rejected: short, or off its running median; empty when kept."""
    return pd.Series("", index=beam.index)


def bottom_elevation(alt_antenna_m: pd.Series, beam_m: pd.Series, antenna_to_transducer_m: float) -> pd.Series:
    """Elevation of the bottom under a vertical beam: antenna − antenna-to-transducer − beam."""
    return pd.Series(np.nan, index=alt_antenna_m.index)
