"""Camera widget: live video from the robot's H.264 stream.

Embedded in the status panel. The panel decides when it plays (panel
visible and camera section open); nothing is decoded or downloaded otherwise.
"""

import time

from qgis.PyQt.QtCore import QDateTime, Qt, QTimer
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .camera import CameraStream
from .i18n import tr
from .settings_dialog import load_settings

DEFAULT_ASPECT = 9 / 16   # height / width until the first image tells the real one


class CameraWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self._aspect = DEFAULT_ASPECT
        self._frames = 0
        self._fps_clock = time.monotonic()
        self._error_detail = ""

        self.stream = CameraStream(self)
        self.stream.frameReady.connect(self._on_frame)
        self.stream.stateChanged.connect(self._on_state)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.video = QLabel()
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.video.setStyleSheet("background-color: black; color: #bdbdbd;")
        layout.addWidget(self.video)

        bar = QHBoxLayout()
        self.btn_play = QPushButton()
        self.btn_play.clicked.connect(self.toggle)
        bar.addWidget(self.btn_play)
        self.btn_snapshot = QPushButton(tr("Save snapshot"))
        self.btn_snapshot.clicked.connect(self.save_snapshot)
        self.btn_snapshot.setEnabled(False)
        bar.addWidget(self.btn_snapshot)
        layout.addLayout(bar)

        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._update_fps)
        self._on_state("stopped", "")
        self._fit_height()

    # -- playback -----------------------------------------------------------------

    def play(self):
        cfg = load_settings()
        if not cfg["camera_url"]:
            self.lbl_status.setText(tr("No camera URL: set it in the settings."))
            return
        self._error_detail = ""
        self.stream.start(cfg["camera_url"], cfg["ffmpeg"], cfg["camera_width"])

    def stop(self):
        if self.stream.is_running():
            self.stream.stop()

    def toggle(self, *_):
        if self.stream.is_running():
            self.stop()
        else:
            self.play()

    def restart_if_running(self):
        """Called when settings change."""
        if self.stream.is_running():
            self.play()

    def shutdown(self):
        self.stream.stop()

    # -- display ------------------------------------------------------------------

    def _fit_height(self):
        """Video height follows the panel width, with the stream's aspect ratio."""
        self.video.setFixedHeight(max(120, int(self.width() * self._aspect)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()
        self._show_image()

    def _on_frame(self, image):
        if self._image is None and image.width():
            self._aspect = image.height() / image.width()
            self._fit_height()
        self._image = image
        self._frames += 1
        self.btn_snapshot.setEnabled(True)
        self._show_image()

    def _show_image(self):
        if self._image is None:
            return
        pixmap = QPixmap.fromImage(self._image).scaled(
            self.video.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.video.setPixmap(pixmap)

    def _update_fps(self):
        now = time.monotonic()
        fps = round(self._frames / max(now - self._fps_clock, 1e-3))
        self._frames, self._fps_clock = 0, now
        if self.stream.is_running() and self._image is not None and not self._error_detail:
            self.lbl_status.setText(tr("Playing · {fps} fps").format(fps=fps))

    def _on_state(self, state, detail):
        running = state not in ("stopped", "ffmpeg_missing")
        self.btn_play.setText(tr("Stop") if running else tr("Play"))
        if state == "connecting":
            self.lbl_status.setText(tr("Connecting…"))
        elif state == "playing":
            self._error_detail = ""
            self._fps_clock, self._frames = time.monotonic(), 0
            self._fps_timer.start()
            self.lbl_status.setText(tr("Playing"))
        elif state == "stopped":
            self._fps_timer.stop()
            self.lbl_status.setText(tr("Stopped"))
            self.video.setText(tr("No video"))
            self._image = None
        elif state == "ffmpeg_missing":
            self._fps_timer.stop()
            self.lbl_status.setText(
                tr("ffmpeg not found: install it or set its path in the settings."))
        elif state == "error":
            self._error_detail = detail
        elif state == "reconnecting":
            self._fps_timer.stop()
            text = tr("Stream lost, reconnecting in {seconds} s…").format(seconds=detail)
            if self._error_detail:
                text += "\n" + tr("ffmpeg error: {detail}").format(detail=self._error_detail)
            self.lbl_status.setText(text)

    def save_snapshot(self, *_):
        if self._image is None:
            return
        name = "valetudo_{}.png".format(QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save snapshot"), name,
                                              tr("PNG images (*.png)"))
        if path:
            self._image.save(path, "PNG")
            self.lbl_status.setText(tr("Snapshot saved: {path}").format(path=path))
