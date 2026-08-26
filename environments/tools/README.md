Tools for authoring GADEN scenarios.

Every script here is invoked the same way: `uv run environments/tools/<script>.py
...`, or directly as `./environments/tools/<script>.py ...` (they're all
executable, with a `#!/usr/bin/env -S uv run --script` shebang). Each one
declares its own dependencies inline (PEP 723) - an empty list for the ones
that only need the standard library, a real list for the few that need numpy/
matplotlib/trimesh/shapely/... Either way [uv](https://docs.astral.sh/uv/)
resolves and caches whatever's needed automatically. Install uv once
(https://docs.astral.sh/uv/getting-started/installation/, or simply
`pip install uv`) and that's the entire setup - no shared venv directory, no
manual pip install, no "which script needs special handling" to remember; the
uniformity is also what lets `tools_gui.py` (below) drive every script through
one identical code path.

Three scripts additionally have a matching `<script>.py.lock` file (generate
with `uv lock --script <script>.py`) pinning exact dependency versions for
reproducibility; `uv run` picks it up automatically if present.

## tools_gui.py

Tkinter GUI for every script below - no extra dependencies (tkinter ships
with system python3). Three tabs: **New scenario** (the create_scenario_from_
raw_data.py -> generate_walls_and_obstacles.py -> update_scenario_models.py
pipeline, same as `create_scenario.py` below, plus an optional polymesh_to_
stl.py pre-step for raw OpenFOAM exports that don't have an `_inner.stl`
yet), **Existing scenario tools** (regenerate walls/obstacles, sync models,
detect/apply wind resolution against a scenario picked from a dropdown), and
**Results** (inspect_result_file.py / plot_result_heatmap.py against a result
file or directory). Every button just runs the matching script via `uv run`
with a streamed log - the GUI needs no per-script dependency handling because
every script is invoked identically (see above).

Usage:

    uv run environments/tools/tools_gui.py

## create_scenario.py

Orchestrator that runs the other three tools below in sequence via `uv run`,
wiring each one's output into the next. Equivalent to running them by hand;
see their individual sections for what each stage actually does.

Usage:

    uv run environments/tools/create_scenario.py path/to/raw_export_dir scenario_name \
        [--thickness 0.2] [--config config1] [--force]

## create_scenario_from_raw_data.py

Scaffolds a brand-new scenario under `environments/scenarios/<name>` from a folder
of raw CFD export data (an "_inner" STL and one or more wind CSVs - if several are
present, the last/highest-numbered one is used). Creates the standard directory
layout with all YAML files set to default values, and copies the STL/CSV into
`cad_models/<name>_inner.stl` and `wind_simulations/static/wind_at_cell_centers_0.csv`.
The default gas source position - and config.yaml's `empty_point` (the
preprocessing flood-fill seed) - are not `[0, 0, 0]`: both are set to the same
point, computed via ray casting against the copied `_inner` mesh so it always
lands strictly inside the free-space volume, regardless of where the room sits
in the world frame. This matters for `empty_point` specifically - if it lands
inside solid geometry instead, the preprocessing flood-fill can't propagate and
the entire environment ends up marked Obstacle. `saveDeltaTime` is also defaulted
to `0`, so the filament simulator saves a result on every iteration instead of
every 0.5s. See the script's module docstring for details. No extra
dependencies - declares an empty PEP 723 dependency list, same as every other
dependency-free script here (see intro above).

Usage:

    uv run environments/tools/create_scenario_from_raw_data.py path/to/raw_export_dir scenario_name

Typical next steps: `generate_walls_and_obstacles.py` (derive walls/obstacle STLs
from the copied `_inner.stl`), then `update_scenario_models.py` (point config.yaml
at them).

## generate_walls_and_obstacles.py

Derives a walls STL and per-obstacle STLs from a scenario's `_inner.stl` mesh.
See the script's module docstring for details on the algorithm and assumptions.
Needs trimesh/shapely/rtree/networkx/scipy/mapbox_earcut - declared inline in
the script (PEP 723) and resolved/cached automatically by uv, keeping
trimesh's numpy requirement isolated from the system numpy that ROS2/cv_bridge
depend on without any manual venv/pip setup.

Usage:

    uv run environments/tools/generate_walls_and_obstacles.py \
        path/to/scenario_inner.stl [--thickness 0.2] [--output-dir DIR] [--prefix NAME]

## polymesh_to_stl.py

Converts an OpenFOAM `polyMesh` directory's boundary surfaces (the ASCII
`points`/`faces`/`neighbour` files) into a binary STL - fan-triangulates each
boundary face after dropping the internal (non-boundary) ones. Useful when a
CFD export only has the raw polyMesh and not an already-exported `_inner.stl`.
Given no explicit directories, recursively finds every `polyMesh` folder under
`--search-root` (default: this script's own directory) and converts each one.
Each STL is written one level up from its `polyMesh` folder (never inside it),
e.g. `case/constant/polyMesh` -> `case/constant/<name>.stl` - the same
"STL sits next to, not inside, its source data" layout the `cad_models`
folders use elsewhere in this project; pass `--output-dir` to write every STL
into one directory instead. Needs numpy - declared inline in the script
(PEP 723) and resolved/cached automatically by uv.

Usage:

    uv run environments/tools/polymesh_to_stl.py path/to/polyMesh [--output-dir DIR]
    uv run environments/tools/polymesh_to_stl.py --search-root path/to/raw_export_dir

## update_scenario_models.py

Syncs a scenario's config.yaml `models:` list with its `cad_models/` folder, so a
scenario's simulation config always references its walls/obstacle STLs (never the
`_inner` CFD-only volume). Detects walls/obstacle files by filename (matching
`*walls.stl` or containing `obstacle` - the convention `generate_walls_and_obstacles.py`
produces). Errors out without touching config.yaml if neither is found.

No extra dependencies (empty PEP 723 dependency list, see intro above).

Usage:

    uv run environments/tools/update_scenario_models.py path/to/scenario [--config config1] [--dry-run]

## detect_wind_resolution.py

Detects the actual spatial resolution of a scenario's exported CFD wind point
cloud (`wind_at_cell_centers_0.csv`) and reports what `cell_size` should be.
gaden_preprocessing does not interpolate wind data onto the occupancy grid -
each CFD point is written directly into the single cell containing it, so a
`cell_size` finer than the wind data's real spacing leaves most cells at a
default `(0, 0, 0)` wind vector instead of anything derived from the CFD
result. See the script's module docstring for the full explanation and the
detection algorithm (exact for the structured/resampled grids this project's
pipeline produces, approximate nearest-neighbor fallback otherwise).

No extra dependencies (empty PEP 723 dependency list, see intro above),
including on the 1M-point CSVs this project's scenarios currently use.

Usage:

    uv run environments/tools/detect_wind_resolution.py path/to/scenario [--config config1]
    uv run environments/tools/detect_wind_resolution.py path/to/scenario --apply [--dry-run]

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

    uv run environments/tools/inspect_result_file.py path/to/result/iteration_100
    uv run environments/tools/inspect_result_file.py path/to/result [--step 10] [--out path.csv]

## plot_result_heatmap.py

Plots a single `iteration_N` result file: a rasterized concentration heatmap
if the simulation ran with `preCalculateConcentrations: true`, otherwise a
scatter of raw filament centers. Overlays walls/obstacles from the scenario's
`OccupancyGrid3D.csv`, auto-located from the standard scenario layout (or pass
`--occupancy`/`--no-occupancy`). Reuses `inspect_result_file.py`'s binary
parser. Needs matplotlib + numpy - declared inline in the script (PEP 723) and
resolved/cached automatically by uv.

Usage:

    uv run environments/tools/plot_result_heatmap.py path/to/result/iteration_100
    uv run environments/tools/plot_result_heatmap.py path/to/result/iteration_100 --z 0 --out heatmap.png --no-show
