from __future__ import annotations
import hashlib
import json
import math
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import laspy
except ImportError:  # build/runtime dependency checked by --self-test
    laspy = None
import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import rasterio
import trimesh
from matplotlib import pyplot as plt
from pyproj import CRS
from rasterio.transform import from_origin
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay, QhullError, cKDTree

from bathymetry.models import CsvInspection, ProcessingConfig
from bathymetry.csv_importer import detect_csv_format, inspect_csv, import_kogger_csv
from bathymetry.klf_parser import inspect_klf, KlfParseResult
from bathymetry.clock_sync import build_clock_model
from bathymetry.matcher import match_csv_to_klf
from bathymetry.quality_control import normalize_observations, build_surface_points, QualityError
from bathymetry.project_store import initialize_database, store_dataframe


class ProcessingError(Exception):
    """Navimetry processing error."""


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def median_spacing(points: np.ndarray) -> float:
    if len(points) < 2:
        raise ProcessingError("Not enough points to calculate spacing")
    distances, _ = cKDTree(points).query(points, k=2)
    positive = distances[:, 1][distances[:, 1] > 0]
    if not len(positive):
        raise ProcessingError("All surface points have identical XY")
    return float(np.median(positive))


def triangle_metrics(xy: np.ndarray, depth: np.ndarray, faces: np.ndarray) -> pd.DataFrame:
    rows = []
    for idx, face in enumerate(faces):
        p = xy[face]
        z = depth[face]
        edges = np.array([
            np.linalg.norm(p[0]-p[1]), np.linalg.norm(p[1]-p[2]), np.linalg.norm(p[2]-p[0])
        ], dtype=float)
        a, b, c = edges
        area = abs(np.cross(p[1]-p[0], p[2]-p[0])) / 2.0
        min_edge = float(edges.min()); max_edge = float(edges.max())
        aspect = max_edge / min_edge if min_edge > 0 else math.inf
        angles = []
        for aa, bb, cc in ((a,b,c),(b,c,a),(c,a,b)):
            den = 2*aa*bb
            cosv = (aa*aa + bb*bb - cc*cc)/den if den > 0 else 1.0
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
        gradient = float((z.max()-z.min())/max_edge) if max_edge > 0 else math.inf
        rows.append({
            "triangle_index": idx, "v0": int(face[0]), "v1": int(face[1]), "v2": int(face[2]),
            "min_edge_m": min_edge, "max_edge_m": max_edge, "area_m2": float(area),
            "aspect_ratio": float(aspect), "max_angle_deg": float(max(angles)),
            "depth_gradient_m_per_m": gradient,
        })
    return pd.DataFrame(rows)


def filter_triangles(metrics: pd.DataFrame, config: ProcessingConfig, spacing: float) -> tuple[np.ndarray, dict]:
    if metrics.empty:
        raise ProcessingError("Triangulation contains no triangles")
    edge_limit = config.max_triangle_edge_m if config.max_triangle_edge_m is not None else max(spacing * 4.0, float(metrics["max_edge_m"].quantile(0.25)))
    mask = (
        metrics["max_edge_m"].le(edge_limit)
        & metrics["aspect_ratio"].le(config.max_triangle_aspect_ratio)
        & metrics["max_angle_deg"].le(config.max_triangle_angle_deg)
        & metrics["area_m2"].ge(config.min_triangle_area_m2)
    )
    if config.max_depth_gradient_m_per_m is not None:
        mask &= metrics["depth_gradient_m_per_m"].le(config.max_depth_gradient_m_per_m)
    metrics["quality_status"] = np.where(mask, "accepted", "rejected")
    accepted_indices = metrics.loc[mask, "triangle_index"].to_numpy(dtype=np.int64)
    if len(accepted_indices) == 0:
        raise ProcessingError("Triangle QC rejected all triangles; review geometry thresholds")
    return accepted_indices, {
        "max_triangle_edge_m": float(edge_limit),
        "max_triangle_edge_mode": "manual" if config.max_triangle_edge_m is not None else "automatic",
    }


def _las_scale_and_offset(values: np.ndarray) -> tuple[float, float]:
    safe_limit = float(np.iinfo(np.int32).max) * 0.90
    span = float(values.max()-values.min())
    scale = max(0.001, span/safe_limit if span else 0.001)
    return scale, float(values.min())


def write_las(points: pd.DataFrame, path: Path, output_crs: str) -> dict:
    if laspy is None:
        raise ProcessingError("laspy is unavailable")
    x = points["x_m"].to_numpy(float); y = points["y_m"].to_numpy(float)
    depth = points["depth_primary_m"].to_numpy(float); z = -depth
    sx,ox=_las_scale_and_offset(x); sy,oy=_las_scale_and_offset(y); sz,oz=_las_scale_and_offset(z)
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales=np.array([sx,sy,sz]); header.offsets=np.array([ox,oy,oz])
    header.add_crs(CRS.from_user_input(output_crs))
    for name, dtype in (("depth_m", np.float32),("beam_distance_m", np.float32),("quality_code", np.uint8),("source_id", np.uint32)):
        header.add_extra_dim(laspy.ExtraBytesParams(name=name, type=dtype))
    las = laspy.LasData(header)
    las.x=x; las.y=y; las.z=z
    las.depth_m=depth.astype(np.float32)
    las.beam_distance_m=points["beam_distance_raw_m"].to_numpy(float).astype(np.float32)
    las.quality_code=points.get("quality_code", pd.Series(1,index=points.index)).to_numpy(np.uint8)
    las.source_id=np.arange(1,len(points)+1,dtype=np.uint32)
    las.write(path)
    return {"scales":[sx,sy,sz],"offsets":[ox,oy,oz]}


def write_xyz(points: pd.DataFrame, path: Path) -> None:
    points[["x_m","y_m","depth_primary_m"]].to_csv(path, sep=" ", index=False, header=False, float_format="%.4f")


def write_mesh(xy: np.ndarray, depth: np.ndarray, faces: np.ndarray, output_dir: Path, output_crs: str) -> None:
    origin_x=float(xy[:,0].min()); origin_y=float(xy[:,1].min())
    vertices=np.column_stack((xy[:,0]-origin_x, xy[:,1]-origin_y, -depth))
    mesh=trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.remove_unreferenced_vertices()
    mesh.export(output_dir/"depth_surface.obj")
    mesh.export(output_dir/"depth_surface.stl")
    (output_dir/"depth_surface_metadata.json").write_text(json.dumps({
        "horizontal_crs":output_crs,"units":"m","local_origin_x_m":origin_x,"local_origin_y_m":origin_y,
        "model_z_definition":"z = -depth_primary_m","depth_source":"KOGGERAPP_BEAM",
        "vertical_datum":"unknown","bottom_elevation_available":False,
    },ensure_ascii=False,indent=2),encoding="utf-8")


def _write_raster(path: Path, array: np.ndarray, transform, crs: str, nodata, dtype: str, description: str, tags: dict) -> None:
    with rasterio.open(path,"w",driver="GTiff",height=array.shape[0],width=array.shape[1],count=1,dtype=dtype,
                       crs=CRS.from_user_input(crs),transform=transform,nodata=nodata,compress="deflate") as ds:
        ds.write(array.astype(dtype),1); ds.set_band_description(1,description); ds.update_tags(**tags)


def write_surface_products(points: pd.DataFrame, triangulation: Delaunay, accepted_faces: np.ndarray,
                           pixel_size: float, output_dir: Path, output_crs: str,
                           config: ProcessingConfig) -> dict:
    x=points["x_m"].to_numpy(float); y=points["y_m"].to_numpy(float); depth=points["depth_primary_m"].to_numpy(float)
    west,east,south,north=float(x.min()),float(x.max()),float(y.min()),float(y.max())
    width=max(1,int(math.ceil((east-west)/pixel_size))); height=max(1,int(math.ceil((north-south)/pixel_size)))
    if width*height>20_000_000: raise ProcessingError("Raster too large; increase pixel size")
    gx=west+(np.arange(width)+0.5)*pixel_size; gy=north-(np.arange(height)+0.5)*pixel_size
    mx,my=np.meshgrid(gx,gy); grid=np.column_stack((mx.ravel(),my.ravel()))
    interp=LinearNDInterpolator(triangulation,depth,fill_value=np.nan)
    values=np.asarray(interp(grid),float)
    simplex=triangulation.find_simplex(grid)
    allowed=set(int(i) for i in accepted_faces.tolist())
    support=np.array([(sid in allowed) for sid in simplex],dtype=bool)
    values[~support]=np.nan
    raster=values.reshape(height,width)
    support_raster=support.reshape(height,width)
    tree=cKDTree(np.column_stack((x,y)))
    nearest=tree.query(grid,k=1)[0].reshape(height,width)
    transform=from_origin(west,north,pixel_size,pixel_size)
    _write_raster(output_dir/"bathymetry_depth.tiff", np.where(np.isfinite(raster),raster,-9999.0), transform, output_crs,-9999.0,"float32",
                  "Primary depth, m", {"units":"m","depth_direction":"positive_down","depth_source":"KOGGERAPP_BEAM","vertical_datum":"unknown"})
    _write_raster(output_dir/"coverage_mask.tiff", support_raster.astype(np.uint8), transform, output_crs,0,"uint8",
                  "Surface support mask", {"meaning":"1=supported_by_accepted_TIN_triangle,0=unsupported"})
    _write_raster(output_dir/"nearest_point_distance.tiff", nearest.astype(np.float32), transform, output_crs,-9999.0,"float32",
                  "Distance to nearest accepted observation, m", {"units":"m"})
    if config.generate_quality_proxy:
        scale=max(float(np.nanmedian(nearest[support_raster])) if support_raster.any() else pixel_size, pixel_size)
        proxy=np.where(support_raster, np.clip(nearest/(scale*4.0),0,1), 1.0).astype(np.float32)
        _write_raster(output_dir/"support_quality.tiff",proxy,transform,output_crs,-9999.0,"float32",
                      "Support quality proxy (0 best, 1 worst)", {"not_metrological_uncertainty":"true"})

    fig,ax=plt.subplots(figsize=(11.69,8.27))
    im=ax.imshow(raster,extent=(west,east,south,north),origin="upper",aspect="equal")
    ax.scatter(x,y,s=1,alpha=0.25,label="Accepted observations")
    fig.colorbar(im,ax=ax,shrink=.8,label="Depth, m")
    ax.set_title("Navimetry 0.2 bathymetry — KoggerApp Beam distance")
    ax.set_xlabel("X, m"); ax.set_ylabel("Y, m"); ax.legend(loc="upper right")
    ax.text(.01,.01,f"CRS: {output_crs}\nDepth source: KOGGERAPP_BEAM\nVertical datum: unknown\nPixel: {pixel_size:.3f} m",
            transform=ax.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.8,"edgecolor":"gray"})
    fig.tight_layout(); fig.savefig(output_dir/"processing_report.pdf",dpi=180); plt.close(fig)
    shutil.copy2(output_dir/"processing_report.pdf", output_dir/"bathymetry_map.pdf")
    return {"width":width,"height":height,"pixel_size_m":pixel_size,"supported_cells":int(support_raster.sum())}


def _store_project_database(db_path: Path, csv_result, klf_result, observations: pd.DataFrame,
                            config: ProcessingConfig, report: dict, started_at: str, finished_at: str,
                            csv_asset: dict, klf_asset: dict | None) -> None:
    conn=initialize_database(db_path)
    try:
        conn.execute("INSERT INTO project(name,created_at,navimetry_version,config_json) VALUES(?,?,?,?)",
                     (config.output_dir.name,started_at,"0.2",json.dumps(asdict(config),default=str,ensure_ascii=False)))
        cur=conn.execute("INSERT INTO source_asset(path,filename,asset_type,sha256,size_bytes,imported_at,parser_version) VALUES(?,?,?,?,?,?,?)",
                         (str(config.input_csv),config.input_csv.name,"CSV",csv_asset["sha256"],csv_asset["size_bytes"],started_at,"csv_importer/0.2"))
        csv_asset_id=cur.lastrowid
        csv_table=csv_result.normalized.copy(); csv_table["source_asset_id"]=csv_asset_id
        csv_table=csv_table.rename(columns={"csv_number":"number"})
        store_dataframe(conn,"csv_observation",csv_table,["source_asset_id","source_row","number","beam_distance_raw_m","rangefinder_raw_m","latitude_raw_deg","longitude_raw_deg","extras_json"])
        if klf_result is not None and klf_asset is not None:
            cur=conn.execute("INSERT INTO source_asset(path,filename,asset_type,sha256,size_bytes,imported_at,parser_version) VALUES(?,?,?,?,?,?,?)",
                             (str(config.input_klf),config.input_klf.name,"KLF",klf_asset["sha256"],klf_asset["size_bytes"],started_at,"kp2_mavlink/0.2"))
            klf_asset_id=cur.lastrowid
            fr=pd.DataFrame(klf_result.frame_records)
            if not fr.empty:
                fr["source_asset_id"]=klf_asset_id
                store_dataframe(conn,"klf_frame",fr,["source_asset_id","frame_index","frame_type","payload_length","ltime_ms","checksum_valid","complete","kogger_id"])
            mv=pd.DataFrame(klf_result.mavlink_records)
            if not mv.empty:
                mv=mv.rename(columns={"frame_index":"klf_frame_id"})
                store_dataframe(conn,"mavlink_message",mv,["klf_frame_id","sysid","compid","msgid","sequence","px4_boot_time_ms","utc_time_us","ltime_ms"])
        ot=observations.copy(); ot["csv_observation_id"]=ot["observation_id"]
        ot["primary_depth_m"]=ot["depth_primary_m"]; ot["primary_depth_source"]=ot["depth_source"]
        ot["validation_depth_m"]=ot["rangefinder_m"]; ot["validation_available"]=ot["rangefinder_available"].astype(int)
        ot["px4_time_ms"]=ot.get("px4_boot_time_ms",np.nan)
        store_dataframe(conn,"observation",ot,["csv_observation_id","primary_depth_m","primary_depth_source","validation_depth_m","validation_available","latitude_deg","longitude_deg","x_m","y_m","kogger_time_ms","px4_time_ms","timestamp_utc","position_source","timestamp_quality","position_quality","depth_quality","overall_quality","quality_flags_json"])
        conn.execute("INSERT INTO processing_run(started_at,finished_at,config_json,report_json) VALUES(?,?,?,?)",
                     (started_at,finished_at,json.dumps(asdict(config),default=str,ensure_ascii=False),json.dumps(report,ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def run_pipeline(config: ProcessingConfig, progress: Callable[[str],None] | None=None) -> dict:
    def notify(msg):
        if progress: progress(msg)
    started=datetime.now(timezone.utc).isoformat()
    if config.apply_vertical_correction:
        raise ProcessingError("Navimetry 0.2 does not automatically apply vertical correction to Beam distance. Use vertical_reduction_mode only after explicit datum design.")
    if config.min_depth_m >= config.max_depth_m:
        raise ProcessingError("Minimum depth must be smaller than maximum depth")
    config.output_dir.mkdir(parents=True,exist_ok=True)

    notify("Import KoggerApp CSV")
    csv_result=import_kogger_csv(config)
    csv_asset={"sha256":sha256sum(config.input_csv),"size_bytes":config.input_csv.stat().st_size}
    shutil.copy2(config.input_csv,config.output_dir/"source.csv")

    klf_result: KlfParseResult | None=None; klf_asset=None; clock={"model_type":"unavailable","quality":"unavailable"}; match_df=None; match_report={"method":"unavailable","identity_confidence":"unavailable"}
    if config.input_klf is not None:
        notify("Parse KLF / KP2 / MAVLink")
        klf_result=inspect_klf(config.input_klf,capture_records=config.create_project_database)
        klf_asset={"sha256":sha256sum(config.input_klf),"size_bytes":config.input_klf.stat().st_size}
        notify("Build Kogger↔PX4 clock model")
        clock=build_clock_model(klf_result.attitude)
        notify("Match CSV to KLF GLOBAL_POSITION_INT")
        match_df,match_report=match_csv_to_klf(csv_result.normalized,klf_result.global_position)
        klf_result.mavlink_inventory.to_csv(config.output_dir/"mavlink_inventory.csv",index=False,encoding="utf-8-sig")
        (config.output_dir/"klf_inventory.json").write_text(json.dumps(asdict(klf_result.inventory),ensure_ascii=False,indent=2),encoding="utf-8")
    else:
        notify("KLF not selected: CSV-only mode; time and KLF QC unavailable")

    notify("Normalize observations and run QC")
    try:
        observations=normalize_observations(csv_result,klf_result,match_df,config)
        surface_points=build_surface_points(observations,config.aggregation_mode)
    except QualityError as error:
        raise ProcessingError(str(error)) from error

    observations.to_csv(config.output_dir/"normalized_observations.csv",index=False,encoding="utf-8-sig")
    accepted=observations[observations["overall_quality"]=="valid_for_surface"].copy()
    suspect=observations[(observations["position_quality"]=="suspect") | (observations["depth_quality"]=="suspect")].copy()
    rejected=observations[observations["overall_quality"]!="valid_for_surface"].copy()
    accepted.to_csv(config.output_dir/"accepted_points.csv",index=False,encoding="utf-8-sig")
    suspect.to_csv(config.output_dir/"suspect_points.csv",index=False,encoding="utf-8-sig")
    rejected.to_csv(config.output_dir/"rejected_points.csv",index=False,encoding="utf-8-sig")

    xy=surface_points[["x_m","y_m"]].to_numpy(float); depths=surface_points["depth_primary_m"].to_numpy(float)
    spacing=median_spacing(xy)
    notify("Build Delaunay and triangle QC")
    try:
        tri=Delaunay(xy)
    except QhullError as error:
        raise ProcessingError("Cannot build Delaunay surface from current points") from error
    tm=triangle_metrics(xy,depths,tri.simplices)
    accepted_indices,triangle_config=filter_triangles(tm,config,spacing)
    tm.to_csv(config.output_dir/"triangle_quality.csv",index=False,encoding="utf-8-sig")
    faces=tri.simplices[accepted_indices]

    pixel=config.pixel_size_m if config.pixel_size_m is not None else max(spacing/2.0,0.05)
    notify("Write XYZ, LAS, OBJ, STL")
    write_xyz(surface_points,config.output_dir/"bottom_points.xyz")
    las_info=write_las(surface_points,config.output_dir/"bottom_points.las",config.output_crs)
    write_mesh(xy,depths,faces,config.output_dir,config.output_crs)
    notify("Write depth/coverage/support GeoTIFF and PDF")
    raster_info=write_surface_products(surface_points,tri,accepted_indices,pixel,config.output_dir,config.output_crs,config)

    klf_inv=asdict(klf_result.inventory) if klf_result is not None else None
    report={
        "application":"Navimetry","version":"0.2-portable","classification":{
            "primary_depth_source":"KOGGERAPP_BEAM","rangefinder_role":"validation_only",
            "vertical_datum":"unknown","manual_beam_correction":"unknown","industrial_accuracy":"not_established",
        },
        "source_assets":{"csv":{"filename":config.input_csv.name,**csv_asset},"klf":({"filename":config.input_klf.name,**klf_asset} if klf_asset else None)},
        "csv_inventory":csv_result.inventory,
        "klf_inventory":klf_inv,
        "clock_model":clock,
        "csv_klf_match":match_report,
        "source_semantics":{
            "Beam distance":"KoggerApp-derived primary depth",
            "Rangefinder":"validation only; unavailable when disabled/not recorded",
            "GLOBAL_POSITION_INT":"PX4 estimated global position",
            "GPS_RAW_INT":"raw GNSS evidence through PX4 MAVLink",
            "ATTITUDE":"PX4 estimated attitude",
            "ID_CHART":"raw sonar acoustic chart fragments",
        },
        "qc":{
            "normalized_rows":int(len(observations)),"surface_eligible_rows":int(len(accepted)),
            "rejected_for_surface_rows":int(len(rejected)),"suspect_rows":int(len(suspect)),
            "zero_coordinate_rows":int(((observations.latitude_raw_deg==0)&(observations.longitude_raw_deg==0)).sum()),
            "beam_valid_rows":int(observations.depth_primary_m.notna().sum()),"beam_missing_rows":int(observations.depth_primary_m.isna().sum()),
            "rangefinder_available_rows":int(observations.rangefinder_available.sum()),
        },
        "surface":{"working_unique_xy_points":int(len(surface_points)),"median_spacing_m":spacing,"triangles_before_qc":int(len(tri.simplices)),"triangles_after_qc":int(len(faces)),**triangle_config,**raster_info},
        "las":{"z_definition":"z = -depth_primary_m","intensity_semantics":"unused; left zero","extra_bytes":["depth_m","beam_distance_m","quality_code","source_id"],**las_info},
        "warnings":[
            "Beam distance is treated as KoggerApp-derived primary depth and is not automatically offset by transducer draft.",
            "Bottom elevation is unavailable because vertical datum/reduction is not established.",
            "Support quality is not a metrological uncertainty surface.",
            "Direct UM982 stream is not inferred from GPS_RAW_INT.",
        ],
    }
    (config.output_dir/"processing_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (config.output_dir/"processing_config.json").write_text(json.dumps(asdict(config),default=str,ensure_ascii=False,indent=2),encoding="utf-8")

    finished=datetime.now(timezone.utc).isoformat()
    if config.create_project_database:
        notify("Write project.navimetry.sqlite")
        _store_project_database(config.output_dir/"project.navimetry.sqlite",csv_result,klf_result,observations,config,report,started,finished,csv_asset,klf_asset)

    manifest=[]
    for p in sorted(config.output_dir.iterdir()):
        if p.is_file(): manifest.append({"path":p.name,"size_bytes":p.stat().st_size,"sha256":sha256sum(p)})
    (config.output_dir/"manifest.json").write_text(json.dumps({"generated_at_utc":finished,"files":manifest},ensure_ascii=False,indent=2),encoding="utf-8")
    notify("Create project ZIP")
    archive=shutil.make_archive(str(config.output_dir.parent/f"{config.output_dir.name}_Navimetry_project"),"zip",root_dir=config.output_dir)
    result={**report,"archive":archive,"accepted_aggregated_points":len(surface_points),"minimum_depth_m":float(surface_points.depth_primary_m.min()),"maximum_depth_m":float(surface_points.depth_primary_m.max())}
    notify("Processing complete")
    return result
