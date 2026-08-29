from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _self_test_report_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "navimetry_self_test.txt"
    return Path.cwd() / "navimetry_self_test.txt"


def run_self_test() -> int:
    """Frozen-runtime smoke test; does not open the GUI or require survey files."""
    report_path = _self_test_report_path()
    try:
        from bathymetry.csv_importer import inspect_csv  # noqa: F401
        from bathymetry.klf_parser import inspect_klf  # noqa: F401
        from bathymetry.clock_sync import build_clock_model  # noqa: F401
        from bathymetry.matcher import match_csv_to_klf  # noqa: F401
        from bathymetry.quality_control import normalize_observations  # noqa: F401
        from bathymetry.processor import run_pipeline, build_local_delaunay  # noqa: F401
        from bathymetry.survey_presets import get_survey_preset  # noqa: F401
        assert get_survey_preset("AUTO").key == "AUTO"
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import scipy  # noqa: F401
        import pyproj  # noqa: F401
        import rasterio  # noqa: F401
        import laspy  # noqa: F401
        import trimesh  # noqa: F401
        import PySide6  # noqa: F401
        import matplotlib.backends.backend_agg  # noqa: F401
        import matplotlib.backends.backend_pdf  # noqa: F401

        report_path.write_text("Navimetry self-test passed\n", encoding="utf-8")
        return 0
    except Exception:
        report_path.write_text(
            "Navimetry self-test failed\n\n" + traceback.format_exc(),
            encoding="utf-8",
        )
        return 1


def main() -> None:
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Navimetry")
    app.setApplicationVersion("0.2")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    main()
