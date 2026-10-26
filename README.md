# Road GIS Tools

Tools that automate routine steps of road inventory work, most of them as an
ArcGIS Pro toolbox (`RoadGISToolbox.pyt`): linear referencing of road axes, kilometre sections,
lengths in the right projection, and exports for people who do not use GIS.

## Why

Road inventory in GIS is mostly the same handful of operations repeated on
hundreds of roads: put measures on an axis, cut it at the kilometre posts,
check the actual length of every kilometre, hand the result to a client as
KMZ. Done by hand in ArcGIS Pro, each of these is a dozen clicks and an easy
place to make a mistake. I wrote these tools for my team so that every step
is one dialog with the same result every time.

## Tools

| Tool | Category | What it does |
|---|---|---|
| **Axis Measures** | Linear referencing | Writes M values to every vertex of the axes: absolute start + distance along the line, measured in a chosen projected system. Optionally fills absolute end and length. |
| **Kilometre Sections** | Linear referencing | Locates kilometre posts on the axis, fills their attributes, splits the axis into kilometre sections with M counted from the posts, and buffers every section. |
| **Length in UTM Zones** | Linear referencing | Length of every line in the UTM zone that contains its midpoint, for road networks that span several zones. |
| **Axis End Points** | Export | Start and end markers of every axis with coordinates, styled green/red and exported to KMZ. |
| **Lines to KMZ** | Export | Lines to KMZ with a fixed style and only the chosen attributes. |
| **Export All Layouts** | Export | Every layout of the project to JPEG or PNG in one go. |
| **JSON Folder to GDB** | Conversion | All `.json` / `.geojson` files of a folder to feature classes in a file geodatabase. |

## How the linear referencing works

**Axis Measures.** Road axes are usually stored in geographic coordinates, but
measures must be in metres. The tool projects each line to the chosen system
only to take distances, and writes the M values back onto the original
vertices, so the data stays in its own coordinate system and keeps its
object IDs. Multipart lines are measured continuously across parts.

**Kilometre Sections.** Input: one road axis and its kilometre posts.

```
axis:  StartKM = 9, absolute start = 9 350 m
posts:        10          11          12
       |------|-----------|-----------|----->
piece:    9        10          11       12
M from:  9350    10000       11000    12000
```

1. The axis is copied to the metric system and measured from its absolute start.
2. Every post is located on the axis (*Locate Features Along Routes*): its
   measure, its offset from the axis and the actual length of the kilometre to
   the next post are written to the post.
3. Posts are snapped to the axis, the axis is split at them, and every piece
   gets its kilometre number and M values that start at `km × 1000 m`. The first
   piece starts where the axis itself starts.
4. Every section is buffered (50 m by default), ready for selecting objects
   by kilometre.

Fields the tool fills on the posts (created if missing): `KM_END`, `MEASURE`,
`OFFSET`, `KM_LENGTH`, `LON`, `LAT`. Outputs are written to the geodatabase of
the axis: `ROUTE_M`, `POSTS_ON_ROUTE`, `KM_SECTIONS`, `KM_BUFFERS`.

## Installation

1. Download or clone the repository.
2. In ArcGIS Pro: **Catalog → Toolboxes → Add Toolbox** and pick `RoadGISToolbox.pyt`.
   Keep the `roadtools` folder next to it.

Requires ArcGIS Pro 2.9 or later.

## Structure

```
RoadGISToolbox.pyt     the tools: parameters, arcpy calls
roadtools/core.py      geometry and bookkeeping logic, no arcpy
tests/                 pytest: core logic and toolbox structure (runs without ArcGIS)
```

The calculations that are easy to get wrong (UTM zone selection including the
Norway and Svalbard exceptions, measures across multipart lines, ordering of
kilometre posts) live in `roadtools/core.py` and are unit-tested:

```bash
pip install pytest
pytest
```

The toolbox itself is checked against a minimal stand-in for `arcpy`, which
verifies that it loads, that every tool defines consistent parameters and that
M values are transferred to the original vertices.
