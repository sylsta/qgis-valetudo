"""Dockable camera panel: live video from the robot's H.264 stream.

The video plays while the panel is visible and stops when it is hidden, so
nothing is decoded or downloaded in the background.
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
from qgis.gui import QgsDockWidget

from .camera import CameraStream
from .i18n import tr
from .settings_dialog import load_settings


class CameraPanel(QgsDockWidget):
    def __init__(self, parent=None):
        super().__init__(tr("Valetudo camera"), parent)
        self.setObjectName("ValetudoCameraPanel")
        self._image = None
        self._frames = 0
        self._fps = 0
        self._error_detail = ""
        self._fps_clock = time.monotonic()

        self.stream = CameraStream(self)
        self.stream.frameReady.connect(self._on_frame)
        self.stream.stateChanged.connect(self._on_state)

        content = QWidget()
        layout = QVBoxLayout(content)

        self.video = QLabel()
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(240, 135)
        self.video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video.setStyleSheet("background-color: black; color: #bdbdbd;")
        layout.addWidget(self.video, 1)

        bar = QHBoxLayout()
        self.btn_play = QPushButton()
        self.btn_play.clicked.connect(self.toggle)
        bar.addWidget(self.btn_play)
        self.btn_snapshot = QPushButton(tr("Save snapshot"))
        self.btn_snapshot.clicked.connect(self.save_snapshot)
        self.btn_snapshot.setEnabled(False)
        bar.addWidget(self.btn_snapshot)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        bar.addWidget(self.lbl_status, 1)
        layout.addLayout(bar)
        self.setWidget(content)

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._update_fps)

        self.visibilityChanged.connect(self._on_visibility)
        self._on_state("stopped", "")

    # -- playback -----------------------------------------------------------------

    def play(self):
        cfg = load_settings()
        if not cfg["camera_url"]:
            self._set_status(tr("No camera URL: set it in the settings."))
            return
        self._error_detail = ""
        self.stream.start(cfg["camera_url"], cfg["ffmpeg"], cfg["camera_width"])

    def stop(self):
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

    def _on_visibility(self, visible):
        if visible:
            self.play()
        else:
            self.stop()

    # -- display ------------------------------------------------------------------

    def _on_frame(self, image):
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_image()

    def _update_fps(self):
        now = time.monotonic()
        elapsed = max(now - self._fps_clock, 1e-3)
        self._fps = round(self._frames / elapsed)
        self._frames = 0
        self._fps_clock = now
        if self.stream.is_running() and self._image is not None and not self._error_detail:
            self._set_status(tr("Playing · {fps} fps").format(fps=self._fps))

    def _on_state(self, state, detail):
        running = state not in ("stopped", "ffmpeg_missing")
        self.btn_play.setText(tr("Stop") if running else tr("Play"))
        if state == "connecting":
            self._set_status(tr("Connecting…"))
        elif state == "playing":
            self._error_detail = ""
            self._fps_clock, self._frames = time.monotonic(), 0
            self._fps_timer.start()
            self._set_status(tr("Playing"))
        elif state == "stopped":
            self._fps_timer.stop()
            self._set_status(tr("Stopped"))
            self.video.setText(tr("No video"))
            self._image = None
        elif state == "ffmpeg_missing":
            self._fps_timer.stop()
            self._set_status(tr("ffmpeg not found: install it or set its path in the settings."))
        elif state == "error":
            self._error_detail = detail
        elif state == "reconnecting":
            self._fps_timer.stop()
            text = tr("Stream lost, reconnecting in {seconds} s…").format(seconds=detail)
            if self._error_detail:
                text += "\n" + tr("ffmpeg error: {detail}").format(detail=self._error_detail)
            self._set_status(text)

    def _set_status(self, text):
        self.lbl_status.setText(text)

    def save_snapshot(self, *_):
        if self._image is None:
            return
        name = "valetudo_{}.png".format(QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save snapshot"), name,
                                              tr("PNG images (*.png)"))
        if path:
            self._image.save(path, "PNG")
            self._set_status(tr("Snapshot saved: {path}").format(path=path))

    def shutdown(self):
        """Called by the plugin on unload."""
        self.stream.stop()
