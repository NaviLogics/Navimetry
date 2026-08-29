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


def _projected_spans(
    xy: np.ndarray,
    faces: np.ndarray,
    heading_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    heading = math.radians(float(heading_deg))
    axis = np.array([math.cos(heading), math.sin(heading)], dtype=float)
    normal = np.array([-axis[1], axis[0]], dtype=float)
    vertices = xy[faces]
    along = vertices @ axis
    cross = vertices @ normal
    return np.ptp(along, axis=1), np.ptp(cross, axis=1)


def evaluate_triangles(
    metrics: pd.DataFrame,
    xy: np.ndarray,
    faces: np.ndarray,
    config,
    point_spacing_m: float,
    effective_geometry: dict,
    track_geometry: dict,
) -> tuple[np.ndarray, dict]:
    """Apply survey-aware triangle QC when line geometry is available.

    In survey-aware mode the ordinary isotropic aspect-ratio test is deliberately not
    used as a rejection criterion. Dense samples along a survey line can form skinny
    but legitimate triangles between adjacent lines. Instead, triangle span is measured
    in the estimated along-track and cross-track axes. Maximum angle, minimum area and
    optional depth-gradient checks remain active to reject degenerate geometry.

    If line spacing or heading is unavailable, the function falls back to the legacy
    isotropic QC with an explicitly reported fallback mode.
    """
    if metrics.empty:
        raise ValueError("Triangulation contains no triangles")

    spacing = effective_geometry.get("effective_line_spacing_m")
    heading = track_geometry.get("dominant_axis_heading_deg") if track_geometry else None
    defaults = SurveyTriangleQcDefaults()
    cross_factor = float(effective_geometry.get("strict_cross_track_factor", defaults.cross_track_factor))
    along_factor = float(effective_geometry.get("strict_along_track_factor", defaults.along_track_factor))
    cross_class_factor = float(effective_geometry.get("cross_line_threshold_factor", defaults.cross_line_threshold_factor))

    survey_aware = (
        spacing is not None
        and float(spacing) > 0
        and heading is not None
        and np.isfinite(float(heading))
        and str(effective_geometry.get("geometry", "single_beam_centerline")) == "single_beam_centerline"
    )

    # Common engineering checks retained in both modes.
    angle_ok = metrics["max_angle_deg"].le(float(config.max_triangle_angle_deg))
    area_ok = metrics["area_m2"].ge(float(config.min_triangle_area_m2))
    gradient_ok = pd.Series(True, index=metrics.index)
    if config.max_depth_gradient_m_per_m is not None:
        gradient_ok = metrics["depth_gradient_m_per_m"].le(float(config.max_depth_gradient_m_per_m))

    if survey_aware:
        spacing = float(spacing)
        along_span, cross_span = _projected_spans(xy, faces, float(heading))
        metrics["along_track_span_m"] = along_span
        metrics["cross_track_span_m"] = cross_span
        metrics["triangle_support_class"] = np.where(
            cross_span >= spacing * cross_class_factor,
            "cross_line",
            "along_track_or_local",
        )

        cross_limit = spacing * cross_factor
        along_limit = spacing * along_factor
        cross_ok = metrics["cross_track_span_m"].le(cross_limit)
        along_ok = metrics["along_track_span_m"].le(along_limit)

        # An explicit manual max-edge value remains a hard user override. Automatic
        # line-spacing-derived isotropic edge limits are not applied in survey-aware mode.
        if config.max_triangle_edge_m is not None:
            manual_edge_limit = float(config.max_triangle_edge_m)
            edge_ok = metrics["max_edge_m"].le(manual_edge_limit)
            edge_mode = "manual_hard_limit"
        else:
            manual_edge_limit = None
            edge_ok = pd.Series(True, index=metrics.index)
            edge_mode = "not_used_in_survey_aware_mode"

        mask = cross_ok & along_ok & angle_ok & area_ok & gradient_ok & edge_ok
        mode = "survey_aware_anisotropic_v1"
        aspect_ratio_used = False

        metrics["fails_cross_track_span"] = ~cross_ok
        metrics["fails_along_track_span"] = ~along_ok
        metrics["fails_max_angle"] = ~angle_ok
        metrics["fails_min_area"] = ~area_ok
        metrics["fails_depth_gradient"] = ~gradient_ok
        metrics["fails_manual_max_edge"] = ~edge_ok
        metrics["fails_aspect_ratio"] = False
    else:
        derived = effective_geometry.get("strict_max_triangle_edge_m")
        if config.max_triangle_edge_m is not None:
            edge_limit = float(config.max_triangle_edge_m)
            edge_mode = "manual"
        elif derived is not None:
            edge_limit = float(derived)
            edge_mode = str(effective_geometry.get("strict_max_triangle_edge_source", "survey_geometry"))
        else:
            edge_limit = max(0.25, float(metrics["max_edge_m"].quantile(0.75)))
            edge_mode = "fallback_triangle_distribution_q75"
        edge_ok = metrics["max_edge_m"].le(edge_limit)
        aspect_ok = metrics["aspect_ratio"].le(float(config.max_triangle_aspect_ratio))
        mask = edge_ok & aspect_ok & angle_ok & area_ok & gradient_ok
        mode = "isotropic_fallback_v1"
        aspect_ratio_used = True
        cross_limit = None
        along_limit = None
        manual_edge_limit = edge_limit

        metrics["along_track_span_m"] = np.nan
        metrics["cross_track_span_m"] = np.nan
        metrics["triangle_support_class"] = "unclassified"
        metrics["fails_cross_track_span"] = False
        metrics["fails_along_track_span"] = False
        metrics["fails_max_angle"] = ~angle_ok
        metrics["fails_min_area"] = ~area_ok
        metrics["fails_depth_gradient"] = ~gradient_ok
        metrics["fails_manual_max_edge"] = ~edge_ok
        metrics["fails_aspect_ratio"] = ~aspect_ok

    metrics["quality_status"] = np.where(mask, "accepted", "rejected")

    reason_columns = [
        ("fails_cross_track_span", "cross_track_span"),
        ("fails_along_track_span", "along_track_span"),
        ("fails_max_angle", "max_angle"),
        ("fails_min_area", "min_area"),
        ("fails_depth_gradient", "depth_gradient"),
        ("fails_manual_max_edge", "max_edge"),
        ("fails_aspect_ratio", "aspect_ratio"),
    ]
    reasons = []
    for _, row in metrics.iterrows():
        failed = [label for column, label in reason_columns if bool(row[column])]
        reasons.append(";".join(failed) if failed else "accepted")
    metrics["quality_reason"] = reasons

    accepted_indices = metrics.loc[mask, "triangle_index"].to_numpy(dtype=np.int64)
    if len(accepted_indices) == 0:
        raise ValueError("Triangle QC rejected all triangles; review survey geometry or manual thresholds")

    rejection_counts = {
        label: int(metrics[column].sum())
        for column, label in reason_columns
        if int(metrics[column].sum()) > 0
    }
    info = {
        "triangle_qc_version": "survey-aware-v1",
        "triangle_qc_mode": mode,
        "survey_aware_enabled": bool(survey_aware),
        "aspect_ratio_used_as_rejection": aspect_ratio_used,
        "dominant_axis_heading_deg": float(heading) if survey_aware else None,
        "effective_line_spacing_m": float(spacing) if survey_aware else None,
        "strict_cross_track_factor": cross_factor if survey_aware else None,
        "strict_along_track_factor": along_factor if survey_aware else None,
        "max_cross_track_span_m": float(cross_limit) if cross_limit is not None else None,
        "max_along_track_span_m": float(along_limit) if along_limit is not None else None,
        "max_triangle_edge_m": manual_edge_limit,
        "max_triangle_edge_mode": edge_mode,
        "max_angle_deg": float(config.max_triangle_angle_deg),
        "min_triangle_area_m2": float(config.min_triangle_area_m2),
        "accepted_triangles": int(mask.sum()),
        "rejected_triangles": int((~mask).sum()),
        "rejection_counts": rejection_counts,
        "defaults": asdict(defaults),
    }
    return accepted_indices, info
