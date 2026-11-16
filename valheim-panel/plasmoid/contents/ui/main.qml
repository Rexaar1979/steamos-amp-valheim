/*
 * Valheim Server panel for a SteamOS desktop (Plasma 6).
 *
 * Shows what ~/valheim-status/check.sh found on its last run (every 30 s):
 * server state, who has AMP or the mod manager open over Tailscale (by their
 * AMP / mod manager username), and who is in the game, split into admins and
 * players. The buttons call ~/valheim-status/control.sh. This file only reads
 * and displays; all the checking lives in the shell/Python scripts next to
 * status.json.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    property var status: null          // parsed ~/valheim-status/status.json
    property string busy: ""           // "start" | "stop" | "restart" while control.sh runs
    property string confirmFor: ""     // "stop" | "restart" while asking "Are you sure?"
    property string readError: ""

    readonly property string dir: "$HOME/valheim-status"
    readonly property string readCmd: "cat " + dir + "/status.json; echo; echo ===BUSY===; cat " + dir + "/busy 2>/dev/null; true"

    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.DefaultBackground | PlasmaCore.Types.ConfigurableBackground
    toolTipMainText: "Valheim Server"
    toolTipSubText: status ? stateText() + " · " + (status.online || 0) + " online" : ""

    // ---------- reading status.json and running buttons ----------
    P5Support.DataSource {
        id: shell
        engine: "executable"
        connectedSources: []
        onNewData: function (source, data) {
            if (source === root.readCmd) {
                root.parse(data["stdout"] || "")
            }
            disconnectSource(source)
        }
    }

    function run(cmd) { shell.connectSource(cmd) }
    function refresh() { run(readCmd) }

    function parse(out) {
        const parts = out.split("===BUSY===")
        try {
            root.status = JSON.parse(parts[0].trim())
            root.readError = ""
        } catch (e) {
            root.readError = parts[0].trim() === "" ? "Waiting for the first check..." : "Couldn't read the status file."
        }
        root.busy = (parts[1] || "").trim()
        // Drop an open "Are you sure?" once it no longer applies.
        if (root.confirmFor !== "" && (root.busy !== "" || (root.status && root.status.server === "stopped")))
            root.confirmFor = ""
    }

    function act(what) {
        root.confirmFor = ""
        if (what === "open-amp" || what === "open-mods") {
            run(dir + "/control.sh " + what)
            return
        }
        root.busy = what                       // show it at once; control.sh writes the busy file too
        run("setsid -f " + dir + "/control.sh " + what + " >/dev/null 2>&1")
    }

    Timer {
        interval: 3000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    // ---------- display helpers ----------
    function serverState() {
        if (busy === "start") return "starting"
        if (busy === "stop") return "stopping"
        if (busy === "restart") return "restarting"
        return status ? status.server : "unknown"
    }
    function stateText() {
        return ({ running: "Running", stopped: "Stopped", starting: "Starting...",
                  stopping: "Stopping...", restarting: "Restarting...", unknown: "Unknown" })[serverState()]
    }
    function stateColor() {
        const s = serverState()
        if (s === "running") return Kirigami.Theme.positiveTextColor
        if (s === "stopped") return Kirigami.Theme.negativeTextColor
        if (s === "unknown") return Kirigami.Theme.disabledTextColor
        return Kirigami.Theme.neutralTextColor
    }
    function remoteRows() {
        const r = status ? status.remote : null
        if (!r || !r.known) return [{ on: false, text: "Remote sign-ins: unknown", note: "" }]
        const rows = (r.users || []).map(u => ({ on: true, text: u.name + " is connected", note: "(" + u.where + ")" }))
        for (let i = 0; i < (r.someone || 0); i++)
            rows.push({ on: true, text: "Someone is connected", note: "(not signed in yet)" })
        if (rows.length === 0)
            rows.push({ on: false, text: "No one connected", note: r.last ? "(last: " + r.last + ")" : "" })
        return rows
    }
    function inGame() { return status ? (status.admins || []).length + (status.players || []).length : 0 }

    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 15
        Layout.preferredWidth: Kirigami.Units.gridUnit * 17
        Layout.preferredHeight: body.implicitHeight + Kirigami.Units.largeSpacing * 2
        Layout.minimumHeight: Layout.preferredHeight

        ColumnLayout {
            id: body
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.smallSpacing

            // Title row: Valheim icon, name, state
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                Kirigami.Icon {
                    source: "applications-games"          // theme icon (no game logo is bundled)
                    Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                    Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                }
                PlasmaComponents.Label {
                    text: "Valheim Server"
                    font.weight: Font.DemiBold
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                Rectangle {
                    implicitWidth: Kirigami.Units.smallSpacing * 2; implicitHeight: implicitWidth; radius: implicitWidth / 2
                    color: root.stateColor()
                }
                PlasmaComponents.Label { text: root.stateText(); color: root.stateColor() }
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                font: Kirigami.Theme.smallFont
                opacity: 0.7
                elide: Text.ElideRight
                text: {
                    if (root.readError) return root.readError
                    const world = (root.status && root.status.world) ? root.status.world + " · " : ""
                    if (root.serverState() === "running")
                        return world + root.status.online + " of " + (root.status.max || 10) + " online"
                    if (root.serverState() === "stopped") return world + "not running"
                    return world + "please wait"
                }
            }

            // Who is signed in to AMP / the mod manager over Tailscale, by username.
            // One row per person; one grey row when nobody is.
            Repeater {
                model: root.remoteRows()
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    Rectangle {
                        implicitWidth: Kirigami.Units.smallSpacing * 2; implicitHeight: implicitWidth; radius: implicitWidth / 2
                        color: modelData.on ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
                    }
                    PlasmaComponents.Label {
                        text: modelData.text
                        elide: Text.ElideRight
                        Layout.maximumWidth: Kirigami.Units.gridUnit * 10
                    }
                    PlasmaComponents.Label {
                        text: modelData.note
                        font: Kirigami.Theme.smallFont
                        opacity: 0.6
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
            }

            // Admins and players, only while the server runs
            Repeater {
                model: root.serverState() === "running" && root.status
                       ? [{ title: "Admins online", list: root.status.admins || [] },
                          { title: "Players online", list: root.status.players || [] }]
                       : []
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.topMargin: Kirigami.Units.smallSpacing
                    spacing: 2
                    Kirigami.Separator { Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        PlasmaComponents.Label {
                            text: modelData.title.toUpperCase()
                            font: Kirigami.Theme.smallFont
                            opacity: 0.7
                            Layout.fillWidth: true
                        }
                        PlasmaComponents.Label {
                            text: modelData.list.length
                            font: Kirigami.Theme.smallFont
                            opacity: 0.7
                        }
                    }
                    PlasmaComponents.Label {
                        visible: modelData.list.length === 0
                        text: "none"
                        font.italic: true
                        opacity: 0.6
                    }
                    Repeater {
                        model: modelData.list
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing
                            PlasmaComponents.Label {
                                text: modelData.loading ? "(still loading in)" : modelData.character
                                font.italic: modelData.loading
                                opacity: modelData.loading ? 0.6 : 1
                                elide: Text.ElideRight
                                Layout.maximumWidth: Kirigami.Units.gridUnit * 10
                            }
                            PlasmaComponents.Label {
                                visible: modelData.steam !== ""
                                text: "(" + modelData.steam + ")"
                                font: Kirigami.Theme.smallFont
                                opacity: 0.6
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }

            // "Are you sure?" for stop and restart
            ColumnLayout {
                visible: root.confirmFor !== ""
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
                spacing: Kirigami.Units.smallSpacing
                Kirigami.Separator { Layout.fillWidth: true }
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: {
                        const n = root.inGame()
                        const who = n === 0 ? "Nobody is on right now." :
                                    "The " + n + " player" + (n > 1 ? "s" : "") + " in the game will be disconnected."
                        return root.confirmFor === "stop" ? "Stop the server? " + who
                                                          : "Restart the server? " + who + " Takes about a minute."
                    }
                }
                GridLayout {
                    columns: 2
                    Layout.fillWidth: true
                    columnSpacing: Kirigami.Units.smallSpacing
                    PlasmaComponents.Button {
                        Layout.fillWidth: true
                        text: root.confirmFor === "stop" ? "Stop" : "Restart"
                        icon.name: root.confirmFor === "stop" ? "media-playback-stop" : "view-refresh"
                        onClicked: root.act(root.confirmFor)
                    }
                    PlasmaComponents.Button {
                        Layout.fillWidth: true
                        text: "Cancel"
                        onClicked: root.confirmFor = ""
                    }
                }
            }

            // Buttons
            GridLayout {
                visible: root.confirmFor === ""
                columns: 2
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
                columnSpacing: Kirigami.Units.smallSpacing
                rowSpacing: Kirigami.Units.smallSpacing

                PlasmaComponents.Button {
                    Layout.fillWidth: true
                    text: "Open AMP"
                    icon.name: "applications-internet"     // theme icon (no AMP logo is bundled)
                    onClicked: root.act("open-amp")
                }
                PlasmaComponents.Button {
                    id: modsButton
                    Layout.fillWidth: true
                    onClicked: root.act("open-mods")
                    // Own content so the puzzle icon takes the theme's text colour.
                    contentItem: RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Item { Layout.fillWidth: true }
                        Kirigami.Icon {
                            source: Qt.resolvedUrl("../images/mods.svg")
                            isMask: true
                            color: Kirigami.Theme.textColor
                            Layout.preferredWidth: Kirigami.Units.iconSizes.small
                            Layout.preferredHeight: Kirigami.Units.iconSizes.small
                        }
                        PlasmaComponents.Label { text: "Open Mods" }
                        Item { Layout.fillWidth: true }
                    }
                }
                PlasmaComponents.Button {
                    Layout.fillWidth: true
                    text: "Restart server"
                    enabled: root.serverState() === "running"
                    onClicked: root.confirmFor = "restart"
                }
                PlasmaComponents.Button {
                    Layout.fillWidth: true
                    readonly property bool stopped: root.serverState() === "stopped"
                    text: stopped ? "Start server" : "Stop server"
                    enabled: stopped || root.serverState() === "running" || root.serverState() === "starting"
                    onClicked: stopped ? root.act("start") : (root.confirmFor = "stop")
                }
            }

            PlasmaComponents.Label {
                Layout.alignment: Qt.AlignRight
                font: Kirigami.Theme.smallFont
                opacity: 0.6
                text: root.status ? "Updated " + root.status.updated : ""
            }
        }
    }
}
