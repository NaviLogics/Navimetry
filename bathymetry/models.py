from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class CsvInspection:
    columns: list[str]
    row_count: int
    delimiter: str
    encoding: str

@dataclass
class ProcessingConfig:
    input_csv: Path
    output_dir: Path
    latitude_field: str = "Latitude"
    longitude_field: str = "Longitude"
    beam_distance_field: str = "Beam distance"
    rangefinder_field: str = "Rangefinder"
    input_klf: Path | None = None
    input_crs: str = "EPSG:4326"
    output_crs: str = "EPSG:32637"
    depth_source: str = "KOGGERAPP_BEAM"
    validation_depth_source: str = "RANGEFINDER"
    rangefinder_zero_is_unavailable: bool = True
    rangefinder_unavailable_reason: str = "rangefinder_disabled_or_not_recorded"
    source_gnss_transducer_offset_applied: bool = True
    apply_vertical_correction: bool = False
    water_surface_to_transducer_m: float = 0.15
    vertical_reduction_mode: str = "NONE"
    apply_lever_arm: bool = False
    lever_arm_x_m: float = 0.0
    lever_arm_y_m: float = 0.0
    lever_arm_z_m: float = 0.0
    time_source: str = "AUTO"
    clock_model: str = "AUTO"
    max_clock_residual_ms: float = 100.0
    position_source: str = "AUTO"
    attitude_source: str = "PX4_ATTITUDE"
    pixel_size_m: float | None = None
    max_triangle_edge_m: float | None = None
    max_triangle_angle_deg: float = 175.0
    max_triangle_aspect_ratio: float = 20.0
    min_triangle_area_m2: float = 1e-6
    max_depth_gradient_m_per_m: float | None = None
    max_nearest_point_distance_m: float | None = None
    min_depth_m: float = 0.05
    max_depth_m: float = 100.0
    max_speed_mps: float = 6.0
    max_coordinate_jump_m: float = 10.0
    max_depth_jump_m: float = 1.0
    include_suspect_points: bool = False
    generate_quality_proxy: bool = True
    aggregation_mode: str = "none"
    create_project_database: bool = True

@dataclass
class Observation:
    observation_id: int
    csv_row: int | None
    csv_number: int | None
    source_file: str
    source_klf_frame: int | None = None
    beam_distance_raw_m: float | None = None
    beam_distance_used_m: float | None = None
    rangefinder_raw_m: float | None = None
    rangefinder_m: float | None = None
    rangefinder_available: bool = False
    rangefinder_unavailable_reason: str | None = None
    latitude_raw_deg: float | None = None
    longitude_raw_deg: float | None = None
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    x_m: float | None = None
    y_m: float | None = None
    event_unix_raw: str | None = None
    event_timestamp_raw: str | None = None
    gnss_utc_date_raw: str | None = None
    gnss_utc_time_raw: str | None = None
    kogger_time_ms: float | None = None
    px4_boot_time_ms: float | None = None
    timestamp_utc: str | None = None
    timestamp_quality: str = "unavailable"
    position_source: str = "CSV_COORDINATES"
    depth_source: str = "KOGGERAPP_BEAM"
    attitude_ref: int | None = None
    gps_raw_ref: int | None = None
    global_position_ref: int | None = None
    position_quality: str = "unknown"
    depth_quality: str = "unknown"
    time_quality: str = "unavailable"
    attitude_quality: str = "unavailable"
    coverage_quality: str = "unknown"
    overall_quality: str = "unknown"
    quality_flags: list[str] = field(default_factory=list)
    quality_reason: str | None = None
    manual_edit_status: str = "unknown"
    offset_applied_in_kogger: str = "unknown"
    offset_applied_in_navimetry: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
