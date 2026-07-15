# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

GADEN is a 3D gas dispersion simulator for ROS2 (mobile robot olfaction). This directory is a ROS2 package set meant to live at `<colcon_ws>/src/gaden`. Current branch `humble` targets ROS2 Humble; there are parallel `jazzy`/`ros2-dev` branches — check `git branch`/history before assuming humble-specific behavior is canonical.

All packages use `ament_cmake` (C++20), no `ament_python` packages. There is no CI config and no automated test suite for GADEN's own packages (vendored third-party deps have their own tests, ignore those).

## Build

From the colcon workspace root (one level above this `gaden` checkout):

```bash
./src/gaden/build_gaden.sh
```

which runs:

```bash
colcon build --symlink-install --packages-select \
  gaden_common gaden_environment gaden_filament_simulator gaden_msgs \
  gaden_player gaden_preprocessing simulated_anemometer simulated_gas_sensor \
  simulated_tdlas test_env
```

Notes:
- Submodules are mandatory: `gaden_common/third_party/{gaden_core,gaden_gui}` must be checked out (`git submodule update --init --recursive`) or `gaden_common` won't build.
- `simulated_anemometer`, `simulated_gas_sensor`, and `simulated_tdlas` additionally depend on the external package `olfaction_msgs` (https://github.com/MAPIRlab/olfaction_msgs), which is **not** a submodule — clone it as a sibling package into the same workspace `src/` before building those three.
- The `gaden_gui` executable (built inside `gaden_common`) optionally depends on `ament_imgui`; if not found, CMake auto-fetches it via `FetchContent` from `PepeOjeda/ament_imgui` (see `gaden_common/third_party/gaden_gui/cmake/ament_imgui.cmake`). Do not add `ament_imgui` back as an uncommented `<depend>` in `gaden_common/package.xml` — it was deliberately commented out (commit `fb82f5a`) because it doesn't behave the same across ROS distros.
- `.IDE-support/` (has a `COLCON_IGNORE` marker) exists purely to give editors/clangd a unified compile view across packages — it is never part of an actual build.

## Architecture

Pipeline, in execution order:

1. **`gaden_preprocessing`** (node `preprocessing`) — converts a scenario's CAD/STL geometry + CFD wind-vector cloud into a discretized 3D occupancy grid and wind sequence (`OccupancyGrid3D.csv`, `occupancy.pgm/.yaml`, `BasicSimScene.yaml`). Must run before simulation.
2. **`gaden_filament_simulator`** (node `filament_simulator`) — the core physics engine; runs the filament-based dispersion model over the preprocessed grid/wind data and writes per-iteration result logs.
3. **`gaden_player`** (node `player`) — loads a preprocessed occupancy grid plus one or more logged simulation results and exposes fast lookup services `odor_value` (`gaden_msgs/GasPosition`) and `wind_value` (`gaden_msgs/WindPosition`), decoupling slow offline simulation from real-time sensor queries.
4. **Sensor simulators** (`simulated_gas_sensor`, `simulated_anemometer`, `simulated_tdlas`) — service clients of `gaden_player`, converting raw concentration/wind lookups into realistic sensor readings (`olfaction_msgs/GasSensor`, `Anemometer`, `TDLAS`) plus RViz markers. `simulated_tdlas` also queries `gaden_environment`'s occupancy service.
5. **`gaden_environment`** (node `environment`) — independent RViz visualization of the static scene (walls/obstacles/source) and serves `Occupancy.srv` (`gaden_environment/occupancyMap3D`).

Supporting packages:
- **`gaden_msgs`** — pure interface package: `GasInCell.msg`, and services `GasPosition.srv`, `Occupancy.srv`, `WindPosition.srv`.
- **`gaden_common`** — thin ament wrapper around the vendored `gaden_core` submodule (the actual physics/IO C++ library, target name `gaden`); every other GADEN package links against it via `ament_target_dependencies(gaden_common)`. Also optionally builds the `gaden_gui` submodule and installs it runnable as `ros2 run gaden_common gaden_gui`. `gaden_common/gaden_internal_py/utils.py` is the one piece of shared Python (YAML helpers for launch files).
- **`test_env`** — ships a full example scenario (`10x6_central_obstacle`, ~700MB with precomputed results) plus the four canonical launch files (`gaden_preproc_launch.py`, `gaden_sim_launch.py`, `gaden_player_launch.py`, `main_simbot_launch.py`) that chain the pipeline above together with nav2/RViz.
- **`environments/`** — a real package (`CMakeLists.txt`, `package.xml`, `launch/`, `navigation_config/`, `ros_params/`, `scenarios/`, `tools/`) that is now tracked in git, though it's still not in `build_gaden.sh`'s package list. Same launch-file/scenario structure as `test_env` but with more scenarios. Currently tracked scenarios: `00_CAD_empty` and `00_CAD_maze`, both produced end-to-end via the `environments/tools/` scripts below (real CAD + real wind data), serving as the minimal reference for scenario-authoring alongside `test_env`'s 3D examples. (These replaced the older, manually-authored `00_empty_2d_manualcad_0.10m` scenario — `00_CAD_empty` is the same room, regenerated through the tool pipeline.) Scenario directories are otherwise unordered; `00_` is just a manual prefix for quick lookup, not an established numbering scheme yet.
  - **Never `git add`/commit any `environments/scenarios/<name>/` other than `00_CAD_empty` and `00_CAD_maze`.** Other scenario directories that show up locally (`10x6_*`, `Exp_C`, `MAPIRlab`, `outdoors`, etc.) are duplicates of the scenarios already tracked under `test_env/scenarios/` — verified identical apart from their (gitignored) `result`/`wind` output — so don't re-add them; if you need to free up space they can simply be deleted. A raw-CFD-export scenario directory (e.g. one matching the name passed as `<raw_export_dir>` to `create_scenario_from_raw_data.py`) that's fully superseded by a scaffolded scenario (identical inner STL/wind CSV) is likewise safe to delete once the scaffolded scenario exists.
  - A scenario's `config.yaml` key `unprocessed_wind_files` is a path *prefix* (folder + filename prefix, no `_0.csv` suffix); preprocessing appends `_<idx>.csv` starting at 0 and reads contiguously until a file is missing — so a single static wind snapshot only needs a `..._0.csv` file. The subfolder name under `wind_simulations/` is just a user-chosen label (e.g. `1ms`, `dynamic`, `static`), not parsed.
  - `environments/tools/` holds standalone scenario-authoring scripts (not part of `build_gaden.sh`/colcon), meant to be run in this order for a brand-new scenario:
    1. `create_scenario_from_raw_data.py <raw_export_dir> <name>` — scaffolds `environments/scenarios/<name>` (the same directory layout as every other scenario, all YAML at gaden_gui-template defaults) from a folder containing one "_inner" STL (any filename; matched by containing "inner", or the sole `.stl` present) and one or more wind CFD CSVs (if several, the highest-numbered/last-iteration one is used — this tool only sets up a single static wind snapshot). Copies the STL to `cad_models/<name>_inner.stl` and the CSV to `wind_simulations/static/wind_at_cell_centers_0.csv`. The default gas source position is computed (via stdlib-only ray casting against the copied `_inner` mesh — no trimesh) to land strictly inside the inner volume, i.e. always in free space, rather than defaulting to `[0,0,0]`; `saveDeltaTime` defaults to `0`, so the filament simulator saves a result every iteration rather than every 0.5s. No extra deps, system `python3`. Example: `python3 environments/tools/create_scenario_from_raw_data.py ~/Gaden_simulationFramework/room_export my_new_room`.
    2. `generate_walls_and_obstacles.py` derives a connected walls STL plus one STL per fully-enclosed obstacle from the scenario's `_inner.stl`. It slices the inner mesh at mid-height to get an exact 2D footprint, so it assumes a vertically-extruded (2.5D) room — true of every current scenario. Run it via its own venv (`environments/tools/.venv`, gitignored), kept isolated because trimesh needs numpy>=1.24 while the system numpy (1.21.5, apt-pinned) is relied on by ROS2/cv_bridge.
    3. `update_scenario_models.py` syncs `config.yaml`'s `models:` list with whatever walls/obstacle STLs now exist in `cad_models/` (never the `_inner` CFD-only volume) — still needed even after step 1, since that step always leaves `models:` empty.

    `create_scenario.py <raw_export_dir> <name> [--thickness 0.2] [--config config1] [--force]` is an orchestrator that runs all three steps above in one command (subprocess, using the correct interpreter for each). See `environments/tools/README.md` for full setup/usage of each.

    `inspect_result_file.py` reads the filament simulator's binary `iteration_N` result files without needing a C++ build (pure Python, stdlib zlib/struct). Given a single `iteration_N` file it prints a summary; given a `result/` directory it decompresses every Nth iteration (`--step`, default 10) and writes a single tidy CSV, `results_readable.csv`, next to that `result/` directory.

Typical end-to-end usage (see `GADEN_tutorial.md` for full detail): author CAD model (Part_1 = walls/obstacles for the simulator, Part_2 = inner volume for CFD) → run external CFD (OpenFOAM) and post-process wind data → `gaden_preproc_launch.py` (optionally driven interactively via `gaden_gui` instead of hand-editing YAML) → `gaden_sim_launch.py` → `gaden_player_launch.py` (RViz + live sensor topics), optionally `main_simbot_launch.py` for nav2-integrated robot testing. `test_env` has a ready-made example runnable via `ros2 launch test_env gaden_sim_launch.py scenario:=Exp_C simulation:=sim1`.
