"""Asynchronous HTTP client for the Valetudo REST API (/api/v2).

All requests go through QgsNetworkAccessManager: they never block the UI and
they honour the QGIS proxy settings. Results are broadcast with Qt signals.

This module contains no user-facing text: commandDone emits a command key
(see COMMAND_KEYS) that the UI layer turns into a translated label.
"""

import json

from qgis.PyQt.QtCore import QObject, QUrl, QTimer, QByteArray, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsApplication, QgsNetworkAccessManager, QgsPointXY

from .coords import q2v

COMMAND_KEYS = ("start", "pause", "stop", "home", "segments", "zone", "goto",
                "fan_speed", "water_usage")

# Preset capabilities handled by set_preset(), with their command key.
PRESET_CAPABILITIES = {
    "FanSpeedControlCapability": "fan_speed",
    "WaterUsageControlCapability": "water_usage",
}


class ValetudoClient(QObject):
    robotInfoReceived = pyqtSignal(dict)
    versionReceived = pyqtSignal(dict)
    capabilitiesReceived = pyqtSignal(list)
    attributesReceived = pyqtSignal(list)
    mapReceived = pyqtSignal(dict)
    consumablesReceived = pyqtSignal(list)
    currentStatisticsReceived = pyqtSignal(list)
    totalStatisticsReceived = pyqtSignal(list)
    wifiReceived = pyqtSignal(dict)
    eventsReceived = pyqtSignal(list)
    presetsReceived = pyqtSignal(str, list)   # capability name, preset names
    commandDone = pyqtSignal(str)
    reachable = pyqtSignal(bool)              # emitted after every request
    error = pyqtSignal(str)

    def __init__(self, base_url, authcfg="", parent=None):
        super().__init__(parent)
        self.base = base_url.rstrip("/") + "/api/v2"
        self.authcfg = authcfg  # QGIS authentication manager config id
        self.nam = QgsNetworkAccessManager.instance()
        self._replies = set()   # keeps replies alive until they finish
        self._map_pending = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.fetch_map(quiet=True))

    # -- plumbing -----------------------------------------------------------

    def _request(self, path):
        req = QNetworkRequest(QUrl(self.base + path))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if self.authcfg:
            res = QgsApplication.authManager().updateNetworkRequest(req, self.authcfg)
            if isinstance(res, tuple):  # depends on the PyQGIS version
                req = res[1]
        return req

    def _get(self, path, callback, on_finish=None, quiet=False):
        self._track(self.nam.get(self._request(path)), callback, on_finish, quiet)

    def _put(self, path, payload, callback=None):
        body = QByteArray(json.dumps(payload).encode())
        self._track(self.nam.put(self._request(path), body), callback)

    def _track(self, reply, callback, on_finish=None, quiet=False):
        """quiet=True: background request, failures only emit reachable(False)."""
        self._replies.add(reply)

        def finished():
            self._replies.discard(reply)
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    self.reachable.emit(False)
                    if not quiet:
                        # errorString() is already localised by Qt
                        self.error.emit(f"{reply.url().toString()}: {reply.errorString()}")
                    return
                self.reachable.emit(True)
                raw = bytes(reply.readAll())
                data = json.loads(raw) if raw.strip() else None
                if callback:
                    callback(data)
            except Exception as exc:
                self.error.emit(f"{type(exc).__name__}: {exc}")
            finally:
                if on_finish:
                    on_finish()
                reply.deleteLater()

        reply.finished.connect(finished)

    @staticmethod
    def _as_list(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values())
        return []

    # -- reading ------------------------------------------------------------

    def fetch_robot_info(self, quiet=False):
        self._get("/robot", lambda d: self.robotInfoReceived.emit(d or {}), quiet=quiet)

    def fetch_version(self, quiet=False):
        self._get("/valetudo/version", lambda d: self.versionReceived.emit(d or {}), quiet=quiet)

    def fetch_capabilities(self, quiet=False):
        self._get("/robot/capabilities",
                  lambda d: self.capabilitiesReceived.emit(self._as_list(d)), quiet=quiet)

    def fetch_attributes(self, quiet=False):
        """Status, battery, presets, attachments, dock status..."""
        self._get("/robot/state/attributes",
                  lambda d: self.attributesReceived.emit(self._as_list(d)), quiet=quiet)

    def fetch_consumables(self, quiet=False):
        self._get("/robot/capabilities/ConsumableMonitoringCapability",
                  lambda d: self.consumablesReceived.emit(self._as_list(d)), quiet=quiet)

    def fetch_current_statistics(self, quiet=False):
        self._get("/robot/capabilities/CurrentStatisticsCapability",
                  lambda d: self.currentStatisticsReceived.emit(self._as_list(d)), quiet=quiet)

    def fetch_total_statistics(self, quiet=False):
        self._get("/robot/capabilities/TotalStatisticsCapability",
                  lambda d: self.totalStatisticsReceived.emit(self._as_list(d)), quiet=quiet)

    def fetch_wifi(self, quiet=False):
        self._get("/robot/capabilities/WifiConfigurationCapability",
                  lambda d: self.wifiReceived.emit(d or {}), quiet=quiet)

    def fetch_events(self, quiet=False):
        self._get("/events", lambda d: self.eventsReceived.emit(self._as_list(d)), quiet=quiet)

    def fetch_presets(self, capability, quiet=False):
        self._get(f"/robot/capabilities/{capability}/presets",
                  lambda d: self.presetsReceived.emit(capability, self._as_list(d)),
                  quiet=quiet)

    def fetch_map(self, quiet=False):
        if self._map_pending:  # do not pile up requests if the robot is slow
            return
        self._map_pending = True
        self._get("/robot/state/map", self.mapReceived.emit,
                  on_finish=lambda: setattr(self, "_map_pending", False), quiet=quiet)

    def start_polling(self, interval_ms=2000):
        """Background map refresh: failures are reported through reachable() only."""
        self.fetch_map(quiet=True)
        self._timer.start(interval_ms)

    def stop_polling(self):
        self._timer.stop()

    def is_polling(self):
        return self._timer.isActive()

    # -- commands -----------------------------------------------------------

    def _command(self, capability, payload, key, path_suffix=""):
        self._put(f"/robot/capabilities/{capability}{path_suffix}", payload,
                  lambda _data: self.commandDone.emit(key))

    def basic_control(self, action):
        """action: 'start', 'stop', 'pause' or 'home'."""
        self._command("BasicControlCapability", {"action": action}, action)

    def set_preset(self, capability, name):
        """Select a preset (e.g. fan speed 'high') on a preset capability."""
        self._command(capability, {"name": name}, PRESET_CAPABILITIES.get(capability, capability),
                      path_suffix="/preset")

    def clean_segments(self, segment_ids, iterations=1):
        self._command("MapSegmentationCapability", {
            "action": "start_segment_action",
            "segment_ids": [str(s) for s in segment_ids],
            "iterations": iterations,
            "customOrder": False,
        }, "segments")

    def clean_zone_qgis(self, rect, iterations=1):
        """Clean a QgsRectangle expressed in the QGIS frame."""
        x1, y1 = q2v(QgsPointXY(rect.xMinimum(), rect.yMaximum()))  # top-left
        x2, y2 = q2v(QgsPointXY(rect.xMaximum(), rect.yMinimum()))  # bottom-right
        zone = {"points": {
            "pA": {"x": x1, "y": y1}, "pB": {"x": x2, "y": y1},
            "pC": {"x": x2, "y": y2}, "pD": {"x": x1, "y": y2},
        }}
        self._command("ZoneCleaningCapability",
                      {"action": "clean", "zones": [zone], "iterations": iterations},
                      "zone")

    def go_to_qgis(self, point):
        x, y = q2v(point)
        self._command("GoToLocationCapability",
                      {"action": "goto", "coordinates": {"x": x, "y": y}}, "goto")
