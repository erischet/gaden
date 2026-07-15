Tools for authoring GADEN scenarios.

## create_scenario.py

Orchestrator that runs the other three tools below in sequence, wiring each
one's output into the next (with the correct interpreter for each - the tools
venv for `generate_walls_and_obstacles.py`, system `python3` for the rest).
Equivalent to running them by hand; see their individual sections for what each
stage actually does.

Requires the `environments/tools/.venv` setup described under
`generate_walls_and_obstacles.py` below.

Usage:

    python3 environments/tools/create_scenario.py path/to/raw_export_dir scenario_name \
        [--thickness 0.2] [--config config1] [--force]

## create_scenario_from_raw_data.py

Scaffolds a brand-new scenario under `environments/scenarios/<name>` from a folder
of raw CFD export data (an "_inner" STL and one or more wind CSVs - if several are
present, the last/highest-numbered one is used). Creates the standard directory
layout with all YAML files set to default values, and copies the STL/CSV into
`cad_models/<name>_inner.stl` and `wind_simulations/static/wind_at_cell_centers_0.csv`.
The default gas source position is not `[0, 0, 0]` - it's computed via ray casting
against the copied `_inner` mesh so it always lands strictly inside the free-space
volume, regardless of where the room sits in the world frame. `saveDeltaTime` is
also defaulted to `0`, so the filament simulator saves a result on every iteration
instead of every 0.5s. See the script's module docstring for details. No extra
dependencies - runs with the system python3.

Usage:

    python3 environments/tools/create_scenario_from_raw_data.py path/to/raw_export_dir scenario_name

Typical next steps: `generate_walls_and_obstacles.py` (derive walls/obstacle STLs
from the copied `_inner.stl`), then `update_scenario_models.py` (point config.yaml
at them).

## generate_walls_and_obstacles.py

Derives a walls STL and per-obstacle STLs from a scenario's `_inner.stl` mesh.
See the script's module docstring for details on the algorithm and assumptions.

Setup (one-time, isolated venv - keeps trimesh's numpy requirement away from the
system numpy that ROS2/cv_bridge depend on):

    python3 -m venv environments/tools/.venv
    environments/tools/.venv/bin/pip install trimesh manifold3d rtree shapely networkx scipy

Usage:

    environments/tools/.venv/bin/python environments/tools/generate_walls_and_obstacles.py \
        path/to/scenario_inner.stl [--thickness 0.2] [--output-dir DIR] [--prefix NAME]

## update_scenario_models.py

Syncs a scenario's config.yaml `models:` list with its `cad_models/` folder, so a
scenario's simulation config always references its walls/obstacle STLs (never the
`_inner` CFD-only volume). Detects walls/obstacle files by filename (matching
`*walls.stl` or containing `obstacle` - the convention `generate_walls_and_obstacles.py`
produces). Errors out without touching config.yaml if neither is found.

No extra dependencies - runs with the system python3.

Usage:

    python3 environments/tools/update_scenario_models.py path/to/scenario [--config config1] [--dry-run]

## inspect_result_file.py

Reads the filament simulator's binary `iteration_N` result files (see
`gaden_filament_simulator`/`gaden_core`'s `RunningSimulation::SaveResults`) without
needing a C++ build - pure Python, stdlib `zlib`/`struct` only. LIBBSC-compressed
files (results bigger than ~5MB uncompressed) aren't supported; use the compiled
`decompress` tool (built as part of `gaden_common`, at
`build/gaden_common/third_party/gaden_core/utils/decompress/decompress`) for those.

Given a single `iteration_N` file, prints a summary (grid dimensions/bounds, gas
source, wind index, and concentration or filament stats) to stdout.

Given a `result/` directory, decompresses every Nth iteration (`--step`, default
10) and writes them all into a single tidy CSV, `results_readable.csv`, saved next
to (as a sibling of) that `result/` directory.

Usage:

    python3 environments/tools/inspect_result_file.py path/to/result/iteration_100
    python3 environments/tools/inspect_result_file.py path/to/result [--step 10] [--out path.csv]
