# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

import pyproj
import rasterio
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None
datas = []
binaries = []

datas += collect_data_files("rasterio")
datas += collect_data_files("pyproj")
binaries += collect_dynamic_libs("rasterio")
binaries += collect_dynamic_libs("pyproj")

# Explicitly include geospatial resource directories required at runtime.
proj_data_dir = Path(pyproj.datadir.get_data_dir())
if (proj_data_dir / "proj.db").is_file():
    datas.append((str(proj_data_dir), "pyproj/proj_dir/share/proj"))

rasterio_dir = Path(rasterio.__file__).resolve().parent
gdal_data_dir = rasterio_dir / "gdal_data"
if (gdal_data_dir / "gdalvrt.xsd").is_file():
    datas.append((str(gdal_data_dir), "rasterio/gdal_data"))

hiddenimports = collect_submodules("rasterio")
hiddenimports += collect_submodules("pyproj")
hiddenimports += [
    "bathymetry.csv_importer",
    "bathymetry.klf_parser",
    "bathymetry.clock_sync",
    "bathymetry.matcher",
    "bathymetry.quality_control",
    "bathymetry.project_store",
    "bathymetry.survey_presets",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_pdf",
    "scipy.spatial._qhull",
]

hiddenimports = [
    name for name in dict.fromkeys(hiddenimports)
    if ".tests" not in name and not name.endswith(".tests")
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook.py"],
    runtime_tmpdir=None,
    excludes=["matplotlib.tests", "scipy.tests", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
executable = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Navimetry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Navimetry",
)
