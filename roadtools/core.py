"""Geometry and bookkeeping logic of the toolbox, independent of arcpy.

Everything here works on plain numbers and coordinate tuples, so it can be
unit-tested without an ArcGIS licence. The tools in RoadGISToolbox.pyt read
the data with arcpy, call these functions and write the results back.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

Point = Sequence[float]


# ---------------------------------------------------------------- UTM zones

def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS 84 / UTM zone that contains a point.

    Includes the two irregular areas of the UTM grid: zone 32V over
    south-west Norway and zones 31X-37X around Svalbard.
    """
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"Not geographic coordinates: lon={lon}, lat={lat}")
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84:
        if 0 <= lon < 9:
            zone = 31
        elif 9 <= lon < 21:
            zone = 33
        elif 21 <= lon < 33:
            zone = 35
        elif 33 <= lon < 42:
            zone = 37
    return (32600 if lat >= 0 else 32700) + zone


# ---------------------------------------------------------------- M values

def cumulative_lengths(points: Sequence[Point]) -> list[float]:
    """Distance from the first vertex to every vertex, along the line."""
    out = [0.0]
    for (x0, y0, *_), (x1, y1, *_) in zip(points, points[1:]):
        out.append(out[-1] + math.hypot(x1 - x0, y1 - y0))
    return out


def measure_parts(parts: Sequence[Sequence[Point]], start: float = 0.0,
                  continuous: bool = True) -> tuple[list[list[float]], float]:
    """M values for every vertex of a (multipart) line.

    `parts` are vertex lists in a projected coordinate system, so distances
    are in metres. M starts at `start` and grows with the distance along the
    line. With `continuous`, a second part continues from where the first
    one ended; otherwise every part starts again at `start`.

    Returns the M values per part and the total length of all parts.
    """
    measures, total = [], 0.0
    for part in parts:
        offsets = cumulative_lengths(part)
        base = start + (total if continuous else 0.0)
        measures.append([base + d for d in offsets])
        total += offsets[-1] if offsets else 0.0
    return measures, total


# ---------------------------------------------------------------- kilometre posts

def km_ends(post_kms: Iterable[float]) -> dict[float, float | None]:
    """For every kilometre post, the number of the next post (None for the last)."""
    kms = sorted(set(post_kms))
    return {km: (kms[i + 1] if i + 1 < len(kms) else None) for i, km in enumerate(kms)}


def km_distances(measure_by_km: dict[float, float]) -> dict[float, float | None]:
    """Actual length of every kilometre: distance along the axis to the next post.

    The input maps the post number to its measure on the axis. Posts are
    taken in kilometre order, not in the order they are stored.
    """
    kms = sorted(measure_by_km)
    out: dict[float, float | None] = {}
    for i, km in enumerate(kms):
        out[km] = (abs(measure_by_km[kms[i + 1]] - measure_by_km[km])
                   if i + 1 < len(kms) else None)
    return out


def piece_kms(post_kms: Iterable[float], start_km: float) -> list[float]:
    """Kilometre number of every piece after the axis is split at the posts.

    Pieces are numbered from the start of the axis (ORIG_SEQ = 1, 2, ...).
    If the axis begins before the first post, the first piece belongs to
    the kilometre in which the axis starts (`start_km`).
    """
    kms = sorted(set(post_kms))
    if not kms:
        return [start_km]
    return kms if kms[0] == start_km else [start_km] + kms


def piece_start_measure(km: float, start_km: float, axis_start_m: float) -> float:
    """Absolute measure (metres from the start of the road) where a piece begins.

    Every piece starts at its kilometre post, km * 1000 m, except the very
    first one, which starts where the axis itself starts.
    """
    return axis_start_m if km == start_km else km * 1000.0


# ---------------------------------------------------------------- misc

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(name: str, default: str = "output") -> str:
    """A name that Windows accepts as a file name."""
    cleaned = _BAD_CHARS.sub("_", name).strip(" .")
    return cleaned or default
