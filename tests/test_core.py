import math

import pytest

from roadtools.core import (cumulative_lengths, km_distances, km_ends, measure_parts,
                            piece_kms, piece_start_measure, safe_filename, utm_epsg)


@pytest.mark.parametrize("lon, lat, epsg", [
    (37.6, 55.75, 32637),      # Moscow
    (30.3, 59.94, 32636),      # Saint Petersburg
    (-74.0, 40.7, 32618),      # New York
    (151.2, -33.9, 32756),     # Sydney, southern hemisphere
    (5.3, 60.4, 32632),        # Bergen: the widened zone 32V
    (15.6, 78.2, 32633),       # Svalbard: zone 33X
    (180.0, 0.0, 32660),       # antimeridian stays in zone 60
])
def test_utm_epsg(lon, lat, epsg):
    assert utm_epsg(lon, lat) == epsg


def test_utm_epsg_rejects_projected_coordinates():
    with pytest.raises(ValueError):
        utm_epsg(415000.0, 6180000.0)


def test_cumulative_lengths():
    assert cumulative_lengths([(0, 0), (3, 4), (3, 10)]) == [0.0, 5.0, 11.0]


def test_measure_parts_continuous():
    parts = [[(0, 0), (100, 0)], [(200, 0), (200, 50)]]
    m, total = measure_parts(parts, start=1500)
    assert m == [[1500, 1600], [1600, 1650]]
    assert total == 150


def test_measure_parts_restarting_each_part():
    parts = [[(0, 0), (100, 0)], [(200, 0), (200, 50)]]
    m, _ = measure_parts(parts, start=1500, continuous=False)
    assert m[1] == [1500, 1550]


def test_measure_parts_keeps_z():
    m, total = measure_parts([[(0, 0, 10), (0, 30, 12)]])
    assert m == [[0.0, 30.0]] and total == 30.0


def test_km_ends_ignores_storage_order():
    assert km_ends([12, 10, 11]) == {10: 11, 11: 12, 12: None}


def test_km_distances_in_km_order():
    # posts stored out of order; the 11th kilometre is 1004 m long
    d = km_distances({12: 2010.0, 10: 0.0, 11: 1006.0})
    assert d == {10: 1006.0, 11: 1004.0, 12: None}


def test_piece_kms_when_axis_starts_before_first_post():
    # axis starts at km 9 + 350 m; posts at 10, 11, 12 -> four pieces
    assert piece_kms([11, 10, 12], start_km=9) == [9, 10, 11, 12]


def test_piece_kms_when_axis_starts_at_a_post():
    assert piece_kms([10, 11], start_km=10) == [10, 11]


def test_piece_start_measure():
    assert piece_start_measure(9, start_km=9, axis_start_m=9350) == 9350
    assert piece_start_measure(10, start_km=9, axis_start_m=9350) == 10000


@pytest.mark.parametrize("name, expected", [
    ("Plan: sheet 1/3", "Plan_ sheet 1_3"),
    ("  ok.  ", "ok"),
    ("???", "_"),
    ("", "output"),
])
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected
