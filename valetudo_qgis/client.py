"""Client HTTP asynchrone pour l'API REST de Valetudo (/api/v2).

Toutes les requêtes passent par QgsNetworkAccessManager : elles ne bloquent
pas l'interface et respectent les réglages proxy de QGIS. Les résultats sont
diffusés par des signaux Qt.
"""

import json

from qgis.PyQt.QtCore import QObject, QUrl, QTimer, QByteArray, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsApplication, QgsNetworkAccessManager, QgsPointXY

from .coords import q2v


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
        self.authcfg = authcfg  # identifiant du gestionnaire d'authentification QGIS
        self.nam = QgsNetworkAccessManager.instance()
        self._replies = set()   # garde les réponses en vie jusqu'à leur fin
        self._map_pending = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.fetch_map)

    # -- plomberie ----------------------------------------------------------

    def _request(self, path):
        req = QNetworkRequest(QUrl(self.base + path))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if self.authcfg:
            res = QgsApplication.authManager().updateNetworkRequest(req, self.authcfg)
            if isinstance(res, tuple):  # selon la version de PyQGIS
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
                    self.error.emit(f"{reply.url().toString()} : {reply.errorString()}")
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

    # -- lecture ------------------------------------------------------------

    def fetch_robot_info(self):
        self._get("/robot", self.robotInfoReceived.emit)

    def fetch_capabilities(self):
        self._get("/robot/capabilities", self.capabilitiesReceived.emit)

    def fetch_attributes(self):
        self._get("/robot/state/attributes", self.attributesReceived.emit)

    def fetch_map(self):
        if self._map_pending:  # évite d'empiler les requêtes si le robot est lent
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

    # -- commandes ----------------------------------------------------------

    def _command(self, capability, payload, label):
        self._put(f"/robot/capabilities/{capability}", payload,
                  lambda _data: self.commandDone.emit(label))

    def basic_control(self, action):
        """action : 'start', 'stop', 'pause' ou 'home'."""
        self._command("BasicControlCapability", {"action": action}, action)

    def clean_segments(self, segment_ids, iterations=1):
        self._command("MapSegmentationCapability", {
            "action": "start_segment_action",
            "segment_ids": [str(s) for s in segment_ids],
            "iterations": iterations,
            "customOrder": False,
        }, "nettoyage des pièces")

    def clean_zone_qgis(self, rect, iterations=1):
        """Nettoie un QgsRectangle exprimé dans le repère QGIS."""
        x1, y1 = q2v(QgsPointXY(rect.xMinimum(), rect.yMaximum()))  # haut-gauche
        x2, y2 = q2v(QgsPointXY(rect.xMaximum(), rect.yMinimum()))  # bas-droite
        zone = {"points": {
            "pA": {"x": x1, "y": y1}, "pB": {"x": x2, "y": y1},
            "pC": {"x": x2, "y": y2}, "pD": {"x": x1, "y": y2},
        }}
        self._command("ZoneCleaningCapability",
                      {"action": "clean", "zones": [zone], "iterations": iterations},
                      "nettoyage de zone")

    def go_to_qgis(self, point):
        x, y = q2v(point)
        self._command("GoToLocationCapability",
                      {"action": "goto", "coordinates": {"x": x, "y": y}}, "déplacement")
