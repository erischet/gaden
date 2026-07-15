#!/usr/bin/env python3
"""Orchestrator: run the full new-scenario pipeline in one command.

Chains the three scenario-authoring tools in this folder:

    1. create_scenario_from_raw_data.py - scaffold environments/scenarios/<name>
       from a folder of raw CFD export data (an "_inner" STL and wind CSV(s)).
    2. generate_walls_and_obstacles.py  - derive walls/obstacle STLs from the
       copied "_inner.stl" (runs under environments/tools/.venv, since it needs
       trimesh).
    3. update_scenario_models.py        - point config.yaml's "models:" list at
       the STLs step 2 produced.

Equivalent to running the three scripts by hand (see environments/tools/README.md
for what each does individually); this just wires the output of one into the
input of the next with the correct interpreter for each.

Usage:

    python3 environments/tools/create_scenario.py path/to/raw_export_dir scenario_name \\
        [--thickness 0.2] [--config config1] [--force]
"""

import argparse
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
SCENARIOS_ROOT = TOOLS_DIR.parent / "scenarios"
VENV_PYTHON = TOOLS_DIR / ".venv" / "bin" / "python"


def run_step(description: str, cmd: list):
    print(f"\n=== {description} ===")
    print(f"$ {' '.join(str(c) for c in cmd)}")
    sys.stdout.flush()
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"error: step failed ({description})", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input_dir", type=Path, help="Folder with a raw '_inner' STL and CFD wind CSV(s)")
    parser.add_argument("name", type=str, help="Name for the new scenario (created at environments/scenarios/<name>)")
    parser.add_argument("--thickness", type=float, default=0.2, help="Wall shell thickness in meters (default: 0.2)")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Name of a single environment_configurations subfolder to update in the final step "
        "(default: update every config.yaml under environment_configurations/)",
    )
    parser.add_argument("--force", action="store_true", help="Write into the scenario directory even if it already exists")
    args = parser.parse_args()

    if not VENV_PYTHON.is_file():
        print(
            f"error: {VENV_PYTHON} not found; set up the tools venv first "
            "(see environments/tools/README.md)",
            file=sys.stderr,
        )
        sys.exit(1)

    scenario_dir = SCENARIOS_ROOT / args.name

    create_cmd = [sys.executable, TOOLS_DIR / "create_scenario_from_raw_data.py", args.input_dir, args.name]
    if args.force:
        create_cmd.append("--force")
    run_step("1/3 scaffold scenario from raw data", create_cmd)

    inner_stl = scenario_dir / "cad_models" / f"{args.name}_inner.stl"
    walls_cmd = [VENV_PYTHON, TOOLS_DIR / "generate_walls_and_obstacles.py", inner_stl, "--thickness", str(args.thickness)]
    run_step("2/3 generate walls and obstacles", walls_cmd)

    update_cmd = [sys.executable, TOOLS_DIR / "update_scenario_models.py", scenario_dir]
    if args.config:
        update_cmd += ["--config", args.config]
    run_step("3/3 sync config.yaml models", update_cmd)

    print(f"\nscenario ready at {scenario_dir}")


if __name__ == "__main__":
    main()
