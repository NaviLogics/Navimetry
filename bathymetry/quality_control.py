from __future__ import annotations
import json
import numpy as np
import pandas as pd
from pyproj import CRS,Transformer
from bathymetry.models import ProcessingConfig

class QualityError(RuntimeError):pass

def _sustained_sentinel_mask(values:pd.Series,config:ProcessingConfig)->pd.Series:
    sentinel=config.beam_invalid_sentinel_m
    if sentinel is None:return pd.Series(False,index=values.index)
    numeric=pd.to_numeric(values,errors="coerce"); candidate=pd.Series(np.isclose(numeric.to_numpy(float),float(sentinel),rtol=0,atol=float(config.beam_invalid_sentinel_tolerance_m),equal_nan=False),index=values.index)
    if not candidate.any():return candidate
    groups=candidate.ne(candidate.shift(fill_value=False)).cumsum(); runs=candidate.groupby(groups).transform("sum")
    return candidate & runs.ge(max(1,int(config.beam_invalid_sentinel_min_run)))

def normalize_observations(csv_result,klf_result,match_result,config:ProcessingConfig)->pd.DataFrame:
    obs=csv_result.normalized.copy(); n=len(obs); obs["latitude_deg"]=obs["latitude_raw_deg"]; obs["longitude_deg"]=obs["longitude_raw_deg"]; obs["beam_distance_used_m"]=obs["beam_distance_raw_m"]; obs["depth_primary_m"]=obs["beam_distance_raw_m"]; obs["depth_source"]=config.depth_source; obs["position_source"]="CSV_COORDINATES"; obs["x_m"]=np.nan; obs["y_m"]=np.nan; obs["timestamp_utc"]=None; obs["timestamp_quality"]="unavailable"; obs["time_quality"]="unavailable"; obs["attitude_quality"]="unavailable"; obs["coverage_quality"]="unknown"; obs["manual_edit_status"]="unknown"; obs["offset_applied_in_navimetry"]=False; obs["quality_flags"]=[[] for _ in range(n)]
    id_dist_count=int(klf_result.inventory.id_counts.get(2,0)) if klf_result is not None else 0; raw_rf=obs["rangefinder_raw_m"]; unavailable=(raw_rf.isna()|(raw_rf==0)) if config.rangefinder_zero_is_unavailable and id_dist_count==0 else raw_rf.isna(); obs["rangefinder_m"]=raw_rf.where(~unavailable,np.nan); obs["rangefinder_available"]=~unavailable; obs["rangefinder_unavailable_reason"]=np.where(unavailable,config.rangefinder_unavailable_reason if id_dist_count==0 else "missing",None)
    if match_result is not None:
        for c in match_result.columns:obs[c]=match_result[c].values
        matched=obs["global_position_ref"].notna() if "global_position_ref" in obs else pd.Series(False,index=obs.index)
        if "gpi_latitude_deg" in obs:
            good=matched & obs["gpi_latitude_deg"].notna() & obs["gpi_longitude_deg"].notna(); obs.loc[good,"latitude_deg"]=obs.loc[good,"gpi_latitude_deg"]; obs.loc[good,"longitude_deg"]=obs.loc[good,"gpi_longitude_deg"]
        obs.loc[matched,"position_source"]="PX4_GLOBAL_POSITION_INT@KOGGER_LTIME"; obs.loc[matched,"timestamp_quality"]=np.where(obs.loc[matched,"match_confidence"].eq("high"),"derived_high","derived_ambiguous"); obs.loc[matched,"time_quality"]=obs.loc[matched,"timestamp_quality"]
        if "attitude_age_ms" in obs:obs.loc[obs["attitude_age_ms"].notna(),"attitude_quality"]="matched_by_kogger_ltime"
        if "timestamp_utc_from_system_time" in obs:obs["timestamp_utc"]=obs["timestamp_utc_from_system_time"]
    obs["position_quality"]="valid"; invalid_position=obs["latitude_deg"].isna()|obs["longitude_deg"].isna()|~obs["latitude_deg"].between(-90,90)|~obs["longitude_deg"].between(-180,180)|((obs["latitude_deg"]==0)&(obs["longitude_deg"]==0)); obs.loc[invalid_position,"position_quality"]="rejected"
    obs["depth_quality"]="valid"; missing_depth=obs["depth_primary_m"].isna()|~np.isfinite(obs["depth_primary_m"]); invalid_depth=(~missing_depth)&((obs["depth_primary_m"]<=0)|(obs["depth_primary_m"]<config.min_depth_m)|(obs["depth_primary_m"]>config.max_depth_m)); sentinel=_sustained_sentinel_mask(obs["beam_distance_raw_m"],config); invalid_depth|=sentinel; obs.loc[missing_depth,"depth_quality"]="missing"; obs.loc[invalid_depth,"depth_quality"]="rejected"
    def flag(mask,name):
        for idx in obs.index[mask]:obs.at[idx,"quality_flags"]=obs.at[idx,"quality_flags"]+[name]
    flag(invalid_position,"invalid_position"); flag(missing_depth,"missing_primary_depth"); flag(invalid_depth&~sentinel,"invalid_primary_depth"); flag(sentinel,"sonar_no_bottom_sentinel"); flag(~obs["rangefinder_available"],"rangefinder_unavailable")
    if "gps_fix_type" in obs:
        no_rtk=obs["gps_fix_type"].notna() & obs["gps_fix_type"].lt(5); flag(no_rtk,"gnss_not_rtk")
    if "gps_age_ms" in obs:flag(obs["gps_age_ms"].isna(),"gnss_time_match_unavailable")
    if "attitude_age_ms" in obs:flag(obs["attitude_age_ms"].isna(),"attitude_time_match_unavailable")
    source_crs=CRS.from_user_input(config.input_crs); target_crs=CRS.from_user_input(config.output_crs)
    if not target_crs.is_projected:raise QualityError("Output CRS must be projected")
    valid_pos=obs["position_quality"].eq("valid"); transformer=Transformer.from_crs(source_crs,target_crs,always_xy=True); x,y=transformer.transform(obs.loc[valid_pos,"longitude_deg"].to_numpy(float),obs.loc[valid_pos,"latitude_deg"].to_numpy(float)); obs.loc[valid_pos,"x_m"]=x; obs.loc[valid_pos,"y_m"]=y; bad=valid_pos&(~np.isfinite(obs["x_m"])|~np.isfinite(obs["y_m"])); obs.loc[bad,"position_quality"]="rejected"; flag(bad,"coordinate_transform_failed")
    valid_depth=obs["depth_quality"].eq("valid"); jump=valid_depth&obs["depth_primary_m"].diff().abs().gt(config.max_depth_jump_m); obs.loc[jump,"depth_quality"]="suspect"; flag(jump,"depth_jump")
    step=np.hypot(obs["x_m"].diff(),obs["y_m"].diff()); obs["coordinate_step_m"]=step; cjump=obs["position_quality"].eq("valid")&step.gt(config.max_coordinate_jump_m); obs.loc[cjump,"position_quality"]="suspect"; flag(cjump,"coordinate_jump")
    prev=obs["beam_distance_raw_m"].shift(1); obs["beam_value_state"]=np.where(obs["beam_distance_raw_m"].isna(),"missing",np.where(obs["beam_distance_raw_m"].eq(prev),"held_or_equal","updated_or_changed"))
    surface_ok=obs["position_quality"].eq("valid")&obs["depth_quality"].eq("valid")
    if config.include_suspect_points:surface_ok=obs["position_quality"].isin(["valid","suspect"])&obs["depth_quality"].isin(["valid","suspect"])
    obs["overall_quality"]=np.where(surface_ok,"valid_for_surface","rejected_for_surface"); obs["quality_flags_json"]=obs["quality_flags"].apply(lambda v:json.dumps(v,ensure_ascii=False)); obs["quality_reason"]=obs["quality_flags"].apply(lambda v:"; ".join(v)); return obs

def build_surface_points(observations:pd.DataFrame,aggregation_mode:str="none")->pd.DataFrame:
    accepted=observations[observations["overall_quality"]=="valid_for_surface"].copy()
    if len(accepted)<3:raise QualityError("Fewer than three surface-eligible observations")
    if aggregation_mode=="spatial_grid":
        accepted["gx"]=accepted["x_m"].round(2); accepted["gy"]=accepted["y_m"].round(2); return accepted.groupby(["gx","gy"],as_index=False).agg(x_m=("x_m","median"),y_m=("y_m","median"),depth_primary_m=("depth_primary_m","median"),beam_distance_raw_m=("beam_distance_raw_m","median"),sample_count=("observation_id","count"),quality_code=("observation_id",lambda s:1))
    grouped=accepted.groupby(["x_m","y_m"],as_index=False).agg(depth_primary_m=("depth_primary_m","median"),beam_distance_raw_m=("beam_distance_raw_m","median"),sample_count=("observation_id","count")); grouped["quality_code"]=1; return grouped
