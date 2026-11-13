"""Main plugin class: actions, toolbar, menu, panels, map tools.

Every command exists once, as a QAction in ``self.act``. The toolbar, the
Valetudo menu and the status panel all show these same objects, so their
enabled/checked state can never disagree.
"""

from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsMessageLog, QgsProject

from .boot_monitor import BootMonitor
from .client import ValetudoClient
from .i18n import install_translator, remove_translator, tr
from .map_layers import ValetudoLayers, layer_names
from .map_tools import GoToMapTool, ZoneMapTool
from .restrictions import RestrictionLayers, layer_names as restriction_layer_names
from .settings_dialog import SettingsDialog, load_settings
from .ssh_reboot import SshReboot
from .status_panel import (
    DOCK_COMPONENT_PROBLEMS,
    state_label as dock_state_label,
    ValetudoPanel,
    dock_component_advice,
    dock_component_labels,
    dock_component_values,
)

MIN_ZONE_SIZE_CM = 20  # smaller rectangles are treated as accidental clicks
WATCH_INTERVAL_MS = 60_000  # background check of the dock station, panel open or not

# Valetudo capability required by each action (absent: always available).
REQUIRED_CAPABILITY = {
    "start": "BasicControlCapability",
    "pause": "BasicControlCapability",
    "stop": "BasicControlCapability",
    "home": "BasicControlCapability",
    "clean_rooms": "MapSegmentationCapability",
    "zone": "ZoneCleaningCapability",
    "goto": "GoToLocationCapability",
    "locate": "LocateCapability",
    "empty_dock": "AutoEmptyDockManualTriggerCapability",
    "mop_wash": "MopDockCleanManualTriggerCapability",
    "mop_dry": "MopDockDryManualTriggerCapability",
    "edit_restrictions": "CombinedVirtualRestrictionsCapability",
    "send_restrictions": "CombinedVirtualRestrictionsCapability",
}

RESTRICTIONS_ROW = ["edit_restrictions", "send_restrictions"]   # menu and panel list

DOCK_ROW = ["locate", "empty_dock", "mop_wash", "mop_dry"]   # menu and panel list

# Layout, None = separator. The panel shows the robot and map rows.
ROBOT_ROW = ["start", "pause", "stop", "home"]
MAP_ROW = ["show_map", "live", None, "clean_rooms", "zone", "goto"]
TOOLBAR = ["show_map", "live", None] + ROBOT_ROW + [None, "clean_rooms", "zone", "goto",
                                                    None, "status_panel"]
MENU = (["settings", "status_panel"] + MAP_ROW + [None] + RESTRICTIONS_ROW + [None] + ROBOT_ROW
        + [None] + DOCK_ROW + [None, "watch_boot", "reboot"])
PANEL_SYSTEM_ROW = ["watch_boot", "reboot"]   # menu and panel command list, not the toolbar
LOG_TAG = "Valetudo"   # tab of the QGIS "Log Messages" panel


def command_labels():
    """Translated labels for the command keys emitted by ValetudoClient."""
    return {
        "start": tr("Start"),
        "pause": tr("Pause"),
        "stop": tr("Stop"),
        "home": tr("Return to dock"),
        "segments": tr("Clean rooms"),
        "zone": tr("Clean zone"),
        "goto": tr("Go to"),
        "fan_speed": tr("Fan speed"),
        "water_usage": tr("Water usage"),
        "camera_light": tr("Camera light"),
        "locate": tr("Locate robot (beep)"),
        "empty_dock": tr("Empty dust bin"),
        "mop_wash": tr("Wash mops"),
        "mop_dry": tr("Dry mops"),
        "restrictions": tr("Send no-go zones and walls"),
    }


class ValetudoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.translator = install_translator()  # before any tr() call
        self.menu = tr("&Valetudo")
        self.act = {}            # key -> QAction
        self.toolbar = None
        self.client = None
        self.capabilities = None  # None = unknown (no robot contacted yet)
        self._reachable = None
        self.layers = ValetudoLayers(QgsProject.instance())
        self.restrictions = RestrictionLayers(QgsProject.instance())
        self.zone_tool = self.goto_tool = None
        self.panel = None
        self._dock_state = {}  # dock component -> last known value
        self._watch = None
        self._reboot = None

    # ------------------------------------------------------------------
    # GUI setup / teardown
    # ------------------------------------------------------------------

    def _new_action(self, key, text, slot=None, checkable=False):
        action = QAction(text, self.iface.mainWindow())
        action.setCheckable(checkable)
        if slot is not None:
            action.triggered.connect(slot)
        self.act[key] = action
        return action

    def initGui(self):
        labels = command_labels()
        win = self.iface.mainWindow()

        self._new_action("settings", tr("Settings…"), self.configure)
        self._new_action("show_map", tr("Show map"), self.show_map)
        self._new_action("live", tr("Live tracking"), self.toggle_live, checkable=True)
        for key in ROBOT_ROW:
            self._new_action(key, labels[key], lambda *_, k=key: self.control(k))
        self._new_action("clean_rooms", tr("Clean selected rooms"), self.clean_selected)
        self._new_action("zone", labels["zone"], self.activate_zone_tool, checkable=True)
        self._new_action("goto", tr("Go here"), self.activate_goto_tool, checkable=True)
        dock_labels = {"locate": tr("Locate robot (beep)"), "empty_dock": tr("Empty dust bin"),
                       "mop_wash": tr("Wash mops"), "mop_dry": tr("Dry mops")}
        for key in DOCK_ROW:
            self._new_action(key, dock_labels[key], lambda *_, k=key: self.simple_command(k))
        self._new_action("edit_restrictions", tr("Edit no-go zones and walls"),
                         self.edit_restrictions)
        self._new_action("send_restrictions", tr("Send no-go zones and walls to robot…"),
                         self.send_restrictions)
        self._new_action("watch_boot", tr("Watch robot boot"), self.watch_boot)
        self._new_action("reboot", tr("Reboot robot (SSH)…"), self.reboot_robot)
        self.boot = BootMonitor()
        self.boot.stage.connect(self._on_boot_stage)
        self.boot.logTail.connect(self._on_boot_log_tail)
        self._reboot = SshReboot()
        self._reboot.finished.connect(self._on_reboot_finished)

        # Panels: hidden by default; their toggle actions follow their visibility.
        self.panel = ValetudoPanel(win)
        self.panel.clientRequested.connect(lambda: self._get_client(prompt=False))
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.panel)
        self.panel.hide()
        self.act["status_panel"] = self.panel.toggleViewAction()
        self.act["status_panel"].setText(tr("Status panel"))

        self.toolbar = self.iface.addToolBar("Valetudo")
        self.toolbar.setObjectName("ValetudoToolbar")
        for key in TOOLBAR:
            if key is None:
                self.toolbar.addSeparator()
            else:
                self.toolbar.addAction(self.act[key])
        for key in MENU:
            if key is not None:
                self.iface.addPluginToMenu(self.menu, self.act[key])
        # Reboot asks for confirmation, so it can safely sit in the same list.
        self.panel.set_actions(
            [self.act[k] for k in ROBOT_ROW + MAP_ROW + RESTRICTIONS_ROW + DOCK_ROW
             + PANEL_SYSTEM_ROW if k])

        canvas = self.iface.mapCanvas()
        self.zone_tool = ZoneMapTool(canvas)
        self.zone_tool.setAction(self.act["zone"])  # QGIS unchecks it when another tool is picked
        self.zone_tool.zoneDrawn.connect(self._on_zone)
        self.goto_tool = GoToMapTool(canvas)
        self.goto_tool.setAction(self.act["goto"])
        self.goto_tool.pointPicked.connect(self._on_goto)

        self._apply_capabilities()

        # Background watch: starts silently if a robot is configured (never prompts).
        self._watch = QTimer()
        self._watch.setInterval(WATCH_INTERVAL_MS)
        self._watch.timeout.connect(self._watch_tick)
        self._watch.start()
        QTimer.singleShot(0, self._watch_tick)

    def unload(self):
        if getattr(self, "boot", None) is not None:
            self.boot.stop()
        if self._watch is not None:
            self._watch.stop()
            self._watch = None
        self._drop_client()
        canvas = self.iface.mapCanvas()
        for tool in (self.zone_tool, self.goto_tool):
            if tool is not None and canvas.mapTool() is tool:
                canvas.unsetMapTool(tool)
        if self.zone_tool is not None:
            self.zone_tool.cleanup()
        self.zone_tool = self.goto_tool = None

        for key in MENU:
            if key is not None and key in self.act:
                self.iface.removePluginMenu(self.menu, self.act[key])
        if self.panel is not None:
            self.panel.shutdown()
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        self.act = {}
        remove_translator(self.translator)
        self.translator = None

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _get_client(self, prompt=True):
        """Return the client, creating it if needed.

        prompt=False never opens the settings dialog (e.g. a panel restored at
        QGIS startup); it just returns None when no robot is configured.
        """
        if self.client:
            return self.client
        cfg = load_settings()
        if not cfg["url"]:
            if not prompt or not self.configure():
                return None
            cfg = load_settings()
            if not cfg["url"]:
                return None
        self.client = ValetudoClient(cfg["url"], cfg["authcfg"])
        self.client.error.connect(self._on_error)
        self.client.reachable.connect(self._on_reachable)
        self.client.commandDone.connect(self._on_command_done)
        self.client.mapReceived.connect(self._on_map)
        self.client.capabilitiesReceived.connect(self._on_capabilities)
        self.client.restrictionsReceived.connect(self._on_restrictions)
        self.client.attributesReceived.connect(self._on_attributes)
        self.panel.attach(self.client)
        self.client.fetch_capabilities(quiet=True)   # retried once the robot answers
        return self.client

    def _drop_client(self):
        if self.client:
            self.client.stop_polling()
            self.client.deleteLater()
            self.client = None
            if self.panel is not None:
                self.panel.attach(None)
        self._reachable = None
        self._dock_state = {}
        if "live" in self.act:
            self.act["live"].setChecked(False)
        self._on_capabilities(None)  # unknown robot: re-enable everything

    def _on_capabilities(self, capabilities):
        self.capabilities = set(capabilities) if capabilities is not None else None
        self._apply_capabilities()

    def _apply_capabilities(self):
        """Grey out actions the robot does not support (all enabled while unknown)."""
        for key, capability in REQUIRED_CAPABILITY.items():
            action = self.act.get(key)
            if action is None:
                continue
            ok = self.capabilities is None or capability in self.capabilities
            if not ok and action.isChecked():
                self.iface.actionPan().trigger()   # leave a map tool that became unsupported
            action.setEnabled(ok)
            action.setToolTip(action.text() if ok else
                              f"{action.text()} — {tr('Not supported by this robot.')}")

    def _on_reachable(self, ok):
        """Report only changes, so an offline robot does not flood the message bar."""
        previous, self._reachable = self._reachable, ok
        if ok and self.capabilities is None and self.client is not None:
            self.client.fetch_capabilities(quiet=True)   # robot was off when we connected
        if ok and previous is False:
            self._message(tr("Robot reachable again."), Qgis.MessageLevel.Success)
        elif not ok and previous is True:
            self._message(tr("Robot unreachable."), Qgis.MessageLevel.Warning)

    # -- dock station watch -----------------------------------------------------

    def _watch_tick(self):
        if not load_settings()["notify_dock"]:
            return
        client = self._get_client(prompt=False)
        if client is not None:
            client.fetch_attributes(quiet=True)

    def _on_attributes(self, attributes):
        """Notify when a dock component becomes empty/full/missing, or OK again."""
        if not load_settings()["notify_dock"]:
            return
        names, values, advice = (dock_component_labels(), dock_component_values(),
                                 dock_component_advice())
        for attr in attributes:
            if attr.get("__class") != "DockComponentStateAttribute":
                continue
            component, value = attr.get("type"), attr.get("value")
            previous = self._dock_state.get(component)
            self._dock_state[component] = value
            if value == previous:
                continue
            name = names.get(component, component)
            if value in DOCK_COMPONENT_PROBLEMS:
                text = tr("{component}: {state}. {advice}").format(
                    component=name, state=values.get(value, value), advice=advice[value])
                self.iface.messageBar().pushMessage(
                    "Valetudo", text, level=Qgis.MessageLevel.Warning, duration=0)
            elif value == "ok" and previous in DOCK_COMPONENT_PROBLEMS:
                self._message(tr("{component}: OK again.").format(component=name),
                              Qgis.MessageLevel.Success)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def configure(self, *_):
        dlg = SettingsDialog(self.iface.mainWindow())
        if not dlg.exec():
            return False
        self._drop_client()  # settings may have changed
        if self.panel is not None and self.panel.isVisible():
            self._get_client(prompt=False)   # reconnect the visible panel at once
        if self.panel is not None:
            self.panel.camera.restart_if_running()
        return True

    def show_map(self, *_):
        client = self._get_client()
        if client:
            client.fetch_map()

    def toggle_live(self, checked):
        client = self._get_client()
        if not client:
            self.act["live"].setChecked(False)
            return
        if checked:
            client.start_polling(load_settings()["interval_s"] * 1000)
        else:
            client.stop_polling()

    def control(self, action):
        client = self._get_client()
        if client:
            client.basic_control(action)

    # -- no-go zones and virtual walls ---------------------------------------------

    def edit_restrictions(self, *_):
        """Load the robot's restrictions into two editable layers."""
        client = self._get_client()
        if not client:
            return
        if not self.layers.exists():
            client.fetch_map()   # show the map too: zones are drawn on top of it
        client.fetch_restrictions()

    def _on_restrictions(self, data):
        if not self.layers.exists():
            self._prepare_project()
        zones, _walls = self.restrictions.load(data)
        zones.startEditing()
        self.iface.setActiveLayer(zones)
        names = restriction_layer_names()
        self.iface.messageBar().pushMessage(
            "Valetudo",
            tr("Edit the “{zones}” and “{walls}” layers (polygons and lines), then use "
               "“Send no-go zones and walls to robot…”.").format(
                zones=names["zones"], walls=names["walls"]),
            level=Qgis.MessageLevel.Info, duration=15)

    def send_restrictions(self, *_):
        if not self.restrictions.exists():
            self._message(tr("Use “Edit no-go zones and walls” first."), Qgis.MessageLevel.Info)
            return
        client = self._get_client()
        if not client:
            return
        payload, warnings = self.restrictions.to_payload()
        text = tr("Send {zones} no-go zone(s) and {walls} virtual wall(s) to the robot?\n"
                  "They replace all the restrictions currently on the robot.").format(
            zones=len(payload["restrictedZones"]), walls=len(payload["virtualWalls"]))
        notes = []
        if warnings["zones_approximated"]:
            notes.append(tr("{n} zone(s) without exactly 4 corners are replaced by their "
                            "bounding rectangle.").format(n=warnings["zones_approximated"]))
        if warnings["walls_simplified"]:
            notes.append(tr("{n} wall(s) with more than 2 points become straight "
                            "lines between their ends.").format(n=warnings["walls_simplified"]))
        if warnings["skipped"]:
            notes.append(tr("{n} empty or invalid shape(s) are ignored.").format(
                n=warnings["skipped"]))
        if notes:
            text += "\n\n" + "\n".join(notes)
        answer = QMessageBox.question(
            self.iface.mainWindow(), tr("Send no-go zones and walls"), text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            client.set_restrictions(payload)

    def simple_command(self, key):
        client = self._get_client()
        if client:
            client.simple_command(key)

    def clean_selected(self, *_):
        ids = self.layers.selected_segment_ids()
        if not ids:
            self._message(
                tr("Select one or more rooms in the “{layer}” layer.").format(
                    layer=layer_names()["rooms"]),
                Qgis.MessageLevel.Info)
            return
        client = self._get_client()
        if client:
            client.clean_segments(ids)

    # -- map tools --------------------------------------------------------------

    def _activate_tool(self, tool, key, checked, hint):
        canvas = self.iface.mapCanvas()
        action = self.act[key]
        if not checked:  # clicking the active tool again turns it off
            if canvas.mapTool() is tool:
                self.iface.actionPan().trigger()
            return
        if not self._get_client():
            action.setChecked(False)
            return
        if not self.layers.exists():
            # Coordinates are only meaningful in the robot's frame, i.e. once its map is shown.
            action.setChecked(False)
            self._message(tr("Show the map first."), Qgis.MessageLevel.Info)
            return
        canvas.setMapTool(tool)
        self._message(hint, Qgis.MessageLevel.Info)

    def activate_zone_tool(self, checked):
        self._activate_tool(self.zone_tool, "zone", checked,
                            tr("Drag a rectangle on the map to clean that zone."))

    def activate_goto_tool(self, checked):
        self._activate_tool(self.goto_tool, "goto", checked,
                            tr("Click on the map to send the robot there."))

    def _on_zone(self, rect):
        self.iface.actionPan().trigger()  # one zone per activation: no accidental resend
        if rect.width() < MIN_ZONE_SIZE_CM or rect.height() < MIN_ZONE_SIZE_CM:
            self._message(tr("Zone too small."), Qgis.MessageLevel.Warning)
            return
        client = self._get_client()
        if client:
            client.clean_zone_qgis(rect)

    def _on_goto(self, point):
        self.iface.actionPan().trigger()
        client = self._get_client()
        if client:
            client.go_to_qgis(point)

    # -- reboot over SSH --------------------------------------------------------

    def reboot_robot(self, *_):
        cfg = load_settings()
        if not cfg["url"] and not self.configure():
            return
        cfg = load_settings()
        host = QUrl(cfg["url"]).host()
        if not host:
            return
        answer = QMessageBox.question(
            self.iface.mainWindow(), tr("Reboot robot"),
            tr("Reboot the robot now over SSH ({user}@{host})?\n\n"
               "Any cleaning in progress will be interrupted. The robot will be "
               "unreachable for a minute or two.").format(user=cfg["ssh_user"], host=host),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.act["reboot"].setEnabled(False)   # one reboot at a time
        self._reboot.start(host, cfg["ssh_user"], cfg["ssh_port"])

    def _on_reboot_finished(self, result, detail):
        if "reboot" in self.act:
            self.act["reboot"].setEnabled(True)
        if result == "sent":
            self._message(tr("Reboot command sent. The robot will be back in a minute or two."),
                          Qgis.MessageLevel.Success)
            self._start_boot_monitor(expect_offline=True)
        elif result == "ssh_missing":
            self._message(tr("ssh not found: install an SSH client."), Qgis.MessageLevel.Critical)
        else:
            self.iface.messageBar().pushMessage(
                "Valetudo", tr("SSH reboot failed: {detail}").format(detail=detail),
                level=Qgis.MessageLevel.Critical, duration=0)

    # -- boot monitor -------------------------------------------------------------

    def watch_boot(self, *_):
        """Follow a boot started elsewhere (power button…), or check that all is up."""
        self._start_boot_monitor(expect_offline=False)

    def _start_boot_monitor(self, expect_offline):
        client = self._get_client()
        if not client:
            return
        cfg = load_settings()
        host = QUrl(cfg["url"]).host()
        camera_port = QUrl(cfg["camera_url"]).port() if cfg["camera_url"] else -1
        self.panel.clear_boot_log()
        self.boot.start(client, host, cfg["ssh_port"],
                        camera_port if camera_port > 0 else None, expect_offline)

    def _boot_text(self, key, detail):
        if key == "started":
            return tr("Watching the robot boot…")
        if key == "offline":
            return tr("Robot went offline (rebooting).")
        if key == "no_offline":
            return tr("The robot did not go offline: the reboot may not have happened.")
        if key == "network_up":
            return tr("Network up: the robot's SSH port answers (system booted).")
        if key == "valetudo_up":
            return tr("Valetudo answers (version {version}).").format(version=detail)
        if key == "robot_ready":
            state, _, battery = detail.partition(";")
            state = dock_state_label(state)
            return tr("Robot firmware ready: {state}, battery {battery} %.").format(
                state=state, battery=battery)
        if key == "camera_up":
            return tr("Camera stream available.")
        if key == "camera_missing":
            return tr("Camera stream still not available after {seconds} s.").format(
                seconds=detail)
        if key == "done":
            return tr("Robot fully back after {seconds} s.").format(seconds=detail)
        if key == "timeout":
            return tr("The robot is still not back after {minutes} min.").format(minutes=detail)
        return key

    def _on_boot_stage(self, key, seconds, detail):
        text = self._boot_text(key, detail)
        line = f"[+{seconds:5.1f} s] {text}"
        problem = key in ("no_offline", "camera_missing", "timeout")
        QgsMessageLog.logMessage(line, LOG_TAG, Qgis.MessageLevel.Warning if problem
                                 else Qgis.MessageLevel.Info)
        self.panel.add_boot_log(line)
        if key == "done":
            self._message(text, Qgis.MessageLevel.Success)
        elif problem:
            self._message(text, Qgis.MessageLevel.Warning)

    def _on_boot_log_tail(self, lines):
        if not lines:
            note = tr("Valetudo's log is not available.")
            QgsMessageLog.logMessage(note, LOG_TAG, Qgis.MessageLevel.Info)
            self.panel.add_boot_log(note)
            return
        header = tr("End of Valetudo's log:")
        QgsMessageLog.logMessage(header + "\n" + "\n".join(lines), LOG_TAG,
                                 Qgis.MessageLevel.Info)
        self.panel.add_boot_log(header)
        for line in lines:
            self.panel.add_boot_log("  " + line)

    # ------------------------------------------------------------------
    # Incoming data
    # ------------------------------------------------------------------

    def _on_map(self, map_json):
        self.panel.update_map(map_json)
        if not self.layers.exists():
            self._prepare_project()
        if self.layers.update(map_json):
            canvas = self.iface.mapCanvas()
            canvas.setExtent(self.layers.layer("rooms").extent())
            canvas.refresh()

    def _prepare_project(self):
        """Switch the project to "No CRS": we work in the robot's own frame."""
        project = QgsProject.instance()
        if project.crs().isValid():
            project.setCrs(QgsCoordinateReferenceSystem())
            self._message(tr("Project switched to “No CRS” (robot's local frame, in cm)."),
                          Qgis.MessageLevel.Info)

    def _on_error(self, msg):
        self._message(msg, Qgis.MessageLevel.Warning)

    def _on_command_done(self, key):
        if key == "restrictions" and self.client is not None:
            self.client.fetch_map()          # show what the robot now uses
            self.client.fetch_restrictions()  # and reload the editable layers from it
        label = command_labels().get(key, key)
        self._message(tr("Command “{command}” sent.").format(command=label),
                      Qgis.MessageLevel.Success)

    def _message(self, text, level):
        self.iface.messageBar().pushMessage("Valetudo", text, level=level, duration=4)
