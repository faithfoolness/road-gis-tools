"""A tiny stand-in for arcpy: enough to load the toolbox and test geometry glue.

It is not a simulation of ArcGIS. It lets the tests check that the .pyt file
imports, that every tool defines valid parameters, and that M values are
transferred onto line vertices correctly.
"""

import types


class ExecuteError(Exception):
    pass


class _Filter:
    def __init__(self):
        self.list = []
        self.type = None


class Parameter:
    def __init__(self, displayName, name, datatype, parameterType, direction, multiValue=False):
        self.displayName, self.name, self.datatype = displayName, name, datatype
        self.parameterType, self.direction, self.multiValue = parameterType, direction, multiValue
        self.filter = _Filter()
        self.parameterDependencies = []
        self.value = None


class Point:
    def __init__(self, X=None, Y=None, Z=None, M=None, ID=None):
        self.X, self.Y, self.Z, self.M = X, Y, Z, M


class Array(list):
    def add(self, item):
        self.append(item)


class Polyline:
    """Parts of Points; projectAs applies a shift, enough to tell CRSs apart."""

    def __init__(self, parts, spatial_reference=None, has_z=False, has_m=False):
        self.parts = [list(p) for p in parts]
        self.spatialReference = spatial_reference
        self.hasM = has_m

    def __iter__(self):
        return iter(self.parts)

    def projectAs(self, sr):
        dx, dy = sr.shift
        return Polyline([[Point(p.X + dx, p.Y + dy, p.Z, p.M) for p in part] for part in self.parts], sr)


class SpatialReference:
    def __init__(self, code=None, shift=(0.0, 0.0), type_="Projected", name="Test CS"):
        self.factoryCode, self.shift, self.type, self.name = code, shift, type_, name


def install():
    """Register the fake as `arcpy` (with the submodules the toolbox touches)."""
    import sys

    mod = types.ModuleType("arcpy")
    for name in ("ExecuteError", "Parameter", "Point", "Array", "Polyline", "SpatialReference"):
        setattr(mod, name, globals()[name])
    for sub in ("da", "management", "conversion", "analysis", "edit", "lr", "mp", "env"):
        setattr(mod, sub, types.SimpleNamespace())
    sys.modules["arcpy"] = mod
    return mod
