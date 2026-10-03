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


FIELD_NAMES = {
    "latitude": LATITUDE_FIELD,
    "longitude": LONGITUDE_FIELD,
    "beam_distance": BEAM_DISTANCE_FIELD,
    "altitude": ALTITUDE_FIELD,
    "utc_date": UTC_DATE_FIELD,
    "utc_time": UTC_TIME_FIELD,
}


def detect_columns(columns: list[str]) -> dict[str, str | None]:
    """The KoggerApp columns found by name, whatever their order; None for those missing."""
    by_name = {str(column).strip().casefold(): str(column).strip() for column in columns}
    return {key: by_name.get(name.casefold()) for key, name in FIELD_NAMES.items()}


def parse_utc(dates: pd.Series, times: pd.Series) -> pd.Series:
    """UTC of every row in seconds since the epoch; NaN when empty or before 2000 (1970-1-1 0:0:0)."""
    text = dates.fillna("").astype(str).str.strip() + " " + times.fillna("").astype(str).str.strip()
    moments = pd.to_datetime(text, format="%Y-%m-%d %H:%M:%S.%f", errors="coerce", utc=True)
    whole_seconds = pd.to_datetime(text, format="%Y-%m-%d %H:%M:%S", errors="coerce", utc=True)
    moments = moments.fillna(whole_seconds)
    seconds = (moments - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
    return seconds.where(moments.dt.year >= MIN_VALID_UTC_YEAR)


def time_axis(utc_s: pd.Series, number: pd.Series) -> TimeAxis:
    """UTC when at least half the rows have it, otherwise the row Number."""
    if len(utc_s) > 0 and utc_s.notna().mean() >= UTC_AXIS_MIN_SHARE:
        return TimeAxis(utc_s.to_numpy(dtype=np.float64), "utc", MAX_GAP_UTC_S)
    return TimeAxis(number.to_numpy(dtype=np.float64), "number", MAX_GAP_ROWS)


def _running_median(values: pd.Series, window: int) -> pd.Series:
    """Centred running median over the values present, ignoring the missing ones."""
    present = values.dropna()
    return present.rolling(window, center=True, min_periods=1).median().reindex(values.index)


def antenna_altitude(altitude: pd.Series, axis: TimeAxis) -> pd.DataFrame:
    """Antenna altitude on every row: measured, or interpolated along the time axis over a short gap.

    Returns the columns alt_antenna_m, alt_source («measured», «interpolated» or empty) and alt_reason.
    """
    altitude = altitude.astype(np.float64).reset_index(drop=True)
    reason = pd.Series("", index=altitude.index, dtype=object)

    out_of_range = altitude.notna() & ~altitude.between(*ALTITUDE_RANGE_M)
    reason[out_of_range] = REASON_ALTITUDE_RANGE
    kept = altitude.where(~out_of_range)
    deviation = (kept - _running_median(kept, ALTITUDE_MEDIAN_WINDOW)).abs()
    outlier = deviation > ALTITUDE_MAX_DEVIATION_M
    reason[outlier] = REASON_ALTITUDE_OUTLIER
    kept = kept.where(~outlier)

    times = np.asarray(axis.values, dtype=np.float64)
    known = np.flatnonzero(kept.notna().to_numpy() & np.isfinite(times))
    result = kept.copy()
    source = pd.Series(np.where(kept.notna(), "measured", ""), index=altitude.index, dtype=object)
    if len(known) >= 2:
        rows = np.arange(len(kept))
        following = np.searchsorted(known, rows)
        inner = (following > 0) & (following < len(known)) & kept.isna().to_numpy() & np.isfinite(times)
        before = known[np.clip(following - 1, 0, len(known) - 1)]
        after = known[np.clip(following, 0, len(known) - 1)]
        gap = times[after] - times[before]
        bridged = inner & (gap <= axis.max_gap) & (gap > 0)
        share = np.divide(times - times[before], gap, out=np.zeros_like(times), where=gap > 0)
        values = kept.to_numpy()
        interpolated = values[before] + share * (values[after] - values[before])
        result[bridged] = interpolated[bridged]
        source[bridged] = "interpolated"
    unit = "с" if axis.kind == "utc" else "строк"
    missing = result.isna() & (reason == "")
    reason[missing] = f"{REASON_NO_ALTITUDE} (разрыв > {axis.max_gap:g} {unit} или край записи)"
    reason[result.isna() & (reason != "") & ~missing] = reason + "; " + REASON_NO_ALTITUDE.lower()
    return pd.DataFrame({"alt_antenna_m": result, "alt_source": source, "alt_reason": reason})


def beam_rejects(beam: pd.Series) -> pd.Series:
    """Why a beam distance is rejected: short, or off its running median; empty when kept."""
    beam = beam.astype(np.float64)
    reasons = pd.Series("", index=beam.index, dtype=object)
    short = beam < MIN_BEAM_M
    reasons[short] = REASON_SHORT_BEAM
    # False short readings would pull the median down: it is taken over the others
    usable = beam.where(~short)
    median = _running_median(usable, BEAM_MEDIAN_WINDOW)
    limit = np.maximum(BEAM_MAX_DEVIATION_M, BEAM_MAX_DEVIATION_SHARE * median)
    outlier = (usable - median).abs() > limit
    reasons[outlier] = REASON_BEAM_OUTLIER
    return reasons


def bottom_elevation(alt_antenna_m: pd.Series, beam_m: pd.Series, antenna_to_transducer_m: float) -> pd.Series:
    """Elevation of the bottom under a vertical beam: antenna − antenna-to-transducer − beam."""
    return alt_antenna_m - antenna_to_transducer_m - beam_m
