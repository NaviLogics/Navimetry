from __future__ import annotations
import csv
from dataclasses import dataclass
from pathlib import Path
import pandas as pd
import numpy as np
from bathymetry.models import CsvInspection, ProcessingConfig

class CsvImportError(RuntimeError):
    pass

def detect_csv_format(path: Path) -> tuple[str, str]:
    if not path.exists() or not path.is_file():
        raise CsvImportError(f"CSV not found: {path}")
    raw = path.read_bytes()[:65536]
    text = None
    used_encoding = "utf-8-sig"
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            text = raw.decode(encoding); used_encoding = encoding; break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise CsvImportError("Unable to determine CSV encoding")
    try:
        delimiter = csv.Sniffer().sniff(text, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    return used_encoding, delimiter

def inspect_csv(path: Path) -> CsvInspection:
    encoding, delimiter = detect_csv_format(path)
    preview = pd.read_csv(path, encoding=encoding, sep=delimiter, nrows=5, dtype=str)
    with path.open("r", encoding=encoding, errors="replace", newline="") as stream:
        row_count = max(0, sum(1 for _ in stream) - 1)
    return CsvInspection(columns=[str(c).strip() for c in preview.columns], row_count=row_count, delimiter=delimiter, encoding=encoding)

def normalize_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")

@dataclass
class CsvImportResult:
    raw: pd.DataFrame
    normalized: pd.DataFrame
    inspection: CsvInspection
    inventory: dict

def import_kogger_csv(config: ProcessingConfig) -> CsvImportResult:
    inspection = inspect_csv(config.input_csv)
    raw = pd.read_csv(config.input_csv, encoding=inspection.encoding, sep=inspection.delimiter, dtype=str, keep_default_na=False)
    raw.columns = [str(c).strip() for c in raw.columns]
    required = [config.latitude_field, config.longitude_field, config.beam_distance_field]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise CsvImportError("Missing required CSV fields: " + ", ".join(missing))
    n = len(raw)
    out = pd.DataFrame(index=raw.index)
    out["observation_id"] = np.arange(1, n + 1, dtype=np.int64)
    out["source_row"] = np.arange(2, n + 2, dtype=np.int64)
    out["csv_number"] = normalize_numeric(raw["Number"]).astype("Int64") if "Number" in raw else pd.Series(pd.array([None] * n, dtype="Int64"))
    out["latitude_raw_deg"] = normalize_numeric(raw[config.latitude_field])
    out["longitude_raw_deg"] = normalize_numeric(raw[config.longitude_field])
    out["beam_distance_raw_m"] = normalize_numeric(raw[config.beam_distance_field])
    out["rangefinder_raw_m"] = normalize_numeric(raw[config.rangefinder_field]) if config.rangefinder_field in raw else np.nan
    for source_name, target_name in (("Event UNIX", "event_unix_raw"), ("Event timestamp", "event_timestamp_raw"), ("GNSS UTC Date", "gnss_utc_date_raw"), ("GNSS UTC Time", "gnss_utc_time_raw")):
        out[target_name] = raw[source_name] if source_name in raw else None
    known = {"Number", config.latitude_field, config.longitude_field, config.beam_distance_field, config.rangefinder_field, "Event UNIX", "Event timestamp", "GNSS UTC Date", "GNSS UTC Time"}
    extra_columns = [c for c in raw.columns if c not in known]
    out["extras_json"] = raw[extra_columns].apply(lambda r: r.to_json(force_ascii=False), axis=1) if extra_columns else "{}"
    inventory = {"rows": n, "columns": len(raw.columns), "column_names": list(raw.columns), "beam_valid": int(out["beam_distance_raw_m"].notna().sum()), "beam_missing": int(out["beam_distance_raw_m"].isna().sum()), "zero_coordinate_rows": int(((out["latitude_raw_deg"] == 0) & (out["longitude_raw_deg"] == 0)).sum()), "rangefinder_non_null": int(out["rangefinder_raw_m"].notna().sum()), "rangefinder_zero": int((out["rangefinder_raw_m"] == 0).sum()), "extra_columns": extra_columns}
    return CsvImportResult(raw=raw, normalized=out, inspection=inspection, inventory=inventory)
