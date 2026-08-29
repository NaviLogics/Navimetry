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
    transition_cross_track_factor: float = 0.75
    transition_along_track_factor: float = 1.00


def _axes(heading_deg: float) -> tuple[np.ndarray,np.ndarray]:
    heading=math.radians(float(heading_deg))
    axis=np.array([math.cos(heading),math.sin(heading)],dtype=float)
    normal=np.array([-axis[1],axis[0]],dtype=float)
    return axis,normal


def _projected_spans(xy: np.ndarray,faces: np.ndarray,heading_deg: float) -> tuple[np.ndarray,np.ndarray]:
    axis,normal=_axes(heading_deg); vertices=xy[faces]
    return np.ptp(vertices@axis,axis=1),np.ptp(vertices@normal,axis=1)


def _assign_line_membership(xy: np.ndarray, heading_deg: float, spacing: float, track_geometry: dict, defaults: SurveyTriangleQcDefaults) -> tuple[np.ndarray,np.ndarray,np.ndarray,float]:
    """Assign each local surface vertex to the nearest detected survey line.

    line_centers_surface_cross_track_m is expressed relative to the exact-unique-XY mean,
    the same local origin used by the default surface-point/Delaunay path.
    """
    centers=np.asarray(track_geometry.get("line_centers_surface_cross_track_m",[]),dtype=float)
    if len(centers)<2:
        return np.full(len(xy),-1,dtype=np.int32),np.full(len(xy),np.nan),np.array(["unclassified"]*len(xy),dtype=object),0.0
    _,normal=_axes(heading_deg); cross=xy@normal
    delta=np.abs(cross[:,None]-centers[None,:]); nearest=np.argmin(delta,axis=1); distance=delta[np.arange(len(xy)),nearest]
    tolerance=max(0.20,float(spacing)*defaults.membership_tolerance_factor)
    line_id=np.where(distance<=tolerance,nearest,-1).astype(np.int32)
    confidence=np.where(line_id<0,"transition_or_offline",np.where(distance<=tolerance*0.5,"high","medium"))
    return line_id,distance,confidence,float(tolerance)


def _triangle_topology(faces: np.ndarray,line_id: np.ndarray,confidence: np.ndarray) -> tuple[list[str],np.ndarray,np.ndarray]:
    topology=[]; gap=np.full(len(faces),-1,dtype=np.int32); assigned_count=np.zeros(len(faces),dtype=np.int8)
    for i,face in enumerate(faces):
        ids=line_id[face]; assigned=ids[ids>=0]; assigned_count[i]=len(assigned)
        if len(assigned)==3:
            unique=np.unique(assigned)
            if len(unique)==1:
                topology.append("same_line"); gap[i]=0
            elif len(unique)==2:
                g=int(unique.max()-unique.min()); gap[i]=g; topology.append("adjacent_lines" if g==1 else "non_adjacent_lines")
            else:
                gap[i]=int(unique.max()-unique.min()); topology.append("multi_line")
        elif len(assigned)>0:
            topology.append("turn_or_transition")
        else:
            topology.append("unclassified")
    return topology,gap,assigned_count


def evaluate_triangles(metrics: pd.DataFrame,xy: np.ndarray,faces: np.ndarray,config,point_spacing_m: float,effective_geometry: dict,track_geometry: dict) -> tuple[np.ndarray,dict]:
    """Survey-aware QC v2 with line membership and adjacent-line topology.

    v2 keeps the v1 anisotropic span checks, but adds a topology gate. Triangles whose
    vertices are confidently assigned to detected survey lines may connect the same line
    or adjacent lines only. Non-adjacent and three-line bridges are rejected even if their
    metric spans happen to fit the global thresholds. Turn/transition triangles are allowed
    only through a stricter local fallback envelope and remain explicitly labelled.
    """
    if metrics.empty: raise ValueError("Triangulation contains no triangles")
    spacing=effective_geometry.get("effective_line_spacing_m"); heading=track_geometry.get("dominant_axis_heading_deg") if track_geometry else None
    defaults=SurveyTriangleQcDefaults(); cross_factor=float(effective_geometry.get("strict_cross_track_factor",defaults.cross_track_factor)); along_factor=float(effective_geometry.get("strict_along_track_factor",defaults.along_track_factor)); cross_class_factor=float(effective_geometry.get("cross_line_threshold_factor",defaults.cross_line_threshold_factor))
    survey_aware=(spacing is not None and float(spacing)>0 and heading is not None and np.isfinite(float(heading)) and str(effective_geometry.get("geometry","single_beam_centerline"))=="single_beam_centerline")

    angle_ok=metrics["max_angle_deg"].le(float(config.max_triangle_angle_deg)); area_ok=metrics["area_m2"].ge(float(config.min_triangle_area_m2)); gradient_ok=pd.Series(True,index=metrics.index)
    if config.max_depth_gradient_m_per_m is not None: gradient_ok=metrics["depth_gradient_m_per_m"].le(float(config.max_depth_gradient_m_per_m))

    topology_counts={}; membership_info={"available":False}
    if survey_aware:
        spacing=float(spacing); along_span,cross_span=_projected_spans(xy,faces,float(heading)); metrics["along_track_span_m"]=along_span; metrics["cross_track_span_m"]=cross_span
        metrics["triangle_support_class"]=np.where(cross_span>=spacing*cross_class_factor,"cross_line","along_track_or_local")
        cross_limit=spacing*cross_factor; along_limit=spacing*along_factor; cross_ok=metrics["cross_track_span_m"].le(cross_limit); along_ok=metrics["along_track_span_m"].le(along_limit)

        if config.max_triangle_edge_m is not None:
            manual_edge_limit=float(config.max_triangle_edge_m); edge_ok=metrics["max_edge_m"].le(manual_edge_limit); edge_mode="manual_hard_limit"
        else:
            manual_edge_limit=None; edge_ok=pd.Series(True,index=metrics.index); edge_mode="not_used_in_survey_aware_mode"

        line_id,line_distance,line_confidence,membership_tolerance=_assign_line_membership(xy,float(heading),spacing,track_geometry,defaults)
        membership_available=bool(np.any(line_id>=0) and len(track_geometry.get("line_centers_surface_cross_track_m",[]))>=2)
        if membership_available:
            topology,line_gap,assigned_count=_triangle_topology(faces,line_id,line_confidence)
            topology=np.asarray(topology,dtype=object); metrics["v0_line_id"]=line_id[faces[:,0]]; metrics["v1_line_id"]=line_id[faces[:,1]]; metrics["v2_line_id"]=line_id[faces[:,2]]
            metrics["v0_line_distance_m"]=line_distance[faces[:,0]]; metrics["v1_line_distance_m"]=line_distance[faces[:,1]]; metrics["v2_line_distance_m"]=line_distance[faces[:,2]]
            metrics["line_membership_assigned_vertices"]=assigned_count; metrics["line_topology"]=topology; metrics["line_index_gap"]=line_gap
            top_ok=np.isin(topology,["same_line","adjacent_lines"])
            transition=np.isin(topology,["turn_or_transition","unclassified"])
            transition_ok=transition & (cross_span<=spacing*defaults.transition_cross_track_factor) & (along_span<=spacing*defaults.transition_along_track_factor)
            topology_ok=pd.Series(top_ok|transition_ok,index=metrics.index)
            topology_counts={str(k):int(v) for k,v in pd.Series(topology).value_counts().to_dict().items()}
            assigned_vertices=int(np.count_nonzero(line_id>=0)); membership_info={
                "available":True,"line_count":int(len(track_geometry.get("line_centers_surface_cross_track_m",[]))),"membership_tolerance_m":float(membership_tolerance),
                "assigned_vertices":assigned_vertices,"unassigned_vertices":int(len(line_id)-assigned_vertices),"assigned_fraction":float(assigned_vertices/len(line_id)) if len(line_id) else 0.0,
                "transition_cross_track_limit_m":float(spacing*defaults.transition_cross_track_factor),"transition_along_track_limit_m":float(spacing*defaults.transition_along_track_factor),
            }
        else:
            metrics["v0_line_id"]=-1; metrics["v1_line_id"]=-1; metrics["v2_line_id"]=-1; metrics["v0_line_distance_m"]=np.nan; metrics["v1_line_distance_m"]=np.nan; metrics["v2_line_distance_m"]=np.nan
            metrics["line_membership_assigned_vertices"]=0; metrics["line_topology"]="unclassified"; metrics["line_index_gap"]=-1; topology_ok=pd.Series(True,index=metrics.index)

        mask=cross_ok&along_ok&angle_ok&area_ok&gradient_ok&edge_ok&topology_ok; mode="survey_aware_topology_v2" if membership_available else "survey_aware_anisotropic_v1_membership_unavailable"; aspect_ratio_used=False
        metrics["fails_cross_track_span"]=~cross_ok; metrics["fails_along_track_span"]=~along_ok; metrics["fails_line_topology"]=~topology_ok; metrics["fails_max_angle"]=~angle_ok; metrics["fails_min_area"]=~area_ok; metrics["fails_depth_gradient"]=~gradient_ok; metrics["fails_manual_max_edge"]=~edge_ok; metrics["fails_aspect_ratio"]=False
    else:
        derived=effective_geometry.get("strict_max_triangle_edge_m")
        if config.max_triangle_edge_m is not None: edge_limit=float(config.max_triangle_edge_m); edge_mode="manual"
        elif derived is not None: edge_limit=float(derived); edge_mode=str(effective_geometry.get("strict_max_triangle_edge_source","survey_geometry"))
        else: edge_limit=max(0.25,float(metrics["max_edge_m"].quantile(0.75))); edge_mode="fallback_triangle_distribution_q75"
        edge_ok=metrics["max_edge_m"].le(edge_limit); aspect_ok=metrics["aspect_ratio"].le(float(config.max_triangle_aspect_ratio)); mask=edge_ok&aspect_ok&angle_ok&area_ok&gradient_ok
        mode="isotropic_fallback_v1"; aspect_ratio_used=True; cross_limit=None; along_limit=None; manual_edge_limit=edge_limit
        metrics["along_track_span_m"]=np.nan; metrics["cross_track_span_m"]=np.nan; metrics["triangle_support_class"]="unclassified"; metrics["v0_line_id"]=-1; metrics["v1_line_id"]=-1; metrics["v2_line_id"]=-1
        metrics["v0_line_distance_m"]=np.nan; metrics["v1_line_distance_m"]=np.nan; metrics["v2_line_distance_m"]=np.nan; metrics["line_membership_assigned_vertices"]=0; metrics["line_topology"]="unclassified"; metrics["line_index_gap"]=-1
        metrics["fails_cross_track_span"]=False; metrics["fails_along_track_span"]=False; metrics["fails_line_topology"]=False; metrics["fails_max_angle"]=~angle_ok; metrics["fails_min_area"]=~area_ok; metrics["fails_depth_gradient"]=~gradient_ok; metrics["fails_manual_max_edge"]=~edge_ok; metrics["fails_aspect_ratio"]=~aspect_ok

    metrics["quality_status"]=np.where(mask,"accepted","rejected")
    reason_columns=[("fails_cross_track_span","cross_track_span"),("fails_along_track_span","along_track_span"),("fails_line_topology","line_topology"),("fails_max_angle","max_angle"),("fails_min_area","min_area"),("fails_depth_gradient","depth_gradient"),("fails_manual_max_edge","max_edge"),("fails_aspect_ratio","aspect_ratio")]
    metrics["quality_reason"]=[";".join([label for column,label in reason_columns if bool(row[column])]) or "accepted" for _,row in metrics.iterrows()]
    accepted_indices=metrics.loc[mask,"triangle_index"].to_numpy(dtype=np.int64)
    if len(accepted_indices)==0: raise ValueError("Triangle QC rejected all triangles; review survey geometry or manual thresholds")
    rejection_counts={label:int(metrics[column].sum()) for column,label in reason_columns if int(metrics[column].sum())>0}
    info={
        "triangle_qc_version":"survey-aware-v2","triangle_qc_mode":mode,"survey_aware_enabled":bool(survey_aware),"aspect_ratio_used_as_rejection":aspect_ratio_used,
        "dominant_axis_heading_deg":float(heading) if survey_aware else None,"effective_line_spacing_m":float(spacing) if survey_aware else None,
        "strict_cross_track_factor":cross_factor if survey_aware else None,"strict_along_track_factor":along_factor if survey_aware else None,
        "max_cross_track_span_m":float(cross_limit) if cross_limit is not None else None,"max_along_track_span_m":float(along_limit) if along_limit is not None else None,
        "max_triangle_edge_m":manual_edge_limit,"max_triangle_edge_mode":edge_mode,"max_angle_deg":float(config.max_triangle_angle_deg),"min_triangle_area_m2":float(config.min_triangle_area_m2),
        "accepted_triangles":int(mask.sum()),"rejected_triangles":int((~mask).sum()),"rejection_counts":rejection_counts,"topology_counts":topology_counts,"line_membership":membership_info,
        "topology_policy":{"accept":["same_line","adjacent_lines"],"reject":["non_adjacent_lines","multi_line"],"conditional":["turn_or_transition","unclassified"]},"defaults":asdict(defaults),
    }
    return accepted_indices,info
