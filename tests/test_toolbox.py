"""Load RoadGISToolbox.pyt against a fake arcpy and check its structure."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

from tests import fake_arcpy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tbx():
    arcpy = fake_arcpy.install()
    loader = importlib.machinery.SourceFileLoader("road_gis_toolbox", str(ROOT / "RoadGISToolbox.pyt"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module, arcpy


def test_every_tool_has_valid_parameters(tbx):
    module, _ = tbx
    tools = module.Toolbox().tools
    assert len(tools) == 7
    for cls in tools:
        tool = cls()
        assert tool.label and tool.description and tool.category
        params = tool.getParameterInfo()
        names = [p.name for p in params]
        assert len(names) == len(set(names)), cls.__name__
        for p in params:
            for dep in p.parameterDependencies:          # field lists point at real inputs
                assert dep in names, (cls.__name__, p.name, dep)
            assert p.parameterType in ("Required", "Optional")


def test_measures_use_metric_distances_but_keep_coordinates(tbx):
    module, arcpy = tbx
    geographic = arcpy.SpatialReference(4326, type_="Geographic")
    metric = arcpy.SpatialReference(32637, shift=(500000.0, 0.0))
    line = arcpy.Polyline([[arcpy.Point(0, 0), arcpy.Point(3, 4), arcpy.Point(3, 10)]], geographic)

    measured, length = module._measured(line, metric, start=1000.0)

    assert length == 11.0
    vertices = list(measured)[0]
    assert [(p.X, p.Y) for p in vertices] == [(0, 0), (3, 4), (3, 10)]   # not moved
    assert [p.M for p in vertices] == [1000.0, 1005.0, 1011.0]
    assert measured.hasM and measured.spatialReference is geographic


def test_geographic_cs_is_rejected_for_measuring(tbx):
    module, arcpy = tbx
    with pytest.raises(arcpy.ExecuteError):
        module._require_projected(arcpy.SpatialReference(4326, type_="Geographic"))
