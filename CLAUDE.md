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
- **`environments/`** — mostly untracked in git (local WIP, not in `build_gaden.sh`'s package list). Same launch-file/scenario structure as `test_env` but with more scenarios; treat as work-in-progress rather than an established package. One exception: `environments/scenarios/00_empty_2d_manualcad_0.10m` is tracked — the first scenario populated with real CAD/wind data end-to-end (a thin 2D room slice, single manually-authored STL, static wind field), serving as a minimal reference for scenario-authoring alongside `test_env`'s 3D examples. Scenario directories are otherwise unordered; `00_` is just a manual prefix for quick lookup, not an established numbering scheme yet.
  - A scenario's `config.yaml` key `unprocessed_wind_files` is a path *prefix* (folder + filename prefix, no `_0.csv` suffix); preprocessing appends `_<idx>.csv` starting at 0 and reads contiguously until a file is missing — so a single static wind snapshot only needs a `..._0.csv` file. The subfolder name under `wind_simulations/` is just a user-chosen label (e.g. `1ms`, `dynamic`, `static`), not parsed.

Typical end-to-end usage (see `GADEN_tutorial.md` for full detail): author CAD model (Part_1 = walls/obstacles for the simulator, Part_2 = inner volume for CFD) → run external CFD (OpenFOAM) and post-process wind data → `gaden_preproc_launch.py` (optionally driven interactively via `gaden_gui` instead of hand-editing YAML) → `gaden_sim_launch.py` → `gaden_player_launch.py` (RViz + live sensor topics), optionally `main_simbot_launch.py` for nav2-integrated robot testing. `test_env` has a ready-made example runnable via `ros2 launch test_env gaden_sim_launch.py scenario:=Exp_C simulation:=sim1`.
