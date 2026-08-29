from __future__ import annotations
import numpy as np
import pandas as pd

def match_csv_to_klf(csv_obs: pd.DataFrame, global_position: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    result = pd.DataFrame(index=csv_obs.index)
    result["global_position_ref"] = pd.Series(pd.array([None] * len(csv_obs), dtype="Int64"), index=csv_obs.index)
    result["px4_boot_time_ms"] = np.nan
    result["kogger_time_ms"] = np.nan
    result["match_method"] = "unmatched"
    result["match_confidence"] = "low"
    result["match_ambiguous"] = False
    result["match_residual_m"] = np.nan
    if global_position.empty:
        return result, {"matched_gpi": 0, "gpi_count": 0, "match_ratio": 0.0, "method": "unavailable"}
    csv_keys = list(zip(csv_obs["latitude_raw_deg"].round(7), csv_obs["longitude_raw_deg"].round(7)))
    gpi_keys = list(zip(global_position["latitude_deg"].round(7), global_position["longitude_deg"].round(7)))
    j = 0; matched_gpi = 0; first_match = None
    for i, key in enumerate(csv_keys):
        if key == (0.0, 0.0) or any(pd.isna(v) for v in key):
            continue
        if j < len(gpi_keys) and key == gpi_keys[j]:
            row = global_position.iloc[j]
            result.at[i, "global_position_ref"] = int(row["frame_index"])
            result.at[i, "px4_boot_time_ms"] = float(row["px4_boot_time_ms"])
            result.at[i, "kogger_time_ms"] = float(row["kogger_ltime_ms"])
            result.at[i, "match_method"] = "exact_coordinate_sequence"
            result.at[i, "match_confidence"] = "high"
            result.at[i, "match_residual_m"] = 0.0
            if first_match is None: first_match = i
            j += 1; matched_gpi += 1
        elif j > 0 and key == gpi_keys[j-1]:
            prev = global_position.iloc[j-1]
            result.at[i, "global_position_ref"] = int(prev["frame_index"])
            result.at[i, "px4_boot_time_ms"] = float(prev["px4_boot_time_ms"])
            result.at[i, "kogger_time_ms"] = float(prev["kogger_ltime_ms"])
            result.at[i, "match_method"] = "held_coordinate_state"
            result.at[i, "match_confidence"] = "medium"
            result.at[i, "match_ambiguous"] = True
            result.at[i, "match_residual_m"] = 0.0
    ratio = matched_gpi / len(gpi_keys) if gpi_keys else 0.0
    return result, {"matched_gpi": matched_gpi, "gpi_count": len(gpi_keys), "match_ratio": ratio, "first_csv_index": first_match, "method": "exact_coordinate_sequence_with_held_state", "identity_confidence": "high" if ratio >= 0.99 else ("medium" if ratio >= 0.9 else "low")}
