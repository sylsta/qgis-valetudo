"""Frame conversion: Valetudo (cm, Y pointing down) <-> QGIS (cm, Y pointing up).

This is the ONLY place in the plugin that knows about the Y-axis flip.
Georeferencing (an affine transform) would later be added here.
"""

from qgis.core import QgsPointXY


def v2q(x, y):
    """Valetudo coordinates -> QGIS point."""
    return QgsPointXY(x, -y)


def q2v(pt):
    """QGIS point -> Valetudo coordinates (integers, in cm)."""
    return int(round(pt.x())), int(round(-pt.y()))
