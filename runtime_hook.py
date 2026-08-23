from __future__ import annotations
import os
import sys
from pathlib import Path
def get_bundle_root() -> Path:
    """Return the root directory of a PyInstaller one-folder bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent
def find_resource_directory(
    root: Path,
    marker_names: set[str],
) -> Path | None:
    """
    Find the first directory containing at least one required marker.
    The recursive search makes the hook tolerant of different PyInstaller
    layouts used by rasterio, pyproj and their binary wheels.
    """
    if not root.exists():
        return None
    direct_candidates = [
        root,
        root / "rasterio",
        root / "pyproj",
        root / "share",
        root / "data",
        root / "proj_data",
        root / "gdal_data",
    ]
    checked: set[Path] = set()
    for candidate in direct_candidates:
        if candidate in checked or not candidate.is_dir():
            continue
        checked.add(candidate)
        try:
            names = {
                item.name.lower()
                for item in candidate.iterdir()
            }
        except OSError:
            continue
        if names.intersection(marker_names):
            return candidate
    for current_dir, dir_names, file_names in os.walk(root):
        dir_names[:] = [
            name
            for name in dir_names
            if name not in {
                "__pycache__",
                ".git",
                "tests",
            }
        ]
        names = {
            name.lower()
            for name in file_names
        }
        if names.intersection(marker_names):
            return Path(current_dir)
    return None
def configure_geospatial_environment() -> None:
    bundle_root = get_bundle_root()
    proj_dir = find_resource_directory(
        bundle_root,
        {
            "proj.db",
            "proj.ini",
            "projjson.schema.json",
        },
    )
    gdal_dir = find_resource_directory(
        bundle_root,
        {
            "gdal_datum.csv",
            "gdaldata",
            "pcs.csv",
            "coordinate_axis.csv",
        },
    )
    if proj_dir is not None:
        os.environ["PROJ_DATA"] = str(proj_dir)
        os.environ["PROJ_LIB"] = str(proj_dir)
    if gdal_dir is not None:
        os.environ["GDAL_DATA"] = str(gdal_dir)
    # Helps rasterio locate bundled GDAL/PROJ resources on Windows.
    os.environ.setdefault(
        "RASTERIO_DATA",
        str(bundle_root / "rasterio"),
    )
configure_geospatial_environment()