"""Dockable status panel: robot state, settings, statistics, consumables...

The panel refreshes itself while it is visible (every 5 s while the robot is
working, every 30 s otherwise), independently of live map tracking. Sections
the robot does not support stay hidden.
"""

import math
import re
import time

from qgis.PyQt.QtCore import QDateTime, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsApplication, QgsSettings
from qgis.gui import QgsCollapsibleGroupBox, QgsDockWidget

from .camera_widget import CameraWidget
from .i18n import tr
from .icons import icon

KEY_TAB = "valetudo/panel_tab"
STATE_COLORS = {
    "cleaning": "#1565c0", "moving": "#1565c0", "manual_control": "#1565c0",
    "returning": "#ef6c00", "paused": "#f9a825", "docked": "#2e7d32",
    "idle": "#757575", "error": "#c62828",
}
ACTIVE_INTERVAL_MS = 5_000
IDLE_INTERVAL_MS = 30_000
ACTIVE_STATES = {"cleaning", "returning", "moving", "manual_control"}
LOW_PERCENT = 10
LOW_MINUTES = 600  # 10 h
MAX_EVENTS = 10

PRESET_ROWS = {  # capability -> attribute type in PresetSelectionStateAttribute
    "FanSpeedControlCapability": "fan_speed",
    "WaterUsageControlCapability": "water_grade",
}


# ---------------------------------------------------------------------------
# Translated labels for Valetudo values (functions: evaluated after the
# translator is installed). Unknown values fall back to the raw value.
# ---------------------------------------------------------------------------

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


def _preset_labels():
    return {
        "off": tr("Off"), "min": tr("Minimum"), "low": tr("Low"), "medium": tr("Medium"),
        "high": tr("High"), "max": tr("Maximum"), "turbo": tr("Turbo"), "custom": tr("Custom"),
    }


def _attachment_labels():
    return {"dustbin": tr("Dust bin"), "watertank": tr("Water tank"), "mop": tr("Mop")}


def _consumable_label(ctype, subtype):
    labels = {
        ("brush", "main"): tr("Main brush"),
        ("brush", "side_right"): tr("Side brush"),
        ("brush", "side_left"): tr("Side brush"),
        ("filter", "main"): tr("Filter"),
        ("sensor", "all"): tr("Sensors"),
        ("mop", "main"): tr("Mop pad"),
        ("detergent", "main"): tr("Detergent"),
        ("bin", "main"): tr("Dust bag"),
    }
    return labels.get((ctype, subtype)) or " ".join(p for p in (ctype, subtype) if p)


# Dock components (DockComponentStateAttribute), in display order.
DOCK_COMPONENT_ORDER = ["water_tank_clean", "water_tank_dirty", "detergent", "dustbag"]
DOCK_COMPONENT_PROBLEMS = {"missing", "empty", "full"}


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


# Capabilities listed with ✗ when missing (the ones the plugin uses).
CORE_CAPABILITIES = [
    "BasicControlCapability", "MapSegmentationCapability", "ZoneCleaningCapability",
    "GoToLocationCapability", "FanSpeedControlCapability", "WaterUsageControlCapability",
    "ConsumableMonitoringCapability", "CurrentStatisticsCapability",
    "TotalStatisticsCapability", "LocateCapability", "AutoEmptyDockManualTriggerCapability",
    "WifiConfigurationCapability", "CameraLightControlCapability",
]


def _humanize(capability):
    """'CarpetSensorModeControlCapability' -> 'Carpet sensor mode control' (fallback)."""
    name = capability[:-len("Capability")] if capability.endswith("Capability") else capability
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).split()
    return " ".join([words[0]] + [w.lower() for w in words[1:]]) if words else capability


def _capability_labels():
    return {
        "AutoEmptyDockAutoEmptyControlCapability": tr("Auto-empty dock"),
        "AutoEmptyDockAutoEmptyDurationControlCapability": tr("Auto-empty duration"),
        "AutoEmptyDockAutoEmptyIntervalControlCapability": tr("Auto-empty interval"),
        "CameraLightControlCapability": tr("Camera light"),
        "CarpetModeControlCapability": tr("Carpet mode"),
        "CarpetSensorModeControlCapability": tr("Carpet sensor mode"),
        "CleanRouteControlCapability": tr("Cleaning route"),
        "CollisionAvoidantNavigationControlCapability": tr("Collision-avoidant navigation"),
        "CombinedVirtualRestrictionsCapability": tr("Virtual walls and no-go zones"),
        "DoNotDisturbCapability": tr("Do not disturb"),
        "FloorMaterialDirectionAwareNavigationControlCapability":
            tr("Floor-direction-aware navigation"),
        "HighResolutionManualControlCapability": tr("Manual control (high resolution)"),
        "KeyLockCapability": tr("Child lock"),
        "ManualControlCapability": tr("Manual control"),
        "MapResetCapability": tr("Map reset"),
        "MapSegmentEditCapability": tr("Room editing"),
        "MapSegmentMaterialControlCapability": tr("Floor materials"),
        "MapSegmentRenameCapability": tr("Room renaming"),
        "MapSnapshotCapability": tr("Map snapshots"),
        "MappingPassCapability": tr("Mapping pass"),
        "MopDockCleanManualTriggerCapability": tr("Mop washing"),
        "MopDockDryManualTriggerCapability": tr("Mop drying"),
        "MopDockMopAutoDryingControlCapability": tr("Automatic mop drying"),
        "MopDockMopDryingTimeControlCapability": tr("Mop drying time"),
        "MopDockMopWashTemperatureControlCapability": tr("Mop wash temperature"),
        "MopExtensionControlCapability": tr("Mop extension"),
        "MopExtensionFurnitureLegHandlingControlCapability":
            tr("Mop extension around furniture legs"),
        "MopTwistControlCapability": tr("Mop twist"),
        "ObstacleAvoidanceControlCapability": tr("Obstacle avoidance"),
        "ObstacleImagesCapability": tr("Obstacle images"),
        "OperationModeControlCapability": tr("Operation mode"),
        "PendingMapChangeHandlingCapability": tr("Pending map changes"),
        "PetObstacleAvoidanceControlCapability": tr("Pet waste avoidance"),
        "QuirksCapability": tr("Quirks"),
        "SpeakerTestCapability": tr("Speaker test"),
        "SpeakerVolumeControlCapability": tr("Speaker volume"),
        "VoicePackManagementCapability": tr("Voice packs"),
        "WifiScanCapability": tr("Wi-Fi scan"),
        "BasicControlCapability": tr("Basic control"),
        "MapSegmentationCapability": tr("Room cleaning"),
        "ZoneCleaningCapability": tr("Zone cleaning"),
        "GoToLocationCapability": tr("Go to location"),
        "FanSpeedControlCapability": tr("Fan speed"),
        "WaterUsageControlCapability": tr("Water usage"),
        "ConsumableMonitoringCapability": tr("Consumables"),
        "CurrentStatisticsCapability": tr("Current statistics"),
        "TotalStatisticsCapability": tr("Total statistics"),
        "LocateCapability": tr("Locate"),
        "AutoEmptyDockManualTriggerCapability": tr("Empty dock"),
        "WifiConfigurationCapability": tr("Wi-Fi"),
    }


def _event_label(event):
    cls = event.get("__class", "")
    if cls == "DustBinFullValetudoEvent":
        return tr("Dust bin full")
    if cls == "ConsumableDepletedValetudoEvent":
        return tr("Consumable depleted: {name}").format(
            name=_consumable_label(event.get("type"), event.get("subType")))
    if cls == "ErrorStateValetudoEvent":
        return tr("Error: {message}").format(message=event.get("message", ""))
    if cls == "MissingResourceValetudoEvent":
        return tr("Missing resource: {message}").format(message=event.get("message", ""))
    if cls == "MopAttachmentReminderValetudoEvent":
        return tr("Mop attachment reminder")
    if cls == "PendingMapChangeValetudoEvent":
        return tr("Pending map change")
    return cls.replace("ValetudoEvent", "") or "?"


def state_label(state):
    """Translated robot state ("docked" -> "Docked" / "À la base")."""
    return _label(_state_labels(), state)


def _set_row_visible(form, field, visible):
    """QFormLayout.setRowVisible() only exists from Qt 6.4: hide label and field."""
    label = form.labelForField(field)
    if label is not None:
        label.setVisible(visible)
    field.setVisible(visible)


def _label(mapping, value):
    return mapping.get(value, value) if value is not None else "—"


def _format_duration(seconds):
    minutes = int(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return tr("{hours} h {minutes} min").format(hours=hours, minutes=minutes)
    return tr("{minutes} min").format(minutes=minutes)


def _format_area_cm2(value):
    return tr("{area} m²").format(area=f"{value / 10_000:.1f}")


def _format_time(iso):
    dt = QDateTime.fromString(iso or "", Qt.DateFormat.ISODateWithMs)
    if not dt.isValid():
        dt = QDateTime.fromString(iso or "", Qt.DateFormat.ISODate)
    return dt.toLocalTime().toString("dd/MM HH:mm") if dt.isValid() else ""


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class ValetudoPanel(QgsDockWidget):
    clientRequested = pyqtSignal()   # the panel was shown but no client exists yet

    def __init__(self, parent=None):
        super().__init__(tr("Valetudo status"), parent)
        self.setObjectName("ValetudoStatusPanel")
        self.client = None
        self.capabilities = None
        self._last_ok = None        # time.monotonic() of the last successful request
        self._last_ok_clock = ""
        self._reachable = None
        self._robot_text = self._version_text = ""
        self._last_map = 0.0   # time.monotonic() of the last map received

        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._age_timer = QTimer(self)
        self._age_timer.setInterval(1000)
        self._age_timer.timeout.connect(self._update_header)

        self._build_ui()
        self.visibilityChanged.connect(self._on_visibility)
        self._show_disconnected()

    # -- UI construction -------------------------------------------------------

    def _section(self, title, key, open_default=True):
        """A native QGIS collapsible section; its open/closed state is remembered."""
        box = QgsCollapsibleGroupBox(title)
        box.setSaveCollapsedState(False)   # we store it ourselves, under our own key
        settings_key = f"valetudo/section_{key}"
        box.setCollapsed(not QgsSettings().value(settings_key, open_default, type=bool))
        box.collapsedStateChanged.connect(
            lambda collapsed: QgsSettings().setValue(settings_key, not collapsed))
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._layout.addWidget(box)
        return box, form

    def _value_label(self):
        label = QLabel("—")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _page(self, title, key):
        """A scrollable tab page; following sections are added to it."""
        page = QWidget()
        self._layout = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        self._tab_keys.append(key)
        return scroll

    def _end_page(self):
        self._layout.addStretch(1)

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)

        # -- Summary header, always visible above the tabs --------------------------
        header = QHBoxLayout()
        self.lbl_connection = QLabel()
        header.addWidget(self.lbl_connection, 1)
        btn_refresh = QToolButton()
        btn_refresh.setIcon(QgsApplication.getThemeIcon("/mActionRefresh.svg"))
        btn_refresh.setToolTip(tr("Refresh now"))
        btn_refresh.clicked.connect(self.refresh)
        header.addWidget(btn_refresh)
        root_layout.addLayout(header)

        self.lbl_robot = QLabel()
        self.lbl_robot.setWordWrap(True)
        font = self.lbl_robot.font()
        font.setItalic(True)
        self.lbl_robot.setFont(font)
        root_layout.addWidget(self.lbl_robot)

        summary = QHBoxLayout()
        self.lbl_badge = QLabel()        # coloured state pill
        self.lbl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_badge(None)
        summary.addWidget(self.lbl_badge)
        self.bar_battery = QProgressBar()
        self.bar_battery.setRange(0, 100)
        self.bar_battery.setMaximumHeight(18)
        self.bar_battery.setToolTip(tr("Battery"))
        summary.addWidget(self.bar_battery, 1)
        root_layout.addLayout(summary)

        self.lbl_error = QLabel()
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setStyleSheet("color: #c62828; font-weight: bold;")
        self.lbl_error.setVisible(False)
        root_layout.addWidget(self.lbl_error)

        # Controls, filled by set_actions() with the plugin's own QActions (same
        # enabled/checked state as the menu): quick buttons, then the command list.
        self._quick = QHBoxLayout()
        self._quick.setSpacing(4)
        root_layout.addLayout(self._quick)
        self._controls = QHBoxLayout()
        self._controls.setSpacing(2)
        root_layout.addLayout(self._controls)
        self.lbl_feedback = QLabel()
        self.lbl_feedback.setWordWrap(True)
        root_layout.addWidget(self.lbl_feedback)

        # -- Tabs ---------------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self._tab_keys = []
        root_layout.addWidget(self.tabs, 1)

        # Robot
        self._page(tr("Robot"), "robot")
        self.box_rooms, form = self._section(tr("Rooms"), "rooms")
        self.rooms = QListWidget()
        self.rooms.setMaximumHeight(160)
        self.rooms.itemChanged.connect(self._on_room_toggled)
        form.addRow(self.rooms)
        self.rooms_layout = form
        self.box_rooms.setVisible(False)
        self._segments, self._checked_rooms = [], set()
        self.box_status, form = self._section(tr("Status"), "status")
        self.lbl_state = self._value_label()
        form.addRow(tr("State"), self.lbl_state)
        self.lbl_dock = self._value_label()
        form.addRow(tr("Dock"), self.lbl_dock)
        self._status_form = form

        # Dock station: fresh/waste water tanks, detergent, dust bag (rows rebuilt on update)
        self.box_dock, self._dock_form = self._section(tr("Dock station"), "dock")

        self.box_settings, form = self._section(tr("Settings"), "settings")
        self.preset_combos = {}
        for capability, text in (("FanSpeedControlCapability", tr("Fan speed")),
                                 ("WaterUsageControlCapability", tr("Water usage"))):
            combo = QComboBox()
            combo.activated.connect(lambda index, c=capability: self._on_preset_chosen(c, index))
            form.addRow(text, combo)
            self.preset_combos[capability] = combo
        self._settings_form = form

        self.box_attachments, form = self._section(tr("Attachments"), "attachments")
        self.lbl_attachments = self._value_label()
        form.addRow(self.lbl_attachments)

        self.box_position, form = self._section(tr("Position"), "position")
        self.lbl_position = self._value_label()
        self.lbl_position.setText(tr("Waiting for the robot position…"))
        form.addRow(self.lbl_position)
        self._end_page()

        # Camera: plays only while this tab is shown and the panel is visible.
        self.camera_page = self._page(tr("Camera"), "camera")
        self.camera = CameraWidget()
        self._layout.addWidget(self.camera)
        self.camera_light = QCheckBox(tr("Camera light"))
        self.camera_light.setVisible(False)    # shown if the robot supports it
        self.camera_light.clicked.connect(self._on_camera_light_clicked)  # user only
        self._layout.addWidget(self.camera_light)
        self._end_page()

        # Maintenance
        self._page(tr("Maintenance"), "maintenance")
        self.box_consumables, self._consumables_form = self._section(
            tr("Consumables"), "consumables")
        self.box_current, form = self._section(tr("Current or last cleaning"), "current")
        self.lbl_cur_time = self._value_label()
        self.lbl_cur_area = self._value_label()
        form.addRow(tr("Duration"), self.lbl_cur_time)
        form.addRow(tr("Area"), self.lbl_cur_area)
        self.box_total, form = self._section(tr("Totals"), "totals")
        self.lbl_tot_count = self._value_label()
        self.lbl_tot_time = self._value_label()
        self.lbl_tot_area = self._value_label()
        form.addRow(tr("Cleanings"), self.lbl_tot_count)
        form.addRow(tr("Duration"), self.lbl_tot_time)
        form.addRow(tr("Area"), self.lbl_tot_area)
        self._end_page()

        # Info
        self._page(tr("Info"), "info")
        self.box_wifi, form = self._section(tr("Wi-Fi"), "wifi")
        self.lbl_wifi = self._value_label()
        form.addRow(self.lbl_wifi)
        self.box_events, form = self._section(tr("Recent events"), "events")
        self.list_events = QListWidget()
        self.list_events.setMaximumHeight(200)
        form.addRow(self.list_events)
        self.box_boot, form = self._section(tr("Boot log"), "boot")
        self.txt_boot = QPlainTextEdit()
        self.txt_boot.setReadOnly(True)
        self.txt_boot.setMaximumHeight(220)
        self.txt_boot.setPlaceholderText(tr("Use “Watch robot boot”, or reboot the robot."))
        form.addRow(self.txt_boot)
        self.box_features, form = self._section(tr("Features"), "features")
        self.lbl_features = self._value_label()
        form.addRow(self.lbl_features)
        self._end_page()

        last = QgsSettings().value(KEY_TAB, "robot", type=str)
        if last in self._tab_keys:
            self.tabs.setCurrentIndex(self._tab_keys.index(last))
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setWidget(root)

        for box in (self.box_settings, self.box_current, self.box_total, self.box_consumables,
                    self.box_attachments, self.box_wifi, self.box_dock):
            box.setVisible(False)   # shown once the robot reports them

    def _set_badge(self, state):
        """Coloured pill with the robot state, readable at a glance."""
        color = STATE_COLORS.get(state, "#757575")
        text = _label(_state_labels(), state) if state else tr("Unknown")
        self.lbl_badge.setText(text)
        self.lbl_badge.setStyleSheet(
            f"background-color: {color}; color: white; font-weight: bold;"
            "border-radius: 9px; padding: 2px 10px;")

    def _on_tab_changed(self, index):
        if 0 <= index < len(self._tab_keys):
            QgsSettings().setValue(KEY_TAB, self._tab_keys[index])
        self._update_camera()

    def set_actions(self, groups, quick=(), room_action=None):
        """Quick buttons, then a drop-down of commands grouped under headers, run with Run.

        groups: [(title, [QAction, …]), …]. Each action's statusTip is shown as a
        hint when it is selected. room_action is placed under the rooms checklist.
        """
        for action in quick:
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._quick.addWidget(button)
        if room_action is not None:
            button = QToolButton()
            button.setDefaultAction(room_action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.rooms_layout.addRow(button)

        self._commands = []
        self.combo_commands = QComboBox()
        # Never let the longest entry widen the panel (it used to clip the right side).
        self.combo_commands.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_commands.setMinimumContentsLength(10)
        self.combo_commands.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        model = self.combo_commands.model()
        for title, actions in groups:
            actions = [a for a in actions if a is not None]
            if not actions:
                continue
            self.combo_commands.addItem(f"— {title} —")
            header = model.item(self.combo_commands.count() - 1) if hasattr(model, "item") else None
            if header is not None:
                header.setEnabled(False)   # a title, not a command
                font = header.font()
                font.setBold(True)
                header.setFont(font)
            for action in actions:
                self.combo_commands.addItem(action.icon(), action.text(), len(self._commands))
                self._commands.append(action)
                action.changed.connect(self._sync_commands)   # enabled / checked / text
        self.combo_commands.currentIndexChanged.connect(self._on_command_selected)
        self.combo_commands.activated.connect(lambda *_: self._show_hint())
        self.btn_run = QPushButton(tr("Run"))
        self.btn_run.clicked.connect(self._run_command)
        self._controls.addWidget(self.combo_commands, 1)
        self._controls.addWidget(self.btn_run)
        # Start on the first real command, not on a header.
        for index in range(self.combo_commands.count()):
            if self.combo_commands.itemData(index) is not None:
                self.combo_commands.setCurrentIndex(index)
                break
        self._sync_commands()
        self._show_hint()

    def _current_command(self):
        data = self.combo_commands.itemData(self.combo_commands.currentIndex())
        return self._commands[data] if isinstance(data, int) else None

    def _sync_commands(self, *_):
        """Mirror each action in the list: ✓ when switched on, greyed when unavailable."""
        model = self.combo_commands.model()
        for index in range(self.combo_commands.count()):
            data = self.combo_commands.itemData(index)
            if not isinstance(data, int):
                continue
            action = self._commands[data]
            text = action.text()
            if action.isCheckable() and action.isChecked():
                text += "  ✓"
            self.combo_commands.setItemText(index, text)
            item = model.item(index) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(action.isEnabled())
                item.setToolTip(action.statusTip() or action.toolTip())
        self._sync_run_button()

    def _on_command_selected(self, *_):
        self._sync_run_button()
        self._show_hint()

    def _show_hint(self):
        """Under the list: what the selected command does / what to do after Run."""
        action = self._current_command()
        hint = action.statusTip() if action is not None else ""
        if action is not None and not action.isEnabled():
            hint = action.toolTip()
        self.lbl_feedback.setStyleSheet("")
        self.lbl_feedback.setText(hint)

    def show_feedback(self, text, kind="info"):
        """Called by the plugin with the result of a command."""
        colors = {"success": "#2e7d32", "warning": "#ef6c00", "critical": "#c62828"}
        self.lbl_feedback.setStyleSheet(f"color: {colors[kind]};" if kind in colors else "")
        self.lbl_feedback.setText(text)

    # -- rooms ----------------------------------------------------------------------

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
        self._checked_rooms &= {r[0] for r in rows}
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for seg_id, name in rows:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, seg_id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if seg_id in self._checked_rooms
                               else Qt.CheckState.Unchecked)
            self.rooms.addItem(item)
        self.rooms.blockSignals(False)
        self.box_rooms.setVisible(bool(rows))

    def _on_room_toggled(self, item):
        seg_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_rooms.add(seg_id)
        else:
            self._checked_rooms.discard(seg_id)

    def checked_room_ids(self):
        """Ticked rooms, in list order."""
        return [seg_id for seg_id, _name in self._segments if seg_id in self._checked_rooms]

    def _sync_run_button(self, *_):
        action = self._current_command()
        self.btn_run.setEnabled(action is not None and action.isEnabled())
        self.btn_run.setToolTip(action.toolTip() if action is not None else "")

    def _run_command(self, *_):
        action = self._current_command()
        if action is not None and action.isEnabled():
            action.trigger()   # same as the menu entry (toggles if checkable)

    # -- client wiring ---------------------------------------------------------

    def attach(self, client):
        """Called by the plugin whenever its client is created (or dropped: None)."""
        self.client = client
        self.capabilities = None
        self._reachable = None
        if client is None:
            self._timer.stop()
            self._show_disconnected()
            for box in (self.box_settings, self.box_current, self.box_total,
                        self.box_consumables, self.box_attachments, self.box_wifi,
                        self.box_dock):
                box.setVisible(False)
            self.lbl_features.setText("—")
            self._segments, self._checked_rooms = [], set()
            self.rooms.clear()
            self.box_rooms.setVisible(False)
            self.camera_light.setVisible(False)
            self._set_badge(None)
            self.lbl_error.setVisible(False)
            return
        client.reachable.connect(self._on_reachable)
        client.robotInfoReceived.connect(self._on_robot_info)
        client.versionReceived.connect(self._on_version)
        client.capabilitiesReceived.connect(self._on_capabilities)
        client.attributesReceived.connect(self._on_attributes)
        client.consumablesReceived.connect(self._on_consumables)
        client.currentStatisticsReceived.connect(self._on_current_statistics)
        client.totalStatisticsReceived.connect(self._on_total_statistics)
        client.wifiReceived.connect(self._on_wifi)
        client.eventsReceived.connect(self._on_events)
        client.presetsReceived.connect(self._on_presets)
        client.segmentsReceived.connect(self._on_segments)
        client.toggleReceived.connect(self._on_toggle)
        client.mapPeeked.connect(self.update_map)
        client.commandDone.connect(lambda *_: QTimer.singleShot(1500, self.refresh))
        self._robot_text = ""
        self._version_text = ""
        client.fetch_robot_info(quiet=True)
        client.fetch_version(quiet=True)
        if self.isVisible():
            self.refresh()
            self._timer.start()

    def _on_camera_light_clicked(self, checked):
        if self.client is not None:
            self.client.set_toggle("CameraLightControlCapability", checked)

    def _on_toggle(self, capability, enabled):
        if capability == "CameraLightControlCapability":
            self.camera_light.setChecked(enabled)   # setChecked does not emit clicked

    def _update_camera(self):
        """Play only while the panel is visible and the Camera tab is shown."""
        if self.isVisible() and self.tabs.currentWidget() is self.camera_page:
            if not self.camera.stream.is_running():
                self.camera.play()
        else:
            self.camera.stop()

    def clear_boot_log(self):
        self.txt_boot.clear()

    def add_boot_log(self, line):
        self.txt_boot.appendPlainText(line)

    def shutdown(self):
        """Called by the plugin on unload."""
        self.camera.shutdown()

    def _on_visibility(self, visible):
        self._update_camera()
        if not visible:
            self._timer.stop()
            self._age_timer.stop()
            return
        self._age_timer.start()
        if self.client is None:
            self.clientRequested.emit()   # the plugin creates the client, then calls attach()
        else:
            self.refresh()
            self._timer.start()

    def refresh(self, *_):
        client = self.client
        if client is None:
            return
        client.fetch_attributes(quiet=True)
        client.fetch_events(quiet=True)
        # Position comes from the map: read it unless live tracking just delivered one.
        if time.monotonic() - self._last_map >= self._timer.interval() / 1000 * 0.9:
            client.peek_map()
        caps = self.capabilities or set()
        if "ConsumableMonitoringCapability" in caps:
            client.fetch_consumables(quiet=True)
        if "CurrentStatisticsCapability" in caps:
            client.fetch_current_statistics(quiet=True)
        if "TotalStatisticsCapability" in caps:
            client.fetch_total_statistics(quiet=True)
        if "WifiConfigurationCapability" in caps:
            client.fetch_wifi(quiet=True)
        if "MapSegmentationCapability" in caps:
            client.fetch_segments()

    def _on_preset_chosen(self, capability, index):
        combo = self.preset_combos[capability]
        name = combo.itemData(index)
        if self.client is not None and name:
            self.client.set_preset(capability, name)

    # -- header ------------------------------------------------------------------

    def _show_disconnected(self):
        self.lbl_connection.setText(f"<span style='color:#9e9e9e'>●</span> {tr('Not connected')}")
        self.lbl_robot.setText("")

    def _on_reachable(self, ok):
        self._reachable = ok
        if ok and self.client is not None and not self._robot_text:
            self.client.fetch_robot_info(quiet=True)   # robot was off when we connected
            self.client.fetch_version(quiet=True)
        if ok:
            self._last_ok = time.monotonic()
            self._last_ok_clock = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._update_header()

    def _update_header(self):
        if self.client is None or self._reachable is None:
            return
        if self._reachable:
            seconds = int(time.monotonic() - (self._last_ok or time.monotonic()))
            text = tr("Connected · updated {seconds} s ago").format(seconds=seconds)
            self.lbl_connection.setText(f"<span style='color:#2e7d32'>●</span> {text}")
        else:
            text = tr("Unreachable · last update {time}").format(
                time=self._last_ok_clock or "—")
            self.lbl_connection.setText(f"<span style='color:#c62828'>●</span> {text}")

    def _on_robot_info(self, info):
        self._robot_text = " ".join(
            p for p in (info.get("manufacturer"), info.get("modelName")) if p)
        self._update_robot_label()

    def _on_version(self, version):
        release = version.get("release")
        self._version_text = f"Valetudo {release}" if release else ""
        self._update_robot_label()

    def _update_robot_label(self):
        self.lbl_robot.setText(" · ".join(p for p in (self._robot_text, self._version_text) if p))

    # -- data --------------------------------------------------------------------

    def _on_capabilities(self, capabilities):
        self.capabilities = set(capabilities)
        caps = self.capabilities

        any_preset = False
        for capability, combo in self.preset_combos.items():
            supported = capability in caps
            _set_row_visible(self._settings_form, combo, supported)
            any_preset |= supported
            if supported:
                self.client.fetch_presets(capability, quiet=True)
        self.box_settings.setVisible(any_preset)
        self.box_consumables.setVisible("ConsumableMonitoringCapability" in caps)
        self.box_current.setVisible("CurrentStatisticsCapability" in caps)
        self.box_total.setVisible("TotalStatisticsCapability" in caps)
        self.box_wifi.setVisible("WifiConfigurationCapability" in caps)

        labels = _capability_labels()
        name = lambda c: labels.get(c) or _humanize(c)
        lines = [f"✓ {n}" for n in sorted(name(c) for c in caps)]
        lines += [f"✗ {name(c)}" for c in CORE_CAPABILITIES if c not in caps]
        if self.camera_light.isVisibleTo(self) != ("CameraLightControlCapability" in caps):
            self.camera_light.setVisible("CameraLightControlCapability" in caps)
        if "CameraLightControlCapability" in caps:
            self.client.fetch_toggle("CameraLightControlCapability")
        self.lbl_features.setText("\n".join(lines))
        if self.isVisible():   # nothing is fetched while the panel is hidden
            self.refresh()
            self._timer.start()

    def _on_presets(self, capability, presets):
        combo = self.preset_combos.get(capability)
        if combo is None:
            return
        current = combo.currentData()
        combo.clear()
        labels = _preset_labels()
        for name in presets:
            combo.addItem(labels.get(name, name), name)
        if current is not None:
            combo.setCurrentIndex(max(0, combo.findData(current)))

    def _on_attributes(self, attributes):
        states, flags = _state_labels(), _flag_labels()
        attachments = []
        dock_value = None
        components = {}
        error_text = ""
        state = None
        for attr in attributes:
            cls = attr.get("__class")
            if cls == "StatusStateAttribute":
                state = attr.get("value")
                flag = attr.get("flag")
                text = _label(states, state)
                if flag and flag != "none":
                    text += f" ({_label(flags, flag)})"
                self.lbl_state.setText(text)
                self._set_badge(state)
                err = attr.get("error") or {}
                error_text = (err.get("message") if isinstance(err, dict) else "") \
                    or (attr.get("metaData") or {}).get("error_description", "")
                if state != "error" and not err:
                    error_text = ""
            elif cls == "BatteryStateAttribute":
                level = int(attr.get("level") or 0)
                flag = attr.get("flag")
                self.bar_battery.setValue(level)
                suffix = f" · {_label(flags, flag)}" if flag and flag != "none" else ""
                self.bar_battery.setFormat(f"{level} %{suffix}")
            elif cls == "PresetSelectionStateAttribute":
                for capability, ptype in PRESET_ROWS.items():
                    if attr.get("type") == ptype:
                        combo = self.preset_combos[capability]
                        index = combo.findData(attr.get("value"))
                        if index >= 0:
                            combo.setCurrentIndex(index)
            elif cls == "AttachmentStateAttribute":
                name = _label(_attachment_labels(), attr.get("type"))
                attachments.append(f"{name} {'✓' if attr.get('attached') else '✗'}")
            elif cls == "DockStatusStateAttribute":
                dock_value = attr.get("value")
            elif cls == "DockComponentStateAttribute":
                components[attr.get("type")] = attr.get("value")

        _set_row_visible(self._status_form, self.lbl_dock, dock_value is not None)
        self.lbl_dock.setText(_label(states, dock_value))
        self.lbl_error.setVisible(bool(error_text))
        self.lbl_error.setText(f"⚠ {error_text}" if error_text else "")
        self.box_attachments.setVisible(bool(attachments))
        self._update_dock_components(components)
        self.lbl_attachments.setText("   ".join(attachments))

        self._timer.setInterval(ACTIVE_INTERVAL_MS if state in ACTIVE_STATES
                                else IDLE_INTERVAL_MS)

    def _update_dock_components(self, components):
        """One row per dock component; problems (missing/empty/full) in red with advice."""
        form = self._dock_form
        while form.rowCount():
            form.removeRow(0)
        self.box_dock.setVisible(bool(components))
        names, values, advice = (dock_component_labels(), dock_component_values(),
                                 dock_component_advice())
        order = DOCK_COMPONENT_ORDER + sorted(c for c in components
                                              if c not in DOCK_COMPONENT_ORDER)
        for component in order:
            if component not in components:
                continue
            value = components[component]
            label = QLabel(_label(values, value))
            name = _label(names, component)
            if value in DOCK_COMPONENT_PROBLEMS:
                name = f"{name} ⚠"
                label.setStyleSheet("color: #c62828; font-weight: bold;")
                label.setToolTip(advice[value])
                label.setText(f"{label.text()} · {advice[value]}")
            elif value == "ok":
                label.setStyleSheet("color: #2e7d32;")
            form.addRow(name, label)

    def _on_consumables(self, consumables):
        form = self._consumables_form
        while form.rowCount():
            form.removeRow(0)
        for item in consumables:
            remaining = item.get("remaining") or {}
            value, unit = remaining.get("value"), remaining.get("unit")
            if value is None:
                continue
            label = _consumable_label(item.get("type"), item.get("subType"))
            if unit == "percent":
                widget = QProgressBar()
                widget.setRange(0, 100)
                widget.setValue(int(value))
                widget.setFormat(f"{int(value)} %")
                low = value < LOW_PERCENT
            else:  # minutes
                widget = QLabel(tr("{hours} h left").format(hours=int(value) // 60))
                low = value < LOW_MINUTES
            if low:
                label = f"{label} ⚠"
                widget.setToolTip(tr("Replace soon"))
            form.addRow(label, widget)

    @staticmethod
    def _stats(items):
        return {s.get("type"): s.get("value") for s in items if isinstance(s, dict)}

    def _on_current_statistics(self, items):
        stats = self._stats(items)
        self.lbl_cur_time.setText(_format_duration(stats["time"]) if "time" in stats else "—")
        self.lbl_cur_area.setText(_format_area_cm2(stats["area"]) if "area" in stats else "—")

    def _on_total_statistics(self, items):
        stats = self._stats(items)
        self.lbl_tot_count.setText(str(stats["count"]) if "count" in stats else "—")
        self.lbl_tot_time.setText(_format_duration(stats["time"]) if "time" in stats else "—")
        self.lbl_tot_area.setText(_format_area_cm2(stats["area"]) if "area" in stats else "—")

    def _on_wifi(self, wifi):
        details = wifi.get("details") or {}
        parts = [details.get("ssid")]
        if details.get("signal") is not None:
            parts.append(f"{details['signal']} dBm")
        ips = details.get("ips") or []
        if ips:
            parts.append(ips[0])
        self.lbl_wifi.setText(" · ".join(str(p) for p in parts if p) or "—")

    def _on_events(self, events):
        self.list_events.clear()
        events = sorted((e for e in events if isinstance(e, dict)),
                        key=lambda e: e.get("timestamp") or "", reverse=True)[:MAX_EVENTS]
        if not events:
            self.list_events.addItem(tr("No recent events."))
            return
        for event in events:
            stamp = _format_time(event.get("timestamp"))
            self.list_events.addItem(f"{stamp}  {_event_label(event)}".strip())

    def update_map(self, map_json):
        """Called with each map: from the plugin (show map / live tracking) or peeked."""
        self._last_map = time.monotonic()
        robot = dock = None
        for ent in map_json.get("entities", []):
            pts = ent.get("points") or []
            if len(pts) < 2:
                continue
            if ent.get("type") == "robot_position":
                robot = (pts[0], pts[1], (ent.get("metaData") or {}).get("angle"))
            elif ent.get("type") == "charger_location":
                dock = (pts[0], pts[1])
        if robot is None:
            return
        x, y, angle = robot
        text = tr("x {x} cm · y {y} cm · heading {angle}°").format(
            x=round(x), y=round(y), angle=round(angle) if angle is not None else "?")
        if dock is not None:
            distance = math.hypot(x - dock[0], y - dock[1]) / 100
            text += "\n" + tr("{distance} m from the dock").format(distance=f"{distance:.1f}")
        self.lbl_position.setText(text)
