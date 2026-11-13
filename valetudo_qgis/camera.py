"""Camera stream: decodes a raw H.264 stream with an ffmpeg subprocess.

QGIS cannot decode raw H.264 by itself, so ffmpeg (run through QProcess, so
nothing blocks the UI) re-encodes the stream into a sequence of JPEG images
on its standard output; each complete JPEG becomes a QImage.

Equivalent of: ffplay -fflags nobuffer -f h264 tcp://<robot>:8555

This module contains no user-facing text: it emits state keys (see STATES)
that the panel turns into translated messages.
"""

import os
import shutil

from qgis.PyQt.QtCore import QObject, QProcess, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QImage

STATES = ("connecting", "playing", "stopped", "reconnecting", "ffmpeg_missing", "error")
RECONNECT_DELAY_S = 3
MAX_BUFFER = 8 * 1024 * 1024   # drop data if ffmpeg outputs faster than we read

SOI = b"\xff\xd8"  # JPEG start of image
EOI = b"\xff\xd9"  # JPEG end of image


def find_ffmpeg(configured=""):
    """Path of the ffmpeg executable, or None."""
    if configured:
        return configured if os.path.isfile(configured) else shutil.which(configured)
    return shutil.which("ffmpeg")


def ffmpeg_arguments(url, width):
    return [
        "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",   # minimal latency, as with ffplay
        # Without a small probe, nobuffer waits ~4 s for the first image and then
        # drops two frames out of three (measured on a raw H.264 TCP test stream).
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "h264", "-i", url,
        "-an",
        "-vf", f"scale={int(width)}:-2",                 # keep aspect ratio, even height
        "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5",
        "-",
    ]


class CameraStream(QObject):
    frameReady = pyqtSignal(QImage)
    stateChanged = pyqtSignal(str, str)   # state key (see STATES), detail

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._buffer = bytearray()
        self._stderr = b""
        self._wanted = False     # True between start() and stop()
        self._args = None
        self._program = None
        self._reconnect = QTimer(self)
        self._reconnect.setSingleShot(True)
        self._reconnect.timeout.connect(self._launch)

    def is_running(self):
        return self._wanted

    def start(self, url, ffmpeg="", width=640):
        self.stop()
        program = find_ffmpeg(ffmpeg)
        if not program:
            self.stateChanged.emit("ffmpeg_missing", ffmpeg or "ffmpeg")
            return
        self._program = program
        self._args = ffmpeg_arguments(url, width)
        self._wanted = True
        self._launch()

    def stop(self):
        self._wanted = False
        self._reconnect.stop()
        process, self._process = self._process, None
        if process is not None:
            process.finished.disconnect()
            process.kill()
            process.waitForFinished(1000)
            process.deleteLater()
        self._buffer.clear()
        self.stateChanged.emit("stopped", "")

    # -- internals ------------------------------------------------------------

    def _launch(self):
        if not self._wanted:
            return
        self._buffer.clear()
        self._stderr = b""
        process = QProcess(self)
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_finished)
        self._process = process
        self._first_frame = True
        self.stateChanged.emit("connecting", "")
        process.start(self._program, self._args)

    def _on_stdout(self):
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput())
        if len(self._buffer) > MAX_BUFFER:
            self._buffer.clear()
            return
        # Extract complete JPEG images; only the latest one is shown (lowest latency).
        latest = None
        while True:
            start = self._buffer.find(SOI)
            if start < 0:
                self._buffer.clear()
                break
            end = self._buffer.find(EOI, start + 2)
            if end < 0:
                del self._buffer[:start]
                break
            latest = bytes(self._buffer[start:end + 2])
            del self._buffer[:end + 2]
        if latest is not None:
            image = QImage.fromData(latest, "JPG")
            if not image.isNull():
                if self._first_frame:
                    self._first_frame = False
                    self.stateChanged.emit("playing", "")
                self.frameReady.emit(image)

    def _on_stderr(self):
        if self._process is not None:
            self._stderr = (self._stderr + bytes(self._process.readAllStandardError()))[-2000:]

    def _last_error_line(self):
        lines = [l.strip() for l in self._stderr.decode("utf-8", "replace").splitlines()]
        return next((l for l in reversed(lines) if l), "")

    def _on_process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._wanted = False
            self.stateChanged.emit("ffmpeg_missing", self._program or "ffmpeg")

    def _on_finished(self, *_):
        process, self._process = self._process, None
        if process is not None:
            process.deleteLater()
        if not self._wanted:
            return
        # Stream lost or never reached: report, then retry.
        self.stateChanged.emit("error", self._last_error_line())
        self.stateChanged.emit("reconnecting", str(RECONNECT_DELAY_S))
        self._reconnect.start(RECONNECT_DELAY_S * 1000)
