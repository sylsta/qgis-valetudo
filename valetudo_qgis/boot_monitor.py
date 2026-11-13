"""Boot monitor: follows the robot's startup stage by stage and times it.

Stages, in order (each one waits for the previous):
  offline      the robot stopped answering (only when a reboot was just sent)
  network_up   its SSH port accepts TCP connections: the system has booted
  valetudo_up  the Valetudo web API answers (detail: version)
  robot_ready  the robot firmware reports a state (detail: "state;battery")
  camera_up    the camera stream port accepts connections (if a camera is set)
  done         everything is back (detail: total seconds)
plus started, no_offline, camera_missing and timeout.

Probes run one at a time, every 2 s, with short timeouts. This module has no
user-facing text: the plugin turns stage keys into translated log lines.
"""

import time

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtNetwork import QTcpSocket

POLL_MS = 2000
TCP_TIMEOUT_MS = 2000
OFFLINE_WAIT_S = 90     # a reboot that does not take the robot down within this is suspicious
CAMERA_WAIT_S = 60      # after the robot is ready
TIMEOUT_S = 300
LOG_TAIL_LINES = 40


class BootMonitor(QObject):
    stage = pyqtSignal(str, float, str)   # stage key, seconds since start, detail
    logTail = pyqtSignal(list)            # last lines of Valetudo's own log ([] if unavailable)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._generation = 0      # invalidates callbacks of a stopped run
        self._running = False

    def is_running(self):
        return self._running

    def start(self, client, host, ssh_port=22, camera_port=None, expect_offline=True):
        """expect_offline=True right after a reboot command; False to just watch."""
        self.stop()
        self._generation += 1
        self._client, self._host = client, host
        self._ssh_port, self._camera_port = ssh_port, camera_port
        self._phase = "wait_offline" if expect_offline else "wait_network"
        self._t0 = time.monotonic()
        self._t_ready = None
        self._running = True
        self.stage.emit("started", 0.0, "")
        self._timer.start(0)

    def stop(self):
        self._running = False
        self._timer.stop()
        self._generation += 1

    # -- internals ----------------------------------------------------------------

    def _elapsed(self):
        return time.monotonic() - self._t0

    def _emit(self, key, detail=""):
        self.stage.emit(key, round(self._elapsed(), 1), detail)

    def _next(self, phase=None, delay=POLL_MS):
        if phase:
            self._phase = phase
        if self._running:
            self._timer.start(delay)

    def _guard(self, callback):
        """Wrap a callback so it does nothing if this run was stopped meanwhile."""
        generation = self._generation

        def wrapped(*args):
            if self._running and generation == self._generation:
                callback(*args)
        return wrapped

    def _tcp_probe(self, port, on_ok, on_fail):
        sock = QTcpSocket(self)
        timer = QTimer(self)
        timer.setSingleShot(True)
        state = {"done": False}

        def finish(ok):
            if state["done"]:
                return
            state["done"] = True
            timer.stop()
            sock.abort()
            sock.deleteLater()
            timer.deleteLater()
            (on_ok if ok else on_fail)()

        timer.timeout.connect(lambda: finish(False))
        sock.connected.connect(lambda: finish(True))
        sock.errorOccurred.connect(lambda *_: finish(False))
        timer.start(TCP_TIMEOUT_MS)
        sock.connectToHost(self._host, int(port))

    def _tick(self):
        if not self._running:
            return
        if self._elapsed() > TIMEOUT_S:
            self._emit("timeout", str(TIMEOUT_S // 60))
            self.stop()
            return
        getattr(self, f"_{self._phase}")()

    def _wait_offline(self):
        if self._elapsed() > OFFLINE_WAIT_S:
            self._emit("no_offline")
            self._next("wait_network", 0)
            return
        self._client.probe(
            "/valetudo/version",
            self._guard(lambda _d: self._next()),
            self._guard(lambda _e: (self._emit("offline"), self._next("wait_network"))))

    def _wait_network(self):
        self._tcp_probe(
            self._ssh_port,
            self._guard(lambda: (self._emit("network_up"), self._next("wait_valetudo", 0))),
            self._guard(lambda: self._next()))

    def _wait_valetudo(self):
        def ok(version):
            self._emit("valetudo_up", (version or {}).get("release", "?"))
            self._next("wait_robot", 0)
        self._client.probe("/valetudo/version", self._guard(ok),
                           self._guard(lambda _e: self._next()))

    def _wait_robot(self):
        def ok(attributes):
            attributes = attributes if isinstance(attributes, list) else []
            state = next((a.get("value") for a in attributes
                          if a.get("__class") == "StatusStateAttribute"), None)
            battery = next((a.get("level") for a in attributes
                            if a.get("__class") == "BatteryStateAttribute"), None)
            if state is None:
                self._next()   # Valetudo is up but the firmware has not reported yet
                return
            self._t_ready = self._elapsed()
            self._emit("robot_ready", f"{state};{battery if battery is not None else '?'}")
            self._next("wait_camera" if self._camera_port else "finish", 0)
        self._client.probe("/robot/state/attributes", self._guard(ok),
                           self._guard(lambda _e: self._next()))

    def _wait_camera(self):
        def fail():
            if self._elapsed() - self._t_ready > CAMERA_WAIT_S:
                self._emit("camera_missing", str(CAMERA_WAIT_S))
                self._next("finish", 0)
            else:
                self._next()
        self._tcp_probe(
            self._camera_port,
            self._guard(lambda: (self._emit("camera_up"), self._next("finish", 0))),
            self._guard(fail))

    def _finish(self):
        self._emit("done", str(round(self._elapsed())))

        def log_ok(text):
            lines = [l for l in (text or "").splitlines() if l.strip()]
            self.logTail.emit(lines[-LOG_TAIL_LINES:])
            self.stop()

        def log_failed(_error):
            self.logTail.emit([])
            self.stop()

        self._client.probe("/valetudo/log/content", self._guard(log_ok), self._guard(log_failed),
                           raw=True, timeout_ms=5000)
