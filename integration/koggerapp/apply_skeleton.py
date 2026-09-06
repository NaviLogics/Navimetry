#!/usr/bin/env python3
"""Apply the first Navimetry rebranding/UI skeleton to the pinned KoggerApp submodule.

This intentionally does not connect the Python bathymetry engine yet.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BASELINE = "4615cc88dcb865134d973bf132034ff6f41dbb57"
NL = chr(10)

PAGE = '''import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Frame {
    id: root
    signal closeRequested()
    padding: 24

    ColumnLayout {
        anchors.fill: parent
        spacing: 14

        Label {
            text: qsTr("Navimetry Processing")
            font.pixelSize: 24
            font.bold: true
            Layout.fillWidth: true
        }

        Label {
            text: qsTr("Bathymetry integration skeleton")
            font.pixelSize: 16
            Layout.fillWidth: true
        }

        Label {
            text: qsTr("The Navimetry bathymetric processing and Survey-aware QC engine will be connected here after the field-calibration dataset is finalized. KoggerApp acquisition and visualization remain unchanged in this integration milestone.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            Layout.fillHeight: true
            verticalAlignment: Text.AlignTop
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button {
                text: qsTr("Close")
                onClicked: root.closeRequested()
            }
        }
    }
}
'''

MENU_BLOCK = '''

    // Navimetry integration skeleton. The processing engine is intentionally not
    // connected yet; this establishes the permanent UI entry point first.
    menuBar: MenuBar {
        visible: !root.isMobilePlatform

        Menu {
            title: qsTr("Bathymetry")

            MenuItem {
                text: qsTr("Navimetry Processing")
                onTriggered: navimetryProcessingPopup.open()
            }
        }
    }

    Popup {
        id: navimetryProcessingPopup
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(680, Math.max(360, root.width - 80))
        height: Math.min(440, Math.max(280, root.height - 80))
        modal: true
        focus: true
        padding: 0
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        NavimetryProcessingPage {
            anchors.fill: parent
            onCloseRequested: navimetryProcessingPopup.close()
        }
    }
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"Expected text not found in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    here = Path(__file__).resolve().parent
    repo = here.parents[1]
    upstream = repo / "upstream" / "KoggerApp"
    if not (upstream / "CMakeLists.txt").is_file():
        raise SystemExit("KoggerApp submodule is missing. Run: git submodule update --init --recursive")

    try:
        head = subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != BASELINE:
            print(f"warning: expected KoggerApp {BASELINE}, found {head}")
    except Exception:
        print("warning: could not verify KoggerApp baseline commit")

    # Keep the internal CMake target name KoggerApp for now because many upstream
    # subdirectories append sources/libraries to that target. Rebrand the project
    # and produced binary without destabilizing upstream build wiring.
    cmake = upstream / "CMakeLists.txt"
    replace_once(cmake,
                 "project(KoggerApp VERSION ${KOGGER_VERSION} LANGUAGES CXX)",
                 "project(Navimetry VERSION ${KOGGER_VERSION} LANGUAGES CXX)")
    replace_once(cmake,
                 "target_compile_features(KoggerApp PRIVATE cxx_std_23)",
                 'set_target_properties(KoggerApp PROPERTIES OUTPUT_NAME "Navimetry")' + NL + NL + 'target_compile_features(KoggerApp PRIVATE cxx_std_23)')

    main_cpp = upstream / "src" / "main.cpp"
    replace_once(main_cpp,
                 'QCoreApplication::setApplicationName("KoggerApp");',
                 'QCoreApplication::setApplicationName("Navimetry");')

    app_cmake = upstream / "qml" / "app" / "CMakeLists.txt"
    text = app_cmake.read_text(encoding="utf-8")
    if "NavimetryProcessingPage.qml" not in text:
        marker = "        MainWindow.qml" + NL
        if marker not in text:
            raise RuntimeError("MainWindow.qml marker not found in qml/app/CMakeLists.txt")
        app_cmake.write_text(text.replace(marker, marker + "        NavimetryProcessingPage.qml" + NL, 1), encoding="utf-8")

    qml = upstream / "qml" / "app" / "MainWindow.qml"
    text = qml.read_text(encoding="utf-8")
    text = text.replace('core.fileTitle + " — KoggerApp, KOGGER") : qsTr("KoggerApp, KOGGER")',
                        'core.fileTitle + " — Navimetry") : qsTr("Navimetry")')
    text = text.replace('core.fileTitle + " — KoggerApp, KOGGER" : qsTr("KoggerApp, KOGGER"))',
                        'core.fileTitle + " — Navimetry" : qsTr("Navimetry"))')
    if "id: navimetryProcessingPopup" not in text:
        marker = "    onActiveChanged: if (active) root.lastActiveWindow = root" + NL
        if marker not in text:
            raise RuntimeError("MainWindow insertion marker not found")
        text = text.replace(marker, marker + MENU_BLOCK, 1)
    qml.write_text(text, encoding="utf-8")

    (upstream / "qml" / "app" / "NavimetryProcessingPage.qml").write_text(PAGE, encoding="utf-8")
    print("Navimetry integration skeleton applied.")
    print("Visible changes: Navimetry title/binary + Bathymetry > Navimetry Processing placeholder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
