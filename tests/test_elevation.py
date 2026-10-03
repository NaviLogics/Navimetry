"""Absolute bottom elevations from the KoggerApp CSV: altitude per row, UTC, rejections, outputs."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import laspy
import numpy as np
import pandas as pd
import pytest

from bathymetry.elevation import (
    REASON_ALTITUDE_OUTLIER,
    REASON_ALTITUDE_RANGE,
    REASON_BEAM_OUTLIER,
    REASON_NO_ALTITUDE,
    REASON_SHORT_BEAM,
    TimeAxis,
    antenna_altitude,
    beam_rejects,
    bottom_elevation,
    detect_columns,
    parse_utc,
    time_axis,
)
from bathymetry.models import ProcessingConfig
from bathymetry.processor import normalize_numeric, run_pipeline

PILOT = Path("sample_data") / "pilot_2026-10-01"
PILOT_CSV = PILOT / "00038_export_csv.csv"
PILOT_CSV_UTC = PILOT / "00038_export_csv_utc.csv"
SAMPLE_CSV = Path("sample_data") / "sample_kogger.csv"


def config_for(csv: Path, output_dir: Path, **overrides) -> ProcessingConfig:
    columns = list(pd.read_csv(csv, nrows=0).columns)
    found = detect_columns(columns)
    fields = dict(
        input_csv=csv,
        output_dir=output_dir,
        latitude_field=found["latitude"],
        longitude_field=found["longitude"],
        beam_distance_field=found["beam_distance"],
        altitude_field=found["altitude"],
        utc_date_field=found["utc_date"],
        utc_time_field=found["utc_time"],
        output_crs="EPSG:32637",
        max_triangle_edge_m=20.0,
        pixel_size_m=0.5,
    )
    fields.update(overrides)
    return ProcessingConfig(**fields)


@pytest.fixture(scope="module")
def pilot(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("pilot") / "result"
    report = run_pipeline(config_for(PILOT_CSV, output_dir))
    quality = pd.read_csv(output_dir / "quality_points.csv", encoding="utf-8-sig", dtype={"utc": str})
    accepted = pd.read_csv(output_dir / "accepted_points.csv", encoding="utf-8-sig")
    return output_dir, report, quality, accepted


# 1. Reading the CSV


def test_columns_are_found_by_name_in_any_order() -> None:
    columns = ["GNSS Altitude MSL", "Rangefinder", "Number", "GNSS UTC Time", "Longitude", "Beam distance",
               "GNSS UTC Date", "Latitude"]
    assert detect_columns(columns) == {
        "latitude": "Latitude",
        "longitude": "Longitude",
        "beam_distance": "Beam distance",
        "altitude": "GNSS Altitude MSL",
        "utc_date": "GNSS UTC Date",
        "utc_time": "GNSS UTC Time",
    }
    assert detect_columns(["Number", "Beam distance", "Latitude", "Longitude"])["altitude"] is None


def test_utc_before_2000_or_empty_is_absent() -> None:
    dates = pd.Series(["1970-1-1", "", None, "2026-10-1"])
    times = pd.Series(["0:0:0", "", None, "9:12:27.987"])

    utc = parse_utc(dates, times)

    assert utc.iloc[:3].isna().all()
    expected = datetime(2026, 10, 1, 9, 12, 27, 987000, tzinfo=timezone.utc).timestamp()
    assert utc.iloc[3] == pytest.approx(expected)


def test_utc_is_the_time_axis_only_when_at_least_half_the_rows_have_it() -> None:
    number = pd.Series(np.arange(10))
    few = pd.Series([1.79e9 + i if i < 4 else np.nan for i in range(10)])
    half = pd.Series([1.79e9 + i if i < 5 else np.nan for i in range(10)])

    assert time_axis(few, number).kind == "number"
    assert time_axis(few, number).max_gap == 10
    assert time_axis(half, number).kind == "utc"
    assert time_axis(half, number).max_gap == 2.0


# 2. Antenna altitude on every row


def number_axis(rows: int) -> TimeAxis:
    return TimeAxis(np.arange(rows, dtype=np.float64), "number", 10)


def test_altitude_is_interpolated_over_up_to_10_rows_and_not_over_more() -> None:
    altitude = pd.Series([np.nan] * 40)
    # Steps of a few centimetres, as on the water: a step over 0.10 m would be an outlier
    altitude[2] = 100.00
    altitude[12] = 100.04  # 10 rows on: bridged
    altitude[23] = 100.08  # 11 rows on: not bridged

    result = antenna_altitude(altitude, number_axis(40))

    assert result.loc[2, "alt_source"] == "measured"
    assert result.loc[7, "alt_source"] == "interpolated"
    assert result.loc[7, "alt_antenna_m"] == pytest.approx(100.02)
    assert np.isnan(result.loc[18, "alt_antenna_m"])
    assert result.loc[18, "alt_reason"].startswith(REASON_NO_ALTITUDE)
    # Before the first and after the last altitude: nothing to interpolate from
    assert np.isnan(result.loc[0, "alt_antenna_m"])
    assert np.isnan(result.loc[30, "alt_antenna_m"])


def test_over_utc_the_gap_limit_is_2_s() -> None:
    utc = 1.79e9 + np.array([0.0, 0.5, 1.0, 2.0, 2.5, 4.6, 5.0])
    altitude = pd.Series([100.00, np.nan, 100.02, np.nan, 100.04, np.nan, 100.06])

    result = antenna_altitude(altitude, TimeAxis(utc, "utc", 2.0))

    assert result.loc[1, "alt_antenna_m"] == pytest.approx(100.01)
    assert result.loc[3, "alt_source"] == "interpolated"  # 1.0 → 2.5 s: 1.5 s
    assert np.isnan(result.loc[5, "alt_antenna_m"])  # 2.5 → 5.0 s: 2.5 s


def test_altitude_off_its_running_median_or_out_of_range_is_rejected() -> None:
    rng = np.random.default_rng(1)
    altitude = pd.Series(183.65 + rng.normal(0, 0.01, 80))
    altitude[40] = 183.80  # 0.15 m off
    altitude[41] = 183.72  # 0.07 m off: kept
    altitude[60] = 9500.0

    result = antenna_altitude(altitude, number_axis(80))

    assert result.loc[40, "alt_reason"] == REASON_ALTITUDE_OUTLIER
    assert result.loc[40, "alt_source"] == "interpolated"
    assert result.loc[40, "alt_antenna_m"] == pytest.approx(
        (altitude[39] + altitude[41]) / 2
    )
    assert result.loc[41, "alt_source"] == "measured"
    assert result.loc[60, "alt_reason"] == REASON_ALTITUDE_RANGE


# 4. Beam distance


def test_short_beams_and_beams_off_their_running_median_are_rejected() -> None:
    beam = pd.Series([2.0] * 30 + [5.0] * 30)
    beam[5] = 0.25
    beam[10] = 2.25  # 0.25 off a median of 2.0: over 0.2
    beam[12] = 2.15  # 0.15 off: kept
    beam[45] = 5.45  # 0.45 off a median of 5.0: under 10 %
    beam[48] = 5.6  # 0.6 off: over 10 %

    reasons = beam_rejects(beam)

    assert reasons[5] == REASON_SHORT_BEAM
    assert reasons[10] == REASON_BEAM_OUTLIER
    assert reasons[12] == ""
    assert reasons[45] == ""
    assert reasons[48] == REASON_BEAM_OUTLIER
    assert (reasons == "").sum() == 57


# 5. Elevation


def test_bottom_elevation_is_antenna_minus_offset_minus_beam() -> None:
    elevation = bottom_elevation(pd.Series([183.65]), pd.Series([1.444]), 0.470)
    assert elevation[0] == pytest.approx(181.736)


# Pilot of 01.10.2026


def test_pilot_csv_altitude_and_time(pilot) -> None:
    _, report, quality, _ = pilot
    altitude = normalize_numeric(quality["GNSS Altitude MSL"])

    assert len(quality) == 8371
    assert altitude.notna().sum() == 4291
    assert altitude.median() == pytest.approx(183.650)
    assert altitude.min() == pytest.approx(183.607)
    assert altitude.max() == pytest.approx(184.808)
    assert report["time_axis"] == "number"
    assert report["height_system"].startswith("MSL по приёмнику")


def test_pilot_bottom_elevation(pilot) -> None:
    _, report, quality, _ = pilot
    beam = quality["beam_distance_m"]
    measured = (quality["alt_source"] == "measured") & (beam > 0.5) & (quality["quality_status"] != "rejected")

    assert quality.loc[measured, "elevation_m"].median() == pytest.approx(181.736, abs=0.02)
    assert report["antenna_to_transducer_m"] == 0.470
    assert report["minimum_elevation_m"] < report["median_elevation_m"] < report["maximum_elevation_m"]


def test_pilot_rejections_are_in_quality_points(pilot) -> None:
    _, report, quality, _ = pilot
    reasons = quality["quality_reason"].fillna("")

    assert (quality.loc[quality["beam_distance_m"] < 0.3, "quality_status"] == "rejected").all()
    assert reasons.str.startswith(REASON_SHORT_BEAM).any()
    assert reasons.str.startswith(REASON_BEAM_OUTLIER).any()
    no_altitude = quality["alt_antenna_m"].isna() & (quality["quality_status"] != "rejected")
    assert not no_altitude.any()
    assert report["rows_with_measured_altitude"] + report["rows_with_interpolated_altitude"] <= 8371
    assert sum(report["rejected_rows_by_reason"].values()) == report["rejected_rows"]


def test_pilot_accepted_points_xyz_and_las_carry_the_elevation(pilot) -> None:
    output_dir, _, _, accepted = pilot

    assert {"alt_antenna_m", "elevation_m", "utc"} <= set(accepted.columns)
    xyz = np.loadtxt(output_dir / "bottom_points.xyz")
    assert xyz.shape[1] == 4
    np.testing.assert_allclose(xyz[:, 2], accepted["elevation_m"], atol=1e-4)
    np.testing.assert_allclose(xyz[:, 3], accepted["depth_m"], atol=1e-4)
    las = laspy.read(output_dir / "bottom_points.las")
    np.testing.assert_allclose(np.asarray(las.z), accepted["elevation_m"], atol=2e-3)
    np.testing.assert_allclose(np.asarray(las.depth_m), accepted["depth_m"], atol=1e-3)


def test_the_utc_export_with_1970_dates_gives_the_same_result(pilot, tmp_path) -> None:
    _, report, _, accepted = pilot
    report_utc = run_pipeline(config_for(PILOT_CSV_UTC, tmp_path / "utc"))
    accepted_utc = pd.read_csv(tmp_path / "utc" / "accepted_points.csv", encoding="utf-8-sig")

    assert report_utc["time_axis"] == "number"
    assert accepted_utc["utc"].isna().all()
    np.testing.assert_allclose(accepted_utc["elevation_m"], accepted["elevation_m"])


def test_processing_report_lists_the_elevation_figures(pilot) -> None:
    output_dir, _, _, _ = pilot
    report = json.loads((output_dir / "processing_report.json").read_text(encoding="utf-8"))
    for key in (
        "height_system",
        "antenna_to_transducer_m",
        "elevation_formula",
        "assumptions",
        "time_axis",
        "rows_with_measured_altitude",
        "rows_with_interpolated_altitude",
        "rejected_rows_by_reason",
        "minimum_elevation_m",
        "median_elevation_m",
        "maximum_elevation_m",
    ):
        assert key in report, key


# Regression: without an altitude column the pipeline is the depth pipeline it was

SAMPLE_HASHES = {
    "bottom_points.xyz": "775f7079f15c27e7f9550c67f08bbaa88a5f9f6ecc5f8e46fa1d34d9bb02a04e",
    "accepted_points.csv": "601b66d0f6d9c422f91c0df797b13ca7e6058e3dba42a6775a5f35910f36acc3",
    "quality_points.csv": "520b6cf4ac0dfe73244521012de771c93111c403243e39a5f6aaed90c7a5d8ff",
}


def test_sample_without_altitude_gives_the_same_outputs(tmp_path) -> None:
    config = ProcessingConfig(
        input_csv=SAMPLE_CSV,
        output_dir=tmp_path / "result",
        latitude_field="Latitude",
        longitude_field="Longitude",
        beam_distance_field="Beam distance",
        water_surface_to_transducer_m=0.15,
        output_crs="EPSG:32637",
        max_triangle_edge_m=20.0,
        pixel_size_m=0.5,
    )
    run_pipeline(config)
    for name, expected in SAMPLE_HASHES.items():
        # Line endings differ on Windows
        lines = (tmp_path / "result" / name).read_text(encoding="utf-8-sig").splitlines()
        assert hashlib.sha256("\n".join(lines).encode()).hexdigest() == expected, name
