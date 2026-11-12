"""Settings dialog, stored in QgsSettings.

Passwords are never stored in clear text: credentials go through the QGIS
authentication manager (QgsAuthConfigSelect), which encrypts them.
"""

from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox
from qgis.core import QgsSettings
from qgis.gui import QgsAuthConfigSelect

from .i18n import tr

KEY_URL = "valetudo/url"
KEY_AUTH = "valetudo/authcfg"
KEY_INTERVAL = "valetudo/interval_s"


def load_settings():
    s = QgsSettings()
    return {
        "url": s.value(KEY_URL, "", type=str),
        "authcfg": s.value(KEY_AUTH, "", type=str),
        "interval_s": s.value(KEY_INTERVAL, 2, type=int),
    }


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Valetudo – settings"))
        cfg = load_settings()

        self.url = QLineEdit(cfg["url"])
        self.url.setPlaceholderText("http://192.168.1.23")

        self.auth = QgsAuthConfigSelect(self)
        self.auth.setConfigId(cfg["authcfg"])

        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setSuffix(" s")
        self.interval.setValue(cfg["interval_s"])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow(tr("Robot address"), self.url)
        form.addRow(tr("Authentication (optional)"), self.auth)
        form.addRow(tr("Live tracking refresh"), self.interval)
        form.addRow(buttons)

    def accept(self):
        s = QgsSettings()
        s.setValue(KEY_URL, self.url.text().strip())
        s.setValue(KEY_AUTH, self.auth.configId())
        s.setValue(KEY_INTERVAL, self.interval.value())
        super().accept()
