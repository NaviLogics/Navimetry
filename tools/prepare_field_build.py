from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one occurrence, got {count}")
    return text.replace(old, new, 1)


processor_path = Path("bathymetry/processor.py")
processor = processor_path.read_text(encoding="utf-8")

old_match = 'notify("Build Kogger↔PX4 clock model"); clock=build_clock_model(klf_result.attitude); notify("Match CSV to KLF GLOBAL_POSITION_INT"); match_df,match_report=match_csv_to_klf(csv_result.normalized,klf_result.global_position)'
new_match = 'notify("Build recorder↔PX4 clock model"); clock=build_clock_model(klf_result.attitude); notify("Match sonar cycles to GNSS / attitude / estimator"); match_df,match_report=match_csv_to_klf(csv_result.normalized,klf_result.global_position,klf_result.sonar_cycles,klf_result.gps_raw,klf_result.attitude,klf_result.estimator_status,klf_result.system_time)'
processor = replace_once(processor, old_match, new_match, "integrated sonar-cycle matcher")

# LAS: preserve millimetre-or-better coordinate quantisation and embed CRS plus producer identity.
processor = replace_once(
    processor,
    'header=laspy.LasHeader(point_format=3,version="1.2"); header.scales=np.array([sx,sy,sz]); header.offsets=np.array([ox,oy,oz]); header.add_crs(CRS.from_user_input(output_crs))',
    'header=laspy.LasHeader(point_format=3,version="1.2"); header.scales=np.array([sx,sy,sz]); header.offsets=np.array([ox,oy,oz]); header.system_identifier="NAVIMETRY"; header.generating_software="Navimetry 0.2"; header.add_crs(CRS.from_user_input(output_crs))',
    "LAS metadata",
)

# Keep presentation products for internal review, but create explicit strict customer aliases.
processor = replace_once(
    processor,
    '    fig,ax=plt.subplots(figsize=(11.69,8.27)); im=ax.imshow(presentation_raster,extent=(west,east,south,north),origin="upper",aspect="equal")\n',
    '    fig,ax=plt.subplots(figsize=(11.69,8.27)); im=ax.imshow(strict_raster,extent=(west,east,south,north),origin="upper",aspect="equal")\n',
    "customer map uses strict raster",
)
processor = replace_once(
    processor,
    '    if strict_mask.any() and not strict_mask.all(): ax.contour(gx,gy[::-1],strict_mask.astype(np.uint8),levels=[0.5],linewidths=0.7)\n',
    '',
    "remove QC contour from customer map",
)
processor = replace_once(processor, 'fig.colorbar(im,ax=ax,shrink=.8,label="Depth, m"); ax.set_title("Navimetry — standard GIS/TIN bathymetric grid"); ax.set_xlabel("X, m"); ax.set_ylabel("Y, m")', 'fig.colorbar(im,ax=ax,shrink=.8,label="Глубина, м"); ax.set_title("Navimetry — батиметрическая карта"); ax.set_xlabel("X, м"); ax.set_ylabel("Y, м")', "Russian map labels")
processor = replace_once(processor, 'line_text="unavailable" if line_spacing is None else f"{line_spacing:.2f} m"', 'line_text="нет данных" if line_spacing is None else f"{line_spacing:.2f} м"', "Russian line spacing")
processor = replace_once(
    processor,
    'ax.text(.01,.01,f"CRS: {output_crs}\\nDepth source: KOGGERAPP_BEAM\\nVertical datum: unknown\\nPixel: {pixel_size:.3f} m\\nEffective line spacing: {line_text}\\nPresentation radius: {radius:.2f} m\\nSurface: linear Delaunay TIN + maximum edge gap limit",transform=ax.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.8,"edgecolor":"gray"})',
    'ax.text(.01,.01,f"Система координат: {output_crs}\\nИсточник глубины: эхолот\\nВертикальная система: не задана\\nРазмер ячейки: {pixel_size:.3f} м\\nРасстояние между галсами: {line_text}\\nПокрытие: только область, поддержанная измерениями",transform=ax.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.8,"edgecolor":"gray"}); ax.annotate("N",xy=(.94,.88),xytext=(.94,.76),xycoords="axes fraction",textcoords="axes fraction",ha="center",va="center",fontsize=11,fontweight="bold",arrowprops={"arrowstyle":"-|>","lw":1.2}); span=max(east-west,pixel_size); raw=span/5.0; power=10.0**math.floor(math.log10(raw)); scaled=raw/power; scale=(1.0 if scaled<1.5 else 2.0 if scaled<3.5 else 5.0)*power; sx1=east-0.05*span; sx0=sx1-scale; sy=south+0.05*(north-south); ax.plot([sx0,sx1],[sy,sy],linewidth=2); ax.plot([sx0,sx0],[sy-0.005*(north-south),sy+0.005*(north-south)],linewidth=1); ax.plot([sx1,sx1],[sy-0.005*(north-south),sy+0.005*(north-south)],linewidth=1); ax.text((sx0+sx1)/2,sy+0.012*(north-south),f"{scale:g} м",ha="center",va="bottom",fontsize=8)',
    "Russian customer annotation, north arrow and scale bar",
)
processor = replace_once(
    processor,
    'fig.tight_layout(); fig.savefig(output_dir/"processing_report.pdf",dpi=180); plt.close(fig); shutil.copy2(output_dir/"processing_report.pdf",output_dir/"bathymetry_map.pdf")',
    'fig.tight_layout(); fig.savefig(output_dir/"processing_report.pdf",dpi=180); plt.close(fig); shutil.copy2(output_dir/"processing_report.pdf",output_dir/"bathymetry_map.pdf"); shutil.copy2(output_dir/"bathymetry_depth_strict.tiff",output_dir/"customer_bathymetry_depth.tiff"); shutil.copy2(output_dir/"bathymetry_map.pdf",output_dir/"customer_bathymetry_map.pdf")',
    "strict customer raster and map aliases",
)

# Explicit customer aliases use the strict point cloud and strict TIN, never presentation interpolation.
processor = replace_once(
    processor,
    'notify("Write XYZ, LAS, strict OBJ/STL"); write_xyz(surface_points,config.output_dir/"bottom_points.xyz"); las_info=write_las(surface_points,config.output_dir/"bottom_points.las",config.output_crs); strict_mesh_info=write_strict_mesh(xy,depths,faces,config.output_dir,config.output_crs,local_origin)',
    'notify("Write XYZ, LAS, strict OBJ/STL"); write_xyz(surface_points,config.output_dir/"bottom_points.xyz"); las_info=write_las(surface_points,config.output_dir/"bottom_points.las",config.output_crs); strict_mesh_info=write_strict_mesh(xy,depths,faces,config.output_dir,config.output_crs,local_origin); shutil.copy2(config.output_dir/"bottom_points.xyz",config.output_dir/"customer_bottom_points.xyz"); shutil.copy2(config.output_dir/"bottom_points.las",config.output_dir/"customer_bottom_points.las"); shutil.copy2(config.output_dir/"depth_surface_strict.obj",config.output_dir/"customer_depth_surface.obj"); shutil.copy2(config.output_dir/"depth_surface_strict.stl",config.output_dir/"customer_depth_surface.stl")',
    "strict customer point cloud and mesh aliases",
)

# Internal pre-delivery validator: cross-check point cloud, CRS, mesh existence and strict GeoTIFF geometry.
validator = '''\n\ndef validate_delivery_products(points: pd.DataFrame, output_dir: Path, output_crs: str, pixel_size: float) -> dict:\n    checks={}\n    expected=len(points)\n    xyz=np.loadtxt(output_dir/"customer_bottom_points.xyz")\n    if xyz.ndim==1: xyz=xyz.reshape(1,-1)\n    checks["xyz_row_count"]={"pass":bool(len(xyz)==expected),"expected":int(expected),"actual":int(len(xyz))}\n    if len(xyz)==expected:\n        dx=float(np.max(np.abs(xyz[:,0]-points["x_m"].to_numpy(float))))\n        dy=float(np.max(np.abs(xyz[:,1]-points["y_m"].to_numpy(float))))\n        dd=float(np.max(np.abs(xyz[:,2]-points["depth_primary_m"].to_numpy(float))))\n        checks["xyz_values"]={"pass":bool(max(dx,dy,dd)<=0.00011),"max_abs_x_m":dx,"max_abs_y_m":dy,"max_abs_depth_m":dd,"z_semantics":"positive_down_depth"}\n    las=laspy.read(output_dir/"customer_bottom_points.las")\n    parsed=las.header.parse_crs()\n    checks["las_point_count"]={"pass":bool(len(las.points)==expected),"expected":int(expected),"actual":int(len(las.points))}\n    checks["las_crs"]={"pass":bool(parsed is not None and CRS.from_user_input(parsed)==CRS.from_user_input(output_crs)),"expected":str(CRS.from_user_input(output_crs)),"actual":str(parsed)}\n    checks["las_xyz_finite"]={"pass":bool(np.isfinite(las.x).all() and np.isfinite(las.y).all() and np.isfinite(las.z).all())}\n    for name in ("customer_depth_surface.obj","customer_depth_surface.stl"):\n        p=output_dir/name; checks[name]={"pass":bool(p.exists() and p.stat().st_size>0),"size_bytes":int(p.stat().st_size) if p.exists() else 0}\n    with rasterio.open(output_dir/"customer_bathymetry_depth.tiff") as ds:\n        rx=abs(float(ds.transform.a)); ry=abs(float(ds.transform.e)); crs_ok=ds.crs is not None and CRS.from_user_input(ds.crs)==CRS.from_user_input(output_crs); data=ds.read(1); valid=data!=ds.nodata\n        checks["geotiff"]={"pass":bool(crs_ok and abs(rx-pixel_size)<1e-9 and abs(ry-pixel_size)<1e-9 and valid.any()),"crs":str(ds.crs),"pixel_x_m":rx,"pixel_y_m":ry,"nodata":ds.nodata,"valid_cells":int(valid.sum()),"surface_role":"strict_engineering_evidence"}\n    passed=all(bool(v.get("pass")) for v in checks.values())\n    result={"status":"PASS" if passed else "FAIL","checks":checks,"customer_files":["customer_bottom_points.las","customer_bottom_points.xyz","customer_depth_surface.obj","customer_depth_surface.stl","customer_bathymetry_depth.tiff","customer_bathymetry_map.pdf"]}\n    (output_dir/"delivery_validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")\n    if not passed: raise ProcessingError("Customer delivery validation failed; see delivery_validation.json")\n    return result\n'''
processor = replace_once(processor, '\n\ndef _store_project_database(', validator+'\n\ndef _store_project_database(', "delivery validator")
processor = replace_once(
    processor,
    'notify("Write standard GIS/TIN GeoTIFF, OBJ/STL and PDF"); raster_info=write_surface_products(surface_points,tri,local_xy,local_origin,accepted_indices,pixel,config.output_dir,config.output_crs,config,point_spacing,effective_geometry)\n\n    klf_inv=',
    'notify("Write Surface QC v5 strict/presentation GeoTIFF, presentation OBJ/STL and PDF"); raster_info=write_surface_products(surface_points,tri,local_xy,local_origin,accepted_indices,pixel,config.output_dir,config.output_crs,config,point_spacing,effective_geometry)\n    notify("Validate customer delivery products"); delivery_validation=validate_delivery_products(surface_points,config.output_dir,config.output_crs,pixel)\n\n    klf_inv=',
    "run delivery validator",
)
processor = replace_once(processor, '"las":{"z_definition":"z = -depth_primary_m","intensity_semantics":"unused; left zero","extra_bytes":["depth_m","beam_distance_m","quality_code","source_id"],**las_info},', '"las":{"z_definition":"z = -depth_primary_m","intensity_semantics":"unused; left zero","extra_bytes":["depth_m","beam_distance_m","quality_code","source_id"],**las_info},"delivery_validation":delivery_validation,', "report delivery validation")

processor_path.write_text(processor, encoding="utf-8")

qc_path = Path("bathymetry/quality_control.py")
qc = qc_path.read_text(encoding="utf-8")
rematch = '    if klf_result is not None and hasattr(klf_result,"sonar_cycles") and not klf_result.sonar_cycles.empty:\n        match_result,_=match_csv_to_klf(csv_result.normalized,klf_result.global_position,klf_result.sonar_cycles,klf_result.gps_raw,klf_result.attitude,klf_result.estimator_status,klf_result.system_time)\n'
qc = replace_once(qc, 'from bathymetry.matcher import match_csv_to_klf\n', '', "remove QC matcher import")
qc = replace_once(qc, rematch, '', "remove duplicate QC matcher")
qc_path.write_text(qc, encoding="utf-8")

print("Field build source prepared: integrated matcher + strict customer exports + delivery validation")
