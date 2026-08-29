from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS project (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT, navimetry_version TEXT, config_json TEXT);
CREATE TABLE IF NOT EXISTS source_asset (id INTEGER PRIMARY KEY, path TEXT, filename TEXT, asset_type TEXT, sha256 TEXT, size_bytes INTEGER, imported_at TEXT, parser_version TEXT);
CREATE TABLE IF NOT EXISTS csv_observation (id INTEGER PRIMARY KEY, source_asset_id INTEGER, source_row INTEGER, number INTEGER, beam_distance_raw_m REAL, rangefinder_raw_m REAL, latitude_raw_deg REAL, longitude_raw_deg REAL, extras_json TEXT);
CREATE TABLE IF NOT EXISTS klf_frame (id INTEGER PRIMARY KEY, source_asset_id INTEGER, frame_index INTEGER, frame_type TEXT, payload_length INTEGER, ltime_ms REAL, checksum_valid INTEGER, complete INTEGER, kogger_id INTEGER);
CREATE TABLE IF NOT EXISTS mavlink_message (id INTEGER PRIMARY KEY, klf_frame_id INTEGER, sysid INTEGER, compid INTEGER, msgid INTEGER, sequence INTEGER, px4_boot_time_ms REAL, utc_time_us INTEGER, ltime_ms REAL);
CREATE TABLE IF NOT EXISTS observation (id INTEGER PRIMARY KEY, csv_observation_id INTEGER, primary_depth_m REAL, primary_depth_source TEXT, validation_depth_m REAL, validation_available INTEGER, latitude_deg REAL, longitude_deg REAL, x_m REAL, y_m REAL, kogger_time_ms REAL, px4_time_ms REAL, timestamp_utc TEXT, position_source TEXT, timestamp_quality TEXT, position_quality TEXT, depth_quality TEXT, overall_quality TEXT, quality_flags_json TEXT);
CREATE TABLE IF NOT EXISTS processing_run (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, config_json TEXT, report_json TEXT);
"""

def initialize_database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn

def store_dataframe(conn: sqlite3.Connection, table: str, frame: pd.DataFrame, columns: list[str]) -> None:
    """Append a DataFrame without exceeding SQLite's bound-variable limit.

    pandas ``method='multi'`` generates one INSERT statement containing all
    values from a chunk. With wide tables and a large chunksize that can exceed
    SQLite's compile-time MAX_VARIABLE_NUMBER (commonly 999 on some builds).
    The default method uses executemany-style inserts, so the bound-variable
    count is only the number of columns per row and is portable across SQLite
    builds.
    """
    usable = [c for c in columns if c in frame.columns]
    if not usable or frame.empty:
        return
    frame[usable].to_sql(
        table,
        conn,
        if_exists="append",
        index=False,
        chunksize=1000,
        method=None,
    )
