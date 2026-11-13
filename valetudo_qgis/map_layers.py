"""Turns the Valetudo map into QGIS layers, and manages those layers.

Layers are created once, in a "Valetudo" group, ordered
robot > lines > zones > map. Afterwards only entities are refreshed; rooms
are recomputed only when the map itself has changed.
"""

from qgis.PyQt.QtCore import QMetaType
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsProperty,
    QgsRectangle,
    QgsRendererCategory,
    QgsVectorLayer,
)

from .coords import v2q
from .i18n import tr

GROUP_NAME = "Valetudo"
AREA_ENTITIES = {"no_go_area", "no_mop_area", "active_zone"}
POINT_ENTITIES = {"robot_position", "charger_location", "go_to_target", "obstacle"}

# Valetudo's robot angle is in degrees, clockwise on screen (our Y flip keeps the
# on-screen orientation). If the arrow points the wrong way, adjust this offset.
ROBOT_ANGLE_OFFSET = 0

STR = QMetaType.Type.QString
DBL = QMetaType.Type.Double


def layer_names():
    """Translated layer names, by role."""
    return {
        "rooms": tr("Valetudo - map"),
        "areas": tr("Valetudo - zones"),
        "lines": tr("Valetudo - lines"),
        "robot": tr("Valetudo - robot"),
    }


def _points_renderer():
    """One symbol per point type, so the robot is not confused with its dock."""
    def marker(**props):
        return QgsMarkerSymbol.createSimple({k: str(v) for k, v in props.items()})

    robot = marker(name="arrow", color="#e31a1c", outline_color="black", size=6)
    robot.setDataDefinedAngle(QgsProperty.fromExpression(
        f'coalesce("angle", 0) + {ROBOT_ANGLE_OFFSET}'))

    categories = [
        QgsRendererCategory("robot_position", robot, tr("Robot")),
        QgsRendererCategory("charger_location", marker(
            name="square", color="#33a02c", outline_color="black", size=4), tr("Dock")),
        QgsRendererCategory("go_to_target", marker(
            name="cross", color="#1f78b4", outline_color="#1f78b4",
            outline_width=0.8, size=5), tr("Go-to target")),
        QgsRendererCategory("obstacle", marker(
            name="triangle", color="#ff7f00", outline_color="black", size=3), tr("Obstacle")),
    ]
    return QgsCategorizedSymbolRenderer("type", categories)


# ---------------------------------------------------------------------------
# Reading the Valetudo JSON
# ---------------------------------------------------------------------------

def _runs(layer):
    """Horizontal runs (x, y, length) in pixels."""
    cp = layer.get("compressedPixels")
    if cp is not None:
        for i in range(0, len(cp), 3):
            yield cp[i], cp[i + 1], cp[i + 2]
    else:  # older Valetudo versions
        px = layer.get("pixels", [])
        for i in range(0, len(px), 2):
            yield px[i], px[i + 1], 1


def _layer_geometry(layer, ps):
    rects = [
        QgsGeometry.fromRect(QgsRectangle(x * ps, -(y + 1) * ps, (x + n) * ps, -y * ps))
        for x, y, n in _runs(layer)
    ]
    return QgsGeometry.unaryUnion(rects) if rects else QgsGeometry()


def _signature(map_json):
    """Cheap fingerprint of the map, to know whether rooms must be recomputed."""
    sig = []
    for layer in map_json.get("layers", []):
        meta = layer.get("metaData") or {}
        pixels = layer.get("compressedPixels") or layer.get("pixels") or []
        sig.append((layer.get("type"), meta.get("segmentId"), meta.get("name"), len(pixels)))
    return tuple(sig)


def _feature(lyr, geom, attrs):
    f = QgsFeature(lyr.fields())
    f.setGeometry(geom)
    f.setAttributes(attrs)
    return f


def _replace_features(lyr, feats):
    pr = lyr.dataProvider()
    pr.truncate()
    pr.addFeatures(feats)
    lyr.updateExtents()
    lyr.triggerRepaint()


def _memory_layer(geom_type, name, fields):
    lyr = QgsVectorLayer(geom_type, name, "memory")
    lyr.setCrs(QgsCoordinateReferenceSystem())
    lyr.dataProvider().addAttributes(fields)
    lyr.updateFields()
    return lyr


# ---------------------------------------------------------------------------
# Layer manager
# ---------------------------------------------------------------------------

class ValetudoLayers:
    def __init__(self, project):
        self.project = project
        self._ids = {}          # role -> layer id (we do NOT keep the objects:
        self._signature = None  # if the user deletes a layer, the Python object dies)

    # -- access ---------------------------------------------------------------

    def layer(self, role):
        lid = self._ids.get(role)
        return self.project.mapLayer(lid) if lid else None

    def exists(self):
        return bool(self._ids) and all(self.layer(r) for r in self._ids)

    # -- life cycle -----------------------------------------------------------

    def _create(self):
        names = layer_names()
        layers = {
            "rooms": _memory_layer("Polygon", names["rooms"], [
                QgsField("type", STR), QgsField("segment_id", STR), QgsField("name", STR)]),
            "areas": _memory_layer("Polygon", names["areas"], [QgsField("type", STR)]),
            "lines": _memory_layer("LineString", names["lines"], [QgsField("type", STR)]),
            "robot": _memory_layer("Point", names["robot"], [
                QgsField("type", STR), QgsField("angle", DBL)]),
        }
        self.project.addMapLayers(list(layers.values()), False)

        root = self.project.layerTreeRoot()
        group = root.findGroup(GROUP_NAME) or root.insertGroup(0, GROUP_NAME)
        for role in ("robot", "lines", "areas", "rooms"):  # top to bottom
            lyr = layers[role]
            lyr.setCrs(QgsCoordinateReferenceSystem())     # undo any default CRS
            group.addLayer(lyr)

        layers["robot"].setRenderer(_points_renderer())
        layers["rooms"].renderer().symbol().setOpacity(0.6)

        self._ids = {role: lyr.id() for role, lyr in layers.items()}
        self._signature = None

    def remove(self):
        ids = [lid for lid in self._ids.values() if self.project.mapLayer(lid)]
        if ids:
            self.project.removeMapLayers(ids)
        group = self.project.layerTreeRoot().findGroup(GROUP_NAME)
        if group is not None and not group.children():
            group.parent().removeChildNode(group)
        self._ids = {}
        self._signature = None

    # -- update ---------------------------------------------------------------

    def update(self, map_json):
        """Refresh the layers. Returns True if they have just been created."""
        created = False
        if not self.exists():
            self.remove()   # clean up leftovers if the user deleted one of them
            self._create()
            created = True

        sig = _signature(map_json)
        if sig != self._signature:
            self._signature = sig
            self._update_rooms(map_json)
        self._update_entities(map_json)
        return created

    def _update_rooms(self, map_json):
        rooms = self.layer("rooms")
        default_ps = map_json.get("pixelSize", 5)
        feats = []
        for layer in map_json.get("layers", []):
            meta = layer.get("metaData") or {}
            geom = _layer_geometry(layer, layer.get("pixelSize", default_ps))
            if not geom.isEmpty():
                feats.append(_feature(rooms, geom, [
                    layer.get("type"),
                    str(meta["segmentId"]) if meta.get("segmentId") is not None else None,
                    meta.get("name"),
                ]))
        _replace_features(rooms, feats)

    def _update_entities(self, map_json):
        areas, lines, robot = self.layer("areas"), self.layer("lines"), self.layer("robot")
        area_f, line_f, point_f = [], [], []
        for ent in map_json.get("entities", []):
            etype = ent.get("type")
            c = ent.get("points", [])
            pts = [v2q(c[i], c[i + 1]) for i in range(0, len(c) - 1, 2)]
            if not pts:
                continue
            if etype in POINT_ENTITIES or len(pts) == 1:
                angle = (ent.get("metaData") or {}).get("angle")
                point_f.append(_feature(robot, QgsGeometry.fromPointXY(pts[0]), [etype, angle]))
            elif etype in AREA_ENTITIES:
                area_f.append(_feature(areas, QgsGeometry.fromPolygonXY([pts + [pts[0]]]), [etype]))
            else:  # path, predicted_path, virtual_wall...
                line_f.append(_feature(lines, QgsGeometry.fromPolylineXY(pts), [etype]))
        # Features are drawn in insertion order: put the robot last, on top of its dock.
        point_f.sort(key=lambda f: f["type"] == "robot_position")
        _replace_features(areas, area_f)
        _replace_features(lines, line_f)
        _replace_features(robot, point_f)

    # -- selection --------------------------------------------------------------

    def selected_segment_ids(self):
        rooms = self.layer("rooms")
        if rooms is None:
            return []
        return [f["segment_id"] for f in rooms.selectedFeatures()
                if f["type"] == "segment" and f["segment_id"]]
