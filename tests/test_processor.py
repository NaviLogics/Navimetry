from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from bathymetry.models import ProcessingConfig
from bathymetry.processor import run_pipeline
from bathymetry.csv_importer import import_kogger_csv
from bathymetry.quality_control import normalize_observations


def test_beam_is_primary_and_zero_coordinates_rejected(tmp_path: Path) -> None:
    csv_path=tmp_path/"kogger.csv"
    pd.DataFrame({
        "Number":[0,1,2,3,4,5],
        "Beam distance":[1.0,1.1,1.2,1.3,1.4,None],
        "Latitude":[0,56.22,56.22001,56.22002,56.22003,56.22004],
        "Longitude":[0,37.01,37.01001,37.01002,37.01003,37.01004],
        "Rangefinder":[0,0,0,0,0,0],
    }).to_csv(csv_path,index=False)
    cfg=ProcessingConfig(input_csv=csv_path,output_dir=tmp_path/"out",max_depth_jump_m=10)
    csv_result=import_kogger_csv(cfg)
    obs=normalize_observations(csv_result,None,None,cfg)
    assert obs.loc[1,"depth_primary_m"] == 1.1
    assert obs.loc[0,"position_quality"] == "rejected"
    assert obs.loc[5,"depth_quality"] == "missing"
    assert not obs["rangefinder_available"].any()
    assert obs.loc[1,"rangefinder_raw_m"] == 0


def test_pipeline_creates_v02_products(tmp_path: Path) -> None:
    import pytest
    pytest.importorskip("laspy")
    input_csv=Path("sample_data")/"sample_kogger.csv"
    output_dir=tmp_path/"result"
    config=ProcessingConfig(
        input_csv=input_csv,output_dir=output_dir,latitude_field="Latitude",longitude_field="Longitude",
        beam_distance_field="Beam distance",output_crs="EPSG:32637",max_triangle_edge_m=20.0,
        # This fixture intentionally uses a large manual triangle edge. Give the presentation
        # surface a matching explicit radius so the test validates the intended superset case
        # instead of comparing two independently configured support thresholds.
        max_nearest_point_distance_m=20.0,
        pixel_size_m=0.5,max_depth_jump_m=10.0,create_project_database=True,
    )
    result=run_pipeline(config)
    assert result["accepted_aggregated_points"] >= 3
    required=[
        "normalized_observations.csv","accepted_points.csv","suspect_points.csv","rejected_points.csv",
        "bottom_points.xyz","bottom_points.las","depth_surface.obj","depth_surface.stl",
        "bathymetry_depth.tiff","bathymetry_depth_strict.tiff","coverage_mask.tiff","presentation_mask.tiff",
        "nearest_point_distance.tiff","support_quality.tiff","triangle_quality.csv","processing_report.json",
        "processing_report.pdf","project.navimetry.sqlite","manifest.json",
    ]
    for name in required: assert (output_dir/name).exists(), name
    with rasterio.open(output_dir/"bathymetry_depth.tiff") as ds:
        presentation=ds.read(1)
        assert ds.crs is not None and ds.nodata == -9999.0
    with rasterio.open(output_dir/"bathymetry_depth_strict.tiff") as ds:
        strict=ds.read(1)
    with rasterio.open(output_dir/"coverage_mask.tiff") as ds:
        strict_mask=ds.read(1).astype(bool)
    with rasterio.open(output_dir/"presentation_mask.tiff") as ds:
        presentation_mask=ds.read(1).astype(bool)
    assert np.all(~strict_mask | presentation_mask)
    assert np.count_nonzero(presentation != -9999.0) >= np.count_nonzero(strict != -9999.0)
    assert result["surface"]["surface_qc_version"] == "2"
    assert result["surface"]["presentation_grid_is_quality_evidence"] is False
    report=result
    assert report["classification"]["primary_depth_source"] == "KOGGERAPP_BEAM"
