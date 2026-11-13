"""Settings dialog, stored in QgsSettings.

Passwords are never stored in clear text: credentials go through the QGIS
authentication manager (QgsAuthConfigSelect), which encrypts them.
"""

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox
from qgis.core import QgsSettings
from qgis.gui import QgsAuthConfigSelect

from .i18n import tr

KEY_URL = "valetudo/url"
KEY_AUTH = "valetudo/authcfg"
KEY_INTERVAL = "valetudo/interval_s"
KEY_CAMERA_URL = "valetudo/camera_url"
KEY_FFMPEG = "valetudo/ffmpeg"
KEY_CAMERA_WIDTH = "valetudo/camera_width"

DEFAULT_CAMERA_PORT = 8555


def default_camera_url(robot_url):
    """tcp://<robot host>:8555, derived from the robot address."""
    host = QUrl(robot_url).host() if robot_url else ""
    return f"tcp://{host}:{DEFAULT_CAMERA_PORT}" if host else ""


def load_settings():
    s = QgsSettings()
    url = s.value(KEY_URL, "", type=str)
    return {
        "url": url,
        "authcfg": s.value(KEY_AUTH, "", type=str),
        "interval_s": s.value(KEY_INTERVAL, 2, type=int),
        "camera_url": s.value(KEY_CAMERA_URL, "", type=str) or default_camera_url(url),
        "ffmpeg": s.value(KEY_FFMPEG, "", type=str),
        "camera_width": s.value(KEY_CAMERA_WIDTH, 640, type=int),
    }


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Valetudo – settings"))
        s = QgsSettings()
        cfg = load_settings()

        self.url = QLineEdit(cfg["url"])
        self.url.setPlaceholderText("http://192.168.1.23")

        self.auth = QgsAuthConfigSelect(self)
        self.auth.setConfigId(cfg["authcfg"])

        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setSuffix(" s")
        self.interval.setValue(cfg["interval_s"])

        # Empty camera URL = derived from the robot address.
        self.camera_url = QLineEdit(s.value(KEY_CAMERA_URL, "", type=str))
        self.camera_url.setPlaceholderText(
            default_camera_url(cfg["url"]) or f"tcp://192.168.1.23:{DEFAULT_CAMERA_PORT}")

        self.ffmpeg = QLineEdit(cfg["ffmpeg"])
        self.ffmpeg.setPlaceholderText(tr("ffmpeg (found in PATH)"))

        self.camera_width = QSpinBox()
        self.camera_width.setRange(160, 1920)
        self.camera_width.setSingleStep(160)
        self.camera_width.setSuffix(" px")
        self.camera_width.setValue(cfg["camera_width"])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow(tr("Robot address"), self.url)
        form.addRow(tr("Authentication (optional)"), self.auth)
        form.addRow(tr("Live tracking refresh"), self.interval)
        form.addRow(tr("Camera stream URL"), self.camera_url)
        form.addRow(tr("ffmpeg path"), self.ffmpeg)
        form.addRow(tr("Video width"), self.camera_width)
        form.addRow(buttons)

    def accept(self):
        s = QgsSettings()
        s.setValue(KEY_URL, self.url.text().strip())
        s.setValue(KEY_AUTH, self.auth.configId())
        s.setValue(KEY_INTERVAL, self.interval.value())
        s.setValue(KEY_CAMERA_URL, self.camera_url.text().strip())
        s.setValue(KEY_FFMPEG, self.ffmpeg.text().strip())
        s.setValue(KEY_CAMERA_WIDTH, self.camera_width.value())
        super().accept()
