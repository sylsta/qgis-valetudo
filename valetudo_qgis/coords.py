"""Conversion de repère : Valetudo (cm, Y vers le bas) <-> QGIS (cm, Y vers le haut).

C'est le SEUL endroit du plugin qui connaît l'inversion de l'axe Y.
Plus tard, un géoréférencement (transformation affine) viendrait se loger ici.
"""

from qgis.core import QgsPointXY


def v2q(x, y):
    """Coordonnées Valetudo -> point QGIS."""
    return QgsPointXY(x, -y)


def q2v(pt):
    """Point QGIS -> coordonnées Valetudo (entiers, en cm)."""
    return int(round(pt.x())), int(round(-pt.y()))
