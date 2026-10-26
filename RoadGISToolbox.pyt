# -*- coding: utf-8 -*-
"""Road GIS Toolbox: ArcGIS Pro geoprocessing tools for road inventory work.

Add this file to a project (Catalog > Toolboxes > Add Toolbox). The pure
geometry logic lives in the `roadtools` package next to it and is covered
by unit tests that run without ArcGIS.
"""

import importlib
import os
import sys

import arcpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roadtools.core as core  # noqa: E402

importlib.reload(core)          # pick up edits without restarting ArcGIS Pro

WGS84 = 4326
NUMERIC_FIELDS = ["Short", "Long", "Float", "Double"]


# ======================================================================
# shared helpers
# ======================================================================

def _param(display, name, datatype, required=True, direction="Input", **kw):
    p = arcpy.Parameter(displayName=display, name=name, datatype=datatype,
                        parameterType="Required" if required else "Optional",
                        direction=direction)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _field_param(display, name, depends_on, required=True, numeric=True):
    p = _param(display, name, "Field", required)
    p.parameterDependencies = [depends_on]
    if numeric:
        p.filter.list = NUMERIC_FIELDS
    return p


def _spatial_ref(param):
    sr = arcpy.SpatialReference()
    sr.loadFromString(param.valueAsText)
    return sr


def _require_projected(sr):
    if sr.type != "Projected":
        raise arcpy.ExecuteError(
            f"'{sr.name}' is not a projected coordinate system. Lengths and measures "
            "must be computed in metres: choose UTM or a local projected system.")


def _active_map():
    aprx = arcpy.mp.ArcGISProject("CURRENT")
    return aprx.activeMap or aprx.listMaps()[0]


def _ensure_field(table, name, ftype, alias=None):
    if name not in {f.name for f in arcpy.ListFields(table)}:
        arcpy.management.AddField(table, name, ftype, field_alias=alias or name)


def _with_measures(polyline, measured_parts):
    """A copy of `polyline` (same coordinate system and vertices) with new M values."""
    parts = arcpy.Array()
    for part, ms in zip(polyline, measured_parts):
        vertices = arcpy.Array()
        for pnt, m in zip(part, ms):
            vertices.add(arcpy.Point(pnt.X, pnt.Y, pnt.Z, m))
        parts.add(vertices)
    has_z = any(pnt.Z is not None for part in polyline for pnt in part)
    return arcpy.Polyline(parts, polyline.spatialReference, has_z, True)


def _measured(polyline, metric_sr, start):
    """Measure a line in a metric CRS, keep its own coordinates.

    Distances are taken from the line projected to `metric_sr`; the M values
    are then written onto the original vertices, so the geometry itself is
    not moved to another coordinate system.
    """
    projected = polyline.projectAs(metric_sr)
    xy = [[(p.X, p.Y) for p in part] for part in projected]
    if [len(p) for p in xy] != [len(list(p)) for p in polyline]:
        raise arcpy.ExecuteError("Projection changed the number of vertices; cannot transfer measures.")
    measures, total = core.measure_parts(xy, start=start)
    return _with_measures(polyline, measures), total


# ======================================================================
# toolbox
# ======================================================================

class Toolbox:
    def __init__(self):
        self.label = "Road GIS Toolbox"
        self.alias = "roadgis"
        self.tools = [AxisMeasures, KilometreSections, AxisEndPoints,
                      LinesToKMZ, UTMLength, JSONFolderToGDB, ExportLayouts]


# ----------------------------------------------------------------------
class AxisMeasures:
    """Recalculate M values of road axes from their absolute start."""

    def __init__(self):
        self.label = "Axis Measures"
        self.category = "Linear referencing"
        self.description = (
            "Writes M values to every vertex of the selected axes: absolute start of the axis "
            "plus the distance along the line, measured in the chosen projected coordinate "
            "system. Optionally fills the absolute end and the length of each axis.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        axes = _param("Road axes", "axes", "GPFeatureLayer")
        axes.filter.list = ["Polyline"]
        return [
            axes,
            _param("Coordinate system for measuring", "metric_cs", "GPCoordinateSystem"),
            _field_param("Absolute start field, m", "start_field", "axes"),
            _field_param("Absolute end field, m", "end_field", "axes", required=False),
            _field_param("Length field, m", "length_field", "axes", required=False),
        ]

    def updateMessages(self, parameters):
        axes = parameters[0]
        if axes.value and not arcpy.Describe(axes.valueAsText).hasM:
            axes.setErrorMessage("The axes have no M values. Copy them with the 'Output has M "
                                 "values' environment enabled, then run the tool on the copy.")

    def execute(self, parameters, messages):
        axes = parameters[0].valueAsText
        metric = _spatial_ref(parameters[1])
        _require_projected(metric)
        start_f, end_f, len_f = (p.valueAsText for p in parameters[2:5])

        fields = ["SHAPE@", start_f] + [f for f in (end_f, len_f) if f]
        count = 0
        with arcpy.da.UpdateCursor(axes, fields) as cursor:      # respects the selection
            for row in cursor:
                if row[0] is None:
                    continue
                start = row[1] if row[1] is not None else 0.0
                row[0], length = _measured(row[0], metric, start)
                row[1] = start
                extra = iter(range(2, len(fields)))
                if end_f:
                    row[next(extra)] = start + length
                if len_f:
                    row[next(extra)] = length
                cursor.updateRow(row)
                count += 1
        messages.addMessage(f"Measured {count} axes in {metric.name}.")


# ----------------------------------------------------------------------
class KilometreSections:
    """Split a road axis at kilometre posts and measure every kilometre."""

    def __init__(self):
        self.label = "Kilometre Sections"
        self.category = "Linear referencing"
        self.description = (
            "Locates kilometre posts on the road axis, fills their attributes (next post, measure "
            "on the axis, offset from the axis, actual kilometre length, coordinates), splits the "
            "axis into kilometre sections with M values counted from the posts, and builds a "
            "buffer around every section.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        axis = _param("Road axis (one line)", "axis", "GPFeatureLayer")
        axis.filter.list = ["Polyline"]
        posts = _param("Kilometre posts", "posts", "GPFeatureLayer")
        posts.filter.list = ["Point"]
        return [
            axis, posts,
            _param("Coordinate system for measuring", "metric_cs", "GPCoordinateSystem"),
            _field_param("Post number field (km)", "km_field", "posts"),
            _field_param("Axis start, km", "start_km_field", "axis"),
            _field_param("Axis start, absolute m", "start_m_field", "axis"),
            _param("Search distance for posts", "tolerance", "GPLinearUnit", value="100 Meters"),
            _param("Section buffer width", "buffer", "GPLinearUnit", value="50 Meters"),
        ]

    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True
        axis, posts = parameters[0].valueAsText, parameters[1].valueAsText
        metric = _spatial_ref(parameters[2])
        _require_projected(metric)
        km_f, start_km_f, start_m_f = (p.valueAsText for p in parameters[3:6])
        tolerance, buffer_width = parameters[6].valueAsText, parameters[7].valueAsText

        gdb = arcpy.Describe(axis).path
        out = {name: os.path.join(gdb, name) for name in
               ("ROUTE_M", "POSTS_M", "POSTS_ON_ROUTE", "KM_SECTIONS", "KM_BUFFERS")}

        # --- the axis: one line with its start
        rows = list(arcpy.da.SearchCursor(axis, [start_km_f, start_m_f]))
        if len(rows) != 1:
            raise arcpy.ExecuteError(f"Expected exactly one axis, found {len(rows)}. Select one.")
        start_km, start_m = rows[0]
        if start_km is None or start_m is None:
            raise arcpy.ExecuteError("Fill the start of the axis (km and absolute metres) first.")

        # --- fill post attributes that depend only on the posts themselves
        for name, ftype, alias in (("KM_END", "DOUBLE", "Next post, km"),
                                   ("MEASURE", "DOUBLE", "Measure on axis, m"),
                                   ("OFFSET", "DOUBLE", "Offset from axis, m"),
                                   ("KM_LENGTH", "DOUBLE", "Kilometre length, m")):
            _ensure_field(posts, name, ftype, alias)
        _ensure_field(posts, "LON", "DOUBLE", "Longitude")
        _ensure_field(posts, "LAT", "DOUBLE", "Latitude")
        arcpy.management.CalculateGeometryAttributes(
            posts, [["LON", "POINT_X"], ["LAT", "POINT_Y"]],
            coordinate_system=arcpy.SpatialReference(WGS84))

        post_kms = [r[0] for r in arcpy.da.SearchCursor(posts, [km_f]) if r[0] is not None]
        if not post_kms:
            raise arcpy.ExecuteError(f"No kilometre numbers in field {km_f}.")
        ends = core.km_ends(post_kms)
        with arcpy.da.UpdateCursor(posts, [km_f, "KM_END"]) as cur:
            for row in cur:
                row[1] = ends.get(row[0])
                cur.updateRow(row)

        # --- metric copies; the route gets absolute measures
        with arcpy.EnvManager(outputMFlag="Enabled", outputCoordinateSystem=metric):
            arcpy.management.CopyFeatures(axis, out["ROUTE_M"])
        with arcpy.EnvManager(outputCoordinateSystem=metric):
            arcpy.management.CopyFeatures(posts, out["POSTS_M"])
        with arcpy.da.UpdateCursor(out["ROUTE_M"], ["SHAPE@"]) as cur:
            for row in cur:
                row[0], _ = _measured(row[0], metric, start_m)
                cur.updateRow(row)
        _ensure_field(out["ROUTE_M"], "ROUTE_ID", "SHORT")
        arcpy.management.CalculateField(out["ROUTE_M"], "ROUTE_ID", "1")

        # --- where every post lies on the route
        table = os.path.join("memory", "post_locations")
        arcpy.lr.LocateFeaturesAlongRoutes(
            out["POSTS_M"], out["ROUTE_M"], "ROUTE_ID", tolerance, table,
            "RID POINT MEAS", "FIRST", "DISTANCE", "ZERO", "FIELDS", "M_DIRECTON")
        located = {}
        for km, meas, dist in arcpy.da.SearchCursor(table, [km_f, "MEAS", "DISTANCE"]):
            located.setdefault(km, (meas, dist))
        arcpy.management.Delete(table)
        missing = sorted(set(post_kms) - set(located))
        if missing:
            messages.addWarningMessage(f"Posts farther than {tolerance} from the axis: {missing}")

        lengths = core.km_distances({km: m for km, (m, _) in located.items()})
        with arcpy.da.UpdateCursor(posts, [km_f, "MEASURE", "OFFSET", "KM_LENGTH"]) as cur:
            for row in cur:
                if row[0] in located:
                    meas, dist = located[row[0]]
                    row[1], row[2], row[3] = round(meas, 2), round(abs(dist), 2), lengths.get(row[0])
                    cur.updateRow(row)

        # --- split the route at the posts and re-measure every kilometre
        arcpy.management.CopyFeatures(out["POSTS_M"], out["POSTS_ON_ROUTE"])
        arcpy.edit.Snap(out["POSTS_ON_ROUTE"], [[out["ROUTE_M"], "EDGE", tolerance]])
        with arcpy.EnvManager(outputMFlag="Enabled"):
            arcpy.management.SplitLineAtPoint(out["ROUTE_M"], out["POSTS_ON_ROUTE"],
                                              out["KM_SECTIONS"], tolerance)
        piece_km = core.piece_kms(located, start_km)
        _ensure_field(out["KM_SECTIONS"], "KM", "LONG", "Kilometre")
        with arcpy.da.UpdateCursor(out["KM_SECTIONS"], ["SHAPE@", "ORIG_SEQ", "KM"]) as cur:
            for row in cur:
                seq = row[1]
                if not 1 <= seq <= len(piece_km):
                    raise arcpy.ExecuteError(f"Unexpected section number {seq}; check the posts.")
                km = piece_km[seq - 1]
                start = core.piece_start_measure(km, start_km, start_m)
                row[0], _ = _measured(row[0], metric, start)
                row[2] = km
                cur.updateRow(row)
        arcpy.management.DeleteField(out["KM_SECTIONS"], ["KM"], "KEEP_FIELDS")

        arcpy.analysis.Buffer(out["KM_SECTIONS"], out["KM_BUFFERS"], buffer_width,
                              "FULL", "FLAT", "NONE", method="PLANAR")

        for key, alias in (("ROUTE_M", "Road axis, measured"),
                           ("POSTS_ON_ROUTE", "Kilometre posts on axis"),
                           ("KM_SECTIONS", "Kilometre sections"),
                           ("KM_BUFFERS", "Kilometre section buffers")):
            arcpy.AlterAliasName(out[key], alias)
        arcpy.management.Delete(out["POSTS_M"])
        m = _active_map()
        for key in ("KM_BUFFERS", "KM_SECTIONS", "POSTS_ON_ROUTE"):
            m.addDataFromPath(out[key])
        messages.addMessage(f"{len(piece_km)} kilometre sections written to {gdb}.")


# ----------------------------------------------------------------------
class AxisEndPoints:
    """Start and end markers of road axes, exported to KMZ."""

    START, END = "Start", "End"

    def __init__(self):
        self.label = "Axis End Points"
        self.category = "Export"
        self.description = (
            "Creates a point at the start and at the end of every axis with its longitude and "
            "latitude, styles starts as green and ends as red triangles and exports them to KMZ.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        axes = _param("Road axes", "axes", "GPFeatureLayer")
        axes.filter.list = ["Polyline"]
        name_field = _field_param("Name field to keep (optional)", "name_field", "axes",
                                  required=False, numeric=False)
        return [axes, name_field, _param("Output folder for KMZ", "folder", "DEFolder")]

    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True
        axes, name_f, folder = (p.valueAsText for p in parameters)
        gdb = arcpy.Describe(axes).path
        points = os.path.join(gdb, "AxisEndPoints")
        temp = os.path.join("memory", "axis_ends")

        arcpy.management.FeatureVerticesToPoints(axes, points, "START")
        arcpy.management.FeatureVerticesToPoints(axes, temp, "END")
        for fc, label in ((points, self.START), (temp, self.END)):
            _ensure_field(fc, "PointType", "TEXT", "Point type")
            arcpy.management.CalculateField(fc, "PointType", repr(label), "PYTHON3")
        arcpy.management.Append(temp, points, "NO_TEST")
        arcpy.management.Delete(temp)
        keep = ["PointType"] + ([name_f] if name_f else [])
        arcpy.management.DeleteField(points, keep, "KEEP_FIELDS")
        _ensure_field(points, "Longitude", "DOUBLE")
        _ensure_field(points, "Latitude", "DOUBLE")
        arcpy.management.CalculateGeometryAttributes(
            points, [["Longitude", "POINT_X"], ["Latitude", "POINT_Y"]],
            coordinate_system=arcpy.SpatialReference(WGS84))
        arcpy.AlterAliasName(points, "Axis end points")

        layer = _active_map().addDataFromPath(points)
        sym = layer.symbology
        sym.updateRenderer("UniqueValueRenderer")
        sym.renderer.fields = ["PointType"]
        colours = {self.START: [0, 200, 0, 100], self.END: [230, 0, 0, 100]}
        for group in sym.renderer.groups:
            for item in group.items:
                value = item.values[0][0]
                if value in colours:
                    item.symbol.applySymbolFromGallery("Triangle 3")
                    item.symbol.color = {"RGB": colours[value]}
                    item.symbol.outlineColor = {"RGB": [0, 0, 0, 100]}
                    item.symbol.size = 14
                    item.label = value
        layer.symbology = sym

        kmz = os.path.join(folder, "AxisEndPoints.kmz")
        arcpy.conversion.LayerToKML(layer, kmz)
        messages.addMessage(f"Saved {kmz}")


# ----------------------------------------------------------------------
class LinesToKMZ:
    """Export lines to KMZ with a fixed style and selected attributes."""

    def __init__(self):
        self.label = "Lines to KMZ"
        self.category = "Export"
        self.description = ("Exports lines to KMZ as red 2 pt lines, keeping only the chosen "
                            "attributes. The source data is not modified.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        lines = _param("Lines", "lines", "GPFeatureLayer")
        lines.filter.list = ["Polyline"]
        fields = _field_param("Attributes to keep", "fields", "lines", required=False, numeric=False)
        fields.multiValue = True
        return [lines, _param("Output folder", "folder", "DEFolder"), fields,
                _param("KMZ name", "name", "GPString", required=False)]

    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True
        lines, folder, fields, name = (p.valueAsText for p in parameters)
        name = core.safe_filename(name or arcpy.Describe(lines).name, "lines")

        # work on a copy: deleting fields on a layer would delete them in the source data
        copy = os.path.join("memory", "lines_to_kmz")
        arcpy.management.CopyFeatures(lines, copy)
        if fields:
            keep = [f.strip("'") for f in fields.split(";")]
            removable = [f.name for f in arcpy.ListFields(copy)
                         if not f.required and f.name not in keep]
            if removable:
                arcpy.management.DeleteField(copy, removable)

        layer = arcpy.management.MakeFeatureLayer(copy, name)[0]
        sym = layer.symbology
        sym.renderer.symbol.color = {"RGB": [255, 0, 0, 100]}
        sym.renderer.symbol.size = 2
        layer.symbology = sym

        kmz = os.path.join(folder, f"{name}.kmz")
        arcpy.conversion.LayerToKML(layer, kmz)
        arcpy.management.Delete([layer, copy])
        messages.addMessage(f"Saved {kmz}")


# ----------------------------------------------------------------------
class UTMLength:
    """Line lengths, each measured in its own UTM zone."""

    def __init__(self):
        self.label = "Length in UTM Zones"
        self.category = "Linear referencing"
        self.description = (
            "Calculates the length of every line in the WGS 84 / UTM zone that contains its "
            "midpoint, so roads across several zones are measured without the distortion of a "
            "single projection.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        lines = _param("Lines", "lines", "GPFeatureLayer")
        lines.filter.list = ["Polyline"]
        return [lines, _field_param("Length field, m", "length_field", "lines"),
                _param("Decimal places", "decimals", "GPLong", value=3)]

    def execute(self, parameters, messages):
        lines, field = parameters[0].valueAsText, parameters[1].valueAsText
        decimals = int(parameters[2].value)
        wgs84 = arcpy.SpatialReference(WGS84)
        zones, count = {}, 0
        with arcpy.da.UpdateCursor(lines, ["SHAPE@", field]) as cursor:
            for geom, _ in cursor:
                if geom is None or geom.pointCount == 0:
                    continue
                mid = geom.positionAlongLine(0.5, True).projectAs(wgs84).firstPoint
                epsg = core.utm_epsg(mid.X, mid.Y)
                if epsg not in zones:
                    zones[epsg] = arcpy.SpatialReference(epsg)
                cursor.updateRow([geom, round(geom.projectAs(zones[epsg]).length, decimals)])
                count += 1
        messages.addMessage(f"{count} lines measured; UTM zones used: {sorted(zones)}")


# ----------------------------------------------------------------------
class JSONFolderToGDB:
    """Convert every JSON / GeoJSON file in a folder to a feature class."""

    def __init__(self):
        self.label = "JSON Folder to GDB"
        self.category = "Conversion"
        self.description = ("Converts all .json and .geojson files in a folder to feature classes "
                            "in a file geodatabase, which is created if it does not exist.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        return [_param("Folder with JSON files", "folder", "DEFolder"),
                _param("Output file geodatabase", "gdb", "DEWorkspace", direction="Output")]

    def execute(self, parameters, messages):
        folder, gdb = parameters[0].valueAsText, parameters[1].valueAsText
        if not arcpy.Exists(gdb):
            arcpy.management.CreateFileGDB(*os.path.split(gdb))
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".json", ".geojson")))
        failed = 0
        for f in files:
            name = arcpy.ValidateTableName(os.path.splitext(f)[0], gdb)
            try:
                arcpy.conversion.JSONToFeatures(os.path.join(folder, f), os.path.join(gdb, name))
                messages.addMessage(f"{f} -> {name}")
            except arcpy.ExecuteError as exc:
                failed += 1
                messages.addWarningMessage(f"{f}: {exc}")
        messages.addMessage(f"Converted {len(files) - failed} of {len(files)} files.")


# ----------------------------------------------------------------------
class ExportLayouts:
    """Export every layout of the project to an image."""

    def __init__(self):
        self.label = "Export All Layouts"
        self.category = "Export"
        self.description = "Exports every layout of the current project to JPEG or PNG."
        self.canRunInBackground = False

    def getParameterInfo(self):
        fmt = _param("Format", "format", "GPString", value="JPEG")
        fmt.filter.list = ["JPEG", "PNG"]
        return [_param("Output folder", "folder", "DEFolder"), fmt,
                _param("Resolution, dpi", "dpi", "GPLong", value=300)]

    def execute(self, parameters, messages):
        folder, fmt = parameters[0].valueAsText, parameters[1].valueAsText
        dpi = int(parameters[2].value)
        layouts = arcpy.mp.ArcGISProject("CURRENT").listLayouts()
        for layout in layouts:
            stem = os.path.join(folder, core.safe_filename(layout.name, "layout"))
            if fmt == "PNG":
                layout.exportToPNG(stem + ".png", resolution=dpi)
            else:
                layout.exportToJPEG(stem + ".jpg", resolution=dpi)
            messages.addMessage(f"Exported {layout.name}")
        messages.addMessage(f"{len(layouts)} layouts exported to {folder}")
