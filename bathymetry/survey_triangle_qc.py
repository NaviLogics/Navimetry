from __future__ import annotations

import numpy as np
import pandas as pd


def evaluate_triangles(metrics: pd.DataFrame, xy: np.ndarray, faces: np.ndarray, config,
                       point_spacing_m: float, effective_geometry: dict,
                       track_geometry: dict) -> tuple[np.ndarray, dict]:
    """Standard GIS/TIN support filter.

    The surface is a Delaunay TIN with linear interpolation.  The only automatic
    spatial gap protection is a maximum triangle-edge length, matching the
    conventional point-cloud-to-raster TIN workflow exposed by QGIS/PDAL.
    Survey-line classification, corridor membership, turn recognition, triangle
    aspect ratio and maximum interior angle are deliberately not used to decide
    whether a triangle represents the measured surface.

    A user-supplied max_triangle_edge_m is authoritative.  Otherwise Navimetry
    derives the edge limit from the estimated line spacing/preset.  The derived
    value and its provenance are written to the processing report.
    """
    if metrics.empty:
        raise ValueError("Triangulation contains no triangles")

    derived = effective_geometry.get("strict_max_triangle_edge_m")
    if config.max_triangle_edge_m is not None:
        edge_limit = float(config.max_triangle_edge_m)
        edge_mode = "manual"
    elif derived is not None and np.isfinite(float(derived)) and float(derived) > 0:
        edge_limit = float(derived)
        edge_mode = str(effective_geometry.get(
            "strict_max_triangle_edge_source", "derived_from_survey_spacing"))
    else:
        # Last-resort deterministic fallback when no survey spacing can be
        # estimated.  It is intentionally reported as a fallback rather than
        # presented as a survey-standard threshold.
        edge_limit = max(0.25, float(metrics["max_edge_m"].quantile(0.75)))
        edge_mode = "fallback_triangle_distribution_q75"

    edge_ok = metrics["max_edge_m"].le(edge_limit)
    area_ok = metrics["area_m2"].ge(float(config.min_triangle_area_m2))
    gradient_ok = pd.Series(True, index=metrics.index)
    if config.max_depth_gradient_m_per_m is not None:
        gradient_ok = metrics["depth_gradient_m_per_m"].le(
            float(config.max_depth_gradient_m_per_m)
        )

    mask = edge_ok & area_ok & gradient_ok

    # Keep the historical diagnostic columns so project exports and downstream
    # readers remain schema-compatible. They are diagnostics only in GIS/TIN v1.
    metrics["along_track_span_m"] = np.nan
    metrics["cross_track_span_m"] = np.nan
    metrics["triangle_support_class"] = "standard_delaunay_tin"
    metrics["adaptive_angle_bypass"] = False
    for j in range(3):
        metrics[f"v{j}_line_id"] = -1
        metrics[f"v{j}_line_distance_m"] = np.nan
        metrics[f"v{j}_endpoint_distance_m"] = np.nan
        metrics[f"v{j}_membership_state"] = "not_used"
    metrics["line_membership_assigned_vertices"] = 0
    metrics["line_topology"] = "not_used"
    metrics["line_index_gap"] = -1

    metrics["fails_cross_track_span"] = False
    metrics["fails_along_track_span"] = False
    metrics["fails_line_topology"] = False
    metrics["fails_max_angle"] = False
    metrics["fails_min_area"] = ~area_ok
    metrics["fails_depth_gradient"] = ~gradient_ok
    metrics["fails_manual_max_edge"] = ~edge_ok
    metrics["fails_aspect_ratio"] = False
    metrics["quality_status"] = np.where(mask, "accepted", "rejected")

    def reason(row) -> str:
        reasons = []
        if bool(row["fails_manual_max_edge"]):
            reasons.append("max_edge")
        if bool(row["fails_min_area"]):
            reasons.append("min_area")
        if bool(row["fails_depth_gradient"]):
            reasons.append("depth_gradient")
        return ";".join(reasons) or "accepted"

    metrics["quality_reason"] = [reason(row) for _, row in metrics.iterrows()]
    accepted_indices = metrics.loc[mask, "triangle_index"].to_numpy(dtype=np.int64)
    if len(accepted_indices) == 0:
        raise ValueError(
            "TIN gap filter rejected all triangles; review maximum triangle edge length"
        )

    rejection_counts = {
        "max_edge": int((~edge_ok).sum()),
        "min_area": int((~area_ok).sum()),
    }
    if config.max_depth_gradient_m_per_m is not None:
        rejection_counts["depth_gradient"] = int((~gradient_ok).sum())
    rejection_counts = {k: v for k, v in rejection_counts.items() if v > 0}

    spacing = effective_geometry.get("effective_line_spacing_m")
    return accepted_indices, {
        "triangle_qc_version": "standard-gis-tin-v1",
        "triangle_qc_mode": "delaunay_linear_max_edge",
        "interpolation_method": "linear_delaunay_tin",
        "survey_aware_enabled": False,
        "survey_line_topology_used_as_rejection": False,
        "aspect_ratio_used_as_rejection": False,
        "max_angle_used_as_rejection": False,
        "effective_line_spacing_m": (
            float(spacing) if spacing is not None and np.isfinite(float(spacing)) else None
        ),
        "max_triangle_edge_m": float(edge_limit),
        "max_triangle_edge_mode": edge_mode,
        "min_triangle_area_m2": float(config.min_triangle_area_m2),
        "depth_gradient_limit_m_per_m": (
            float(config.max_depth_gradient_m_per_m)
            if config.max_depth_gradient_m_per_m is not None else None
        ),
        "accepted_triangles": int(mask.sum()),
        "rejected_triangles": int((~mask).sum()),
        "rejection_counts": rejection_counts,
        "method_note": (
            "Standard Delaunay TIN with linear interpolation. Triangles whose "
            "maximum edge exceeds the configured/derived gap limit are excluded. "
            "Survey-line topology, corridor membership, aspect ratio and maximum "
            "interior angle do not control surface support."
        ),
    }
