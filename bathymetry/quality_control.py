from __future__ import annotations
import json
import math
import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from scipy.spatial import cKDTree
from bathymetry.models import ProcessingConfig


class QualityError(RuntimeError):
    pass


def normalize_observations(csv_result, klf_result, match_result, config: ProcessingConfig) -> pd.DataFrame:
    obs = csv_result.normalized.copy()
    n = len(obs)
    obs["latitude_deg"] = obs["latitude_raw_deg"]
    obs["longitude_deg"] = obs["longitude_raw_deg"]
    obs["beam_distance_used_m"] = obs["beam_distance_raw_m"]
    obs["depth_primary_m"] = obs["beam_distance_raw_m"]
    obs["depth_source"] = config.depth_source
    obs["position_source"] = "CSV_COORDINATES"
    obs["x_m"] = np.nan
    obs["y_m"] = np.nan
    obs["timestamp_utc"] = None
    obs["timestamp_quality"] = "unavailable"
    obs["time_quality"] = "unavailable"
    obs["attitude_quality"] = "unavailable"
    obs["coverage_quality"] = "unknown"
    obs["manual_edit_status"] = "unknown"
    obs["offset_applied_in_navimetry"] = False
    obs["quality_flags"] = [[] for _ in range(n)]

    id_dist_count = 0
    if klf_result is not None:
        id_dist_count = int(klf_result.inventory.id_counts.get(2, 0))
    raw_rf = obs["rangefinder_raw_m"]
    if config.rangefinder_zero_is_unavailable and id_dist_count == 0:
        unavailable = raw_rf.isna() | (raw_rf == 0)
    else:
        unavailable = raw_rf.isna()
    obs["rangefinder_m"] = raw_rf.where(~unavailable, np.nan)
    obs["rangefinder_available"] = ~unavailable
    obs["rangefinder_unavailable_reason"] = np.where(
        unavailable, config.rangefinder_unavailable_reason if id_dist_count == 0 else "missing", None
    )

    if match_result is not None:
        for c in match_result.columns:
            obs[c] = match_result[c].values
        matched = obs["global_position_ref"].notna() if "global_position_ref" in obs else pd.Series(False, index=obs.index)
        obs.loc[matched, "position_source"] = "PX4_GLOBAL_POSITION_INT"
        obs.loc[matched, "timestamp_quality"] = np.where(
            obs.loc[matched, "match_confidence"].eq("high"), "derived_high", "derived_ambiguous"
        )
        obs.loc[matched, "time_quality"] = obs.loc[matched, "timestamp_quality"]

    obs["position_quality"] = "valid"
    invalid_position = (
        obs["latitude_deg"].isna() | obs["longitude_deg"].isna()
        | ~obs["latitude_deg"].between(-90, 90)
        | ~obs["longitude_deg"].between(-180, 180)
        | ((obs["latitude_deg"] == 0) & (obs["longitude_deg"] == 0))
    )
    obs.loc[invalid_position, "position_quality"] = "rejected"

    obs["depth_quality"] = "valid"
    missing_depth = obs["depth_primary_m"].isna() | ~np.isfinite(obs["depth_primary_m"])
    invalid_depth = (~missing_depth) & (
        (obs["depth_primary_m"] <= 0)
        | (obs["depth_primary_m"] < config.min_depth_m)
        | (obs["depth_primary_m"] > config.max_depth_m)
    )
    obs.loc[missing_depth, "depth_quality"] = "missing"
    obs.loc[invalid_depth, "depth_quality"] = "rejected"

    for idx in obs.index[invalid_position]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["invalid_position"]
    for idx in obs.index[missing_depth]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["missing_primary_depth"]
    for idx in obs.index[invalid_depth]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["invalid_primary_depth"]
    for idx in obs.index[~obs["rangefinder_available"]]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["rangefinder_unavailable"]

    source_crs = CRS.from_user_input(config.input_crs)
    target_crs = CRS.from_user_input(config.output_crs)
    if not target_crs.is_projected:
        raise QualityError("Output CRS must be projected")
    valid_pos = obs["position_quality"].eq("valid")
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    x, y = transformer.transform(
        obs.loc[valid_pos, "longitude_deg"].to_numpy(dtype=float),
        obs.loc[valid_pos, "latitude_deg"].to_numpy(dtype=float),
    )
    obs.loc[valid_pos, "x_m"] = x
    obs.loc[valid_pos, "y_m"] = y
    finite_xy = np.isfinite(obs["x_m"]) & np.isfinite(obs["y_m"])
    bad_transform = valid_pos & ~finite_xy
    obs.loc[bad_transform, "position_quality"] = "rejected"
    for idx in obs.index[bad_transform]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["coordinate_transform_failed"]

    valid_depth = obs["depth_quality"].eq("valid")
    depth_delta = obs["depth_primary_m"].diff().abs()
    depth_jump = valid_depth & depth_delta.gt(config.max_depth_jump_m)
    obs.loc[depth_jump, "depth_quality"] = "suspect"
    for idx in obs.index[depth_jump]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["depth_jump"]

    dx = obs["x_m"].diff(); dy = obs["y_m"].diff()
    coord_jump_m = np.hypot(dx, dy)
    obs["coordinate_step_m"] = coord_jump_m
    coordinate_jump = obs["position_quality"].eq("valid") & coord_jump_m.gt(config.max_coordinate_jump_m)
    obs.loc[coordinate_jump, "position_quality"] = "suspect"
    for idx in obs.index[coordinate_jump]:
        obs.at[idx, "quality_flags"] = obs.at[idx, "quality_flags"] + ["coordinate_jump"]

    prev_beam = obs["beam_distance_raw_m"].shift(1)
    obs["beam_value_state"] = np.where(
        obs["beam_distance_raw_m"].isna(), "missing",
        np.where(obs["beam_distance_raw_m"].eq(prev_beam), "held_or_equal", "updated_or_changed")
    )

    surface_ok = obs["position_quality"].isin(["valid"]) & obs["depth_quality"].isin(["valid"])
    if config.include_suspect_points:
        surface_ok = obs["position_quality"].isin(["valid", "suspect"]) & obs["depth_quality"].isin(["valid", "suspect"])
    obs["overall_quality"] = np.where(surface_ok, "valid_for_surface", "rejected_for_surface")
    obs["quality_flags_json"] = obs["quality_flags"].apply(lambda v: json.dumps(v, ensure_ascii=False))
    obs["quality_reason"] = obs["quality_flags"].apply(lambda v: "; ".join(v))
    return obs


def build_surface_points(observations: pd.DataFrame, aggregation_mode: str = "none") -> pd.DataFrame:
    accepted = observations[observations["overall_quality"] == "valid_for_surface"].copy()
    if len(accepted) < 3:
        raise QualityError("Fewer than three surface-eligible observations")
    if aggregation_mode == "spatial_grid":
        accepted["gx"] = accepted["x_m"].round(2)
        accepted["gy"] = accepted["y_m"].round(2)
        return accepted.groupby(["gx", "gy"], as_index=False).agg(
            x_m=("x_m", "median"), y_m=("y_m", "median"), depth_primary_m=("depth_primary_m", "median"),
            beam_distance_raw_m=("beam_distance_raw_m", "median"), sample_count=("observation_id", "count"),
            quality_code=("observation_id", lambda s: 1),
        )
    grouped = accepted.groupby(["x_m", "y_m"], as_index=False).agg(
        depth_primary_m=("depth_primary_m", "median"), beam_distance_raw_m=("beam_distance_raw_m", "median"),
        sample_count=("observation_id", "count")
    )
    grouped["quality_code"] = 1
    return grouped
