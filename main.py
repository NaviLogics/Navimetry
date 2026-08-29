from __future__ import annotations
import sys

def run_self_test() -> int:
    """Frozen-runtime smoke test; does not open the GUI or require survey files."""
    try:
        from bathymetry.csv_importer import inspect_csv  # noqa: F401
        from bathymetry.klf_parser import inspect_klf  # noqa: F401
        from bathymetry.clock_sync import build_clock_model  # noqa: F401
        from bathymetry.matcher import match_csv_to_klf  # noqa: F401
        from bathymetry.quality_control import normalize_observations  # noqa: F401
        from bathymetry.processor import run_pipeline  # noqa: F401
        import numpy, pandas, scipy, pyproj, rasterio, laspy, trimesh, PySide6  # noqa: F401
    except Exception as error:
        print(f"Navimetry self-test failed: {error}", file=sys.stderr)
        return 1
    print("Navimetry self-test passed")
    return 0

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
