"""Classe principale du plugin : barre d'outils, menu, liaison client <-> couches."""

from qgis.PyQt.QtWidgets import QAction
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsProject

from .client import ValetudoClient
from .map_layers import ValetudoLayers
from .settings_dialog import SettingsDialog, load_settings

MENU = "&Valetudo"


class ValetudoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.toolbar = None
        self.client = None
        self.layers = ValetudoLayers(QgsProject.instance())

    # ------------------------------------------------------------------
    # Installation / désinstallation dans l'interface
    # ------------------------------------------------------------------

    def initGui(self):
        self.toolbar = self.iface.addToolBar("Valetudo")
        self.toolbar.setObjectName("ValetudoToolbar")

        self._add("Paramètres…", self.configure, toolbar=False)
        self._add("Afficher la carte", self.show_map)
        self.act_live = self._add("Suivi en direct", self.toggle_live, checkable=True)
        self.toolbar.addSeparator()
        self._add("Démarrer", lambda *_: self.control("start"))
        self._add("Pause", lambda *_: self.control("pause"))
        self._add("Stop", lambda *_: self.control("stop"))
        self._add("Retour base", lambda *_: self.control("home"))
        self.toolbar.addSeparator()
        self._add("Nettoyer les pièces sélectionnées", self.clean_selected)

    def _add(self, text, slot, checkable=False, toolbar=True):
        action = QAction(text, self.iface.mainWindow())
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        if toolbar:
            self.toolbar.addAction(action)
        self.iface.addPluginToMenu(MENU, action)
        self.actions.append(action)
        return action

    def unload(self):
        self._drop_client()
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
        self.actions = []
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _get_client(self):
        """Renvoie le client, en le créant si besoin. None si pas configuré."""
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
            self._drop_client()  # les paramètres ont pu changer
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
            self._message("Sélectionne une ou plusieurs pièces dans la couche "
                          "« Valetudo - carte ».", Qgis.MessageLevel.Info)
            return
        client = self._get_client()
        if client:
            client.clean_segments(ids)

    # ------------------------------------------------------------------
    # Réception des données
    # ------------------------------------------------------------------

    def _on_map(self, map_json):
        first_time = not self.layers.exists()
        if first_time:
            self._prepare_project()
        created = self.layers.update(map_json)
        if created:
            canvas = self.iface.mapCanvas()
            canvas.setExtent(self.layers.layer("rooms").extent())
            canvas.refresh()

    def _prepare_project(self):
        """Passe le projet en « Pas de SCR » : on travaille dans le repère du robot."""
        project = QgsProject.instance()
        if project.crs().isValid():
            project.setCrs(QgsCoordinateReferenceSystem())
            self._message("Projet passé en « Pas de SCR » (repère local du robot, en cm).",
                          Qgis.MessageLevel.Info)

    def _on_error(self, msg):
        self._message(msg, Qgis.MessageLevel.Warning)

    def _on_command_done(self, label):
        self._message(f"Commande « {label} » envoyée.", Qgis.MessageLevel.Success)

    def _message(self, text, level):
        self.iface.messageBar().pushMessage("Valetudo", text, level=level, duration=4)
