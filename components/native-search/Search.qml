import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Commons

Item {
    id: root
    property var shell: null
    property var manifest: null
    property string omarchyPath: Quickshell.env("OMARCHY_PATH")
    property bool opened: false
    property bool requestedOpen: false
    property bool settingsOpen: false
    property bool manualSettings: false
    property string searchMode: "literal"
    property var draft: ({})
    readonly property bool expanded: input.text.trim().length > 0 || settingsOpen
    property bool busy: false
    property bool primed: false
    property int lastLockCode: -1
    property string lastCloseReason: ""
    property int helperExit: -1
    property string helperError: ""
    property string lastAction: ""
    property bool actionPending: false
    property bool helperReady: false
    property bool helperStopping: false
    readonly property bool selectedActionable: {
        const row = rows.find(r => r.id === selectedId)
        return !!row && !!row.actionable
    }
    property var rows: []
    property var roots: []
    property var settings: ({})
    property int serial: 0
    property int searchId: -1
    property int minimumResponseId: 0
    property string selectedId: ""
    property int previewId: -1
    property string previewKind: "none"
    property string previewSource: ""
    property string previewText: ""
    property string previewLabel: ""
    readonly property var selectedRow: rows.find(r => r.id === selectedId)
    readonly property bool previewable: !!selectedRow && selectedRow.kind !== "folder" && /\.(pdf|docx|odt|png|jpe?g|webp|gif|bmp|tiff?|txt|md|json|ya?ml|toml|ini|cfg|log|csv|py|js|ts|qml|lua|sh|xml|html|css)$/i.test(selectedRow.name)
    onSelectedIdChanged: {
        previewDelay.stop()
        previewId = -1
        previewKind = "none"
        previewSource = ""
        previewText = ""
        previewLabel = "Loading preview…"
        // Let selectedRow/previewable bindings settle after replacing the results.
        Qt.callLater(() => { if (opened && previewable) previewDelay.restart() })
    }
    property string message: "Type a filename or path"
    property string scope: "Loading root status…"
    property string kind: "all"
    property string rootId: ""
    property int budget: 100
    property bool partial: false
    property var targetScreen: null

    component ActionButton: Button {
        id: control
        implicitHeight: 32
        padding: 8
        activeFocusOnTab: true
        Keys.onReturnPressed: event => { if (control.enabled) { control.clicked(); event.accepted = true } }
        Keys.onEnterPressed: event => { if (control.enabled) { control.clicked(); event.accepted = true } }
        background: Rectangle {
            radius: 4
            color: control.down || control.highlighted ? Color.menu.selectedBackground : control.hovered ? Color.popups.background : "transparent"
            border.color: control.activeFocus || control.highlighted ? Color.accent : Color.menu.border
            border.width: 1
            opacity: control.enabled ? 1 : .4
        }
        contentItem: Text {
            text: control.text
            textFormat: Text.PlainText
            color: Color.menu.text
            opacity: control.enabled ? 1 : .4
            font.family: Style.font.menuFamily
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component SettingLabel: Text {
        color: Color.menu.text
        font.family: Style.font.menuFamily
        font.pixelSize: 13
        wrapMode: Text.Wrap
    }
    function showSettings() {
        settingsOpen = !settingsOpen
        manualSettings = false
        if (settingsOpen) { message = "Changes are validated before saving. Back discards unsaved edits."; send("settings-read") }
        else input.forceActiveFocus()
    }
    function editSetting(key, value) {
        const next = JSON.parse(JSON.stringify(draft))
        next[key] = value
        draft = next
    }
    function saveSettings() {
        try { send("settings-write", {settings: manualSettings ? JSON.parse(settingsText.text) : draft}) }
        catch (e) { message = "Invalid JSON; settings not saved" }
    }
    function diagnostic() {
        return JSON.stringify({opened: opened, requested: requestedOpen, lockCode: lastLockCode, closedBy: lastCloseReason, focused: input.activeFocus, busy: busy,
            count: rows.length, selected: rows.findIndex(r => r.id === selectedId),
            resultRoots: rows.map(r => r.root).filter((id, index, all) => all.indexOf(id) === index),
            partial: partial, helper: backend.running, helperExit: helperExit, helperError: helperError, action: lastAction, actionPending: actionPending, settings: settingsOpen,
            expanded: expanded, searchMode: searchMode, manualSettings: manualSettings, settingsButtonY: settingsButton.mapToItem(card, 0, 0).y + card.y, preview: {visible: previewCard.visible, kind: previewKind, label: previewLabel, x: previewCard.x, y: previewCard.y, width: previewCard.width}, viewport: {width: panel.width, height: panel.height},
            card: {width: card.width, height: card.height, x: card.x, y: card.y},
            screen: panel.screen ? panel.screen.name : ""})
    }
    function query(text) {
        input.text = String(text).slice(0, 512)
        input.forceActiveFocus()
    }
    function filters(payload) {
        const next = JSON.parse(payload)
        if (next.kind) kind = next.kind
        if (next.mode) searchMode = next.mode
        rootId = next.root || ""
        extension.text = next.extension || ""
        changed()
    }

    function send(operation, args) {
        if (!helperReady) return -1
        const id = ++serial
        backend.write(JSON.stringify({version: 1, request_id: id, operation: operation, arguments: args || {}}) + "\n")
        return id
    }
    function open(payload) {
        requestedOpen = true
        lockProbe.running = true
    }
    function close() {
        requestedOpen = false
        opened = false
        debounce.stop()
        rows = []
        selectedId = ""
        searchId = -1
        busy = false
        partial = false
        input.text = ""
        settingsOpen = false
        actionPending = false
        if (helperReady) send("cancel")
        helperStop.restart()
    }
    function dismiss(reason) {
        lastCloseReason = reason || "explicit"
        close()
        if (shell && shell.hide) shell.hide("nda.native-search")
    }
    function begin() {
        minimumResponseId = serial + 1
        helperStop.stop()
        targetScreen = Quickshell.screens.find(s => Hyprland.focusedMonitor && s.name === Hyprland.focusedMonitor.name) || Quickshell.screens[0]
        opened = true
        primed = false
        if (!helperStopping) backend.running = true
        message = "Type a filename or path"
        if (helperReady) initializeHelper()
        focusPrime.restart()
        Qt.callLater(() => input.forceActiveFocus())
    }
    function initializeHelper() {
        send("settings-read")
        send("status")
        if (input.text.trim()) changed()
    }
    function changed() {
        if (!opened || input.inputMethodComposing) return
        busy = true
        selectedId = ""
        searchId = -1
        if (backend.running) send("cancel")
        debounce.restart()
    }
    function search() {
        if (!helperReady) { message = "Starting search helper…"; return }
        searchId = send("search", {query: input.text, mode: searchMode, roots: rootId ? [rootId] : [], kind: kind,
                                   extension: extension.text, limit: budget})
    }
    function move(delta) {
        if (busy || !rows.length) return
        let index = rows.findIndex(r => r.id === selectedId)
        index = Math.max(0, Math.min(rows.length - 1, index + delta))
        selectedId = rows[index].id
        results.positionViewAtIndex(index, ListView.Contain)
    }
    function activate(operation) {
        if (busy || settingsOpen || actionPending || !selectedId) return
        const selected = rows.find(r => r.id === selectedId)
        if (selected && selected.actionable) { lastAction = operation; actionPending = true; send("action", {id: selectedId, action: operation}) }
        else message = "File unavailable or unreadable; refresh the index"
    }
    function receive(line) {
        let event
        try { event = JSON.parse(line) } catch (e) { message = "Invalid helper response"; busy = false; return }
        if (!opened) return
        if (typeof event.request_id === "number" && event.request_id < minimumResponseId) return
        if (event.operation === "action") {
            actionPending = false
            // Some clipboard backends briefly create a surface. Re-prime the
            // layer's compositor focus after ownership settles, not just Qt focus.
            if (event.message && event.message.startsWith("Copied")) {
                primed = false
                focusPrime.restart()
                Qt.callLater(() => input.forceActiveFocus())
            }
        }
        if (event.operation === "search") {
            if (event.request_id !== searchId) return
            busy = false
            if (event.type === "error") { rows = []; message = event.error; return }
            rows = event.results || []
            partial = !!event.partial
            selectedId = rows.length ? rows[0].id : ""
            message = !input.text.trim() ? "Type a filename or path" : rows.length ?
                (partial ? "Partial results — narrow the query" : rows.length + " shown · " + event.duration_ms + " ms") :
                "No matches in the selected indexed locations"
            if (event.errors && event.errors.length) message = (rows.length ? rows.length + " shown · Partial results" : "Search unavailable") + " · " + event.errors.join("; ")
        } else if (event.operation === "preview") {
            if (event.request_id !== previewId) return
            previewKind = event.type === "error" ? "text" : event.kind
            previewSource = event.source || ""
            previewText = event.type === "error" ? "Preview unavailable: " + event.error : event.text || ""
            previewLabel = event.label || (event.type === "error" ? "Preview unavailable" : "No preview")
        } else if (event.type === "error") message = event.error
        else if (event.operation === "status") {
            roots = event.roots
            rootFilter.currentIndex = rootId ? Math.max(0, roots.findIndex(r => r.id === rootId) + 1) : 0
            scope = roots.map(r => r.id + ": " + (!r.enabled ? "disabled" : !r.online ? "offline" : r.building ? "indexing…" :
                !r.indexed ? "Build index" : (r.stale ? "stale · " : "") + Math.floor(r.age_seconds / 60) + "m ago") +
                (r.error ? " · refresh failed" : "") + (r.deferred ? " · battery deferred" : "")).join("   |   ")
        } else if (event.operation === "settings-read" || event.operation === "settings-write") {
            settings = event.settings
            draft = JSON.parse(JSON.stringify(settings))
            settingsText.text = JSON.stringify(settings, null, 2)
            if (!settingsOpen || event.operation === "settings-write") {
                searchMode = settings.search_mode || "literal"
                kind = settings.kind; budget = settings.result_budget; extension.text = settings.extension
            }
            if (event.operation === "settings-write") { message = "Settings saved; refresh indexes if scope/exclusions changed"; send("status"); changed() }
        } else if (event.message) {
            message = event.message
            if (event.operation === "action" && (event.message.startsWith("Open requested") || event.message.startsWith("Opened") || event.message.startsWith("Revealed"))) dismiss()
            else send("status")
        }
    }
    Process {
        id: backend
        command: ["/usr/bin/python3", "-B", Qt.resolvedUrl("../helper.py").toString().replace("file://", ""), "serve"]
        stdinEnabled: true
        onStarted: { root.helperReady = true; if (root.opened) root.initializeHelper() }
        stdout: SplitParser { onRead: line => root.receive(line) }
        stderr: SplitParser { onRead: line => { root.helperError = line.slice(0, 512) } }
        onExited: (code, status) => {
            root.helperExit = code
            root.helperReady = false
            const expectedStop = root.helperStopping
            root.helperStopping = false
            if (root.opened && expectedStop) Qt.callLater(() => { backend.running = true })
            else if (root.opened) { root.busy = false; root.message = "Helper exited (" + code + "); close and reopen to retry" }
        }
    }
    Process {
        id: lockProbe
        command: ["omarchy-hyprland-session-locked"]
        onExited: (code, status) => {
            root.lastLockCode = code
            if (code === 1 && root.requestedOpen && !root.opened) root.begin()
            else if (code !== 1) root.dismiss("lock-or-undetermined")
        }
    }
    Timer {
        id: helperStop
        interval: 300
        onTriggered: {
            if (!root.opened && backend.running) {
                root.helperStopping = true
                root.helperReady = false
                backend.running = false
            }
        }
    }
    Timer { id: focusPrime; interval: 150; onTriggered: { root.primed = true; input.forceActiveFocus() } }
    Timer { id: debounce; interval: 80; onTriggered: root.search() }
    Timer { id: previewDelay; interval: 140; onTriggered: {
        if (root.opened && root.previewable && !root.busy)
            root.previewId = root.send("preview", {id: root.selectedId})
    } }
    Timer { interval: 2000; repeat: true; running: root.opened; onTriggered: { root.send("status"); if (!lockProbe.running) lockProbe.running = true } }
    Component.onDestruction: { backend.running = false }

    PanelWindow {
        id: panel
        screen: root.targetScreen
        visible: root.opened
        anchors { top: true; bottom: true; left: true; right: true }
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "nda-native-search"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.opened ? (root.primed ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.Exclusive) : WlrKeyboardFocus.None
        MouseArea { anchors.fill: parent; onClicked: root.dismiss("outside-click") }
        Rectangle {
            id: previewCard
            x: card.x + card.width + 12
            y: card.y
            width: Math.max(0, Math.min(550, panel.width - x - 12))
            height: card.height
            visible: root.expanded && !root.settingsOpen && root.previewable && width >= 220
            color: Color.menu.background
            border.color: Color.menu.border
            border.width: 2
            radius: Style.cornerRadius
            MouseArea { anchors.fill: parent; onClicked: {} }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: root.selectedRow ? root.selectedRow.name : ""
                    textFormat: Text.PlainText
                    elide: Text.ElideMiddle
                    color: Color.menu.text
                    font.family: Style.font.menuFamily
                }
                Text {
                    Layout.fillWidth: true
                    text: root.previewLabel
                    textFormat: Text.PlainText
                    color: Color.muted
                    font.pixelSize: 12
                }
                Image {
                    visible: root.previewKind === "image"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    source: root.previewSource
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                }
                ScrollView {
                    visible: root.previewKind !== "image"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        readOnly: true
                        text: root.previewText
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.Wrap
                        selectByMouse: true
                        color: Color.menu.text
                        font.family: Style.font.menuFamily
                        font.pixelSize: 14
                        background: null
                    }
                }
            }
        }
        Rectangle {
            id: card
            width: Math.min(760, panel.width - 24)
            height: root.expanded ? Math.min(640, panel.height - y - 12) : searchRow.implicitHeight + 32
            anchors.horizontalCenter: parent.horizontalCenter
            y: (panel.height - searchRow.implicitHeight - 32) / 2
            color: Color.menu.background
            border.color: Color.menu.border
            border.width: 2
            radius: Style.cornerRadius
            MouseArea { anchors.fill: parent; onClicked: {} }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10
                Shortcut {
                    sequence: "Ctrl+,"
                    context: Qt.WindowShortcut
                    enabled: root.opened
                    onActivated: root.showSettings()
                }
                Shortcut {
                    sequence: "Ctrl+Shift+U"
                    context: Qt.WindowShortcut
                    enabled: root.opened && !root.settingsOpen && !input.inputMethodComposing
                    onActivated: root.activate("copy-uri")
                }
                Shortcut {
                    sequence: "Ctrl+Shift+C"
                    context: Qt.WindowShortcut
                    enabled: root.opened && !root.settingsOpen && !input.inputMethodComposing
                    onActivated: root.activate("copy-path")
                }
                Shortcut {
                    sequence: "Ctrl+Alt+U"
                    context: Qt.ApplicationShortcut
                    enabled: root.opened && !root.settingsOpen && !input.inputMethodComposing
                    onActivated: root.activate("copy-uri")
                }
                Keys.priority: Keys.BeforeItem
                Keys.onPressed: event => {
                    if (event.key === Qt.Key_Escape) { if (root.settingsOpen) root.settingsOpen = false; else root.dismiss("escape"); event.accepted = true }
                    else if (!root.settingsOpen && !input.inputMethodComposing) {
                        if ((input.activeFocus || results.activeFocus) && (event.key === Qt.Key_Down || event.key === Qt.Key_Up || event.key === Qt.Key_PageDown || event.key === Qt.Key_PageUp)) {
                            root.move(event.key === Qt.Key_Down ? 1 : event.key === Qt.Key_Up ? -1 : event.key === Qt.Key_PageDown ? 8 : -8); event.accepted = true
                        } else if ((input.activeFocus || results.activeFocus) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                            root.activate(event.modifiers & Qt.ControlModifier ? "reveal" : "open"); event.accepted = true
                        } else if ((event.modifiers & Qt.ControlModifier) && (event.modifiers & Qt.ShiftModifier) && (event.key === Qt.Key_C || event.key === Qt.Key_U)) {
                            root.activate(event.key === Qt.Key_C ? "copy-path" : "copy-uri"); event.accepted = true
                        } else if ((event.modifiers & Qt.ControlModifier) && (event.modifiers & Qt.AltModifier) && event.key === Qt.Key_U) {
                            root.activate("copy-uri"); event.accepted = true
                        }
                    }
                }
                RowLayout {
                    id: searchRow
                    Layout.fillWidth: true
                    Layout.minimumHeight: 40
                    Layout.maximumHeight: 40
                    implicitHeight: 40
                    TextField {
                        id: input
                        Layout.fillWidth: true
                        placeholderText: "Search files…"
                        color: Color.menu.text
                        placeholderTextColor: Color.muted
                        font.family: Style.font.menuFamily
                        font.pixelSize: 20
                        selectByMouse: true
                        background: Rectangle { color: "transparent"; border.color: input.activeFocus ? Color.accent : Color.menu.border; radius: 4 }
                        onTextChanged: { root.budget = root.settings.result_budget || 100; root.changed() }
                        onInputMethodComposingChanged: if (!inputMethodComposing) root.changed()
                    }
                    ActionButton { visible: root.expanded; text: "×"; onClicked: root.dismiss() }
                }
                RowLayout {
                    visible: root.expanded && !root.settingsOpen
                    ActionButton { text: root.searchMode === "fuzzy" ? "Fuzzy" : "Exact"; highlighted: root.searchMode === "fuzzy"; onClicked: { root.searchMode = root.searchMode === "fuzzy" ? "literal" : "fuzzy"; root.changed() } }
                    Repeater {
                        model: ["all", "files", "folders"]
                        ActionButton { required property string modelData; text: modelData; highlighted: root.kind === modelData; onClicked: { root.kind = modelData; root.changed() } }
                    }
                    ComboBox {
                        id: rootFilter
                        Layout.fillWidth: true
                        model: ["All roots"].concat(root.roots.map(r => r.id))
                        palette.button: Color.menu.background
                        palette.buttonText: Color.menu.text
                        palette.base: Color.menu.background
                        palette.text: Color.menu.text
                        palette.highlight: Color.accent
                        font.family: Style.font.menuFamily
                        onActivated: { root.rootId = currentIndex ? model[currentIndex] : ""; root.changed() }
                    }
                    TextField {
                        id: extension
                        Layout.preferredWidth: 110
                        placeholderText: "Extension"
                        selectByMouse: true
                        color: Color.menu.text
                        placeholderTextColor: Color.muted
                        background: Rectangle { color: "transparent"; border.color: extension.activeFocus ? Color.accent : Color.menu.border; radius: 4 }
                        onTextChanged: root.changed()
                    }
                }
                ScrollView {
                    visible: root.settingsOpen && root.manualSettings
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    TextArea {
                        id: settingsText
                        color: Color.menu.text
                        font.family: Style.font.menuFamily
                        wrapMode: TextEdit.Wrap
                        textFormat: TextEdit.PlainText
                        selectByMouse: true
                        background: Rectangle { color: Color.menu.background; border.color: Color.menu.border; radius: 4 }
                    }
                }
                ScrollView {
                    visible: root.settingsOpen && !root.manualSettings
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 12
                        SettingLabel { text: "Search preferences"; font.bold: true }
                        RowLayout {
                            SettingLabel { text: "Default matching"; Layout.preferredWidth: 180 }
                            ComboBox { Layout.preferredWidth: 260; model: ["Exact / literal", "Fuzzy"]; currentIndex: root.draft.search_mode === "fuzzy" ? 1 : 0; onActivated: root.editSetting("search_mode", currentIndex ? "fuzzy" : "literal") }
                        }
                        RowLayout {
                            SettingLabel { text: "Fuzzy tolerance"; Layout.preferredWidth: 180 }
                            ComboBox { Layout.preferredWidth: 260; model: ["Subsequence only", "Balanced · 1 typo", "Flexible · up to 2 typos"]; currentIndex: root.draft.fuzzy_tolerance || 0; onActivated: root.editSetting("fuzzy_tolerance", currentIndex) }
                        }
                        SettingLabel { Layout.fillWidth: true; text: "Fuzzy matches skipped letters and spelling mistakes. All words must match. Short words allow fewer typos; broad queries may return partial results." }
                        Repeater {
                            model: [{key: "result_budget", label: "Results per search", min: 1, max: 500, step: 10},
                                {key: "candidate_budget", label: "Candidate limit", min: 100, max: 10000, step: 100},
                                {key: "timeout_seconds", label: "Timeout (seconds)", min: 1, max: 10, step: 1},
                                {key: "refresh_seconds", label: "Refresh interval (minutes)", min: 5, max: 1440, step: 5}]
                            RowLayout {
                                required property var modelData
                                SettingLabel { text: modelData.label; Layout.preferredWidth: 220 }
                                SpinBox {
                                    from: modelData.min; to: modelData.max; stepSize: modelData.step; editable: true
                                    value: Math.round((root.draft[modelData.key] || modelData.min) / (modelData.key === "refresh_seconds" ? 60 : 1))
                                    onValueModified: root.editSetting(modelData.key, value * (modelData.key === "refresh_seconds" ? 60 : 1))
                                }
                            }
                        }
                        RowLayout {
                            SettingLabel { text: "Default file type"; Layout.preferredWidth: 180 }
                            ComboBox { model: ["all", "files", "folders"]; currentIndex: Math.max(0, model.indexOf(root.draft.kind)); onActivated: root.editSetting("kind", currentText) }
                            TextField { placeholderText: "Default extension"; text: root.draft.extension || ""; maximumLength: 32; onTextEdited: root.editSetting("extension", text) }
                        }
                        SettingLabel { text: "Indexed locations"; font.bold: true }
                        Repeater {
                            model: root.draft.roots || []
                            CheckBox {
                                required property var modelData
                                required property int index
                                text: modelData.id + " · " + modelData.path
                                checked: modelData.enabled
                                onToggled: {
                                    const locations = JSON.parse(JSON.stringify(root.draft.roots))
                                    locations[index].enabled = checked
                                    root.editSetting("roots", locations)
                                }
                            }
                        }
                        SettingLabel { text: "Exclusions · one entry per line; refresh indexes after changes"; font.bold: true }
                        Repeater {
                            model: [{key: "prunenames", label: "Directory names"}, {key: "prunepaths", label: "Absolute paths"}, {key: "prunefs", label: "Filesystem types"}]
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                SettingLabel { text: modelData.label }
                                TextArea {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 90
                                    text: (root.draft[modelData.key] || []).join("\n")
                                    color: Color.menu.text
                                    wrapMode: TextEdit.Wrap
                                    onActiveFocusChanged: if (!activeFocus && root.settingsOpen && root.draft.version) root.editSetting(modelData.key, text.split("\n").map(v => v.trim()).filter(v => v.length))
                                }
                            }
                        }
                    }
                }
                ListView {
                    id: results
                    visible: root.expanded && !root.settingsOpen
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    activeFocusOnTab: true
                    model: root.rows
                    spacing: 2
                    opacity: root.busy ? .45 : 1
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: results.width
                        height: 48
                        radius: 4
                        color: root.selectedId === modelData.id ? Color.menu.selectedBackground : "transparent"
                        border.width: root.selectedId === modelData.id ? 1 : 0
                        border.color: Color.accent
                        Column {
                            anchors { left: parent.left; right: parent.right; margins: 10; verticalCenter: parent.verticalCenter }
                            Text { width: parent.width; textFormat: Text.PlainText; text: (modelData.kind === "folder" ? "▸ " : modelData.kind === "unknown" ? "? " : "· ") + modelData.name; color: root.selectedId === modelData.id ? Color.menu.selectedText : Color.menu.text; elide: Text.ElideRight; font.family: Style.font.menuFamily; font.pixelSize: 16 }
                            Text { width: parent.width; textFormat: Text.PlainText; text: modelData.root + " · " + modelData.parent; color: root.selectedId === modelData.id ? Color.menu.selectedText : Color.muted; elide: Text.ElideMiddle; font.family: Style.font.menuFamily; font.pixelSize: 12 }
                        }
                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            enabled: !root.busy
                            hoverEnabled: true
                            onClicked: root.selectedId = modelData.id
                            onDoubleClicked: { root.selectedId = modelData.id; root.activate("open") }
                            ToolTip {
                                id: rowTip
                                visible: rowMouse.containsMouse
                                text: modelData.parent + "/" + modelData.name
                                delay: 700
                                width: Math.min(700, contentItem.implicitWidth + padding * 2)
                                contentItem: Text { text: rowTip.text; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Color.tooltip.text; font.family: Style.font.menuFamily }
                                background: Rectangle { color: Color.tooltip.background; border.color: Color.tooltip.border; radius: 4 }
                            }
                        }
                    }
                }
                Text { visible: root.expanded; Layout.fillWidth: true; textFormat: Text.PlainText; text: root.busy ? "Searching…" : root.message; color: Color.menu.text; wrapMode: Text.Wrap; font.pixelSize: 13 }
                Text { visible: root.expanded; Layout.fillWidth: true; textFormat: Text.PlainText; text: root.scope; color: Color.muted; wrapMode: Text.Wrap; font.pixelSize: 12 }
                RowLayout {
                    visible: root.expanded
                    ActionButton { id: settingsButton; text: root.settingsOpen ? "Back" : "Settings"; onClicked: root.showSettings() }
                    ActionButton { visible: root.settingsOpen; text: root.manualSettings ? "Validate and save JSON" : "Save settings"; onClicked: root.saveSettings() }
                    ActionButton { visible: root.settingsOpen; text: root.manualSettings ? "Options" : "Advanced: edit JSON"; onClicked: {
                        if (!root.manualSettings) { settingsText.text = JSON.stringify(root.draft, null, 2); root.manualSettings = true }
                        else { try { root.draft = JSON.parse(settingsText.text); root.manualSettings = false } catch (e) { root.message = "Fix invalid JSON or use Back to discard" } }
                    } }
                    ActionButton { visible: !root.settingsOpen; text: "Open"; enabled: !root.busy && !root.actionPending && root.selectedActionable; onClicked: root.activate("open") }
                    ActionButton { visible: !root.settingsOpen; text: "Reveal"; enabled: !root.busy && !root.actionPending && root.selectedActionable; onClicked: root.activate("reveal") }
                    ActionButton { visible: !root.settingsOpen; text: "Copy path"; enabled: !root.busy && !root.actionPending && root.selectedActionable; onClicked: root.activate("copy-path") }
                    ActionButton { visible: !root.settingsOpen; text: "Copy URI"; enabled: !root.busy && !root.actionPending && root.selectedActionable; onClicked: root.activate("copy-uri") }
                    ActionButton { visible: !root.settingsOpen; text: "Refresh / Build"; onClicked: root.send("refresh") }
                    ActionButton { text: "More"; visible: !root.settingsOpen && root.partial && root.rows.length > 0 && root.budget < 500; onClicked: { root.budget = 500; root.changed() } }
                }
                Text { visible: root.expanded; Layout.fillWidth: true; text: "Enter Open · Ctrl+Enter Reveal · Ctrl+Shift+C Path · Ctrl+Alt+U URI"; color: Color.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
            }
        }
    }
}
