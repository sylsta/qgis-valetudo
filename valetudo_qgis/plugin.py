"""Main plugin class: actions, toolbar, menu, panels, map tools.

Every command exists once, as a QAction in ``self.act``. The toolbar, the
Valetudo menu and the status panel all show these same objects, so their
enabled/checked state can never disagree.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsProject

from .camera_panel import CameraPanel
from .client import ValetudoClient
from .i18n import install_translator, remove_translator, tr
from .map_layers import ValetudoLayers, layer_names
from .map_tools import GoToMapTool, ZoneMapTool
from .settings_dialog import SettingsDialog, load_settings
from .status_panel import ValetudoPanel

MIN_ZONE_SIZE_CM = 20  # smaller rectangles are treated as accidental clicks

# Valetudo capability required by each action (absent: always available).
REQUIRED_CAPABILITY = {
    "start": "BasicControlCapability",
    "pause": "BasicControlCapability",
    "stop": "BasicControlCapability",
    "home": "BasicControlCapability",
    "clean_rooms": "MapSegmentationCapability",
    "zone": "ZoneCleaningCapability",
    "goto": "GoToLocationCapability",
}

# Layout, None = separator. The panel shows the robot and map rows.
ROBOT_ROW = ["start", "pause", "stop", "home"]
MAP_ROW = ["show_map", "live", None, "clean_rooms", "zone", "goto"]
TOOLBAR = ["show_map", "live", None] + ROBOT_ROW + [None, "clean_rooms", "zone", "goto",
                                                    None, "status_panel", "camera"]
MENU = ["settings", "status_panel", "camera"] + MAP_ROW + ROBOT_ROW


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
        self.zone_tool = self.goto_tool = None
        self.panel = self.camera_panel = None

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

        # Panels: hidden by default; their toggle actions follow their visibility.
        self.panel = ValetudoPanel(win)
        self.panel.clientRequested.connect(lambda: self._get_client(prompt=False))
        self.camera_panel = CameraPanel(win)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.panel)
        self.iface.addTabifiedDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                         self.camera_panel, [self.panel.objectName()])
        for dock in (self.panel, self.camera_panel):
            dock.hide()
        self.act["status_panel"] = self.panel.toggleViewAction()
        self.act["status_panel"].setText(tr("Status panel"))
        self.act["camera"] = self.camera_panel.toggleViewAction()
        self.act["camera"].setText(tr("Camera"))

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
        self.panel.set_actions([self.act[k] for k in ROBOT_ROW],
                               [self.act[k] if k else None for k in MAP_ROW])

        canvas = self.iface.mapCanvas()
        self.zone_tool = ZoneMapTool(canvas)
        self.zone_tool.setAction(self.act["zone"])  # QGIS unchecks it when another tool is picked
        self.zone_tool.zoneDrawn.connect(self._on_zone)
        self.goto_tool = GoToMapTool(canvas)
        self.goto_tool.setAction(self.act["goto"])
        self.goto_tool.pointPicked.connect(self._on_goto)

        self._apply_capabilities()

    def unload(self):
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
        if self.camera_panel is not None:
            self.camera_panel.shutdown()
        for dock in (self.panel, self.camera_panel):
            if dock is not None:
                self.iface.removeDockWidget(dock)
                dock.deleteLater()
        self.panel = self.camera_panel = None
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
        self.panel.attach(self.client)
        self.client.fetch_capabilities()
        return self.client

    def _drop_client(self):
        if self.client:
            self.client.stop_polling()
            self.client.deleteLater()
            self.client = None
            if self.panel is not None:
                self.panel.attach(None)
        self._reachable = None
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
        if ok and previous is False:
            self._message(tr("Robot reachable again."), Qgis.MessageLevel.Success)
        elif not ok and previous is not False:
            self._message(tr("Robot unreachable."), Qgis.MessageLevel.Warning)

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
        if self.camera_panel is not None:
            self.camera_panel.restart_if_running()
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
        label = command_labels().get(key, key)
        self._message(tr("Command “{command}” sent.").format(command=label),
                      Qgis.MessageLevel.Success)

    def _message(self, text, level):
        self.iface.messageBar().pushMessage("Valetudo", text, level=level, duration=4)
