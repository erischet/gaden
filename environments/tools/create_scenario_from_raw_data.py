#!/usr/bin/env python3
"""Scaffold a new GADEN scenario from a folder of raw CFD export data.

Takes a folder containing one "inner volume" STL (the CFD-only free-space mesh,
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

Usage:

    python3 environments/tools/create_scenario_from_raw_data.py path/to/raw_export_dir scenario_name
"""

import argparse
import re
import shutil
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
  position: [0, 0, 0]
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
saveDeltaTime: 0.5
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
    stls = sorted(input_dir.glob("*.stl"))
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
    csvs = sorted(input_dir.glob("*.csv"))
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
    (sim_dir / "sim.yaml").write_text(SIM_YAML_TEMPLATE)
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
