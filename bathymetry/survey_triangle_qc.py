from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SurveyTriangleQcDefaults:
    cross_track_factor: float = 1.35
    along_track_factor: float = 2.00
    cross_line_threshold_factor: float = 0.25
    membership_tolerance_factor: float = 0.35
    endpoint_margin_factor: float = 0.50
    transition_cross_track_factor: float = 0.75
    transition_along_track_factor: float = 1.00


def _axes(heading_deg: float) -> tuple[np.ndarray,np.ndarray]:
    heading=math.radians(float(heading_deg)); axis=np.array([math.cos(heading),math.sin(heading)],dtype=float); normal=np.array([-axis[1],axis[0]],dtype=float); return axis,normal


def _projected_spans(xy: np.ndarray,faces: np.ndarray,heading_deg: float) -> tuple[np.ndarray,np.ndarray]:
    axis,normal=_axes(heading_deg); vertices=xy[faces]; return np.ptp(vertices@axis,axis=1),np.ptp(vertices@normal,axis=1)


def _assign_corridor_membership(xy: np.ndarray,heading_deg: float,spacing: float,track_geometry: dict,defaults: SurveyTriangleQcDefaults):
    corridors=track_geometry.get("line_corridors_surface_local",[]) or []
    if len(corridors)<2:
        n=len(xy); return np.full(n,-1,dtype=np.int32),np.full(n,np.nan),np.full(n,np.nan),np.array(["unclassified"]*n,dtype=object),0.0,0.0
    axis,normal=_axes(heading_deg); along=xy@axis; cross=xy@normal
    centers=np.asarray([c["cross_track_center_m"] for c in corridors],dtype=float); amin=np.asarray([c["along_track_min_m"] for c in corridors],dtype=float); amax=np.asarray([c["along_track_max_m"] for c in corridors],dtype=float)
    cross_delta=np.abs(cross[:,None]-centers[None,:]); nearest=np.argmin(cross_delta,axis=1); cross_distance=cross_delta[np.arange(len(xy)),nearest]
    below=np.maximum(amin[nearest]-along,0.0); above=np.maximum(along-amax[nearest],0.0); endpoint_distance=below+above
    tolerance=max(0.20,float(spacing)*defaults.membership_tolerance_factor); endpoint_margin=max(0.25,float(spacing)*defaults.endpoint_margin_factor)
    cross_close=cross_distance<=tolerance; inside=endpoint_distance<=1e-9; endpoint=cross_close&(~inside)&(endpoint_distance<=endpoint_margin)
    line_id=np.where(cross_close,nearest,-1).astype(np.int32)
    state=np.where(cross_close&inside,"line",np.where(endpoint,"endpoint_transition","offline"))
    return line_id,cross_distance,endpoint_distance,state.astype(object),float(tolerance),float(endpoint_margin)


def _triangle_topology(faces: np.ndarray,line_id: np.ndarray,state: np.ndarray):
    topology=[]; gap=np.full(len(faces),-1,dtype=np.int32); assigned_count=np.zeros(len(faces),dtype=np.int8)
    for i,face in enumerate(faces):
        ids=line_id[face]; states=state[face]; known=ids[ids>=0]; assigned_count[i]=int(np.count_nonzero(states=="line"))
        if len(known)==0:
            topology.append("unclassified"); continue
        unique=np.unique(known); g=int(unique.max()-unique.min()) if len(unique) else -1; gap[i]=g
        if len(unique)>=3: topology.append("multi_line"); continue
        if len(unique)==2 and g>1: topology.append("non_adjacent_lines"); continue
        if np.any(states=="offline"): topology.append("turn_or_transition"); continue
        if np.any(states=="endpoint_transition"): topology.append("line_endpoint_transition"); continue
        topology.append("same_line" if len(unique)==1 else "adjacent_lines")
    return np.asarray(topology,dtype=object),gap,assigned_count


def evaluate_triangles(metrics: pd.DataFrame,xy: np.ndarray,faces: np.ndarray,config,point_spacing_m: float,effective_geometry: dict,track_geometry: dict) -> tuple[np.ndarray,dict]:
    """Survey-aware QC v3 using finite survey-line corridors and adjacent-line topology."""
    if metrics.empty: raise ValueError("Triangulation contains no triangles")
    spacing=effective_geometry.get("effective_line_spacing_m"); heading=track_geometry.get("dominant_axis_heading_deg") if track_geometry else None; defaults=SurveyTriangleQcDefaults()
    cross_factor=float(effective_geometry.get("strict_cross_track_factor",defaults.cross_track_factor)); along_factor=float(effective_geometry.get("strict_along_track_factor",defaults.along_track_factor)); cross_class_factor=float(effective_geometry.get("cross_line_threshold_factor",defaults.cross_line_threshold_factor))
    survey_aware=(spacing is not None and float(spacing)>0 and heading is not None and np.isfinite(float(heading)) and str(effective_geometry.get("geometry","single_beam_centerline"))=="single_beam_centerline")
    angle_ok=metrics["max_angle_deg"].le(float(config.max_triangle_angle_deg)); area_ok=metrics["area_m2"].ge(float(config.min_triangle_area_m2)); gradient_ok=pd.Series(True,index=metrics.index)
    if config.max_depth_gradient_m_per_m is not None: gradient_ok=metrics["depth_gradient_m_per_m"].le(float(config.max_depth_gradient_m_per_m))
    topology_counts={}; membership_info={"available":False}

    if survey_aware:
        spacing=float(spacing); along_span,cross_span=_projected_spans(xy,faces,float(heading)); metrics["along_track_span_m"]=along_span; metrics["cross_track_span_m"]=cross_span; metrics["triangle_support_class"]=np.where(cross_span>=spacing*cross_class_factor,"cross_line","along_track_or_local")
        cross_limit=spacing*cross_factor; along_limit=spacing*along_factor; cross_ok=metrics["cross_track_span_m"].le(cross_limit); along_ok=metrics["along_track_span_m"].le(along_limit)
        if config.max_triangle_edge_m is not None: manual_edge_limit=float(config.max_triangle_edge_m); edge_ok=metrics["max_edge_m"].le(manual_edge_limit); edge_mode="manual_hard_limit"
        else: manual_edge_limit=None; edge_ok=pd.Series(True,index=metrics.index); edge_mode="not_used_in_survey_aware_mode"

        line_id,line_distance,endpoint_distance,state,membership_tolerance,endpoint_margin=_assign_corridor_membership(xy,float(heading),spacing,track_geometry,defaults)
        membership_available=bool(np.any(state=="line") and len(track_geometry.get("line_corridors_surface_local",[]) or [])>=2)
        if membership_available:
            topology,line_gap,assigned_count=_triangle_topology(faces,line_id,state)
            for j in range(3):
                metrics[f"v{j}_line_id"]=line_id[faces[:,j]]; metrics[f"v{j}_line_distance_m"]=line_distance[faces[:,j]]; metrics[f"v{j}_endpoint_distance_m"]=endpoint_distance[faces[:,j]]; metrics[f"v{j}_membership_state"]=state[faces[:,j]]
            metrics["line_membership_assigned_vertices"]=assigned_count; metrics["line_topology"]=topology; metrics["line_index_gap"]=line_gap
            base_ok=np.isin(topology,["same_line","adjacent_lines"])
            endpoint_top=np.isin(topology,["line_endpoint_transition"]); endpoint_ok=endpoint_top&(cross_span<=spacing*defaults.transition_cross_track_factor)&(along_span<=spacing*defaults.transition_along_track_factor)
            transition=np.isin(topology,["turn_or_transition","unclassified"]); transition_ok=transition&(cross_span<=spacing*defaults.transition_cross_track_factor)&(along_span<=spacing*defaults.transition_along_track_factor)
            topology_ok=pd.Series(base_ok|endpoint_ok|transition_ok,index=metrics.index); topology_counts={str(k):int(v) for k,v in pd.Series(topology).value_counts().to_dict().items()}
            assigned_vertices=int(np.count_nonzero(state=="line")); endpoint_vertices=int(np.count_nonzero(state=="endpoint_transition")); offline_vertices=int(np.count_nonzero(state=="offline"))
            membership_info={"available":True,"line_count":int(len(track_geometry.get("line_corridors_surface_local",[]))),"membership_tolerance_m":membership_tolerance,"endpoint_margin_m":endpoint_margin,
                "corridor_assigned_vertices":assigned_vertices,"endpoint_transition_vertices":endpoint_vertices,"offline_vertices":offline_vertices,"assigned_fraction":float(assigned_vertices/len(line_id)) if len(line_id) else 0.0,
                "endpoint_transition_fraction":float(endpoint_vertices/len(line_id)) if len(line_id) else 0.0,"transition_cross_track_limit_m":float(spacing*defaults.transition_cross_track_factor),"transition_along_track_limit_m":float(spacing*defaults.transition_along_track_factor)}
        else:
            for j in range(3): metrics[f"v{j}_line_id"]=-1; metrics[f"v{j}_line_distance_m"]=np.nan; metrics[f"v{j}_endpoint_distance_m"]=np.nan; metrics[f"v{j}_membership_state"]="unclassified"
            metrics["line_membership_assigned_vertices"]=0; metrics["line_topology"]="unclassified"; metrics["line_index_gap"]=-1; topology_ok=pd.Series(True,index=metrics.index)
        mask=cross_ok&along_ok&angle_ok&area_ok&gradient_ok&edge_ok&topology_ok; mode="survey_aware_finite_corridor_v3" if membership_available else "survey_aware_anisotropic_v1_membership_unavailable"; aspect_ratio_used=False
        metrics["fails_cross_track_span"]=~cross_ok; metrics["fails_along_track_span"]=~along_ok; metrics["fails_line_topology"]=~topology_ok; metrics["fails_max_angle"]=~angle_ok; metrics["fails_min_area"]=~area_ok; metrics["fails_depth_gradient"]=~gradient_ok; metrics["fails_manual_max_edge"]=~edge_ok; metrics["fails_aspect_ratio"]=False
    else:
        derived=effective_geometry.get("strict_max_triangle_edge_m")
        if config.max_triangle_edge_m is not None: edge_limit=float(config.max_triangle_edge_m); edge_mode="manual"
        elif derived is not None: edge_limit=float(derived); edge_mode=str(effective_geometry.get("strict_max_triangle_edge_source","survey_geometry"))
        else: edge_limit=max(0.25,float(metrics["max_edge_m"].quantile(0.75))); edge_mode="fallback_triangle_distribution_q75"
        edge_ok=metrics["max_edge_m"].le(edge_limit); aspect_ok=metrics["aspect_ratio"].le(float(config.max_triangle_aspect_ratio)); mask=edge_ok&aspect_ok&angle_ok&area_ok&gradient_ok
        mode="isotropic_fallback_v1"; aspect_ratio_used=True; cross_limit=None; along_limit=None; manual_edge_limit=edge_limit
        metrics["along_track_span_m"]=np.nan; metrics["cross_track_span_m"]=np.nan; metrics["triangle_support_class"]="unclassified"
        for j in range(3): metrics[f"v{j}_line_id"]=-1; metrics[f"v{j}_line_distance_m"]=np.nan; metrics[f"v{j}_endpoint_distance_m"]=np.nan; metrics[f"v{j}_membership_state"]="unclassified"
        metrics["line_membership_assigned_vertices"]=0; metrics["line_topology"]="unclassified"; metrics["line_index_gap"]=-1; metrics["fails_cross_track_span"]=False; metrics["fails_along_track_span"]=False; metrics["fails_line_topology"]=False; metrics["fails_max_angle"]=~angle_ok; metrics["fails_min_area"]=~area_ok; metrics["fails_depth_gradient"]=~gradient_ok; metrics["fails_manual_max_edge"]=~edge_ok; metrics["fails_aspect_ratio"]=~aspect_ok

    metrics["quality_status"]=np.where(mask,"accepted","rejected"); reason_columns=[("fails_cross_track_span","cross_track_span"),("fails_along_track_span","along_track_span"),("fails_line_topology","line_topology"),("fails_max_angle","max_angle"),("fails_min_area","min_area"),("fails_depth_gradient","depth_gradient"),("fails_manual_max_edge","max_edge"),("fails_aspect_ratio","aspect_ratio")]
    metrics["quality_reason"]=[";".join([label for column,label in reason_columns if bool(row[column])]) or "accepted" for _,row in metrics.iterrows()]
    accepted_indices=metrics.loc[mask,"triangle_index"].to_numpy(dtype=np.int64)
    if len(accepted_indices)==0: raise ValueError("Triangle QC rejected all triangles; review survey geometry or manual thresholds")
    rejection_counts={label:int(metrics[column].sum()) for column,label in reason_columns if int(metrics[column].sum())>0}
    info={"triangle_qc_version":"survey-aware-v3","triangle_qc_mode":mode,"survey_aware_enabled":bool(survey_aware),"aspect_ratio_used_as_rejection":aspect_ratio_used,"dominant_axis_heading_deg":float(heading) if survey_aware else None,
        "effective_line_spacing_m":float(spacing) if survey_aware else None,"strict_cross_track_factor":cross_factor if survey_aware else None,"strict_along_track_factor":along_factor if survey_aware else None,"max_cross_track_span_m":float(cross_limit) if cross_limit is not None else None,"max_along_track_span_m":float(along_limit) if along_limit is not None else None,
        "max_triangle_edge_m":manual_edge_limit,"max_triangle_edge_mode":edge_mode,"max_angle_deg":float(config.max_triangle_angle_deg),"min_triangle_area_m2":float(config.min_triangle_area_m2),"accepted_triangles":int(mask.sum()),"rejected_triangles":int((~mask).sum()),"rejection_counts":rejection_counts,
        "topology_counts":topology_counts,"line_membership":membership_info,"topology_policy":{"accept":["same_line","adjacent_lines"],"reject":["non_adjacent_lines","multi_line"],"conditional":["line_endpoint_transition","turn_or_transition","unclassified"]},"defaults":asdict(defaults)}
    return accepted_indices,info
