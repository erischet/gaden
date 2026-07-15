#!/usr/bin/env python3
"""Sync a scenario's config.yaml "models:" list with its cad_models/ folder.

Detects walls and obstacle STLs by the naming convention produced by
generate_walls_and_obstacles.py (`<prefix>_walls.stl`, `<prefix>_obstacle_<N>.stl`,
or any filename containing "walls"/"obstacle") and rewrites the "models:" block of
one or more environment_configurations/*/config.yaml files to reference exactly
those files - so files like the scenario's "_inner" CAD part (the CFD-only free-space
volume, never meant to be listed under "models:") are never included.

If no walls or obstacle STLs are found in cad_models/, nothing is written and an
error is reported instead - a scenario always needs at least a walls (or obstacle)
mesh to be simulatable.

This is a standalone authoring tool, not part of the ROS2 build.

Usage:

    python3 environments/tools/update_scenario_models.py path/to/scenario [--config config1] [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path

WALLS_RE = re.compile(r"walls\.stl$", re.IGNORECASE)
OBSTACLE_RE = re.compile(r"obstacle", re.IGNORECASE)


def classify_cad_models(cad_models_dir: Path):
    """Return (walls_files, obstacle_files), both sorted by name."""
    walls, obstacles = [], []
    for stl in sorted(cad_models_dir.glob("*.stl")):
        if WALLS_RE.search(stl.name):
            walls.append(stl)
        elif OBSTACLE_RE.search(stl.name):
            obstacles.append(stl)
    return walls, obstacles


def render_models_block(model_files, config_dir: Path, cad_models_dir: Path):
    rel_dir = Path(os.path.relpath(cad_models_dir, config_dir))
    lines = ["models:\n"]
    for f in sorted(model_files, key=lambda p: p.name):
        lines.append(f"  - {rel_dir.as_posix()}/{f.name}\n")
    return lines


def update_config_file(config_path: Path, model_files, cad_models_dir: Path, dry_run: bool):
    text = config_path.read_text().splitlines(keepends=True)
    new_block = render_models_block(model_files, config_path.parent, cad_models_dir)

    start = None
    for i, line in enumerate(text):
        if re.match(r"^models:\s*$", line):
            start = i
            break

    if start is None:
        # no existing "models:" key - insert one at the top of the file
        updated = new_block + text
    else:
        end = start + 1
        while end < len(text) and (text[end].startswith((" ", "\t")) or text[end].strip() == ""):
            end += 1
        updated = text[:start] + new_block + text[end:]

    if dry_run:
        print(f"--- would write {config_path} ---")
        sys.stdout.writelines(new_block)
        return

    config_path.write_text("".join(updated))
    print(f"updated {config_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario_dir", type=Path, help="Path to a scenario directory (contains cad_models/)")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Name of a single environment_configurations subfolder to update "
        "(default: update every config.yaml under environment_configurations/)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the new models: block without writing it")
    args = parser.parse_args()

    scenario_dir = args.scenario_dir.resolve()
    cad_models_dir = scenario_dir / "cad_models"
    if not cad_models_dir.is_dir():
        print(f"error: {cad_models_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    walls_files, obstacle_files = classify_cad_models(cad_models_dir)
    if not walls_files and not obstacle_files:
        print(
            f"error: no walls or obstacle STL files found in {cad_models_dir} "
            "(expected filenames matching '*walls.stl' or containing 'obstacle')",
            file=sys.stderr,
        )
        sys.exit(1)

    model_files = walls_files + obstacle_files
    print("walls:     " + (", ".join(f.name for f in walls_files) or "(none)"))
    print("obstacles: " + (", ".join(f.name for f in obstacle_files) or "(none)"))

    configs_root = scenario_dir / "environment_configurations"
    if not configs_root.is_dir():
        print(f"error: {configs_root} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.config:
        config_paths = [configs_root / args.config / "config.yaml"]
        if not config_paths[0].is_file():
            print(f"error: {config_paths[0]} does not exist", file=sys.stderr)
            sys.exit(1)
    else:
        config_paths = sorted(configs_root.glob("*/config.yaml"))
        if not config_paths:
            print(f"error: no config.yaml files found under {configs_root}", file=sys.stderr)
            sys.exit(1)

    for config_path in config_paths:
        update_config_file(config_path, model_files, cad_models_dir, args.dry_run)


if __name__ == "__main__":
    main()
