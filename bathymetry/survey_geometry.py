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
    """Smallest angular difference for an undirected line axis, in radians."""
    return np.abs((angle - reference + math.pi / 2.0) % math.pi - math.pi / 2.0)


def _smooth_axis_heading(headings: np.ndarray, window: int) -> np.ndarray:
    if len(headings) == 0:
        return headings.copy()
    window = max(1, int(window))
    doubled = np.exp(2j * headings)
    real = pd.Series(doubled.real).rolling(window, center=True, min_periods=1).mean().to_numpy()
    imag = pd.Series(doubled.imag).rolling(window, center=True, min_periods=1).mean().to_numpy()
    return 0.5 * np.arctan2(imag, real)


def _dominant_axis(headings: np.ndarray, lengths: np.ndarray) -> float:
    if len(headings) == 0:
        return 0.0
    positive = lengths[lengths > 0]
    clip = float(np.quantile(positive, 0.90)) if len(positive) else 1.0
    weights = np.clip(lengths, 0.0, max(clip, 1e-9))
    vector = np.sum(weights * np.exp(2j * headings))
    if abs(vector) < 1e-12:
        return float(np.median(headings) % math.pi)
    return float((0.5 * np.angle(vector)) % math.pi)


def _contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask.tolist()):
        if value and start is None:
            start = i
        elif not value and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def estimate_track_geometry(
    observations: pd.DataFrame,
    settings: TrackSegmentationConfig | None = None,
) -> tuple[dict, pd.DataFrame]:
    """Segment survey lines from ordered accepted observations and estimate line spacing.

    The estimator intentionally uses source-order track geometry rather than nearest-neighbour
    point spacing. Opposite travel directions are treated as the same survey-line axis.
    """
    settings = settings or TrackSegmentationConfig()
    required = {"x_m", "y_m"}
    if not required.issubset(observations.columns):
        return {
            "status": "unavailable",
            "reason": "missing_projected_coordinates",
            "settings": asdict(settings),
        }, pd.DataFrame()

    ordered = observations.copy()
    if "overall_quality" in ordered.columns:
        ordered = ordered[ordered["overall_quality"].eq("valid_for_surface")]
    ordered = ordered[np.isfinite(ordered["x_m"]) & np.isfinite(ordered["y_m"])].copy()
    if len(ordered) < max(3, settings.min_segment_points):
        return {
            "status": "unavailable",
            "reason": "too_few_ordered_surface_observations",
            "input_observations": int(len(ordered)),
            "settings": asdict(settings),
        }, pd.DataFrame()

    xy = ordered[["x_m", "y_m"]].to_numpy(dtype=float)
    step = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    keep = np.r_[True, step > settings.minimum_motion_m]
    ordered = ordered.iloc[np.flatnonzero(keep)].copy().reset_index(drop=True)
    xy = ordered[["x_m", "y_m"]].to_numpy(dtype=float)
    if len(xy) < max(3, settings.min_segment_points):
        return {
            "status": "unavailable",
            "reason": "too_few_moving_observations",
            "input_observations": int(len(xy)),
            "settings": asdict(settings),
        }, pd.DataFrame()

    delta = np.diff(xy, axis=0)
    lengths = np.hypot(delta[:, 0], delta[:, 1])
    headings = np.arctan2(delta[:, 1], delta[:, 0])
    smooth_heading = _smooth_axis_heading(headings, settings.smoothing_window)
    dominant = _dominant_axis(smooth_heading, lengths)
    deviation = _axis_angle_difference(smooth_heading, dominant)

    positive = lengths[lengths > settings.minimum_motion_m]
    typical_step = float(np.median(positive)) if len(positive) else settings.minimum_motion_m
    p99_step = float(np.quantile(positive, 0.99)) if len(positive) else typical_step
    gap_limit = max(1.0, typical_step * 25.0, p99_step * 3.0)
    on_axis = (
        (deviation <= math.radians(settings.heading_tolerance_deg))
        & (lengths <= gap_limit)
        & (lengths > settings.minimum_motion_m)
    )

    axis = np.array([math.cos(dominant), math.sin(dominant)], dtype=float)
    normal = np.array([-axis[1], axis[0]], dtype=float)
    origin = np.mean(xy, axis=0)
    segments: list[dict] = []
    for segment_id, (edge_start, edge_end) in enumerate(_contiguous_runs(on_axis), start=1):
        point_start = edge_start
        point_end = edge_end + 1
        pts = xy[point_start:point_end + 1]
        n_points = len(pts)
        path_length = float(np.sum(lengths[edge_start:edge_end + 1]))
        if n_points < settings.min_segment_points or path_length < settings.min_segment_length_m:
            continue
        local = pts - origin
        cross = local @ normal
        along = local @ axis
        cross_center = float(np.median(cross))
        cross_mad = float(np.median(np.abs(cross - cross_center)))
        row_start = ordered.iloc[point_start]
        row_end = ordered.iloc[point_end]
        segments.append({
            "segment_id": segment_id,
            "start_source_row": int(row_start["source_row"]) if "source_row" in ordered and pd.notna(row_start.get("source_row")) else None,
            "end_source_row": int(row_end["source_row"]) if "source_row" in ordered and pd.notna(row_end.get("source_row")) else None,
            "start_observation_id": int(row_start["observation_id"]) if "observation_id" in ordered and pd.notna(row_start.get("observation_id")) else None,
            "end_observation_id": int(row_end["observation_id"]) if "observation_id" in ordered and pd.notna(row_end.get("observation_id")) else None,
            "point_count": int(n_points),
            "path_length_m": path_length,
            "axis_heading_deg": float(math.degrees(dominant) % 180.0),
            "cross_track_offset_m": cross_center,
            "cross_track_mad_m": cross_mad,
            "along_track_span_m": float(np.ptp(along)),
        })

    tracks = pd.DataFrame(segments)
    if tracks.empty:
        return {
            "status": "unavailable",
            "reason": "no_straight_survey_segments_detected",
            "input_observations": int(len(ordered)),
            "dominant_axis_heading_deg": float(math.degrees(dominant) % 180.0),
            "typical_step_m": typical_step,
            "gap_limit_m": gap_limit,
            "settings": asdict(settings),
        }, tracks

    offsets = np.sort(tracks["cross_track_offset_m"].to_numpy(dtype=float))
    lateral_mad = float(np.median(tracks["cross_track_mad_m"].to_numpy(dtype=float)))
    cluster_tolerance = float(np.clip(max(0.15, lateral_mad * 6.0), 0.15, 1.0))
    clusters: list[list[float]] = []
    for value in offsets:
        if not clusters or value - float(np.mean(clusters[-1])) > cluster_tolerance:
            clusters.append([float(value)])
        else:
            clusters[-1].append(float(value))
    centers = np.array([float(np.median(group)) for group in clusters], dtype=float)
    spacings = np.diff(centers)
    spacings = spacings[spacings > cluster_tolerance]

    if len(spacings):
        spacing = float(np.median(spacings))
        spacing_mad = float(np.median(np.abs(spacings - spacing)))
        relative_mad = spacing_mad / spacing if spacing > 0 else math.inf
        confidence = "high" if len(spacings) >= 3 and relative_mad <= 0.20 else "medium" if len(spacings) >= 1 else "low"
        status = "estimated"
        reason = None
    else:
        spacing = None
        spacing_mad = None
        confidence = "low"
        status = "unavailable"
        reason = "fewer_than_two_distinct_survey_lines"

    summary = {
        "status": status,
        "reason": reason,
        "input_observations": int(len(ordered)),
        "detected_segments": int(len(tracks)),
        "distinct_line_clusters": int(len(centers)),
        "dominant_axis_heading_deg": float(math.degrees(dominant) % 180.0),
        "estimated_line_spacing_m": spacing,
        "line_spacing_mad_m": spacing_mad,
        "line_spacing_samples": int(len(spacings)),
        "confidence": confidence,
        "typical_step_m": typical_step,
        "gap_limit_m": gap_limit,
        "line_cluster_tolerance_m": cluster_tolerance,
        "line_centers_cross_track_m": centers.tolist(),
        "settings": asdict(settings),
        "method": "ordered_track_segmentation_dominant_axis_cross_track_clusters",
    }
    return summary, tracks
