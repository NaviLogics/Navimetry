from types import SimpleNamespace

import numpy as np
import pandas as pd

from bathymetry.survey_triangle_qc import evaluate_triangles


def _config():
    return SimpleNamespace(
        max_triangle_angle_deg=120.0,
        min_triangle_area_m2=0.001,
        max_depth_gradient_m_per_m=None,
        max_triangle_edge_m=None,
        max_triangle_aspect_ratio=20.0,
    )


def _metrics(rows=1, max_angle=150.0):
    return pd.DataFrame([{
        "triangle_index": i,
        "max_edge_m": 2.25,
        "aspect_ratio": 10.0,
        "max_angle_deg": max_angle,
        "area_m2": 0.2,
        "depth_gradient_m_per_m": 0.02,
    } for i in range(rows)])


def _geometry():
    return {"geometry": "single_beam_centerline", "effective_line_spacing_m": 2.0}


def test_adjacent_survey_lines_can_bypass_isotropic_max_angle():
    xy = np.array([[0.0, 0.0], [0.2, 0.0], [0.1, 2.0]])
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    tracks = {
        "dominant_axis_heading_deg": 0.0,
        "line_corridors_surface_local": [
            {"cross_track_center_m": 0.0, "along_track_min_m": -1.0, "along_track_max_m": 2.0},
            {"cross_track_center_m": 2.0, "along_track_min_m": -1.0, "along_track_max_m": 2.0},
        ],
    }
    metrics = _metrics(1, 150.0)
    accepted, info = evaluate_triangles(metrics, xy, faces, _config(), 0.1, _geometry(), tracks)
    assert accepted.tolist() == [0]
    assert metrics.loc[0, "line_topology"] == "adjacent_lines"
    assert bool(metrics.loc[0, "adaptive_angle_bypass"])
    assert info["triangle_qc_version"] == "survey-aware-v4"


def test_non_adjacent_line_bridge_remains_rejected_while_local_triangle_survives():
    # Face 0 bridges line 0 directly to line 2 and must remain rejected.
    # Face 1 is a local line-0 triangle so the QC call still has a valid output.
    xy = np.array([[0.0, 0.0], [0.2, 0.0], [0.1, 4.0], [0.4, 0.0]])
    faces = np.array([[0, 1, 2], [0, 1, 3]], dtype=np.int64)
    tracks = {
        "dominant_axis_heading_deg": 0.0,
        "line_corridors_surface_local": [
            {"cross_track_center_m": 0.0, "along_track_min_m": -1.0, "along_track_max_m": 2.0},
            {"cross_track_center_m": 2.0, "along_track_min_m": -1.0, "along_track_max_m": 2.0},
            {"cross_track_center_m": 4.0, "along_track_min_m": -1.0, "along_track_max_m": 2.0},
        ],
    }
    metrics = _metrics(2, 100.0)
    geometry = _geometry(); geometry["strict_cross_track_factor"] = 3.0
    accepted, _ = evaluate_triangles(metrics, xy, faces, _config(), 0.1, geometry, tracks)
    assert accepted.tolist() == [1]
    assert metrics.loc[0, "line_topology"] == "non_adjacent_lines"
    assert bool(metrics.loc[0, "fails_line_topology"])
