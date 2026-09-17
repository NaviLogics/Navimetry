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

processor = replace_once(
    processor,
    '    if strict_mask.any() and not strict_mask.all(): ax.contour(gx,gy[::-1],strict_mask.astype(np.uint8),levels=[0.5],linewidths=0.7)\n',
    '',
    "remove QC contour from customer map",
)
processor = replace_once(processor, 'fig.colorbar(im,ax=ax,shrink=.8,label="Depth, m"); ax.set_title("Navimetry 0.2 Surface QC v5 — survey-aware bathymetric grid"); ax.set_xlabel("X, m"); ax.set_ylabel("Y, m")', 'fig.colorbar(im,ax=ax,shrink=.8,label="Глубина, м"); ax.set_title("Navimetry — батиметрическая карта"); ax.set_xlabel("X, м"); ax.set_ylabel("Y, м")', "Russian map labels")
processor = replace_once(processor, 'line_text="unavailable" if line_spacing is None else f"{line_spacing:.2f} m"', 'line_text="нет данных" if line_spacing is None else f"{line_spacing:.2f} м"', "Russian line spacing")
processor = replace_once(processor, 'ax.text(.01,.01,f"CRS: {output_crs}\\nDepth source: KOGGERAPP_BEAM\\nVertical datum: unknown\\nPixel: {pixel_size:.3f} m\\nEffective line spacing: {line_text}\\nPresentation radius: {radius:.2f} m\\nStrict coverage: survey-aware triangle QC",transform=ax.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.8,"edgecolor":"gray"})', 'ax.text(.01,.01,f"Система координат: {output_crs}\\nИсточник глубины: эхолот\\nВертикальная система: не задана\\nРазмер пикселя: {pixel_size:.3f} м\\nРасстояние между галсами: {line_text}\\nРадиус представления: {radius:.2f} м\\nКонтроль покрытия: по геометрии съёмки",transform=ax.transAxes,fontsize=8,va="bottom",bbox={"facecolor":"white","alpha":.8,"edgecolor":"gray"})', "Russian customer annotation")
processor_path.write_text(processor, encoding="utf-8")

qc_path = Path("bathymetry/quality_control.py")
qc = qc_path.read_text(encoding="utf-8")
rematch = '    if klf_result is not None and hasattr(klf_result,"sonar_cycles") and not klf_result.sonar_cycles.empty:\n        match_result,_=match_csv_to_klf(csv_result.normalized,klf_result.global_position,klf_result.sonar_cycles,klf_result.gps_raw,klf_result.attitude,klf_result.estimator_status,klf_result.system_time)\n'
qc = replace_once(qc, 'from bathymetry.matcher import match_csv_to_klf\n', '', "remove QC matcher import")
qc = replace_once(qc, rematch, '', "remove duplicate QC matcher")
qc_path.write_text(qc, encoding="utf-8")

print("Field build source prepared: integrated matcher + Russian customer map")
