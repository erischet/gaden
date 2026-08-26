#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "trimesh",
#     "mapbox_earcut",
#     "rtree",
#     "shapely",
#     "networkx",
#     "scipy",
# ]
# ///
"""Generate a GADEN walls STL and per-obstacle STLs from a scenario's "_inner" mesh.

The "_inner" CAD part (Part_2 in the GADEN_tutorial.md convention) represents the free
(gas-carrying) volume of a scenario. This tool derives:

  - a single connected "walls" mesh: a shell around the outside of the inner volume,
    with configurable thickness, spanning the same height as the inner volume.
  - one mesh per fully-enclosed void found inside the inner volume ("blank space
    within the volume"), each extruded to the same height as the walls.

Obstacles that touch the outer boundary of the free space (e.g. a wall-attached
partition) are not enclosed voids and are folded into the connected walls shell
instead of becoming a separate obstacle - by construction, an object is only
recognized as an individual obstacle if free space surrounds it on every side.

All output meshes are written in the exact same coordinate frame as the input
"_inner" mesh (no re-centering or normalization), so every generated file, plus the
original "_inner" file, share one coordinate system.

This is a standalone authoring tool, not part of the ROS2 build. It needs packages
beyond the standard library (trimesh, shapely, ...), declared inline above (PEP 723)
and resolved/cached automatically by uv (https://docs.astral.sh/uv/) - no manual
venv/pip setup needed. Run it with `uv run generate_walls_and_obstacles.py ...`, or
directly as `./generate_walls_and_obstacles.py ...` (the shebang already invokes uv).

(mapbox_earcut is the polygon-triangulation engine trimesh's extrude_polygon
needs; manifold3d also provides one but is a much heavier compiled
mesh-boolean library this script has no other use for.)
"""

import argparse
import re
import sys
from pathlib import Path

import trimesh
from shapely.geometry import Polygon, box
from shapely.ops import unary_union


def load_inner_mesh(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh")
    if not mesh.is_watertight:
        raise ValueError(
            f"{path} is not watertight; a closed inner-volume mesh is required "
            "to determine free space vs. solid space."
        )
    return mesh


def slice_footprint(mesh: trimesh.Trimesh):
    """Cross-section the mesh at mid-height, returning (list of shapely Polygons, world transform)."""
    zmin, zmax = mesh.bounds[0][2], mesh.bounds[1][2]
    zmid = (zmin + zmax) / 2
    section = mesh.section(plane_origin=[0, 0, zmid], plane_normal=[0, 0, 1])
    if section is None:
        raise ValueError(
            "Could not slice the inner mesh at mid-height; it may not be a "
            "vertically-extruded (2.5D) volume, which this tool assumes."
        )
    planar, transform = section.to_2D()
    polygons = list(planar.polygons_full)
    if not polygons:
        raise ValueError("Slicing the inner mesh produced no closed regions.")
    return polygons, transform, zmax - zmin


def extrude_footprint(footprint, height: float, transform) -> trimesh.Trimesh:
    """Extrude a shapely Polygon/MultiPolygon to `height`, centered on the slicing plane,
    then map it into world coordinates via `transform`."""
    parts = list(footprint.geoms) if footprint.geom_type == "MultiPolygon" else [footprint]
    meshes = []
    for part in parts:
        if part.is_empty or part.area <= 0:
            continue
        m = trimesh.creation.extrude_polygon(part, height=height)
        m.apply_translation([0, 0, -height / 2])
        meshes.append(m)
    if not meshes:
        raise ValueError("Footprint has zero area; nothing to extrude.")
    combined = trimesh.util.concatenate(meshes)
    combined.apply_transform(transform)
    return combined


def derive_prefix(inner_path: Path) -> str:
    stem = inner_path.stem
    stripped = re.sub(r"[_-]inner$", "", stem, flags=re.IGNORECASE)
    return stripped if stripped else stem


def generate(inner_path: Path, output_dir: Path, prefix: str, thickness: float):
    mesh = load_inner_mesh(inner_path)
    polygons, transform, height = slice_footprint(mesh)

    exterior_shapes = [Polygon(poly.exterior) for poly in polygons]
    free_space = unary_union(exterior_shapes)

    xmin, ymin, xmax, ymax = free_space.bounds
    padded_box = box(xmin - thickness, ymin - thickness, xmax + thickness, ymax + thickness)
    walls_footprint = padded_box.difference(free_space)
    if walls_footprint.is_empty:
        raise ValueError(
            "Wall thickness produced an empty shell; increase --thickness."
        )

    walls_mesh = extrude_footprint(walls_footprint, height, transform)
    output_dir.mkdir(parents=True, exist_ok=True)
    walls_path = output_dir / f"{prefix}_walls.stl"
    walls_mesh.export(walls_path)
    print(f"wrote {walls_path}  (bounds: {walls_mesh.bounds.tolist()})")

    holes = []
    for poly in polygons:
        for ring in poly.interiors:
            holes.append(Polygon(ring))
    holes.sort(key=lambda h: (h.centroid.x, h.centroid.y))

    if not holes:
        print("no fully-enclosed voids found; no obstacle files generated")

    for idx, hole in enumerate(holes, start=1):
        obstacle_mesh = extrude_footprint(hole, height, transform)
        obstacle_path = output_dir / f"{prefix}_obstacle_{idx}.stl"
        obstacle_mesh.export(obstacle_path)
        print(f"wrote {obstacle_path}  (bounds: {obstacle_mesh.bounds.tolist()})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inner_stl", type=Path, help="Path to the scenario's _inner.stl file")
    parser.add_argument(
        "--thickness",
        type=float,
        default=0.2,
        help="Wall shell thickness in meters (default: 0.2)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write generated STLs into (default: same directory as inner_stl)",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="Filename prefix for outputs (default: inner_stl's stem with a trailing "
        "'_inner'/'-inner' stripped)",
    )
    args = parser.parse_args()

    inner_path = args.inner_stl.resolve()
    if not inner_path.is_file():
        print(f"error: {inner_path} does not exist", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output_dir.resolve() if args.output_dir else inner_path.parent
    prefix = args.prefix if args.prefix else derive_prefix(inner_path)

    generate(inner_path, output_dir, prefix, args.thickness)


if __name__ == "__main__":
    main()
