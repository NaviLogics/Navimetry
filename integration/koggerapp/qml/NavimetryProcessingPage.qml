import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: root

    signal closeRequested()

    Rectangle {
        anchors.fill: parent
        color: "#20242a"
    }

    Column {
        anchors.centerIn: parent
        spacing: 18
        width: Math.min(parent.width * 0.72, 720)

        Label {
            width: parent.width
            text: qsTr("Bathymetry / Navimetry Processing")
            color: "white"
            font.pixelSize: 28
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Label {
            width: parent.width
            text: qsTr("Integration skeleton. The Navimetry bathymetric processing and Survey-aware QC engine will be connected here after the release mathematics is frozen and validated on the new field dataset.")
            color: "#d3d8df"
            font.pixelSize: 16
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Frame {
            width: parent.width

            Column {
                width: parent.width
                spacing: 8

                Label { text: qsTr("Planned modules"); font.bold: true }
                Label { text: qsTr("• Data / KLF and project source") }
                Label { text: qsTr("• Survey geometry and line corridors") }
                Label { text: qsTr("• Observation and triangle QC") }
                Label { text: qsTr("• Strict / presentation bathymetric surfaces") }
                Label { text: qsTr("• Export and processing report") }
            }
        }

        Button {
            anchors.horizontalCenter: parent.horizontalCenter
            text: qsTr("Close")
            onClicked: root.closeRequested()
        }
    }
}
