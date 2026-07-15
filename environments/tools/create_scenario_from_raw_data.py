#!/usr/bin/env python3
"""Scaffold a new GADEN scenario from a folder of raw CFD export data.

Takes a folder (searched recursively, so the STL and CSVs don't need to sit in the
same directory) containing one "inner volume" STL (the CFD-only free-space mesh,
Part_2 in the GADEN_tutorial.md convention - filename does not matter) and one or
more wind-vector-cloud CSVs from a CFD run. If several CSVs are present (e.g. one
per timestep of a transient simulation), only the last (highest-numbered, most
converged) one is used - this tool always sets up a single static wind snapshot.

It creates the standard scenario directory layout (see repo CLAUDE.md) under
environments/scenarios/<name>, with all YAML files populated with the same default
values gaden_gui's "New configuration" would produce (empty config.yaml fields, a
single "sim1" simulation and "scene1" playback scene), and copies the input STL/CSV
into their conventional locations:

    cad_models/<name>_inner.stl
    wind_simulations/static/wind_at_cell_centers_0.csv

This is a standalone authoring tool, not part of the ROS2 build. The usual next
steps after running it are generate_walls_and_obstacles.py (to derive walls/obstacle
STLs from the copied _inner.stl) and update_scenario_models.py (to point
config.yaml at them).

The default gas source position is *not* the origin: since the _inner mesh is
exactly the CFD free-space volume (obstacles/walls are holes in it, by
construction - see generate_walls_and_obstacles.py's docstring), we pick a point
that tests as strictly inside that mesh via ray casting, so the source always
starts in free space regardless of where the room happens to sit in the world
frame. No extra deps beyond the standard library are needed for this.

Usage:

    python3 environments/tools/create_scenario_from_raw_data.py path/to/raw_export_dir scenario_name
"""

import argparse
import random
import re
import shutil
import struct
import sys
from pathlib import Path

SCENARIOS_ROOT = Path(__file__).resolve().parent.parent / "scenarios"

CONFIG_YAML_TEMPLATE = """models:
outlets_models:
unprocessed_wind_files: {wind_prefix}
empty_point: [0, 0, 0]
cell_size: 0.1
uniformWind: false
"""

SCENE_YAML_TEMPLATE = """playback_initial_iteration: 0
playback_loop:
  loop: false
  from: 0
  to: 0
simulations:
  - sim: sim1
    gas_color: [0.4, 0.4, 0.4]
"""

SIM_YAML_TEMPLATE = """source:
  sourceType: point
  position: [{source_position[0]}, {source_position[1]}, {source_position[2]}]
  gasType: 0
deltaTime: 0.1
windIterationDeltaTime: 1
temperature: 298
pressure: 1
filamentPPMcenter: 20
filamentInitialSigma: 10
filamentGrowthGamma: 10
filamentNoise_std: 0.02
numFilaments_sec: 10
expectedNumIterations: 600
saveResults: true
saveDeltaTime: 0
preCalculateConcentrations: true
windLooping:
  loop: false
  from: 0
  to: 0
"""

GPROJ_TEMPLATE = """# Gaden Project
versionMajor: 3
versionMinor: 0
configurationsDirectory: environment_configurations
"""


def find_inner_stl(input_dir: Path) -> Path:
    stls = sorted(input_dir.rglob("*.stl"))
    if not stls:
        raise ValueError(f"no .stl file found in {input_dir}")

    inner = [p for p in stls if "inner" in p.stem.lower()]
    if len(inner) == 1:
        return inner[0]
    if len(stls) == 1:
        return stls[0]

    candidates = ", ".join(p.name for p in (inner or stls))
    raise ValueError(
        f"could not identify a single inner-volume STL in {input_dir} "
        f"(candidates: {candidates}); leave only one .stl file in the folder, "
        "or name the intended one so it contains 'inner'"
    )


def find_last_iteration_csv(input_dir: Path) -> Path:
    csvs = sorted(input_dir.rglob("*.csv"))
    if not csvs:
        raise ValueError(f"no .csv file found in {input_dir}")
    if len(csvs) == 1:
        return csvs[0]

    def trailing_number(p: Path):
        m = re.search(r"(\d+)(?!.*\d)", p.stem)
        return int(m.group(1)) if m else None

    numbered = [(trailing_number(p), p) for p in csvs]
    if all(n is not None for n, _ in numbered):
        return max(numbered, key=lambda t: t[0])[1]

    print(
        f"warning: could not find a trailing iteration number in all of "
        f"{[p.name for p in csvs]}; falling back to the last file by name",
        file=sys.stderr,
    )
    return csvs[-1]


def _parse_stl_binary(data: bytes):
    count = struct.unpack_from("<I", data, 80)[0]
    triangles = []
    offset = 84
    for _ in range(count):
        v1 = struct.unpack_from("<3f", data, offset + 12)
        v2 = struct.unpack_from("<3f", data, offset + 24)
        v3 = struct.unpack_from("<3f", data, offset + 36)
        triangles.append((v1, v2, v3))
        offset += 50
    return triangles


def _parse_stl_ascii(text: str):
    triangles = []
    verts = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex"):
            _, x, y, z = line.split()
            verts.append((float(x), float(y), float(z)))
            if len(verts) == 3:
                triangles.append(tuple(verts))
                verts = []
    return triangles


def load_stl_triangles(path: Path):
    """Parse a binary or ASCII STL into a list of (v0, v1, v2) triangles. Stdlib only,
    deliberately not using trimesh here so this script keeps its "no extra deps" property."""
    data = path.read_bytes()
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            return _parse_stl_binary(data)
    return _parse_stl_ascii(data.decode("ascii", errors="ignore"))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _ray_intersects_triangle(origin, direction, triangle):
    """Moeller-Trumbore ray-triangle intersection; True if the ray (origin + t*direction,
    t > 0) crosses the triangle."""
    eps = 1e-9
    v0, v1, v2 = triangle
    e1 = _sub(v1, v0)
    e2 = _sub(v2, v0)
    h = _cross(direction, e2)
    a = _dot(e1, h)
    if -eps < a < eps:
        return False
    f = 1.0 / a
    s = _sub(origin, v0)
    u = f * _dot(s, h)
    if u < 0.0 or u > 1.0:
        return False
    q = _cross(s, e1)
    v = f * _dot(direction, q)
    if v < 0.0 or u + v > 1.0:
        return False
    t = f * _dot(e2, q)
    return t > eps


def point_in_mesh(point, triangles, direction=(1.0, 1e-4, 1e-4)):
    """Even-odd rule: a point is inside a closed mesh if a ray cast from it crosses
    the surface an odd number of times."""
    crossings = sum(1 for tri in triangles if _ray_intersects_triangle(point, direction, tri))
    return crossings % 2 == 1


def find_free_space_point(triangles, attempts: int = 500, seed: int = 0):
    """Find a point strictly inside the (closed, watertight) `_inner` mesh - which,
    since obstacles/walls are holes cut out of it, is guaranteed to be free space."""
    xs = [v[0] for tri in triangles for v in tri]
    ys = [v[1] for tri in triangles for v in tri]
    zs = [v[2] for tri in triangles for v in tri]
    bbox_min = (min(xs), min(ys), min(zs))
    bbox_max = (max(xs), max(ys), max(zs))

    centroid = tuple((bbox_min[i] + bbox_max[i]) / 2 for i in range(3))
    if point_in_mesh(centroid, triangles):
        return centroid

    rng = random.Random(seed)
    for _ in range(attempts):
        candidate = tuple(rng.uniform(bbox_min[i], bbox_max[i]) for i in range(3))
        if point_in_mesh(candidate, triangles):
            return candidate

    print(
        f"warning: could not find a point strictly inside {len(triangles)}-triangle inner mesh "
        f"after {attempts} random attempts; falling back to its bounding-box centroid "
        "(you will likely need to move the source position by hand)",
        file=sys.stderr,
    )
    return centroid


def create_scenario(input_dir: Path, name: str, force: bool):
    scenario_dir = SCENARIOS_ROOT / name
    if scenario_dir.exists() and not force:
        raise FileExistsError(f"{scenario_dir} already exists (use --force to write into it anyway)")

    inner_stl = find_inner_stl(input_dir)
    wind_csv = find_last_iteration_csv(input_dir)
    print(f"using inner volume mesh: {inner_stl.name}")
    print(f"using wind data (last iteration): {wind_csv.name}")

    cad_models_dir = scenario_dir / "cad_models"
    wind_dir = scenario_dir / "wind_simulations" / "static"
    config_dir = scenario_dir / "environment_configurations" / "config1"
    scenes_dir = config_dir / "scenes"
    sim_dir = config_dir / "simulations" / "sim1"

    for d in (cad_models_dir, wind_dir, scenes_dir, sim_dir):
        d.mkdir(parents=True, exist_ok=True)

    dest_inner = cad_models_dir / f"{name}_inner.stl"
    shutil.copyfile(inner_stl, dest_inner)
    print(f"wrote {dest_inner}")

    dest_wind = wind_dir / "wind_at_cell_centers_0.csv"
    shutil.copyfile(wind_csv, dest_wind)
    print(f"wrote {dest_wind}")

    (config_dir / "config.yaml").write_text(
        CONFIG_YAML_TEMPLATE.format(wind_prefix="../../wind_simulations/static/wind_at_cell_centers")
    )
    (scenes_dir / "scene1.yaml").write_text(SCENE_YAML_TEMPLATE)

    source_position = find_free_space_point(load_stl_triangles(dest_inner))
    print(f"placing default gas source at {source_position} (inside the inner mesh's free space)")
    (sim_dir / "sim.yaml").write_text(SIM_YAML_TEMPLATE.format(source_position=source_position))
    (scenario_dir / "gaden.gproj").write_text(GPROJ_TEMPLATE)
    print(f"scaffolded scenario at {scenario_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input_dir", type=Path, help="Folder with a raw '_inner' STL and CFD wind CSV(s)")
    parser.add_argument("name", type=str, help="Name for the new scenario (created at environments/scenarios/<name>)")
    parser.add_argument("--force", action="store_true", help="Write into the scenario directory even if it already exists")
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        print(f"error: {input_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        create_scenario(input_dir, args.name, args.force)
    except (ValueError, FileExistsError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
