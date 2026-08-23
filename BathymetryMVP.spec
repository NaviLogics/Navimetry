# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
block_cipher = None
datas = []
binaries = []
hiddenimports = []
for package_name in (
    "rasterio",
    "pyproj",
    "matplotlib",
    "trimesh",
    "laspy",
    "scipy",
):
    package_datas, package_binaries, package_hiddenimports = (
        collect_all(package_name)
    )
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)
hiddenimports.extend(
    [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySideПодготовлю содержимое трех файлов: runtime hook, spec с подключением hook и workflow/изменение batch-сборки. Отдельно учту расположение ресурсов внутри `_MEIPASS`, чтобы `PROJ_DATA` и `GDAL_DATA` выставлялись до импорта `pyproj` и `rasterio`.Для portable-сборки добавьте файл `runtime_hook.py` в корень проекта и подключите его в `BathymetryMVP.spec`. Hook должен выполняться до импорта `pyproj`, `rasterio` и связанных модулей.
`runtime_hook.py`
```python
from __future__ import annotations
import os
import sys
from pathlib import Path
def find_directory(
    root: Path,
    candidates: tuple[Path, ...],
    markers: tuple[str, ...],
) -> Path | None:
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if all((candidate / marker).exists() for marker in markers):
            return candidate
    for marker in markers:
        for marker_path in root.rglob(marker):
            candidate = marker_path.parent
            if all((candidate / other).exists() for other in markers):
                return candidate
    return None
def configure_proj() -> None:
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).parent)
    ).resolve()
    proj_candidates = (
        bundle_root / "pyproj" / "proj_dir" / "share" / "proj",
        bundle_root / "pyproj" / "proj_data",
        bundle_root / "pyproj" / "data",
        bundle_root / "proj",
        bundle_root / "share" / "proj",
    )
    proj_data = find_directory(
        bundle_root,
        proj_candidates,
        ("proj.db",),
    )
    if proj_data is not None:
        proj_data_string = str(proj_data)
        os.environ["PROJ_DATA"] = proj_data_string
        os.environ["PROJ_LIB"] = proj_data_string
def configure_gdal() -> None:
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).parent)
    ).resolve()
    gdal_candidates = (
        bundle_root / "rasterio" / "gdal_data",
        bundle_root / "rasterio" / "data" / "gdal",
        bundle_root / "gdal_data",
        bundle_root / "share" / "gdal",
        bundle_root / "osgeo" / "data" / "gdal",
    )
    gdal_data = find_directory(
        bundle_root,
        gdal_candidates,
        ("gdalvrt.xsd",),
    )
    if gdal_data is None:
        gdal_data = find_directory(
            bundle_root,
            gdal_candidates,
            ("header.dxf",),
        )
    if gdal_data is not None:
        os.environ["GDAL_DATA"] = str(gdal_data)
configure_proj()
configure_gdal()