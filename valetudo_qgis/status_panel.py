"""Dockable panel organised around tasks.

  header        robot, connection, state badge, battery, problems banner
  Clean         whole home / rooms (checklist) / zone (drawn on the map),
                passes, suction, water, one Clean button
  Robot         pause, stop, dock, go to, locate, camera
  Dock station  tanks and dust bag states, with their actions next to them
  collapsible   Camera, Map, Maintenance, Advanced

Buttons reuse the plugin's own QActions, so they share their state with the
toolbar and the menu. The panel refreshes itself while it is visible (every
5 s while the robot works, every 30 s otherwise). Sections the robot does not
support stay hidden.
"""

import math
import re
import time

from qgis.PyQt.QtCore import QDateTime, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsApplication, QgsSettings
from qgis.gui import QgsCollapsibleGroupBox, QgsDockWidget

from .camera_widget import CameraWidget
from .i18n import tr
from .icons import icon

KEY_CLEAN_MODE = "valetudo/clean_mode"
CLEAN_MODES = ("home", "rooms", "zone")
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
    zoneCleared = pyqtSignal()       # the pending zone was used or cleared

    def __init__(self, parent=None):
        super().__init__(tr("Valetudo"), parent)
        self.setObjectName("ValetudoStatusPanel")
        self.client = None
        self.capabilities = None
        self.act = {}
        self._last_ok = None
        self._last_ok_clock = ""
        self._reachable = None
        self._robot_text = self._version_text = ""
        self._last_map = 0.0
        self._robot_error = ""
        self._dock_problems = []
        self._pending_zone = None
        self._segments = []            # [(id, name)]
        self._checked_rooms = set()

        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._age_timer = QTimer(self)
        self._age_timer.setInterval(1000)
        self._age_timer.timeout.connect(self._update_header)

        self._build_ui()
        self.visibilityChanged.connect(self._on_visibility)
        self._show_disconnected()

    # ======================================================================
    # Construction
    # ======================================================================

    @staticmethod
    def _card(title, icon_name=None):
        """A plain framed block with a bold title; returns (frame, inner layout)."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        header = QLabel(f"<b>{title}</b>")
        layout.addWidget(header)
        return frame, layout

    def _section(self, title, key, open_default=False):
        """A native QGIS collapsible section; its open/closed state is remembered."""
        box = QgsCollapsibleGroupBox(title)
        box.setSaveCollapsedState(False)
        settings_key = f"valetudo/section_{key}"
        box.setCollapsed(not QgsSettings().value(settings_key, open_default, type=bool))
        box.collapsedStateChanged.connect(
            lambda collapsed: QgsSettings().setValue(settings_key, not collapsed))
        layout = QVBoxLayout(box)
        self._layout.addWidget(box)
        return box, layout

    @staticmethod
    def _form(layout):
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        return form

    @staticmethod
    def _value_label(text="—"):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _action_grid(layout, columns=3):
        grid = QGridLayout()
        grid.setSpacing(4)
        layout.addLayout(grid)
        return grid, columns

    @staticmethod
    def _action_button(action, hide_when_disabled=False):
        """A labelled button bound to a plugin QAction (text, icon, state, tooltip)."""
        button = QToolButton()
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if hide_when_disabled:
            action.changed.connect(lambda b=button, a=action: b.setVisible(a.isEnabled()))
            button.setVisible(action.isEnabled())
        return button

    def _build_ui(self):
        content = QWidget()
        self._layout = QVBoxLayout(content)

        # -- Header --------------------------------------------------------------
        top = QHBoxLayout()
        names = QVBoxLayout()
        self.lbl_robot = QLabel()
        font = self.lbl_robot.font()
        font.setBold(True)
        self.lbl_robot.setFont(font)
        self.lbl_robot.setWordWrap(True)
        names.addWidget(self.lbl_robot)
        self.lbl_connection = QLabel()
        self.lbl_connection.setWordWrap(True)
        names.addWidget(self.lbl_connection)
        top.addLayout(names, 1)
        self.lbl_badge = QLabel()
        self.lbl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.lbl_badge, 0, Qt.AlignmentFlag.AlignTop)
        btn_refresh = QToolButton()
        btn_refresh.setIcon(QgsApplication.getThemeIcon("/mActionRefresh.svg"))
        btn_refresh.setToolTip(tr("Refresh now"))
        btn_refresh.setAutoRaise(True)
        btn_refresh.clicked.connect(self.refresh)
        top.addWidget(btn_refresh, 0, Qt.AlignmentFlag.AlignTop)
        self._layout.addLayout(top)

        self.bar_battery = QProgressBar()
        self.bar_battery.setRange(0, 100)
        self.bar_battery.setToolTip(tr("Battery"))
        self._layout.addWidget(self.bar_battery)

        # Problems: robot error and dock station problems, one red banner.
        self.lbl_problems = QLabel()
        self.lbl_problems.setWordWrap(True)
        self.lbl_problems.setStyleSheet(
            "background-color: #fdecea; color: #b71c1c; border-radius: 4px; padding: 6px;")
        self.lbl_problems.setVisible(False)
        self._layout.addWidget(self.lbl_problems)
        self._set_badge(None)

        # -- Clean card ----------------------------------------------------------
        self.card_clean, layout = self._card(tr("Clean"))
        modes = QHBoxLayout()
        modes.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for mode, text in (("home", tr("Whole home")), ("rooms", tr("Rooms")),
                           ("zone", tr("Zone"))):
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            modes.addWidget(button)
            button.toggled.connect(lambda on, m=mode: on and self._set_mode(m))
        layout.addLayout(modes)

        self.rooms_list = QListWidget()
        self.rooms_list.setMaximumHeight(150)
        self.rooms_list.itemChanged.connect(self._on_room_toggled)
        layout.addWidget(self.rooms_list)

        self.zone_box = QWidget()
        zone_layout = QVBoxLayout(self.zone_box)
        zone_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_zone = QLabel()
        self.lbl_zone.setWordWrap(True)
        zone_layout.addWidget(self.lbl_zone)
        self.zone_buttons = QHBoxLayout()
        zone_layout.addLayout(self.zone_buttons)
        self.btn_clear_zone = QPushButton(tr("Clear"))
        self.btn_clear_zone.clicked.connect(self.clear_pending_zone)
        layout.addWidget(self.zone_box)

        options = self._form(layout)
        self.spin_passes = QSpinBox()
        self.spin_passes.setRange(1, 3)
        options.addRow(tr("Passes"), self.spin_passes)
        self.preset_combos = {}
        for capability, text in (("FanSpeedControlCapability", tr("Fan speed")),
                                 ("WaterUsageControlCapability", tr("Water usage"))):
            combo = QComboBox()
            combo.activated.connect(lambda index, c=capability: self._on_preset_chosen(c, index))
            options.addRow(text, combo)
            self.preset_combos[capability] = combo
        self._options_form = options

        self.btn_clean = QPushButton()
        self.btn_clean.setIcon(icon("start"))
        self.btn_clean.setMinimumHeight(34)
        bold = self.btn_clean.font()
        bold.setBold(True)
        self.btn_clean.setFont(bold)
        self.btn_clean.clicked.connect(self._clean)
        layout.addWidget(self.btn_clean)
        self._layout.addWidget(self.card_clean)

        # -- Robot card ----------------------------------------------------------
        self.card_robot, layout = self._card(tr("Robot"))
        self.robot_grid, _ = self._action_grid(layout)
        self._layout.addWidget(self.card_robot)

        # -- Dock station card ---------------------------------------------------
        self.card_dock, layout = self._card(tr("Dock station"))
        self._dock_form = self._form(layout)
        self.dock_grid, _ = self._action_grid(layout)
        self.card_dock.setVisible(False)
        self._layout.addWidget(self.card_dock)

        # -- Collapsible sections ------------------------------------------------
        self.box_camera, layout = self._section(tr("Camera"), "camera")
        self.camera = CameraWidget()
        layout.addWidget(self.camera)
        self.camera_light = QCheckBox(tr("Camera light"))
        self.camera_light.setVisible(False)    # shown if the robot supports it
        self.camera_light.clicked.connect(self._on_camera_light_clicked)  # user only
        layout.addWidget(self.camera_light)
        self.box_camera.collapsedStateChanged.connect(lambda *_: self._update_camera())

        self.box_map, layout = self._section(tr("Map"), "map")
        self.map_grid, _ = self._action_grid(layout, columns=2)
        form = self._form(layout)
        self.lbl_position = self._value_label(tr("Waiting for the robot position…"))
        form.addRow(tr("Position"), self.lbl_position)

        self.box_maintenance, layout = self._section(tr("Maintenance"), "maintenance")
        self._consumables_form = self._form(layout)
        form = self._form(layout)
        self.lbl_cur_time, self.lbl_cur_area = self._value_label(), self._value_label()
        self.lbl_tot_count, self.lbl_tot_time = self._value_label(), self._value_label()
        self.lbl_tot_area = self._value_label()
        form.addRow(tr("Last cleaning"), self.lbl_cur_time)
        form.addRow("", self.lbl_cur_area)
        form.addRow(tr("Cleanings"), self.lbl_tot_count)
        form.addRow(tr("Total time"), self.lbl_tot_time)
        form.addRow(tr("Total area"), self.lbl_tot_area)
        self._stats_form = form

        self.box_advanced, layout = self._section(tr("Advanced"), "advanced")
        form = self._form(layout)
        self.lbl_state = self._value_label()
        form.addRow(tr("State"), self.lbl_state)
        self.lbl_dock = self._value_label()
        form.addRow(tr("Dock"), self.lbl_dock)
        self.lbl_attachments = self._value_label()
        form.addRow(tr("Attachments"), self.lbl_attachments)
        self.lbl_wifi = self._value_label()
        form.addRow(tr("Wi-Fi"), self.lbl_wifi)
        self.list_events = QListWidget()
        self.list_events.setMaximumHeight(140)
        form.addRow(tr("Recent events"), self.list_events)
        self.lbl_features = self._value_label()
        form.addRow(tr("Features"), self.lbl_features)
        self.txt_boot = QPlainTextEdit()
        self.txt_boot.setReadOnly(True)
        self.txt_boot.setMaximumHeight(180)
        self.txt_boot.setPlaceholderText(tr("Use “Watch robot boot”, or reboot the robot."))
        form.addRow(tr("Boot log"), self.txt_boot)
        self.advanced_grid, _ = self._action_grid(layout, columns=2)

        self._layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.setWidget(scroll)

        mode = QgsSettings().value(KEY_CLEAN_MODE, "home", type=str)
        self.mode_buttons[mode if mode in CLEAN_MODES else "home"].setChecked(True)
        self._fill_rooms()

    def bind_actions(self, act):
        """Place the plugin's actions in the cards (called once by the plugin)."""
        self.act = act

        def fill(grid, keys, columns, hide_when_disabled=False):
            for i, key in enumerate(k for k in keys if k in act):
                grid.addWidget(self._action_button(act[key], hide_when_disabled),
                               i // columns, i % columns)

        fill(self.robot_grid, ["pause", "stop", "home", "goto", "locate"], 3)
        self.btn_camera = QToolButton()
        self.btn_camera.setText(tr("Camera"))
        self.btn_camera.setIcon(QgsApplication.getThemeIcon("/mIconCamera.svg"))
        self.btn_camera.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_camera.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_camera.clicked.connect(self._toggle_camera)
        self.robot_grid.addWidget(self.btn_camera, 1, 2)

        fill(self.dock_grid, ["empty_dock", "mop_wash", "mop_dry"], 3, hide_when_disabled=True)
        fill(self.map_grid, ["show_map", "live", "edit_restrictions", "send_restrictions"], 2)
        fill(self.advanced_grid, ["watch_boot", "reboot"], 2)
        if "zone" in act:
            self.zone_buttons.addWidget(self._action_button(act["zone"]))
            self.zone_buttons.addWidget(self.btn_clear_zone)
        self._update_clean_button()

    # ======================================================================
    # Clean card
    # ======================================================================

    def _set_mode(self, mode):
        QgsSettings().setValue(KEY_CLEAN_MODE, mode)
        self.rooms_list.setVisible(mode == "rooms")
        self.zone_box.setVisible(mode == "zone")
        self._update_zone_label()
        self._update_clean_button()

    def _mode(self):
        for mode, button in self.mode_buttons.items():
            if button.isChecked():
                return mode
        return "home"

    def set_mode(self, mode):
        if mode in self.mode_buttons:
            self.mode_buttons[mode].setChecked(True)

    def _fill_rooms(self):
        self.rooms_list.blockSignals(True)
        self.rooms_list.clear()
        if not self._segments:
            item = QListWidgetItem(tr("The room list appears once the robot is connected.")
                                   if self.client is None else tr("No rooms on the map yet."))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.rooms_list.addItem(item)
        for seg_id, name in self._segments:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, seg_id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if seg_id in self._checked_rooms
                               else Qt.CheckState.Unchecked)
            self.rooms_list.addItem(item)
        self.rooms_list.blockSignals(False)
        self._update_clean_button()

    def _on_segments(self, segments):
        rows = []
        for seg in segments:
            if not isinstance(seg, dict) or seg.get("id") is None:
                continue
            seg_id = str(seg["id"])
            rows.append((seg_id, seg.get("name") or f"{tr('Room')} {seg_id}"))
        rows.sort(key=lambda r: r[1].lower())
        if rows != self._segments:
            self._segments = rows
            self._checked_rooms &= {r[0] for r in rows}
            self._fill_rooms()

    def _on_room_toggled(self, item):
        seg_id = item.data(Qt.ItemDataRole.UserRole)
        if seg_id is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_rooms.add(seg_id)
        else:
            self._checked_rooms.discard(seg_id)
        self._update_clean_button()

    def set_pending_zone(self, rect):
        """Called by the plugin when a zone was drawn on the map."""
        self._pending_zone = rect
        self.set_mode("zone")
        self._update_zone_label()
        self._update_clean_button()

    def clear_pending_zone(self, *_):
        self._pending_zone = None
        self.zoneCleared.emit()
        self._update_zone_label()
        self._update_clean_button()

    def _update_zone_label(self):
        rect = self._pending_zone
        if rect is None:
            self.lbl_zone.setText(tr("Draw the zone on the map, then click Clean."))
            self.btn_clear_zone.setVisible(False)
        else:
            self.lbl_zone.setText(tr("Zone ready: {w} × {h} m").format(
                w=f"{rect.width() / 100:.1f}", h=f"{rect.height() / 100:.1f}"))
            self.btn_clear_zone.setVisible(True)

    def _update_clean_button(self):
        mode, caps = self._mode(), self.capabilities
        supported = {
            "home": caps is None or "BasicControlCapability" in caps,
            "rooms": caps is None or "MapSegmentationCapability" in caps,
            "zone": caps is None or "ZoneCleaningCapability" in caps,
        }
        for m, button in self.mode_buttons.items():
            button.setEnabled(supported[m])
        self.spin_passes.setEnabled(mode != "home")
        n = len(self._checked_rooms)
        if self.client is None:
            text, ok = tr("Not connected"), False
        elif not supported[mode]:
            text, ok = tr("Not supported by this robot."), False
        elif mode == "home":
            text, ok = tr("Clean the whole home"), True
        elif mode == "rooms":
            ok = n > 0
            text = tr("Clean {n} room(s)").format(n=n) if ok else tr("Select at least one room")
        else:
            ok = self._pending_zone is not None
            text = tr("Clean the zone") if ok else tr("Draw a zone first")
        self.btn_clean.setText(text)
        self.btn_clean.setEnabled(ok)

    def _clean(self, *_):
        client, mode, passes = self.client, self._mode(), self.spin_passes.value()
        if client is None:
            return
        if mode == "home":
            client.basic_control("start")
        elif mode == "rooms" and self._checked_rooms:
            order = [seg_id for seg_id, _name in self._segments if seg_id in self._checked_rooms]
            client.clean_segments(order, iterations=passes)
        elif mode == "zone" and self._pending_zone is not None:
            client.clean_zone_qgis(self._pending_zone, iterations=passes)
            self.clear_pending_zone()

    def _on_preset_chosen(self, capability, index):
        combo = self.preset_combos[capability]
        name = combo.itemData(index)
        if self.client is not None and name:
            self.client.set_preset(capability, name)

    # ======================================================================
    # Camera, boot log
    # ======================================================================

    def _toggle_camera(self, *_):
        self.box_camera.setCollapsed(not self.box_camera.isCollapsed())
        if not self.box_camera.isCollapsed():
            self.widget().ensureWidgetVisible(self.box_camera)

    def _on_camera_light_clicked(self, checked):
        if self.client is not None:
            self.client.set_toggle("CameraLightControlCapability", checked)

    def _on_toggle(self, capability, enabled):
        if capability == "CameraLightControlCapability":
            self.camera_light.setChecked(enabled)   # setChecked does not emit clicked

    def _update_camera(self):
        """Play only while the panel is visible and the Camera section is open."""
        if self.isVisible() and not self.box_camera.isCollapsed():
            if not self.camera.stream.is_running():
                self.camera.play()
        else:
            self.camera.stop()

    def clear_boot_log(self):
        self.txt_boot.clear()

    def add_boot_log(self, line):
        self.txt_boot.appendPlainText(line)
        self.box_advanced.setCollapsed(False)

    def shutdown(self):
        """Called by the plugin on unload."""
        self.camera.shutdown()

    # ======================================================================
    # Client wiring and refresh
    # ======================================================================

    def attach(self, client):
        """Called by the plugin whenever its client is created (or dropped: None)."""
        self.client = client
        self.capabilities = None
        self._reachable = None
        if client is None:
            self._timer.stop()
            self._show_disconnected()
            self.card_dock.setVisible(False)
            self.camera_light.setVisible(False)
            self.lbl_features.setText("—")
            self._robot_error, self._dock_problems = "", []
            self._update_problems()
            self._set_badge(None)
            self._segments = []
            self._fill_rooms()
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
        client.toggleReceived.connect(self._on_toggle)
        client.segmentsReceived.connect(self._on_segments)
        client.mapPeeked.connect(self.update_map)
        client.commandDone.connect(lambda *_: QTimer.singleShot(1500, self.refresh))
        self._robot_text = self._version_text = ""
        client.fetch_robot_info(quiet=True)
        client.fetch_version(quiet=True)
        self._fill_rooms()
        if self.isVisible():
            self.refresh()
            self._timer.start()

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
        if "MapSegmentationCapability" in caps:
            client.fetch_segments()
        if "ConsumableMonitoringCapability" in caps:
            client.fetch_consumables(quiet=True)
        if "CurrentStatisticsCapability" in caps:
            client.fetch_current_statistics(quiet=True)
        if "TotalStatisticsCapability" in caps:
            client.fetch_total_statistics(quiet=True)
        if "WifiConfigurationCapability" in caps:
            client.fetch_wifi(quiet=True)

    # ======================================================================
    # Header
    # ======================================================================

    def _set_badge(self, state):
        """Coloured pill with the robot state, readable at a glance."""
        color = STATE_COLORS.get(state, "#757575")
        text = _label(_state_labels(), state) if state else tr("Unknown")
        self.lbl_badge.setText(text)
        self.lbl_badge.setStyleSheet(
            f"background-color: {color}; color: white; font-weight: bold;"
            "border-radius: 9px; padding: 2px 10px;")

    def _show_disconnected(self):
        self.lbl_connection.setText(f"<span style='color:#9e9e9e'>●</span> {tr('Not connected')}")
        self.lbl_robot.setText("Valetudo")
        self._update_clean_button()

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
        self.lbl_robot.setText(self._robot_text or "Valetudo")
        self.lbl_robot.setToolTip(self._version_text)

    def _update_problems(self):
        lines = ([f"⚠ {self._robot_error}"] if self._robot_error else []) + \
            [f"⚠ {p}" for p in self._dock_problems]
        self.lbl_problems.setText("<br>".join(lines))
        self.lbl_problems.setVisible(bool(lines))

    # ======================================================================
    # Data
    # ======================================================================

    def _on_capabilities(self, capabilities):
        self.capabilities = set(capabilities)
        caps = self.capabilities
        for capability, combo in self.preset_combos.items():
            supported = capability in caps
            _set_row_visible(self._options_form, combo, supported)
            if supported:
                self.client.fetch_presets(capability, quiet=True)
        self.box_maintenance.setVisible(bool(caps & {
            "ConsumableMonitoringCapability", "CurrentStatisticsCapability",
            "TotalStatisticsCapability"}))
        self.camera_light.setVisible("CameraLightControlCapability" in caps)
        if "CameraLightControlCapability" in caps:
            self.client.fetch_toggle("CameraLightControlCapability")
        if "MapSegmentationCapability" in caps:
            self.client.fetch_segments()

        labels = _capability_labels()
        name = lambda c: labels.get(c) or _humanize(c)
        lines = [f"✓ {n}" for n in sorted(name(c) for c in caps)]
        lines += [f"✗ {name(c)}" for c in CORE_CAPABILITIES if c not in caps]
        self.lbl_features.setText("\n".join(lines))
        self._update_clean_button()
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
        attachments, components = [], {}
        dock_value, error_text, state = None, "", None
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

        self.lbl_dock.setText(_label(states, dock_value))
        self.lbl_attachments.setText("   ".join(attachments) or "—")
        self._robot_error = error_text
        self._update_dock_components(components)
        self._update_problems()
        self._timer.setInterval(ACTIVE_INTERVAL_MS if state in ACTIVE_STATES
                                else IDLE_INTERVAL_MS)

    def _update_dock_components(self, components):
        """One row per dock component; problems also go to the banner."""
        form = self._dock_form
        while form.rowCount():
            form.removeRow(0)
        names, values, advice = (dock_component_labels(), dock_component_values(),
                                 dock_component_advice())
        self._dock_problems = []
        order = DOCK_COMPONENT_ORDER + sorted(c for c in components
                                              if c not in DOCK_COMPONENT_ORDER)
        for component in order:
            if component not in components:
                continue
            value = components[component]
            name = _label(names, component)
            label = QLabel(_label(values, value))
            if value in DOCK_COMPONENT_PROBLEMS:
                label.setStyleSheet("color: #c62828; font-weight: bold;")
                label.setText(f"{label.text()} · {advice[value]}")
                self._dock_problems.append(f"{name}: {_label(values, value)}. {advice[value]}")
            elif value == "ok":
                label.setStyleSheet("color: #2e7d32;")
            form.addRow(name, label)
        has_dock_actions = any(self.act.get(k) is not None and self.act[k].isEnabled()
                               for k in ("empty_dock", "mop_wash", "mop_dry"))
        self.card_dock.setVisible(bool(components) or has_dock_actions)

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
        self.lbl_cur_area.setText(_format_area_cm2(stats["area"]) if "area" in stats else "")

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
