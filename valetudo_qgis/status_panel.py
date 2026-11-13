"""The Valetudo panel, kept simple.

  robot name · state badge · ⋯ (tools menu)
  battery
  one red banner, only when something needs attention
  rooms checklist (if the robot knows its rooms)
  [Clean everything / Clean N rooms]
  [Pause] [Dock]
  [Clean a zone] [Go to…]
  [Show on map]
  Camera (collapsed)

Buttons reuse the plugin's QActions, so they share their state with the
menu. The panel refreshes itself while visible: every 5 s while the robot
works, every 30 s otherwise.
"""

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsSettings
from qgis.gui import QgsCollapsibleGroupBox, QgsDockWidget

from .camera_widget import CameraWidget
from .i18n import tr
from .icons import icon

STATE_COLORS = {
    "cleaning": "#1565c0", "moving": "#1565c0", "manual_control": "#1565c0",
    "returning": "#ef6c00", "paused": "#f9a825", "docked": "#2e7d32",
    "idle": "#757575", "error": "#c62828",
}
ACTIVE_INTERVAL_MS = 5_000
IDLE_INTERVAL_MS = 30_000
ACTIVE_STATES = {"cleaning", "returning", "moving", "manual_control"}
KEY_CAMERA_OPEN = "valetudo/section_camera"

# Panel layout: action keys from the plugin (rows of 2 buttons), and the ⋯ menu.
BUTTON_ROWS = [["pause", "home"], ["zone", "goto"], ["live"]]


DOCK_COMPONENT_ORDER = ["water_tank_clean", "water_tank_dirty", "detergent", "dustbag"]
DOCK_COMPONENT_PROBLEMS = {"missing", "empty", "full"}


def _state_labels():
    return {
        "idle": tr("Idle"), "cleaning": tr("Cleaning"), "returning": tr("Returning to dock"),
        "docked": tr("Docked"), "paused": tr("Paused"), "error": tr("Error"),
        "manual_control": tr("Manual control"), "moving": tr("Moving"),
        # dock status values
        "pause": tr("Paused"), "emptying": tr("Emptying"), "drying": tr("Drying"),
        "washing": tr("Washing"),
    }


def _flag_labels():
    return {
        "segment": tr("Rooms"), "zone": tr("Zone"), "spot": tr("Spot"),
        "target": tr("Target"), "resumable": tr("Resumable"), "mapping": tr("Mapping"),
        "charging": tr("Charging"), "discharging": tr("Discharging"), "charged": tr("Charged"),
    }


def dock_component_labels():
    return {
        "water_tank_clean": tr("Fresh water"),
        "water_tank_dirty": tr("Waste water"),
        "detergent": tr("Detergent"),
        "dustbag": tr("Dust bag"),
    }


def dock_component_values():
    return {
        "ok": tr("OK"), "missing": tr("Missing"), "empty": tr("Empty"),
        "full": tr("Full"), "unknown": tr("Unknown"),
    }


def dock_component_advice():
    return {"missing": tr("Put it back in place."), "empty": tr("Refill it."),
            "full": tr("Empty it.")}


def state_label(state):
    """Translated robot state ("docked" -> "Docked" / "À la base")."""
    return _label(_state_labels(), state)


def _label(mapping, value):
    return mapping.get(value, value) if value is not None else "—"


class ValetudoPanel(QgsDockWidget):
    clientRequested = pyqtSignal()   # the panel was shown but no client exists yet

    def __init__(self, parent=None):
        super().__init__(tr("Valetudo"), parent)
        self.setObjectName("ValetudoStatusPanel")
        self.client = None
        self.capabilities = None
        self._segments = []          # [(id, name)]
        self._checked = set()
        self._robot_error = ""
        self._dock_problems = []

        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)

        self._build_ui()
        self.visibilityChanged.connect(self._on_visibility)
        self._show_disconnected()

    # -- construction ---------------------------------------------------------------

    def _build_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)

        top = QHBoxLayout()
        self.lbl_robot = QLabel("Valetudo")
        bold = self.lbl_robot.font()
        bold.setBold(True)
        self.lbl_robot.setFont(bold)
        top.addWidget(self.lbl_robot, 1)
        self.lbl_badge = QLabel()
        top.addWidget(self.lbl_badge)
        self.btn_tools = QToolButton()
        self.btn_tools.setIcon(icon("more"))
        self.btn_tools.setIconSize(QSize(20, 20))
        self.btn_tools.setAutoRaise(True)
        self.btn_tools.setToolTip(tr("Tools"))
        self.btn_tools.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        top.addWidget(self.btn_tools)
        layout.addLayout(top)

        self.bar_battery = QProgressBar()
        self.bar_battery.setRange(0, 100)
        layout.addWidget(self.bar_battery)

        self.lbl_problems = QLabel()
        self.lbl_problems.setWordWrap(True)
        self.lbl_problems.setStyleSheet(
            "background-color: #fdecea; color: #b71c1c; border-radius: 4px; padding: 6px;")
        self.lbl_problems.setVisible(False)
        layout.addWidget(self.lbl_problems)

        self.rooms = QListWidget()
        self.rooms.setMaximumHeight(130)
        self.rooms.setVisible(False)
        self.rooms.itemChanged.connect(self._on_room_toggled)
        layout.addWidget(self.rooms)

        self.btn_clean = QPushButton()
        self.btn_clean.setIcon(icon("start"))
        self.btn_clean.setMinimumHeight(36)
        self.btn_clean.setFont(bold)
        self.btn_clean.clicked.connect(self._clean)
        layout.addWidget(self.btn_clean)

        self._buttons = QGridLayout()
        self._buttons.setSpacing(4)
        layout.addLayout(self._buttons)

        self.box_camera = QgsCollapsibleGroupBox(tr("Camera"))
        self.box_camera.setSaveCollapsedState(False)
        self.box_camera.setCollapsed(not QgsSettings().value(KEY_CAMERA_OPEN, False, type=bool))
        camera_layout = QVBoxLayout(self.box_camera)
        self.camera = CameraWidget()
        camera_layout.addWidget(self.camera)
        self.camera_light = QCheckBox(tr("Camera light"))
        self.camera_light.setVisible(False)
        self.camera_light.clicked.connect(self._on_camera_light_clicked)
        camera_layout.addWidget(self.camera_light)
        self.box_camera.collapsedStateChanged.connect(self._on_camera_section)
        layout.addWidget(self.box_camera)

        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.setWidget(scroll)

    def bind_actions(self, act, tool_keys):
        """Buttons from BUTTON_ROWS, and the ⋯ menu from tool_keys (None = separator)."""
        for row, keys in enumerate(BUTTON_ROWS):
            for col, key in enumerate(keys):
                if key not in act:
                    continue
                button = QToolButton()
                button.setDefaultAction(act[key])
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                self._buttons.addWidget(button, row, col, 1, 2 if len(keys) == 1 else 1)
        menu = QMenu(self)
        for key in tool_keys:
            if key is None:
                menu.addSeparator()
            elif key in act:
                menu.addAction(act[key])
        self.btn_tools.setMenu(menu)
        self._tools_menu = menu

    # -- cleaning -------------------------------------------------------------------

    def _on_segments(self, segments):
        rows = []
        for seg in segments:
            if isinstance(seg, dict) and seg.get("id") is not None:
                seg_id = str(seg["id"])
                rows.append((seg_id, seg.get("name") or f"{tr('Room')} {seg_id}"))
        rows.sort(key=lambda r: r[1].lower())
        if rows == self._segments:
            return
        self._segments = rows
        self._checked &= {r[0] for r in rows}
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for seg_id, name in rows:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, seg_id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if seg_id in self._checked
                               else Qt.CheckState.Unchecked)
            self.rooms.addItem(item)
        self.rooms.blockSignals(False)
        self.rooms.setVisible(bool(rows))
        self._update_clean_button()

    def _on_room_toggled(self, item):
        seg_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(seg_id)
        else:
            self._checked.discard(seg_id)
        self._update_clean_button()

    def _update_clean_button(self):
        n = len(self._checked)
        if self.client is None:
            self.btn_clean.setText(tr("Not connected"))
            self.btn_clean.setEnabled(False)
            return
        self.btn_clean.setText(tr("Clean {n} room(s)").format(n=n) if n
                               else tr("Clean everything"))
        self.btn_clean.setEnabled(True)

    def _clean(self, *_):
        if self.client is None:
            return
        if self._checked:
            order = [seg_id for seg_id, _ in self._segments if seg_id in self._checked]
            self.client.clean_segments(order)
        else:
            self.client.basic_control("start")

    # -- camera ---------------------------------------------------------------------

    def _on_camera_section(self, collapsed):
        QgsSettings().setValue(KEY_CAMERA_OPEN, not collapsed)
        self._update_camera()

    def _update_camera(self):
        if self.isVisible() and not self.box_camera.isCollapsed():
            if not self.camera.stream.is_running():
                self.camera.play()
        else:
            self.camera.stop()

    def _on_camera_light_clicked(self, checked):
        if self.client is not None:
            self.client.set_toggle("CameraLightControlCapability", checked)

    def _on_toggle(self, capability, enabled):
        if capability == "CameraLightControlCapability":
            self.camera_light.setChecked(enabled)

    def shutdown(self):
        self.camera.shutdown()

    # -- client ---------------------------------------------------------------------

    def attach(self, client):
        """Called by the plugin whenever its client is created (or dropped: None)."""
        self.client = client
        self.capabilities = None
        if client is None:
            self._timer.stop()
            self._show_disconnected()
            return
        client.reachable.connect(self._on_reachable)
        client.robotInfoReceived.connect(self._on_robot_info)
        client.capabilitiesReceived.connect(self._on_capabilities)
        client.attributesReceived.connect(self._on_attributes)
        client.segmentsReceived.connect(self._on_segments)
        client.toggleReceived.connect(self._on_toggle)
        client.commandDone.connect(lambda *_: QTimer.singleShot(1500, self.refresh))
        client.fetch_robot_info(quiet=True)
        self._update_clean_button()
        if self.isVisible():
            self.refresh()
            self._timer.start()

    def _on_visibility(self, visible):
        self._update_camera()
        if not visible:
            self._timer.stop()
        elif self.client is None:
            self.clientRequested.emit()
        else:
            self.refresh()
            self._timer.start()

    def refresh(self, *_):
        if self.client is None:
            return
        self.client.fetch_attributes(quiet=True)
        if self.capabilities and "MapSegmentationCapability" in self.capabilities:
            self.client.fetch_segments()

    def _show_disconnected(self):
        self.lbl_robot.setText("Valetudo")
        self.lbl_robot.setToolTip(tr("Not connected"))
        self._set_badge(None)
        self.bar_battery.setValue(0)
        self.bar_battery.setFormat("")
        self._robot_error, self._dock_problems = "", []
        self._update_problems()
        self.camera_light.setVisible(False)
        self._segments, self._checked = [], set()
        self.rooms.clear()
        self.rooms.setVisible(False)
        self._update_clean_button()

    def _on_reachable(self, ok):
        if ok and self.client is not None and self.lbl_robot.text() == "Valetudo":
            self.client.fetch_robot_info(quiet=True)
        if not ok:
            self._set_badge("unreachable")

    def _on_robot_info(self, info):
        name = " ".join(p for p in (info.get("manufacturer"), info.get("modelName")) if p)
        self.lbl_robot.setText(name or "Valetudo")
        self.lbl_robot.setToolTip("")

    def _on_capabilities(self, capabilities):
        self.capabilities = set(capabilities)
        light = "CameraLightControlCapability" in self.capabilities
        self.camera_light.setVisible(light)
        if light:
            self.client.fetch_toggle("CameraLightControlCapability")
        if self.isVisible():
            self.refresh()
            self._timer.start()

    # -- state ----------------------------------------------------------------------

    def _set_badge(self, state):
        if state == "unreachable":
            text, color = tr("Unreachable"), "#c62828"
        else:
            text = _label(_state_labels(), state) if state else tr("Not connected")
            color = STATE_COLORS.get(state, "#757575")
        self.lbl_badge.setText(text)
        self.lbl_badge.setStyleSheet(
            f"background-color: {color}; color: white; font-weight: bold;"
            "border-radius: 9px; padding: 2px 10px;")

    def _update_problems(self):
        lines = ([self._robot_error] if self._robot_error else []) + self._dock_problems
        self.lbl_problems.setText("<br>".join(f"⚠ {line}" for line in lines))
        self.lbl_problems.setVisible(bool(lines))

    def _on_attributes(self, attributes):
        flags = _flag_labels()
        names, values, advice = (dock_component_labels(), dock_component_values(),
                                 dock_component_advice())
        state, error, problems = None, "", []
        for attr in attributes:
            cls = attr.get("__class")
            if cls == "StatusStateAttribute":
                state = attr.get("value")
                err = attr.get("error") or {}
                error = (err.get("message") if isinstance(err, dict) else "") \
                    or (attr.get("metaData") or {}).get("error_description", "")
                if state != "error" and not err:
                    error = ""
            elif cls == "BatteryStateAttribute":
                level = int(attr.get("level") or 0)
                flag = attr.get("flag")
                self.bar_battery.setValue(level)
                suffix = f" · {_label(flags, flag)}" if flag and flag != "none" else ""
                self.bar_battery.setFormat(f"{level} %{suffix}")
            elif cls == "DockComponentStateAttribute":
                value = attr.get("value")
                if value in DOCK_COMPONENT_PROBLEMS:
                    problems.append(f"{_label(names, attr.get('type'))}: "
                                    f"{_label(values, value)}. {advice[value]}")
        self._set_badge(state)
        self._robot_error, self._dock_problems = error, problems
        self._update_problems()
        self._timer.setInterval(ACTIVE_INTERVAL_MS if state in ACTIVE_STATES
                                else IDLE_INTERVAL_MS)
