from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
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
    latitude_field: str
    longitude_field: str
    beam_distance_field: str
    input_crs: str = "EPSG:4326"
    output_crs: str = "EPSG:32637"
    source_gnss_transducer_offset_applied: bool = True
    water_surface_to_transducer_m: float = 0.15
    pixel_size_m: float | None = None
    max_triangle_edge_m: float | None = None
    min_depth_m: float = 0.05
    max_depth_m: float = 100.0