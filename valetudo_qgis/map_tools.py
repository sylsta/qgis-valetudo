"""Map tools: draw a cleaning zone, pick a go-to target.

The tools only emit coordinates in the QGIS frame; the plugin converts and
sends them through the client (the Y flip stays in coords.py).
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.core import Qgis, QgsPointXY, QgsRectangle
from qgis.gui import QgsMapTool, QgsMapToolEmitPoint, QgsRubberBand

ZONE_COLOR = QColor(31, 120, 180)


class ZoneMapTool(QgsMapTool):
    """Press, drag and release to draw an axis-aligned rectangle."""

    zoneDrawn = pyqtSignal(QgsRectangle)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._start = None
        self._band = QgsRubberBand(canvas, Qgis.GeometryType.Polygon)
        fill = QColor(ZONE_COLOR)
        fill.setAlpha(60)
        self._band.setFillColor(fill)
        self._band.setStrokeColor(ZONE_COLOR)
        self._band.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _show(self, rect):
        self._band.reset(Qgis.GeometryType.Polygon)
        corners = [
            QgsPointXY(rect.xMinimum(), rect.yMinimum()),
            QgsPointXY(rect.xMaximum(), rect.yMinimum()),
            QgsPointXY(rect.xMaximum(), rect.yMaximum()),
            QgsPointXY(rect.xMinimum(), rect.yMaximum()),
        ]
        for i, pt in enumerate(corners):
            self._band.addPoint(pt, i == len(corners) - 1)  # redraw on the last one

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = self.toMapCoordinates(event.pos())

    def canvasMoveEvent(self, event):
        if self._start is not None:
            self._show(QgsRectangle(self._start, self.toMapCoordinates(event.pos())))

    def canvasReleaseEvent(self, event):
        if self._start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        rect = QgsRectangle(self._start, self.toMapCoordinates(event.pos()))
        self._start = None
        self._band.reset(Qgis.GeometryType.Polygon)
        self.zoneDrawn.emit(rect)

    def deactivate(self):
        self._start = None
        self._band.reset(Qgis.GeometryType.Polygon)
        super().deactivate()

    def cleanup(self):
        """Remove the rubber band from the canvas (call on plugin unload)."""
        self._band.reset(Qgis.GeometryType.Polygon)
        self.canvas().scene().removeItem(self._band)


class GoToMapTool(QgsMapToolEmitPoint):
    """Click once to pick a target point (left button only)."""

    pointPicked = pyqtSignal(QgsPointXY)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvasClicked.connect(self._on_click)

    def _on_click(self, point, button):
        if button == Qt.MouseButton.LeftButton:
            self.pointPicked.emit(QgsPointXY(point))
