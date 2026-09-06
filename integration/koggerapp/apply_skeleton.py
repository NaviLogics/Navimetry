from __future__ import annotations

import argparse
import shutil
from pathlib import Path

UPSTREAM_COMMIT = "4615cc88dcb865134d973bf132034ff6f41dbb57"


def replace_text(path: Path, replacements: list[tuple[str, str]]) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def patch_cmake(root: Path) -> bool:
    path = root / "CMakeLists.txt"
    return replace_text(
        path,
        [
            ("project(KoggerApp VERSION ${KOGGER_VERSION} LANGUAGES CXX)",
             "project(Navimetry VERSION ${KOGGER_VERSION} LANGUAGES CXX)"),
            ("qt_add_executable(KoggerApp", "qt_add_executable(Navimetry"),
            ("target_compile_features(KoggerApp", "target_compile_features(Navimetry"),
            ("target_include_directories(KoggerApp", "target_include_directories(Navimetry"),
            ("target_compile_definitions(KoggerApp", "target_compile_definitions(Navimetry"),
            ("target_sources(KoggerApp", "target_sources(Navimetry"),
            ("target_link_libraries(KoggerApp", "target_link_libraries(Navimetry"),
            ("qt_add_resources(KoggerApp", "qt_add_resources(Navimetry"),
            ("qt_add_translations(KoggerApp", "qt_add_translations(Navimetry"),
            ("set_target_properties(KoggerApp", "set_target_properties(Navimetry"),
            ("add_android_openssl_libraries(KoggerApp)", "add_android_openssl_libraries(Navimetry)"),
        ],
    )


def patch_main_window(root: Path) -> bool:
    path = root / "qml" / "app" / "MainWindow.qml"
    return replace_text(
        path,
        [
            ('core.fileTitle + " — KoggerApp, KOGGER"', 'core.fileTitle + " — Navimetry"'),
            ('qsTr("KoggerApp, KOGGER")', 'qsTr("Navimetry")'),
        ],
    )


def install_qml_overlay(root: Path, overlay_root: Path) -> list[Path]:
    qml_app = root / "qml" / "app"
    qml_app.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in ("NavimetryProcessingPage.qml", "NavimetryBathymetryButton.qml"):
        source = overlay_root / "qml" / name
        target = qml_app / name
        shutil.copy2(source, target)
        installed.append(target)
    cmake_path = qml_app / "CMakeLists.txt"
    if cmake_path.exists():
        text = cmake_path.read_text(encoding="utf-8")
        changed = False
        for name in ("NavimetryProcessingPage.qml", "NavimetryBathymetryButton.qml"):
            if name not in text:
                marker = "        MainWindow.qml\n"
                if marker in text:
                    text = text.replace(marker, marker + f"        {name}\n", 1)
                    changed = True
        if changed:
            cmake_path.write_text(text, encoding="utf-8")
    return installed


def write_integration_marker(root: Path) -> None:
    marker = root / "NAVIMETRY_INTEGRATION.md"
    marker.write_text(
        "# Navimetry integration skeleton\n\n"
        f"KoggerApp upstream baseline: `{UPSTREAM_COMMIT}`.\n\n"
        "This checkout has received the first Navimetry rebranding and the placeholder "
        "Bathymetry / Navimetry Processing QML components. The processing engine is not "
        "connected yet. Preserve upstream GPLv3 notices and attribution.\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the Navimetry integration skeleton to a clean KoggerApp checkout")
    parser.add_argument("koggerapp_root", type=Path)
    args = parser.parse_args()
    root = args.koggerapp_root.resolve()
    overlay_root = Path(__file__).resolve().parent

    required = [root / "CMakeLists.txt", root / "qml" / "app" / "MainWindow.qml"]
    missing = [p for p in required if not p.exists()]
    if missing:
        raise SystemExit("Not a compatible KoggerApp checkout; missing: " + ", ".join(str(p) for p in missing))

    patch_cmake(root)
    patch_main_window(root)
    install_qml_overlay(root, overlay_root)
    write_integration_marker(root)
    print("Navimetry integration skeleton applied to", root)
    print("Next: wire NavimetryBathymetryButton into the existing navigation and load NavimetryProcessingPage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
