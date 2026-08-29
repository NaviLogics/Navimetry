# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
block_cipher = None
datas = []
binaries = []
hiddenimports = []
for package_name in ("rasterio", "pyproj", "matplotlib", "trimesh", "laspy", "scipy"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)
hiddenimports.extend([
    "bathymetry.csv_importer", "bathymetry.klf_parser", "bathymetry.clock_sync",
    "bathymetry.matcher", "bathymetry.quality_control", "bathymetry.project_store",
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "matplotlib.backends.backend_agg",
    "rasterio._base", "rasterio._env", "rasterio._io", "rasterio._warp", "rasterio._features",
    "pyproj.database", "pyproj.datadir", "scipy.spatial._qhull", "scipy.interpolate._interpnd",
])
a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=["runtime_hook.py"],
             runtime_tmpdir=None, excludes=[], noarchive=False)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
executable = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Navimetry", debug=False,
                 bootloader_ignore_signals=False, strip=False, upx=False, console=False,
                 disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
                 codesign_identity=None, entitlements_file=None)
collection = COLLECT(executable, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="Navimetry")
