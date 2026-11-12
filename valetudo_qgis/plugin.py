"""Main plugin class: toolbar, menu, wiring between the client and the layers."""

from qgis.PyQt.QtWidgets import QAction
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsProject

from .client import ValetudoClient
from .i18n import install_translator, remove_translator, tr
from .map_layers import ValetudoLayers, layer_names
from .settings_dialog import SettingsDialog, load_settings


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
    }


class ValetudoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.translator = install_translator()  # before any tr() call
        self.menu = tr("&Valetudo")
        self.actions = []
        self.toolbar = None
        self.client = None
        self.layers = ValetudoLayers(QgsProject.instance())

    # ------------------------------------------------------------------
    # GUI setup / teardown
    # ------------------------------------------------------------------

    def initGui(self):
        labels = command_labels()
        self.toolbar = self.iface.addToolBar("Valetudo")
        self.toolbar.setObjectName("ValetudoToolbar")

        self._add(tr("Settings…"), self.configure, toolbar=False)
        self._add(tr("Show map"), self.show_map)
        self.act_live = self._add(tr("Live tracking"), self.toggle_live, checkable=True)
        self.toolbar.addSeparator()
        for key in ("start", "pause", "stop", "home"):
            self._add(labels[key], lambda *_, k=key: self.control(k))
        self.toolbar.addSeparator()
        self._add(tr("Clean selected rooms"), self.clean_selected)

    def _add(self, text, slot, checkable=False, toolbar=True):
        action = QAction(text, self.iface.mainWindow())
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        if toolbar:
            self.toolbar.addAction(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def unload(self):
        self._drop_client()
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
        self.actions = []
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        remove_translator(self.translator)
        self.translator = None

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _get_client(self):
        """Return the client, creating it if needed. None if not configured."""
        if self.client:
            return self.client
        cfg = load_settings()
        if not cfg["url"]:
            if not self.configure():
                return None
            cfg = load_settings()
            if not cfg["url"]:
                return None
        self.client = ValetudoClient(cfg["url"], cfg["authcfg"])
        self.client.error.connect(self._on_error)
        self.client.commandDone.connect(self._on_command_done)
        self.client.mapReceived.connect(self._on_map)
        return self.client

    def _drop_client(self):
        if self.client:
            self.client.stop_polling()
            self.client.deleteLater()
            self.client = None
        if hasattr(self, "act_live"):
            self.act_live.setChecked(False)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def configure(self, *_):
        dlg = SettingsDialog(self.iface.mainWindow())
        if dlg.exec():
            self._drop_client()  # settings may have changed
            return True
        return False

    def show_map(self, *_):
        client = self._get_client()
        if client:
            client.fetch_map()

    def toggle_live(self, checked):
        client = self._get_client()
        if not client:
            self.act_live.setChecked(False)
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

    # ------------------------------------------------------------------
    # Incoming data
    # ------------------------------------------------------------------

    def _on_map(self, map_json):
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
