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
except ImportError:
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
from bathymetry.survey_geometry import estimate_track_geometry
from bathymetry.survey_presets import get_survey_preset, preset_metadata, resolve_surface_geometry


class ProcessingError(Exception):
    """Navimetry processing error."""


def sha256sum(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


def median_spacing(points: np.ndarray) -> float:
    if len(points)<2:
        raise ProcessingError("Not enough points to calculate spacing")
    distances,_=cKDTree(points).query(points,k=2)
    positive=distances[:,1][distances[:,1]>0]
    if not len(positive):
        raise ProcessingError("All surface points have identical XY")
    return float(np.median(positive))


def build_local_delaunay(xy: np.ndarray) -> tuple[Delaunay,np.ndarray,np.ndarray,dict]:
    if len(xy)<3:
        raise ProcessingError("At least three surface points are required for Delaunay triangulation")
    origin=np.mean(xy,axis=0,dtype=np.float64)
    local_xy=xy.astype(np.float64)-origin
    try:
        tri=Delaunay(local_xy)
    except QhullError as error:
        raise ProcessingError("Cannot build local-coordinate Delaunay surface from current points") from error
    used=np.unique(tri.simplices.ravel()) if len(tri.simplices) else np.array([],dtype=np.int64)
    coplanar_count=int(len(getattr(tri,"coplanar",[])))
    info={
        "coordinate_frame":"local_xy_for_computation",
        "local_origin_x_m":float(origin[0]),"local_origin_y_m":float(origin[1]),
        "input_vertices":int(len(xy)),"used_vertices":int(len(used)),"coplanar_vertices":coplanar_count,
        "all_vertices_used":bool(len(used)==len(xy) and coplanar_count==0),
    }
    return tri,local_xy,origin,info


def triangle_metrics(xy: np.ndarray, depth: np.ndarray, faces: np.ndarray) -> pd.DataFrame:
    rows=[]
    for idx,face in enumerate(faces):
        p=xy[face]; z=depth[face]
        edges=np.array([np.linalg.norm(p[0]-p[1]),np.linalg.norm(p[1]-p[2]),np.linalg.norm(p[2]-p[0])],dtype=float)
        a,b,c=edges; area=abs(np.cross(p[1]-p[0],p[2]-p[0]))/2.0
        min_edge=float(edges.min()); max_edge=float(edges.max()); aspect=max_edge/min_edge if min_edge>0 else math.inf
        angles=[]
        for aa,bb,cc in ((a,b,c),(b,c,a),(c,a,b)):
            den=2*aa*bb; cosv=(aa*aa+bb*bb-cc*cc)/den if den>0 else 1.0
            angles.append(math.degrees(math.acos(max(-1.0,min(1.0,cosv)))))
        gradient=float((z.max()-z.min())/max_edge) if max_edge>0 else math.inf
        rows.append({"triangle_index":idx,"v0":int(face[0]),"v1":int(face[1]),"v2":int(face[2]),
                     "min_edge_m":min_edge,"max_edge_m":max_edge,"area_m2":float(area),"aspect_ratio":float(aspect),
                     "max_angle_deg":float(max(angles)),"depth_gradient_m_per_m":gradient})
    return pd.DataFrame(rows)


def filter_triangles(metrics: pd.DataFrame, config: ProcessingConfig, point_spacing: float, effective_geometry: dict) -> tuple[np.ndarray,dict]:
    if metrics.empty:
        raise ProcessingError("Triangulation contains no triangles")
    derived=effective_geometry.get("strict_max_triangle_edge_m")
    if derived is not None:
        edge_limit=float(derived); edge_mode=str(effective_geometry.get("strict_max_triangle_edge_source","survey_geometry"))
    else:
        # Geometry estimation can fail on irregular/non-line surveys. Do not fall back to the along-track
        # nearest-neighbour scale; use a clearly labelled distribution fallback instead.
        edge_limit=max(0.25,float(metrics["max_edge_m"].quantile(0.75)))
        edge_mode="fallback_triangle_distribution_q75"
    mask=(metrics["max_edge_m"].le(edge_limit)
          & metrics["aspect_ratio"].le(config.max_triangle_aspect_ratio)
          & metrics["max_angle_deg"].le(config.max_triangle_angle_deg)
          & metrics["area_m2"].ge(config.min_triangle_area_m2))
    if config.max_depth_gradient_m_per_m is not None:
        mask &= metrics["depth_gradient_m_per_m"].le(config.max_depth_gradient_m_per_m)
    metrics["quality_status"]=np.where(mask,"accepted","rejected")
    accepted_indices=metrics.loc[mask,"triangle_index"].to_numpy(dtype=np.int64)
    if len(accepted_indices)==0:
        raise ProcessingError("Triangle QC rejected all triangles; review survey geometry or manual thresholds")
    return accepted_indices,{"max_triangle_edge_m":float(edge_limit),"max_triangle_edge_mode":edge_mode}


def _las_scale_and_offset(values: np.ndarray) -> tuple[float,float]:
    safe_limit=float(np.iinfo(np.int32).max)*0.90; span=float(values.max()-values.min())
    return max(0.001,span/safe_limit if span else 0.001),float(values.min())


def write_las(points: pd.DataFrame,path: Path,output_crs: str) -> dict:
    if laspy is None:
        raise ProcessingError("laspy is unavailable")
    x=points["x_m"].to_numpy(float); y=points["y_m"].to_numpy(float); depth=points["depth_primary_m"].to_numpy(float); z=-depth
    sx,ox=_las_scale_and_offset(x); sy,oy=_las_scale_and_offset(y); sz,oz=_las_scale_and_offset(z)
    header=laspy.LasHeader(point_format=3,version="1.2"); header.scales=np.array([sx,sy,sz]); header.offsets=np.array([ox,oy,oz]); header.add_crs(CRS.from_user_input(output_crs))
    for name,dtype in (("depth_m",np.float32),("beam_distance_m",np.float32),("quality_code",np.uint8),("source_id",np.uint32)):
        header.add_extra_dim(laspy.ExtraBytesParams(name=name,type=dtype))
    las=laspy.LasData(header); las.x=x; las.y=y; las.z=z
    las.depth_m=depth.astype(np.float32); las.beam_distance_m=points["beam_distance_raw_m"].to_numpy(float).astype(np.float32)
    las.quality_code=points.get("quality_code",pd.Series(1,index=points.index)).to_numpy(np.uint8); las.source_id=np.arange(1,len(points)+1,dtype=np.uint32)
    las.write(path); return {"scales":[sx,sy,sz],"offsets":[ox,oy,oz]}


def write_xyz(points: pd.DataFrame,path: Path) -> None:
    points[["x_m","y_m","depth_primary_m"]].to_csv(path,sep=" ",index=False,header=False,float_format="%.4f")


def _export_mesh(vertices: np.ndarray,faces: np.ndarray,output_dir: Path,basename: str) -> dict:
    mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=False); mesh.remove_unreferenced_vertices()
    mesh.export(output_dir/f"{basename}.obj"); mesh.export(output_dir/f"{basename}.stl")
    return {"vertices":int(len(mesh.vertices)),"faces":int(len(mesh.faces))}


def write_strict_mesh(xy: np.ndarray,depth: np.ndarray,faces: np.ndarray,output_dir: Path,output_crs: str,local_origin: np.ndarray) -> dict:
    vertices=np.column_stack((xy[:,0]-local_origin[0],xy[:,1]-local_origin[1],-depth)); info=_export_mesh(vertices,faces,output_dir,"depth_surface_strict")
    shutil.copy2(output_dir/"depth_surface_strict.obj",output_dir/"depth_surface.obj"); shutil.copy2(output_dir/"depth_surface_strict.stl",output_dir/"depth_surface.stl")
    metadata={"horizontal_crs":output_crs,"units":"m","local_origin_x_m":float(local_origin[0]),"local_origin_y_m":float(local_origin[1]),
              "model_z_definition":"z = -depth_primary_m","depth_source":"KOGGERAPP_BEAM","vertical_datum":"unknown","bottom_elevation_available":False,
              "mesh_role":"strict_triangle_qc","legacy_aliases":["depth_surface.obj","depth_surface.stl"],**info}
    (output_dir/"depth_surface_strict_metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    (output_dir/"depth_surface_metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    return info


def write_presentation_grid_mesh(raster: np.ndarray,west: float,north: float,pixel_size: float,output_dir: Path,output_crs: str) -> dict:
    valid=np.isfinite(raster); rows,cols=np.nonzero(valid)
    if len(rows)<3: return {"vertices":0,"faces":0,"written":False}
    ids=np.full(raster.shape,-1,dtype=np.int64); ids[rows,cols]=np.arange(len(rows),dtype=np.int64)
    x=west+(cols+0.5)*pixel_size; y=north-(rows+0.5)*pixel_size; origin=np.array([float(np.min(x)),float(np.min(y))])
    vertices=np.column_stack((x-origin[0],y-origin[1],-raster[rows,cols]))
    a=ids[:-1,:-1]; b=ids[:-1,1:]; c=ids[1:,:-1]; d=ids[1:,1:]; quads=(a>=0)&(b>=0)&(c>=0)&(d>=0); qr,qc=np.nonzero(quads)
    if len(qr)==0: return {"vertices":int(len(vertices)),"faces":0,"written":False}
    faces=np.vstack((np.column_stack((a[qr,qc],c[qr,qc],b[qr,qc])),np.column_stack((b[qr,qc],c[qr,qc],d[qr,qc])))).astype(np.int64)
    info=_export_mesh(vertices,faces,output_dir,"depth_surface_presentation")
    metadata={"horizontal_crs":output_crs,"units":"m","local_origin_x_m":float(origin[0]),"local_origin_y_m":float(origin[1]),"model_z_definition":"z = -presentation_depth_m",
              "depth_source":"KOGGERAPP_BEAM","vertical_datum":"unknown","mesh_role":"presentation_grid_not_quality_evidence","pixel_size_m":float(pixel_size),**info}
    (output_dir/"depth_surface_presentation_metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    return {**info,"written":True}


def _write_raster(path: Path,array: np.ndarray,transform,crs: str,nodata,dtype: str,description: str,tags: dict) -> None:
    with rasterio.open(path,"w",driver="GTiff",height=array.shape[0],width=array.shape[1],count=1,dtype=dtype,crs=CRS.from_user_input(crs),transform=transform,nodata=nodata,compress="deflate") as ds:
        ds.write(array.astype(dtype),1); ds.set_band_description(1,description); ds.update_tags(**tags)


def write_surface_products(points: pd.DataFrame,triangulation: Delaunay,local_xy: np.ndarray,local_origin: np.ndarray,accepted_faces: np.ndarray,
                           pixel_size: float,output_dir: Path,output_crs: str,config: ProcessingConfig,point_spacing: float,effective_geometry: dict) -> dict:
    """Surface QC v4: local interpolation with track-derived survey geometry."""
    x=points["x_m"].to_numpy(float); y=points["y_m"].to_numpy(float); depth=points["depth_primary_m"].to_numpy(float)
    west,east,south,north=float(x.min()),float(x.max()),float(y.min()),float(y.max())
    width=max(1,int(math.ceil((east-west)/pixel_size))); height=max(1,int(math.ceil((north-south)/pixel_size)))
    if width*height>20_000_000: raise ProcessingError("Raster too large; increase pixel size")
    gx=west+(np.arange(width)+0.5)*pixel_size; gy=north-(np.arange(height)+0.5)*pixel_size; mx,my=np.meshgrid(gx,gy)
    grid=np.column_stack((mx.ravel(),my.ravel())); grid_local=grid-local_origin
    interp=LinearNDInterpolator(triangulation,depth,fill_value=np.nan); full_values=np.asarray(interp(grid_local),float)
    simplex=triangulation.find_simplex(grid_local); inside_hull=simplex>=0; allowed=set(int(i) for i in accepted_faces.tolist())
    strict_support=np.array([(sid in allowed) for sid in simplex],dtype=bool); strict_values=full_values.copy(); strict_values[~strict_support]=np.nan
    strict_raster=strict_values.reshape(height,width); strict_mask=strict_support.reshape(height,width)
    tree=cKDTree(local_xy); nearest=tree.query(grid_local,k=1)[0].reshape(height,width); nearest_flat=nearest.ravel()
    radius=effective_geometry.get("presentation_radius_m")
    if radius is None:
        radius=max(1.0,float(effective_geometry.get("strict_max_triangle_edge_m") or point_spacing*20.0)*0.75)
        radius_mode="fallback_from_strict_edge"
    else:
        radius=float(radius); radius_mode=str(effective_geometry.get("presentation_radius_source","survey_geometry"))
    presentation_support=(inside_hull & (nearest_flat<=radius)) | strict_support
    presentation_values=full_values.copy(); presentation_values[~presentation_support]=np.nan
    presentation_raster=presentation_values.reshape(height,width); presentation_mask=presentation_support.reshape(height,width)
    transform=from_origin(west,north,pixel_size,pixel_size); common={"units":"m","depth_direction":"positive_down","depth_source":"KOGGERAPP_BEAM","vertical_datum":"unknown"}
    _write_raster(output_dir/"bathymetry_depth_strict.tiff",np.where(np.isfinite(strict_raster),strict_raster,-9999.0),transform,output_crs,-9999.0,"float32","Primary depth, strict triangle-QC support",{**common,"surface_role":"strict_engineering_evidence"})
    _write_raster(output_dir/"bathymetry_depth.tiff",np.where(np.isfinite(presentation_raster),presentation_raster,-9999.0),transform,output_crs,-9999.0,"float32","Primary depth, presentation grid",{**common,"surface_role":"presentation_only","quality_warning":"Use strict products for engineering support evidence"})
    _write_raster(output_dir/"coverage_mask.tiff",strict_mask.astype(np.uint8),transform,output_crs,0,"uint8","Strict surface support mask",{"meaning":"1=supported_by_triangle_that_passed_strict_QC,0=not_strictly_supported"})
    _write_raster(output_dir/"presentation_mask.tiff",presentation_mask.astype(np.uint8),transform,output_crs,0,"uint8","Presentation grid support mask",{"meaning":"1=inside_Delaunay_hull_and_within_radius_or_strict_support","radius_m":f"{radius:.6f}","not_quality_evidence":"true"})
    _write_raster(output_dir/"nearest_point_distance.tiff",nearest.astype(np.float32),transform,output_crs,-9999.0,"float32","Distance to nearest accepted observation, m",{"units":"m"})
    if config.generate_quality_proxy:
        proxy=np.where(presentation_mask,np.clip(nearest/max(radius,1e-9),0,1),1.0).astype(np.float32)
        _write_raster(output_dir/"support_quality.tiff",proxy,transform,output_crs,-9999.0,"float32","Support distance proxy (0 best, 1 worst)",{"not_metrological_uncertainty":"true","reference_radius_m":f"{radius:.6f}"})
    presentation_mesh=write_presentation_grid_mesh(presentation_raster,west,north,pixel_size,output_dir,output_crs)
    fig,ax=plt.subplots(figsize=(11.69,8.27)); im=ax.imshow(presentation_raster,extent=(west,east,south,north),origin="upper",aspect="equal")
    if strict_mask.any() and not strict_mask.all(): ax.contour(gx,gy[::-1],strict_mask.astype(np.uint8),levels=[0.5],linewidths=0.7)
    fig.colorbar(im,ax=ax,shrink=.8,label="Depth, m"); ax.set_title("Navimetry 0.2 Surface QC v4 — survey-geometry presentation grid"); ax.set_xlabel("X, m"); ax.set_ylabel("Y, m")
    line_spacing=effective_geometry.get("effective_line_spacing_m"); line_text="unavailable" if line_spacing is None else f"{line_spacing:.2f} m"
    ax.text(.01,.01,f"CRS: {output_crs}\nDepth source: KOGGERAPP_BEAM\nVertical datum: unknown\nPixel: {pixel_size:.3f} m\nEffective line spacing: {line_text}\nPresentation radius: {radius:.2f} m\nStrict coverage outline: triangle QC",transform=ax.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.8,"edgecolor":"gray"})
    fig.tight_layout(); fig.savefig(output_dir/"processing_report.pdf",dpi=180); plt.close(fig); shutil.copy2(output_dir/"processing_report.pdf",output_dir/"bathymetry_map.pdf")
    return {"surface_qc_version":"4","width":width,"height":height,"pixel_size_m":pixel_size,"strict_supported_cells":int(strict_mask.sum()),
            "presentation_supported_cells":int(presentation_mask.sum()),"presentation_radius_m":radius,"presentation_radius_mode":radius_mode,
            "presentation_grid_is_quality_evidence":False,"presentation_mesh":presentation_mesh}


def _store_project_database(db_path: Path,csv_result,klf_result,observations: pd.DataFrame,config: ProcessingConfig,report: dict,started_at: str,finished_at: str,csv_asset: dict,klf_asset: dict|None) -> None:
    conn=initialize_database(db_path)
    try:
        conn.execute("INSERT INTO project(name,created_at,navimetry_version,config_json) VALUES(?,?,?,?)",(config.output_dir.name,started_at,"0.2",json.dumps(asdict(config),default=str,ensure_ascii=False)))
        cur=conn.execute("INSERT INTO source_asset(path,filename,asset_type,sha256,size_bytes,imported_at,parser_version) VALUES(?,?,?,?,?,?,?)",(str(config.input_csv),config.input_csv.name,"CSV",csv_asset["sha256"],csv_asset["size_bytes"],started_at,"csv_importer/0.2")); csv_asset_id=cur.lastrowid
        csv_table=csv_result.normalized.copy(); csv_table["source_asset_id"]=csv_asset_id; csv_table=csv_table.rename(columns={"csv_number":"number"})
        store_dataframe(conn,"csv_observation",csv_table,["source_asset_id","source_row","number","beam_distance_raw_m","rangefinder_raw_m","latitude_raw_deg","longitude_raw_deg","extras_json"])
        if klf_result is not None and klf_asset is not None:
            cur=conn.execute("INSERT INTO source_asset(path,filename,asset_type,sha256,size_bytes,imported_at,parser_version) VALUES(?,?,?,?,?,?,?)",(str(config.input_klf),config.input_klf.name,"KLF",klf_asset["sha256"],klf_asset["size_bytes"],started_at,"kp2_mavlink/0.2")); klf_asset_id=cur.lastrowid
            fr=pd.DataFrame(klf_result.frame_records)
            if not fr.empty:
                fr["source_asset_id"]=klf_asset_id; store_dataframe(conn,"klf_frame",fr,["source_asset_id","frame_index","frame_type","payload_length","ltime_ms","checksum_valid","complete","kogger_id"])
            mv=pd.DataFrame(klf_result.mavlink_records)
            if not mv.empty:
                mv=mv.rename(columns={"frame_index":"klf_frame_id"}); store_dataframe(conn,"mavlink_message",mv,["klf_frame_id","sysid","compid","msgid","sequence","px4_boot_time_ms","utc_time_us","ltime_ms"])
        ot=observations.copy(); ot["csv_observation_id"]=ot["observation_id"]; ot["primary_depth_m"]=ot["depth_primary_m"]; ot["primary_depth_source"]=ot["depth_source"]
        ot["validation_depth_m"]=ot["rangefinder_m"]; ot["validation_available"]=ot["rangefinder_available"].astype(int); ot["px4_time_ms"]=ot.get("px4_boot_time_ms",np.nan)
        store_dataframe(conn,"observation",ot,["csv_observation_id","primary_depth_m","primary_depth_source","validation_depth_m","validation_available","latitude_deg","longitude_deg","x_m","y_m","kogger_time_ms","px4_time_ms","timestamp_utc","position_source","timestamp_quality","position_quality","depth_quality","overall_quality","quality_flags_json"])
        conn.execute("INSERT INTO processing_run(started_at,finished_at,config_json,report_json) VALUES(?,?,?,?)",(started_at,finished_at,json.dumps(asdict(config),default=str,ensure_ascii=False),json.dumps(report,ensure_ascii=False))); conn.commit()
    finally:
        conn.close()


def run_pipeline(config: ProcessingConfig,progress: Callable[[str],None]|None=None) -> dict:
    def notify(msg):
        if progress: progress(msg)
    started=datetime.now(timezone.utc).isoformat()
    if config.apply_vertical_correction: raise ProcessingError("Navimetry 0.2 does not automatically apply vertical correction to Beam distance. Use vertical_reduction_mode only after explicit datum design.")
    if config.min_depth_m>=config.max_depth_m: raise ProcessingError("Minimum depth must be smaller than maximum depth")
    try: preset=get_survey_preset(config.survey_preset)
    except ValueError as error: raise ProcessingError(str(error)) from error
    if preset.requires_georeferenced_swath_points and not config.has_georeferenced_swath_points:
        raise ProcessingError("The SWATH_SIDESCAN preset requires georeferenced across-track/swath bottom points. Vessel-centerline Beam distance alone cannot reconstruct port/starboard swath geometry.")
    if preset.key=="MANUAL" and config.expected_line_spacing_m is None and config.max_triangle_edge_m is None and config.max_nearest_point_distance_m is None:
        raise ProcessingError("MANUAL survey preset requires expected line spacing and/or explicit surface thresholds")
    config.output_dir.mkdir(parents=True,exist_ok=True)

    notify("Import KoggerApp CSV"); csv_result=import_kogger_csv(config); csv_asset={"sha256":sha256sum(config.input_csv),"size_bytes":config.input_csv.stat().st_size}; shutil.copy2(config.input_csv,config.output_dir/"source.csv")
    klf_result: KlfParseResult|None=None; klf_asset=None; clock={"model_type":"unavailable","quality":"unavailable"}; match_df=None; match_report={"method":"unavailable","identity_confidence":"unavailable"}
    if config.input_klf is not None:
        notify("Parse KLF / KP2 / MAVLink"); klf_result=inspect_klf(config.input_klf,capture_records=config.create_project_database); klf_asset={"sha256":sha256sum(config.input_klf),"size_bytes":config.input_klf.stat().st_size}
        notify("Build Kogger↔PX4 clock model"); clock=build_clock_model(klf_result.attitude); notify("Match CSV to KLF GLOBAL_POSITION_INT"); match_df,match_report=match_csv_to_klf(csv_result.normalized,klf_result.global_position)
        klf_result.mavlink_inventory.to_csv(config.output_dir/"mavlink_inventory.csv",index=False,encoding="utf-8-sig"); (config.output_dir/"klf_inventory.json").write_text(json.dumps(asdict(klf_result.inventory),ensure_ascii=False,indent=2),encoding="utf-8")
    else: notify("KLF not selected: CSV-only mode; time and KLF QC unavailable")

    notify("Normalize observations and run QC")
    try:
        observations=normalize_observations(csv_result,klf_result,match_df,config); surface_points=build_surface_points(observations,config.aggregation_mode)
    except QualityError as error: raise ProcessingError(str(error)) from error
    observations.to_csv(config.output_dir/"normalized_observations.csv",index=False,encoding="utf-8-sig")
    accepted=observations[observations["overall_quality"]=="valid_for_surface"].copy(); suspect=observations[(observations["position_quality"]=="suspect")|(observations["depth_quality"]=="suspect")].copy(); rejected=observations[observations["overall_quality"]!="valid_for_surface"].copy()
    accepted.to_csv(config.output_dir/"accepted_points.csv",index=False,encoding="utf-8-sig"); suspect.to_csv(config.output_dir/"suspect_points.csv",index=False,encoding="utf-8-sig"); rejected.to_csv(config.output_dir/"rejected_points.csv",index=False,encoding="utf-8-sig")

    notify("Segment survey tracks and estimate line spacing")
    track_geometry,tracks=estimate_track_geometry(observations); tracks.to_csv(config.output_dir/"survey_tracks.csv",index=False,encoding="utf-8-sig")
    effective_geometry=resolve_surface_geometry(config,preset,track_geometry)

    xy=surface_points[["x_m","y_m"]].to_numpy(float); depths=surface_points["depth_primary_m"].to_numpy(float); point_spacing=median_spacing(xy)
    notify("Build local-coordinate Delaunay and triangle QC"); tri,local_xy,local_origin,delaunay_info=build_local_delaunay(xy); tm=triangle_metrics(local_xy,depths,tri.simplices)
    accepted_indices,triangle_config=filter_triangles(tm,config,point_spacing,effective_geometry); tm.to_csv(config.output_dir/"triangle_quality.csv",index=False,encoding="utf-8-sig"); faces=tri.simplices[accepted_indices]
    if effective_geometry.get("strict_max_triangle_edge_m") is None:
        effective_geometry["strict_max_triangle_edge_m"]=triangle_config["max_triangle_edge_m"]; effective_geometry["strict_max_triangle_edge_source"]=triangle_config["max_triangle_edge_mode"]
    if effective_geometry.get("presentation_radius_m") is None:
        effective_geometry["presentation_radius_m"]=max(1.0,triangle_config["max_triangle_edge_m"]*0.75); effective_geometry["presentation_radius_source"]="fallback_from_strict_edge"

    pixel=config.pixel_size_m if config.pixel_size_m is not None else max(point_spacing/2.0,0.05)
    notify("Write XYZ, LAS, strict OBJ/STL"); write_xyz(surface_points,config.output_dir/"bottom_points.xyz"); las_info=write_las(surface_points,config.output_dir/"bottom_points.las",config.output_crs); strict_mesh_info=write_strict_mesh(xy,depths,faces,config.output_dir,config.output_crs,local_origin)
    notify("Write Surface QC v4 strict/presentation GeoTIFF, presentation OBJ/STL and PDF"); raster_info=write_surface_products(surface_points,tri,local_xy,local_origin,accepted_indices,pixel,config.output_dir,config.output_crs,config,point_spacing,effective_geometry)

    klf_inv=asdict(klf_result.inventory) if klf_result is not None else None
    report={
        "application":"Navimetry","version":"0.2-portable","classification":{"primary_depth_source":"KOGGERAPP_BEAM","rangefinder_role":"validation_only","vertical_datum":"unknown","manual_beam_correction":"unknown","industrial_accuracy":"not_established"},
        "source_assets":{"csv":{"filename":config.input_csv.name,**csv_asset},"klf":({"filename":config.input_klf.name,**klf_asset} if klf_asset else None)},"csv_inventory":csv_result.inventory,"klf_inventory":klf_inv,"clock_model":clock,"csv_klf_match":match_report,
        "survey_geometry":{"version":"1","selected_preset":preset_metadata(config.survey_preset),"configured_geometry":config.survey_geometry,"track_estimation":track_geometry,"effective":effective_geometry,"swath_half_width_m":config.swath_half_width_m,"has_georeferenced_swath_points":config.has_georeferenced_swath_points,"preset_values_are_accuracy_claims":False},
        "source_semantics":{"Beam distance":"KoggerApp-derived primary depth","Rangefinder":"validation only; unavailable when disabled/not recorded","GLOBAL_POSITION_INT":"PX4 estimated global position","GPS_RAW_INT":"raw GNSS evidence through PX4 MAVLink","ATTITUDE":"PX4 estimated attitude","ID_CHART":"raw sonar acoustic chart fragments"},
        "qc":{"normalized_rows":int(len(observations)),"surface_eligible_rows":int(len(accepted)),"rejected_for_surface_rows":int(len(rejected)),"suspect_rows":int(len(suspect)),"zero_coordinate_rows":int(((observations.latitude_raw_deg==0)&(observations.longitude_raw_deg==0)).sum()),"beam_valid_rows":int(observations.depth_primary_m.notna().sum()),"beam_missing_rows":int(observations.depth_primary_m.isna().sum()),"rangefinder_available_rows":int(observations.rangefinder_available.sum())},
        "surface":{"working_unique_xy_points":int(len(surface_points)),"median_nearest_point_spacing_m":point_spacing,"triangles_before_qc":int(len(tri.simplices)),"triangles_after_qc":int(len(faces)),"delaunay":delaunay_info,"strict_mesh":strict_mesh_info,**triangle_config,**raster_info},
        "las":{"z_definition":"z = -depth_primary_m","intensity_semantics":"unused; left zero","extra_bytes":["depth_m","beam_distance_m","quality_code","source_id"],**las_info},
        "warnings":["Beam distance is treated as KoggerApp-derived primary depth and is not automatically offset by transducer draft.","Bottom elevation is unavailable because vertical datum/reduction is not established.","Delaunay computation uses a local XY frame for numerical stability; exported geospatial products remain in the configured projected CRS.","Survey-line spacing is estimated from ordered track segments, not nearest-neighbour point spacing.","Automatic survey geometry is an algorithmic estimate, not a survey-standard or accuracy claim.","bathymetry_depth.tiff and depth_surface_presentation.* are presentation products; strict evidence remains bathymetry_depth_strict.tiff, coverage_mask.tiff and depth_surface_strict.*.","Presentation interpolation is not metrological uncertainty or strict coverage evidence.","Side-scan/swath reconstruction requires georeferenced across-track observations; centerline Beam distance is insufficient.","Direct UM982 stream is not inferred from GPS_RAW_INT."]}
    (config.output_dir/"processing_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); (config.output_dir/"processing_config.json").write_text(json.dumps(asdict(config),default=str,ensure_ascii=False,indent=2),encoding="utf-8")
    finished=datetime.now(timezone.utc).isoformat()
    if config.create_project_database:
        notify("Write project.navimetry.sqlite"); _store_project_database(config.output_dir/"project.navimetry.sqlite",csv_result,klf_result,observations,config,report,started,finished,csv_asset,klf_asset)
    manifest=[]
    for p in sorted(config.output_dir.iterdir()):
        if p.is_file(): manifest.append({"path":p.name,"size_bytes":p.stat().st_size,"sha256":sha256sum(p)})
    (config.output_dir/"manifest.json").write_text(json.dumps({"generated_at_utc":finished,"files":manifest},ensure_ascii=False,indent=2),encoding="utf-8")
    notify("Create project ZIP"); archive=shutil.make_archive(str(config.output_dir.parent/f"{config.output_dir.name}_Navimetry_project"),"zip",root_dir=config.output_dir)
    result={**report,"archive":archive,"accepted_aggregated_points":len(surface_points),"accepted_observations":len(accepted),"minimum_depth_m":float(surface_points.depth_primary_m.min()),"maximum_depth_m":float(surface_points.depth_primary_m.max())}; notify("Processing complete"); return result
