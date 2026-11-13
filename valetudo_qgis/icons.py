"""Plugin icons (SVG files in icons/, named after the action keys)."""

import os

from qgis.PyQt.QtGui import QIcon

ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")


def icon(name):
    """QIcon for icons/<name>.svg, or an empty icon if there is none."""
    path = os.path.join(ICON_DIR, f"{name}.svg")
    return QIcon(path) if os.path.exists(path) else QIcon()


def has_icon(name):
    return os.path.exists(os.path.join(ICON_DIR, f"{name}.svg"))
