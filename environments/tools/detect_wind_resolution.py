#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Detect the actual spatial resolution of a scenario's exported CFD wind point
cloud, and (optionally) set config.yaml's `cell_size` to match it.

Why this matters: gaden_preprocessing does not interpolate wind data onto the
occupancy grid. For each point in the wind CSV it looks up the single grid cell
containing that point and writes the vector directly into it (see gaden_core's
Preprocessing::ParseOpenFoamVectorCloud) - every other cell keeps a default
wind vector of (0, 0, 0). So:
  - cell_size coarser than the wind data spacing: several CFD points land in
    the same cell, the last one read silently wins - some detail is lost, but
    every cell still gets a real vector.
  - cell_size finer than the wind data spacing: most cells contain no CFD
    point at all and are left at (0, 0, 0) - an artificial dead-air patchwork
    with no basis in the actual CFD result.
So cell_size should be at least as large as the coarsest axis spacing in the
wind data - never finer, and not much coarser than necessary (which would
throw away real spatial detail for no reason).

Detection strategy: the wind CSVs produced by this project's pipeline (see
GADEN_tutorial.md's ParaView "ResampleToImage" step) are a structured,
axis-aligned grid - every unique X value pairs with every unique Y and Z. This
is detected by checking whether (#unique X) * (#unique Y) * (#unique Z) equals
the point count; when it does, per-axis spacing is read directly off the
sorted unique coordinates (exact, and fast even for 1M+ point clouds - no
external dependencies needed). If the point cloud turns out not to be a full
structured grid (e.g. wind data exported directly from an unstructured
OpenFOAM mesh, without resampling), this falls back to an approximate
nearest-neighbor spacing estimate over a random sample of points, using a
uniform spatial hash grid to avoid an O(n^2) search.

The recommended cell_size is the max of the three axis spacings (the
coarsest axis is what determines whether cells go empty), since GADEN's
occupancy grid uses a single isotropic cell_size for all three axes.

Note the per-axis spacing reflects the *export* grid used when the wind data
was resampled (e.g. ParaView's "Sampling Dimensions"), not necessarily the
resolution of the original OpenFOAM CFD mesh - a finer resample would change
these numbers even for the same underlying CFD result. If the recommended
cell_size feels coarser than expected, re-exporting the wind data at a finer
sampling resolution (up to the real CFD mesh's resolution) is the fix, not
just picking a smaller cell_size that gaden_preprocessing can't actually make
use of.

No extra dependencies - runs with the system python3.

Usage:

    uv run environments/tools/detect_wind_resolution.py path/to/scenario [--config config1]
    uv run environments/tools/detect_wind_resolution.py path/to/scenario --apply
    uv run environments/tools/detect_wind_resolution.py path/to/some_wind_file.csv
"""

import argparse
import csv
import random
import re
import sys
from pathlib import Path


def find_wind_csv(scenario_dir: Path) -> Path:
    candidates = sorted(scenario_dir.rglob("wind_simulations/**/*_0.csv"))
    if not candidates:
        raise FileNotFoundError(f"no '..._0.csv' wind file found under {scenario_dir}/wind_simulations/")
    if len(candidates) > 1:
        print(
            f"warning: multiple wind snapshot folders found, using the first: {candidates[0]}\n"
            f"  (others: {', '.join(str(c) for c in candidates[1:])})",
            file=sys.stderr,
        )
    return candidates[0]


def read_points(csv_path: Path):
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        points_first = "Points" in header[0]
        px, py, pz = (0, 1, 2) if points_first else (3, 4, 5)

        xs, ys, zs = [], [], []
        for row in reader:
            if not row:
                continue
            xs.append(float(row[px]))
            ys.append(float(row[py]))
            zs.append(float(row[pz]))
    return xs, ys, zs


def axis_spacing(values, round_ndigits=6):
    """Spacing stats between consecutive unique coordinate values along one axis."""
    uniq = sorted(set(round(v, round_ndigits) for v in values))
    if len(uniq) < 2:
        return {"n_unique": len(uniq), "min": 0.0, "max": 0.0, "median": 0.0}

    diffs = sorted(round(b - a, round_ndigits) for a, b in zip(uniq[:-1], uniq[1:]))
    n = len(diffs)
    median = diffs[n // 2] if n % 2 else (diffs[n // 2 - 1] + diffs[n // 2]) / 2
    return {"n_unique": len(uniq), "min": diffs[0], "max": diffs[-1], "median": median}


def nearest_neighbor_spacing(xs, ys, zs, sample_size=1000, seed=0):
    """Approximate spacing for an irregular/unstructured point cloud: nearest-neighbor
    distance over a random sample, using a uniform spatial hash grid so it stays
    roughly O(n) instead of the O(n^2) a brute-force search would need."""
    n = len(xs)
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = min(zs), max(zs)
    volume = max(xmax - xmin, 1e-9) * max(ymax - ymin, 1e-9) * max(zmax - zmin, 1e-9)
    cell = max((volume / n) ** (1 / 3), 1e-6)

    def bucket_of(i):
        return (int((xs[i] - xmin) / cell), int((ys[i] - ymin) / cell), int((zs[i] - zmin) / cell))

    grid = {}
    for i in range(n):
        grid.setdefault(bucket_of(i), []).append(i)

    random.seed(seed)
    sample = random.sample(range(n), min(sample_size, n))

    distances = []
    for i in sample:
        bx, by, bz = bucket_of(i)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((bx + dx, by + dy, bz + dz), ()):
                        if j == i:
                            continue
                        d = ((xs[i] - xs[j]) ** 2 + (ys[i] - ys[j]) ** 2 + (zs[i] - zs[j]) ** 2) ** 0.5
                        if best is None or d < best:
                            best = d
        if best is not None:
            distances.append(best)

    if not distances:
        return {"n_sampled": 0, "min": 0.0, "max": 0.0, "median": 0.0}
    distances.sort()
    n2 = len(distances)
    median = distances[n2 // 2] if n2 % 2 else (distances[n2 // 2 - 1] + distances[n2 // 2]) / 2
    return {"n_sampled": n2, "min": distances[0], "max": distances[-1], "median": median}


def analyze(csv_path: Path):
    xs, ys, zs = read_points(csv_path)
    n = len(xs)

    x_stats = axis_spacing(xs)
    y_stats = axis_spacing(ys)
    z_stats = axis_spacing(zs)
    expected_if_grid = x_stats["n_unique"] * y_stats["n_unique"] * z_stats["n_unique"]
    is_structured_grid = expected_if_grid == n

    print(f"wind file: {csv_path}")
    print(f"points: {n}")

    if is_structured_grid:
        print(f"structured grid detected: {x_stats['n_unique']} x {y_stats['n_unique']} x {z_stats['n_unique']}")
        for axis, stats in (("x", x_stats), ("y", y_stats), ("z", z_stats)):
            print(f"  {axis} spacing: min={stats['min']:.5f}  max={stats['max']:.5f}  median={stats['median']:.5f}")
        spacings = [x_stats["median"], y_stats["median"], z_stats["median"]]
    else:
        print(
            f"not a full structured grid ({x_stats['n_unique']} x {y_stats['n_unique']} x "
            f"{z_stats['n_unique']} = {expected_if_grid} unique-axis combinations != {n} points)"
        )
        print("falling back to an approximate nearest-neighbor spacing estimate")
        nn_stats = nearest_neighbor_spacing(xs, ys, zs)
        print(
            f"  nearest-neighbor distance (n={nn_stats['n_sampled']} sampled): "
            f"min={nn_stats['min']:.5f}  max={nn_stats['max']:.5f}  median={nn_stats['median']:.5f}"
        )
        spacings = [nn_stats["median"]]

    recommended_cell_size = max(spacings)
    print(f"recommended cell_size (coarsest axis, so no cell goes unmatched): {recommended_cell_size:.5f}")
    return recommended_cell_size


def update_cell_size(config_path: Path, cell_size: float, dry_run: bool):
    text = config_path.read_text().splitlines(keepends=True)
    updated = False
    for i, line in enumerate(text):
        if re.match(r"^cell_size:\s*", line):
            text[i] = f"cell_size: {cell_size:.5f}\n"
            updated = True
            break

    if not updated:
        print(f"error: no 'cell_size:' key found in {config_path}", file=sys.stderr)
        return

    if dry_run:
        print(f"--- would write {config_path} ---")
        print(f"cell_size: {cell_size:.5f}")
        return

    config_path.write_text("".join(text))
    print(f"updated {config_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "path", type=Path, help="Path to a scenario directory, or directly to a wind_at_cell_centers_*.csv file"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Name of a single environment_configurations subfolder to update "
        "(default: every config.yaml under environment_configurations/)",
    )
    parser.add_argument("--apply", action="store_true", help="Write the recommended cell_size into config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="With --apply, print the change without writing it")
    args = parser.parse_args()

    path = args.path.resolve()
    scenario_dir = None
    if path.is_dir():
        scenario_dir = path
        try:
            csv_path = find_wind_csv(scenario_dir)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        csv_path = path

    recommended_cell_size = analyze(csv_path)

    if not args.apply:
        return

    if scenario_dir is None:
        print("error: --apply requires a scenario directory, not a bare CSV file", file=sys.stderr)
        sys.exit(1)

    config_root = scenario_dir / "environment_configurations"
    if args.config:
        config_paths = [config_root / args.config / "config.yaml"]
    else:
        config_paths = sorted(config_root.glob("*/config.yaml"))

    if not config_paths:
        print(f"error: no config.yaml found under {config_root}", file=sys.stderr)
        sys.exit(1)

    for config_path in config_paths:
        if not config_path.is_file():
            print(f"error: {config_path} does not exist", file=sys.stderr)
            continue
        update_cell_size(config_path, recommended_cell_size, args.dry_run)


if __name__ == "__main__":
    main()
