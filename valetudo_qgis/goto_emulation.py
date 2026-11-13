"""'Go here' for robots without GoToLocationCapability.

The robot is sent to clean a tiny zone centred on the target, so its own
navigation takes it there; the robot position is watched and, once it is
close enough while working, the robot is paused: it stays at the target.

This module has no user-facing text: it emits result keys that the plugin
turns into translated messages ("arrived", "gave_up", "timeout").
"""

import math
import time

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import QgsRectangle

from .coords import q2v

ZONE_SIZE_CM = 60       # side of the zone sent to the robot
ARRIVAL_CM = 50         # distance at which the robot is considered arrived
POLL_MS = 2000
TIMEOUT_S = 300
WORKING_STATES = {"cleaning", "moving"}
FINISHED_STATES = {"docked", "idle", "error", "returning"}


def target_zone(point):
    """QgsRectangle of ZONE_SIZE_CM around a QGIS point."""
    half = ZONE_SIZE_CM / 2
    return QgsRectangle(point.x() - half, point.y() - half, point.x() + half, point.y() + half)


class GoToEmulator(QObject):
    finished = pyqtSignal(str, str)   # result key, detail

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._client = None
        self._target = None
        self._state = None
        self._seen_working = False

    def is_running(self):
        return self._timer.isActive()

    def start(self, client, point):
        """Send the robot towards a QGIS point, then pause it there."""
        self.stop()
        self._client = client
        self._target = q2v(point)        # Valetudo frame, cm
        self._state = None
        self._seen_working = False
        self._t0 = time.monotonic()
        client.mapPeeked.connect(self._on_map)
        client.mapReceived.connect(self._on_map)
        client.attributesReceived.connect(self._on_attributes)
        client.clean_zone_qgis(target_zone(point))
        self._timer.start()

    def stop(self):
        self._timer.stop()
        if self._client is not None:
            for signal, slot in ((self._client.mapPeeked, self._on_map),
                                 (self._client.mapReceived, self._on_map),
                                 (self._client.attributesReceived, self._on_attributes)):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._client = None

    def _tick(self):
        if time.monotonic() - self._t0 > TIMEOUT_S:
            self._end("timeout", str(TIMEOUT_S // 60))
            return
        client = self._client
        if client is None:
            return
        client.fetch_attributes(quiet=True)
        if self._client is client:   # the run may have ended in the meantime
            client.peek_map()

    def _on_attributes(self, attributes):
        for attr in attributes:
            if attr.get("__class") == "StatusStateAttribute":
                self._state = attr.get("value")
        if self._state in WORKING_STATES:
            self._seen_working = True
        elif self._seen_working and self._state in FINISHED_STATES:
            # The zone job ended (or failed) before we saw the robot arrive.
            self._end("gave_up", self._state or "")

    def _on_map(self, map_json):
        if not self._seen_working or self._target is None:
            return
        for ent in map_json.get("entities", []):
            if ent.get("type") == "robot_position" and len(ent.get("points") or []) >= 2:
                x, y = ent["points"][0], ent["points"][1]
                distance = math.hypot(x - self._target[0], y - self._target[1])
                if distance <= ARRIVAL_CM:
                    client = self._client
                    self._end("arrived", str(round(distance)))
                    client.basic_control("pause")
                return

    def _end(self, result, detail):
        self.stop()
        self.finished.emit(result, detail)
