from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TrackSegmentationConfig:
    heading_tolerance_deg: float = 25.0
    smoothing_window: int = 9
    min_segment_points: int = 12
    min_segment_length_m: float = 2.0
    minimum_motion_m: float = 0.002


def _axis_angle_difference(angle: np.ndarray, reference: float) -> np.ndarray:
    return np.abs((angle-reference+math.pi/2.0)%math.pi-math.pi/2.0)


def _smooth_axis_heading(headings: np.ndarray, window: int) -> np.ndarray:
    if len(headings)==0: return headings.copy()
    window=max(1,int(window)); doubled=np.exp(2j*headings)
    real=pd.Series(doubled.real).rolling(window,center=True,min_periods=1).mean().to_numpy()
    imag=pd.Series(doubled.imag).rolling(window,center=True,min_periods=1).mean().to_numpy()
    return 0.5*np.arctan2(imag,real)


def _dominant_axis(headings: np.ndarray,lengths: np.ndarray) -> float:
    if len(headings)==0: return 0.0
    positive=lengths[lengths>0]; clip=float(np.quantile(positive,0.90)) if len(positive) else 1.0
    weights=np.clip(lengths,0.0,max(clip,1e-9)); vector=np.sum(weights*np.exp(2j*headings))
    if abs(vector)<1e-12: return float(np.median(headings)%math.pi)
    return float((0.5*np.angle(vector))%math.pi)


def _contiguous_runs(mask: np.ndarray) -> list[tuple[int,int]]:
    runs=[]; start=None
    for i,value in enumerate(mask.tolist()):
        if value and start is None: start=i
        elif not value and start is not None: runs.append((start,i-1)); start=None
    if start is not None: runs.append((start,len(mask)-1))
    return runs


def estimate_track_geometry(observations: pd.DataFrame,settings: TrackSegmentationConfig|None=None) -> tuple[dict,pd.DataFrame]:
    """Segment ordered survey tracks and estimate finite survey-line corridors.

    Geometry v2 keeps the dominant undirected survey axis and cross-track line clustering,
    but each detected line also receives a finite along-track extent. Corridor coordinates
    are expressed in the exact-unique-XY local frame used by the default Delaunay path.
    """
    settings=settings or TrackSegmentationConfig()
    if not {"x_m","y_m"}.issubset(observations.columns):
        return {"status":"unavailable","reason":"missing_projected_coordinates","settings":asdict(settings)},pd.DataFrame()

    accepted=observations.copy()
    if "overall_quality" in accepted.columns: accepted=accepted[accepted["overall_quality"].eq("valid_for_surface")]
    accepted=accepted[np.isfinite(accepted["x_m"])&np.isfinite(accepted["y_m"])].copy()
    if len(accepted)<max(3,settings.min_segment_points):
        return {"status":"unavailable","reason":"too_few_ordered_surface_observations","input_observations":int(len(accepted)),"settings":asdict(settings)},pd.DataFrame()

    unique_xy=accepted[["x_m","y_m"]].drop_duplicates().to_numpy(dtype=float); surface_origin=np.mean(unique_xy,axis=0)
    ordered=accepted.copy(); xy=ordered[["x_m","y_m"]].to_numpy(dtype=float)
    step=np.hypot(np.diff(xy[:,0]),np.diff(xy[:,1])); keep=np.r_[True,step>settings.minimum_motion_m]
    ordered=ordered.iloc[np.flatnonzero(keep)].copy().reset_index(drop=True); xy=ordered[["x_m","y_m"]].to_numpy(dtype=float)
    if len(xy)<max(3,settings.min_segment_points):
        return {"status":"unavailable","reason":"too_few_moving_observations","input_observations":int(len(xy)),"settings":asdict(settings)},pd.DataFrame()

    delta=np.diff(xy,axis=0); lengths=np.hypot(delta[:,0],delta[:,1]); headings=np.arctan2(delta[:,1],delta[:,0])
    smooth_heading=_smooth_axis_heading(headings,settings.smoothing_window); dominant=_dominant_axis(smooth_heading,lengths); deviation=_axis_angle_difference(smooth_heading,dominant)
    positive=lengths[lengths>settings.minimum_motion_m]; typical_step=float(np.median(positive)) if len(positive) else settings.minimum_motion_m
    p99_step=float(np.quantile(positive,0.99)) if len(positive) else typical_step; gap_limit=max(1.0,typical_step*25.0,p99_step*3.0)
    on_axis=(deviation<=math.radians(settings.heading_tolerance_deg))&(lengths<=gap_limit)&(lengths>settings.minimum_motion_m)

    axis=np.array([math.cos(dominant),math.sin(dominant)],dtype=float); normal=np.array([-axis[1],axis[0]],dtype=float); origin=np.mean(xy,axis=0); segments=[]
    for segment_id,(edge_start,edge_end) in enumerate(_contiguous_runs(on_axis),start=1):
        point_start=edge_start; point_end=edge_end+1; pts=xy[point_start:point_end+1]; n_points=len(pts); path_length=float(np.sum(lengths[edge_start:edge_end+1]))
        if n_points<settings.min_segment_points or path_length<settings.min_segment_length_m: continue
        local=pts-origin; cross=local@normal; along=local@axis; cross_center=float(np.median(cross)); cross_mad=float(np.median(np.abs(cross-cross_center)))
        row_start=ordered.iloc[point_start]; row_end=ordered.iloc[point_end]
        segments.append({
            "segment_id":segment_id,"start_source_row":int(row_start["source_row"]) if "source_row" in ordered and pd.notna(row_start.get("source_row")) else None,
            "end_source_row":int(row_end["source_row"]) if "source_row" in ordered and pd.notna(row_end.get("source_row")) else None,
            "start_observation_id":int(row_start["observation_id"]) if "observation_id" in ordered and pd.notna(row_start.get("observation_id")) else None,
            "end_observation_id":int(row_end["observation_id"]) if "observation_id" in ordered and pd.notna(row_end.get("observation_id")) else None,
            "point_count":int(n_points),"path_length_m":path_length,"axis_heading_deg":float(math.degrees(dominant)%180.0),"cross_track_offset_m":cross_center,
            "cross_track_mad_m":cross_mad,"along_track_min_m":float(np.min(along)),"along_track_max_m":float(np.max(along)),"along_track_span_m":float(np.ptp(along)),
        })

    tracks=pd.DataFrame(segments)
    if tracks.empty:
        return {"status":"unavailable","reason":"no_straight_survey_segments_detected","input_observations":int(len(ordered)),"dominant_axis_heading_deg":float(math.degrees(dominant)%180.0),"typical_step_m":typical_step,"gap_limit_m":gap_limit,"settings":asdict(settings)},tracks

    offsets=np.sort(tracks["cross_track_offset_m"].to_numpy(dtype=float)); lateral_mad=float(np.median(tracks["cross_track_mad_m"].to_numpy(dtype=float)))
    cluster_tolerance=float(np.clip(max(0.15,lateral_mad*6.0),0.15,1.0)); clusters=[]
    for value in offsets:
        if not clusters or value-float(np.mean(clusters[-1]))>cluster_tolerance: clusters.append([float(value)])
        else: clusters[-1].append(float(value))
    centers=np.array([float(np.median(group)) for group in clusters],dtype=float); spacings=np.diff(centers); spacings=spacings[spacings>cluster_tolerance]
    if len(spacings):
        spacing=float(np.median(spacings)); spacing_mad=float(np.median(np.abs(spacings-spacing))); relative_mad=spacing_mad/spacing if spacing>0 else math.inf
        confidence="high" if len(spacings)>=3 and relative_mad<=0.20 else "medium"; status="estimated"; reason=None
    else:
        spacing=None; spacing_mad=None; confidence="low"; status="unavailable"; reason="fewer_than_two_distinct_survey_lines"

    # Assign every straight segment to its nearest cross-track line cluster.
    track_offsets=tracks["cross_track_offset_m"].to_numpy(dtype=float); track_line_ids=np.argmin(np.abs(track_offsets[:,None]-centers[None,:]),axis=1).astype(np.int32)
    tracks["survey_line_id"]=track_line_ids

    cross_shift=float((origin-surface_origin)@normal); along_shift=float((origin-surface_origin)@axis); surface_centers=centers+cross_shift
    tracks["surface_cross_track_center_m"]=tracks["cross_track_offset_m"]+cross_shift
    tracks["surface_along_track_min_m"]=tracks["along_track_min_m"]+along_shift; tracks["surface_along_track_max_m"]=tracks["along_track_max_m"]+along_shift

    corridors=[]
    for line_id,center in enumerate(surface_centers.tolist()):
        group=tracks[tracks["survey_line_id"].eq(line_id)]
        if group.empty: continue
        corridor_cross_mad=float(np.median(group["cross_track_mad_m"].to_numpy(float)))
        corridors.append({
            "survey_line_id":int(line_id),"cross_track_center_m":float(center),"along_track_min_m":float(group["surface_along_track_min_m"].min()),
            "along_track_max_m":float(group["surface_along_track_max_m"].max()),"segment_count":int(len(group)),"cross_track_mad_m":corridor_cross_mad,
            "path_length_sum_m":float(group["path_length_m"].sum()),
        })

    summary={
        "status":status,"reason":reason,"input_observations":int(len(ordered)),"detected_segments":int(len(tracks)),"distinct_line_clusters":int(len(centers)),
        "dominant_axis_heading_deg":float(math.degrees(dominant)%180.0),"estimated_line_spacing_m":spacing,"line_spacing_mad_m":spacing_mad,"line_spacing_samples":int(len(spacings)),
        "confidence":confidence,"typical_step_m":typical_step,"gap_limit_m":gap_limit,"line_cluster_tolerance_m":cluster_tolerance,"line_centers_cross_track_m":centers.tolist(),
        "surface_reference_origin_x_m":float(surface_origin[0]),"surface_reference_origin_y_m":float(surface_origin[1]),"line_centers_surface_cross_track_m":surface_centers.tolist(),
        "line_corridors_surface_local":corridors,"surface_reference_mode":"mean_of_exact_unique_surface_xy","geometry_version":"2","corridor_model":"finite_along_track_extent",
        "settings":asdict(settings),"method":"ordered_track_segmentation_dominant_axis_cross_track_clusters_finite_corridors",
    }
    return summary,tracks
