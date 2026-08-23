from pathlib import Path
import rasterio
from bathymetry.models import ProcessingConfig
from bathymetry.processor import run_pipeline
def test_pipeline_creates_required_files(
    tmp_path: Path,
) -> None:
    input_csv = (
        Path("sample_data")
        / "sample_kogger.csv"
    )
    output_dir = tmp_path / "result"
    config = ProcessingConfig(
        input_csv=input_csv,
        output_dir=output_dir,
        latitude_field="Latitude",
        longitude_field="Longitude",
        beam_distance_field="Beam distance",
        water_surface_to_transducer_m=0.15,
        output_crs="EPSG:32637",
        max_triangle_edge_m=20.0,
        pixel_size_m=0.5,
    )
    result = run_pipeline(config)
    assert result["accepted_aggregated_points"] >= 3
    assert (
        output_dir / "bottom_points.xyz"
    ).exists()
    assert (
        output_dir / "bottom_points.las"
    ).exists()
    assert (
        output_dir / "depth_surface.obj"
    ).exists()
    assert (
        output_dir / "depth_surface.stl"
    ).exists()
    assert (
        output_dir / "bathymetry_depth.tiff"
    ).exists()
    assert (
        output_dir / "bathymetry_map.pdf"
    ).exists()
    assert Path(result["archive"]).exists()
    with rasterio.open(
        output_dir / "bathymetry_depth.tiff"
    ) as dataset:
        assert dataset.crs is not None
        assert dataset.nodata == -9999.0