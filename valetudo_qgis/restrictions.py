"""No-go zones and virtual walls, edited in QGIS and sent to the robot.

CombinedVirtualRestrictionsCapability uses the same object for GET and PUT:

    {"virtualWalls":    [{"points": {"pA": {x, y}, "pB": {x, y}}}],
     "restrictedZones": [{"type": "regular" | "mop",
                          "points": {"pA": …, "pB": …, "pC": …, "pD": …}}]}

Two editable memory layers hold them. Valetudo needs exactly 2 points per wall
and 4 per zone, so other shapes are converted (first/last vertex of a line,
oriented bounding box of a polygon) and reported as warnings.
"""

from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsDefaultValue,
    QgsEditorWidgetSetup,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsRendererCategory,
    QgsVectorLayer,
)

from .coords import q2v, v2q
from .i18n import tr

GROUP_NAME = "Valetudo"
ZONE_TYPES = ("regular", "mop")
POINT_KEYS = ("pA", "pB", "pC", "pD")


def layer_names():
    return {"zones": tr("Valetudo - no-go zones (editable)"),
            "walls": tr("Valetudo - virtual walls (editable)")}


def zone_type_labels():
    return {"regular": tr("No-go"), "mop": tr("No-mop")}


def _new_layer(geom_type, name, fields):
    lyr = QgsVectorLayer(geom_type, name, "memory")
    lyr.setCrs(QgsCoordinateReferenceSystem())
    lyr.dataProvider().addAttributes(fields)
    lyr.updateFields()
    return lyr


def _zones_renderer():
    def fill(color):
        return QgsFillSymbol.createSimple({
            "color": f"{color},80", "outline_color": color, "outline_width": "0.6",
            "style": "b_diagonal"})
    labels = zone_type_labels()
    return QgsCategorizedSymbolRenderer("type", [
        QgsRendererCategory("regular", fill("198,40,40"), labels["regular"]),
        QgsRendererCategory("mop", fill("21,101,192"), labels["mop"]),
    ])


def _vertices(geometry):
    """Vertices of each single part: polygon rings (without closing vertex) or lines."""
    parts = geometry.asGeometryCollection() if geometry.isMultipart() else [geometry]
    for part in parts:
        if part.isEmpty():
            continue
        polygon = part.asPolygon()
        if polygon:
            ring = polygon[0][:-1]
            yield part, ring
        else:
            yield part, part.asPolyline()


class RestrictionLayers:
    def __init__(self, project):
        self.project = project
        self._ids = {}   # role -> layer id

    def layer(self, role):
        lid = self._ids.get(role)
        return self.project.mapLayer(lid) if lid else None

    def exists(self):
        return bool(self._ids) and all(self.layer(r) for r in ("zones", "walls"))

    def _create(self):
        names = layer_names()
        zones = _new_layer("Polygon", names["zones"],
                           [QgsField("type", QMetaType.Type.QString)])
        walls = _new_layer("LineString", names["walls"], [])

        # "type": drop-down No-go / No-mop, "regular" by default.
        labels = zone_type_labels()
        index = zones.fields().indexOf("type")
        zones.setEditorWidgetSetup(index, QgsEditorWidgetSetup(
            "ValueMap", {"map": [{labels[t]: t} for t in ZONE_TYPES]}))
        zones.setDefaultValueDefinition(index, QgsDefaultValue("'regular'"))
        zones.setRenderer(_zones_renderer())
        wall_symbol = QgsLineSymbol.createSimple({"line_color": "198,40,40",
                                                  "line_width": "1.2"})
        walls.renderer().setSymbol(wall_symbol)

        self.project.addMapLayers([zones, walls], False)
        root = self.project.layerTreeRoot()
        group = root.findGroup(GROUP_NAME) or root.insertGroup(0, GROUP_NAME)
        for lyr in (walls, zones):
            lyr.setCrs(QgsCoordinateReferenceSystem())
            group.insertLayer(0, lyr)   # on top of the map layers
        self._ids = {"zones": zones.id(), "walls": walls.id()}

    def load(self, data):
        """Fill the layers with the robot's current restrictions (creating them if needed)."""
        if not self.exists():
            self._create()
        zones, walls = self.layer("zones"), self.layer("walls")
        for lyr in (zones, walls):
            if lyr.isEditable():
                lyr.rollBack()
            lyr.dataProvider().truncate()

        feats = []
        for zone in (data or {}).get("restrictedZones", []):
            pts = [v2q(zone["points"][k]["x"], zone["points"][k]["y"]) for k in POINT_KEYS]
            f = QgsFeature(zones.fields())
            f.setGeometry(QgsGeometry.fromPolygonXY([pts + [pts[0]]]))
            f.setAttributes([zone.get("type", "regular")])
            feats.append(f)
        zones.dataProvider().addFeatures(feats)

        feats = []
        for wall in (data or {}).get("virtualWalls", []):
            pts = [v2q(wall["points"][k]["x"], wall["points"][k]["y"]) for k in ("pA", "pB")]
            f = QgsFeature(walls.fields())
            f.setGeometry(QgsGeometry.fromPolylineXY(pts))
            feats.append(f)
        walls.dataProvider().addFeatures(feats)

        for lyr in (zones, walls):
            lyr.updateExtents()
            lyr.triggerRepaint()
        return zones, walls

    def to_payload(self):
        """Build the PUT body from the layers.

        Returns (payload, warnings), warnings being a dict of counters:
        walls_simplified, zones_approximated, skipped.
        """
        warnings = {"walls_simplified": 0, "zones_approximated": 0, "skipped": 0}
        zones, walls = self.layer("zones"), self.layer("walls")
        for lyr in (zones, walls):
            if lyr.isEditable():
                lyr.commitChanges()   # take pending edits into account

        def point(p):
            x, y = q2v(p)
            return {"x": x, "y": y}

        restricted = []
        for feature in zones.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                warnings["skipped"] += 1
                continue
            zone_type = feature["type"] if feature["type"] in ZONE_TYPES else "regular"
            for part, ring in _vertices(geometry):
                if len(ring) != 4:
                    box = part.orientedMinimumBoundingBox()
                    box = box[0] if isinstance(box, tuple) else box
                    ring = box.asPolygon()[0][:-1]
                    warnings["zones_approximated"] += 1
                if len(ring) != 4:
                    warnings["skipped"] += 1
                    continue
                restricted.append({"type": zone_type,
                                   "points": {k: point(p) for k, p in zip(POINT_KEYS, ring)}})

        virtual = []
        for feature in walls.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                warnings["skipped"] += 1
                continue
            for _part, line in _vertices(geometry):
                if len(line) < 2:
                    warnings["skipped"] += 1
                    continue
                if len(line) > 2:
                    warnings["walls_simplified"] += 1
                virtual.append({"points": {"pA": point(line[0]), "pB": point(line[-1])}})

        return {"virtualWalls": virtual, "restrictedZones": restricted}, warnings
