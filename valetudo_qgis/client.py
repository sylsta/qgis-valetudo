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

COMMAND_KEYS = ("start", "pause", "stop", "home", "segments", "zone", "goto")


class ValetudoClient(QObject):
    robotInfoReceived = pyqtSignal(dict)
    capabilitiesReceived = pyqtSignal(list)
    attributesReceived = pyqtSignal(list)
    mapReceived = pyqtSignal(dict)
    commandDone = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, base_url, authcfg="", parent=None):
        super().__init__(parent)
        self.base = base_url.rstrip("/") + "/api/v2"
        self.authcfg = authcfg  # QGIS authentication manager config id
        self.nam = QgsNetworkAccessManager.instance()
        self._replies = set()   # keeps replies alive until they finish
        self._map_pending = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.fetch_map)

    # -- plumbing -----------------------------------------------------------

    def _request(self, path):
        req = QNetworkRequest(QUrl(self.base + path))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if self.authcfg:
            res = QgsApplication.authManager().updateNetworkRequest(req, self.authcfg)
            if isinstance(res, tuple):  # depends on the PyQGIS version
                req = res[1]
        return req

    def _get(self, path, callback, on_finish=None):
        self._track(self.nam.get(self._request(path)), callback, on_finish)

    def _put(self, path, payload, callback=None):
        body = QByteArray(json.dumps(payload).encode())
        self._track(self.nam.put(self._request(path), body), callback)

    def _track(self, reply, callback, on_finish=None):
        self._replies.add(reply)

        def finished():
            self._replies.discard(reply)
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    # errorString() is already localised by Qt
                    self.error.emit(f"{reply.url().toString()}: {reply.errorString()}")
                    return
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

    # -- reading ------------------------------------------------------------

    def fetch_robot_info(self):
        self._get("/robot", self.robotInfoReceived.emit)

    def fetch_capabilities(self):
        self._get("/robot/capabilities", self.capabilitiesReceived.emit)

    def fetch_attributes(self):
        self._get("/robot/state/attributes", self.attributesReceived.emit)

    def fetch_map(self):
        if self._map_pending:  # do not pile up requests if the robot is slow
            return
        self._map_pending = True
        self._get("/robot/state/map", self.mapReceived.emit,
                  on_finish=lambda: setattr(self, "_map_pending", False))

    def start_polling(self, interval_ms=2000):
        self.fetch_map()
        self._timer.start(interval_ms)

    def stop_polling(self):
        self._timer.stop()

    def is_polling(self):
        return self._timer.isActive()

    # -- commands -----------------------------------------------------------

    def _command(self, capability, payload, key):
        self._put(f"/robot/capabilities/{capability}", payload,
                  lambda _data: self.commandDone.emit(key))

    def basic_control(self, action):
        """action: 'start', 'stop', 'pause' or 'home'."""
        self._command("BasicControlCapability", {"action": action}, action)

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
