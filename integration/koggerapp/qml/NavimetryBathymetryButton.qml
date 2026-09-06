import QtQuick 2.15
import QtQuick.Controls 2.15

Button {
    id: root
    text: qsTr("Bathymetry")
    checkable: true
    property string targetPage: "navimetryProcessing"
    ToolTip.visible: hovered
    ToolTip.text: qsTr("Open Navimetry Processing")
}
