from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from bathymetry.models import ProcessingConfig
from bathymetry.processor import run_pipeline, build_local_delaunay, triangle_metrics
from bathymetry.csv_importer import import_kogger_csv
from bathymetry.quality_control import normalize_observations
from bathymetry.survey_geometry import estimate_track_geometry
from bathymetry.survey_presets import get_survey_preset, resolve_surface_geometry
from bathymetry.survey_triangle_qc import evaluate_triangles


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


def test_local_delaunay_uses_large_projected_coordinates_stably() -> None:
    gx,gy=np.meshgrid(np.linspace(0,12,13),np.linspace(0,7,8))
    xy=np.column_stack((500000.0+gx.ravel()*0.1,6230000.0+gy.ravel()*0.1))
    tri,local_xy,origin,info=build_local_delaunay(xy)
    assert len(tri.simplices)>0
    assert info["input_vertices"]==len(xy)
    assert info["used_vertices"]==len(xy)
    assert info["coplanar_vertices"]==0
    assert info["all_vertices_used"] is True
    assert np.max(np.abs(local_xy))<2.0
    assert origin[1]>6_000_000


def test_track_segmentation_estimates_line_spacing() -> None:
    rows=[]; obs_id=0
    for line_index,y in enumerate((0.0,5.0,10.0,15.0)):
        xs=np.linspace(0.0,20.0,81)
        if line_index%2: xs=xs[::-1]
        for x in xs:
            rows.append({"observation_id":obs_id,"source_row":obs_id,"x_m":500000.0+x,"y_m":6230000.0+y,"overall_quality":"valid_for_surface"})
            obs_id+=1
        if line_index<3:
            end_x=float(xs[-1])
            for frac in np.linspace(0.2,1.0,5):
                rows.append({"observation_id":obs_id,"source_row":obs_id,"x_m":500000.0+end_x,"y_m":6230000.0+y+5.0*frac,"overall_quality":"valid_for_surface"})
                obs_id+=1
    summary,tracks=estimate_track_geometry(pd.DataFrame(rows))
    assert summary["status"]=="estimated"
    assert summary["detected_segments"]>=4
    assert summary["distinct_line_clusters"]>=4
    assert abs(summary["estimated_line_spacing_m"]-5.0)<0.25
    assert not tracks.empty


def test_auto_preset_uses_estimated_spacing_not_point_spacing(tmp_path: Path) -> None:
    cfg=ProcessingConfig(input_csv=tmp_path/"x.csv",output_dir=tmp_path/"out",survey_preset="AUTO")
    preset=get_survey_preset("AUTO")
    effective=resolve_surface_geometry(cfg,preset,{"estimated_line_spacing_m":5.0})
    assert effective["line_spacing_source"]=="estimated_from_track_segments"
    assert abs(effective["strict_max_triangle_edge_m"]-6.75)<1e-9
    assert abs(effective["presentation_radius_m"]-3.75)<1e-9
    assert effective["strict_cross_track_factor"]==1.35
    assert effective["strict_along_track_factor"]==2.0


def test_survey_aware_qc_accepts_skinny_adjacent_line_triangle(tmp_path: Path) -> None:
    # Triangle 0 is intentionally skinny: two dense along-track samples only 2 cm apart
    # and one point on the adjacent 5 m survey line. Its isotropic aspect ratio is huge,
    # but its survey-axis spans are valid. Triangle 1 skips more than one line spacing.
    xy=np.array([
        [0.00,0.0], [0.02,0.0], [0.00,5.0],
        [0.00,0.0], [0.02,0.0], [0.00,12.0],
    ],dtype=float)
    depth=np.array([2.0,2.01,2.1,2.0,2.01,2.2],dtype=float)
    faces=np.array([[0,1,2],[3,4,5]],dtype=np.int64)
    metrics=triangle_metrics(xy,depth,faces)
    assert metrics.loc[0,"aspect_ratio"] > 20.0
    cfg=ProcessingConfig(input_csv=tmp_path/"x.csv",output_dir=tmp_path/"out")
    effective={
        "effective_line_spacing_m":5.0,
        "geometry":"single_beam_centerline",
        "strict_cross_track_factor":1.35,
        "strict_along_track_factor":2.0,
        "cross_line_threshold_factor":0.25,
        "strict_max_triangle_edge_m":6.75,
        "strict_max_triangle_edge_source":"line_spacing_x_1.350",
    }
    track={"dominant_axis_heading_deg":0.0}
    accepted,info=evaluate_triangles(metrics,xy,faces,cfg,0.02,effective,track)
    assert accepted.tolist()==[0]
    assert info["triangle_qc_mode"]=="survey_aware_anisotropic_v1"
    assert info["aspect_ratio_used_as_rejection"] is False
    assert metrics.loc[0,"quality_status"]=="accepted"
    assert metrics.loc[1,"fails_cross_track_span"]


def test_pipeline_creates_v02_products(tmp_path: Path) -> None:
    import pytest
    pytest.importorskip("laspy")
    input_csv=Path("sample_data")/"sample_kogger.csv"
    output_dir=tmp_path/"result"
    config=ProcessingConfig(
        input_csv=input_csv,output_dir=output_dir,latitude_field="Latitude",longitude_field="Longitude",
        beam_distance_field="Beam distance",output_crs="EPSG:32637",max_triangle_edge_m=20.0,
        max_nearest_point_distance_m=20.0,pixel_size_m=0.5,max_depth_jump_m=10.0,create_project_database=True,
    )
    result=run_pipeline(config)
    assert result["accepted_aggregated_points"] >= 3
    required=[
        "normalized_observations.csv","accepted_points.csv","suspect_points.csv","rejected_points.csv","survey_tracks.csv",
        "bottom_points.xyz","bottom_points.las","depth_surface.obj","depth_surface.stl","depth_surface_strict.obj","depth_surface_strict.stl",
        "depth_surface_presentation.obj","depth_surface_presentation.stl","bathymetry_depth.tiff","bathymetry_depth_strict.tiff","coverage_mask.tiff","presentation_mask.tiff",
        "nearest_point_distance.tiff","support_quality.tiff","triangle_quality.csv","processing_report.json","processing_report.pdf","project.navimetry.sqlite","manifest.json",
    ]
    for name in required: assert (output_dir/name).exists(), name
    with rasterio.open(output_dir/"bathymetry_depth.tiff") as ds:
        presentation=ds.read(1); assert ds.crs is not None and ds.nodata == -9999.0
    with rasterio.open(output_dir/"bathymetry_depth_strict.tiff") as ds: strict=ds.read(1)
    with rasterio.open(output_dir/"coverage_mask.tiff") as ds: strict_mask=ds.read(1).astype(bool)
    with rasterio.open(output_dir/"presentation_mask.tiff") as ds: presentation_mask=ds.read(1).astype(bool)
    assert np.all(~strict_mask | presentation_mask)
    assert np.count_nonzero(presentation != -9999.0) >= np.count_nonzero(strict != -9999.0)
    assert result["surface"]["surface_qc_version"] == "5"
    assert result["surface"]["presentation_grid_is_quality_evidence"] is False
    assert result["surface"]["delaunay"]["all_vertices_used"] is True
    assert result["surface"]["presentation_mesh"]["written"] is True
    assert result["surface"]["triangle_qc"]["triangle_qc_version"] == "survey-aware-v1"
    assert result["survey_geometry"]["version"] == "1"
    assert result["survey_geometry"]["selected_preset"]["key"] == "AUTO"
    assert result["classification"]["primary_depth_source"] == "KOGGERAPP_BEAM"
