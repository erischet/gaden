#!/usr/bin/env python3
"""Plot a GADEN filament_simulator iteration_N result file, with walls/obstacles
overlaid from the scenario's OccupancyGrid3D.csv.

Dispatches on the file's payload mode (see inspect_result_file.py's docstring
for the binary format):
  - "concentrations" (preCalculateConcentrations: true) -> a rasterized heatmap,
    one cell value per grid cell.
  - "filaments" (preCalculateConcentrations: false) -> a scatter plot of raw
    filament centers, one point per active filament. This is a direct transfer
    of the logged (x, y, z, sigma) tuples - no Gaussian summation is performed,
    so it does not reconstruct a concentration field, only shows where puffs
    currently are and how large (diffused) they currently are.
    Note the unit mismatch in the raw data: x/y/z are meters, but sigma is
    logged in *centimeters* (see gaden_core's Simulation::CalculateConcentrationSingleFilament,
    which compares sigma directly against a distance in cm) - this script
    converts sigma to meters before using it.

Either way the plot's title always states which mode was plotted, so a
filament scatter is never mistaken for a concentration heatmap or vice versa.

Reuses inspect_result_file.py's binary parser (same directory), so it stays in
sync with that format automatically. Needs matplotlib + numpy, both already
present in the system python3 on this machine - no venv required.

The occupancy overlay is auto-located from the standard scenario layout:
  <scenario>/environment_configurations/<config>/OccupancyGrid3D.csv
  <scenario>/environment_configurations/<config>/simulations/<sim>/result/iteration_N
i.e. three directories up from the result file. Pass --occupancy to override, or
--no-occupancy to skip it (e.g. if preprocessing hasn't produced that file yet).

Usage:
    python3 plot_result_heatmap.py path/to/result/iteration_100
    python3 plot_result_heatmap.py path/to/result/iteration_100 --z 0 --out heatmap.png --no-show
"""
import argparse
import os
import re

import matplotlib.pyplot as plt
import numpy as np

from inspect_result_file import parse

ITERATION_RE = re.compile(r"iteration_(\d+)")


def find_occupancy_file(iteration_path):
    """Locate OccupancyGrid3D.csv for the scenario a result file belongs to, given the
    standard <config>/simulations/<sim>/result/iteration_N layout (see module docstring)."""
    candidate = os.path.normpath(
        os.path.join(os.path.dirname(iteration_path), "..", "..", "..", "OccupancyGrid3D.csv")
    )
    return candidate if os.path.isfile(candidate) else None


def parse_occupancy(path):
    """Parse OccupancyGrid3D.csv (gaden_core Environment::ReadFromFile/WriteToFile format):
    4 header lines, then per z-layer one line per x_idx of dimy space-separated cell states
    (0=free, 1=occupied, 2=outlet), terminated by a lone ';' line."""
    with open(path) as f:
        lines = [line.rstrip("\n") for line in f]

    minc = [float(v) for v in lines[0].split()[1:]]
    maxc = [float(v) for v in lines[1].split()[1:]]
    dimx, dimy, dimz = (int(v) for v in lines[2].split()[1:])

    layers = np.zeros((dimz, dimx, dimy), dtype=np.uint8)
    z_idx = 0
    x_idx = 0
    for line in lines[4:]:
        if line.strip() == ";":
            z_idx += 1
            x_idx = 0
            continue
        layers[z_idx, x_idx] = [int(v) for v in line.split()]
        x_idx += 1

    # transpose x/y so layers[z] is indexed [y, x], matching the concentration grid's layout
    return {
        "layers": layers.transpose(0, 2, 1),
        "minCoord": minc,
        "maxCoord": maxc,
        "dimensions": (dimx, dimy, dimz),
    }


def _plot_concentrations(ax, fig, r, z_index):
    dimx, dimy, dimz = r["dimensions"]
    # flat array is x-fastest, then y, then z (see inspect_result_file.py's docstring)
    grid = np.array(r["concentrations"], dtype=np.float32).reshape(dimz, dimy, dimx)
    layer = grid[z_index]

    minc, maxc = r["minCoord"], r["maxCoord"]
    extent = [minc[0], maxc[0], minc[1], maxc[1]]

    # sequential single-hue colormap for a magnitude value (ppm) - never a rainbow/jet colormap
    im = ax.imshow(layer, origin="lower", extent=extent, cmap="viridis", aspect="equal")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("concentration (ppm)")


def _plot_filaments(ax, fig, r, z_index):
    minc, maxc = r["minCoord"], r["maxCoord"]
    cellSize = r["cellSize"]
    z_lo = minc[2] + z_index * cellSize
    z_hi = z_lo + cellSize
    layer_filaments = [f for f in r["filaments"] if z_lo <= f[2] < z_hi]

    if layer_filaments:
        xs, ys, _, sigmas_cm = zip(*layer_filaments)
        sigmas_m = [s / 100.0 for s in sigmas_cm]  # sigma is logged in cm; x/y/z are meters
        # same sequential colormap as the concentration heatmap, reused here for sigma;
        # low alpha so overlapping filaments build up visible density, like gaden_gui's live view
        sc = ax.scatter(xs, ys, c=sigmas_m, cmap="viridis", s=40, alpha=0.7, edgecolors="none")
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("filament sigma (m)")
    else:
        print(f"warning: no active filaments in z-layer {z_index} ({z_lo:.3g}-{z_hi:.3g} m)")

    ax.set_xlim(minc[0], maxc[0])
    ax.set_ylim(minc[1], maxc[1])


def plot_result(iteration_path, z_index=0, out_path=None, show=True, occupancy_path="auto"):
    r = parse(iteration_path)

    dimx, dimy, dimz = r["dimensions"]
    if not (0 <= z_index < dimz):
        raise ValueError(f"--z {z_index} out of range (grid has {dimz} layer(s): 0..{dimz - 1})")

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.set_aspect("equal")

    if r["mode"] == "concentrations":
        _plot_concentrations(ax, fig, r, z_index)
    else:
        _plot_filaments(ax, fig, r, z_index)

    auto_detecting = occupancy_path == "auto"
    if auto_detecting:
        occupancy_path = find_occupancy_file(iteration_path)

    if occupancy_path:
        occ = parse_occupancy(occupancy_path)
        occ_dimx, occ_dimy, occ_dimz = occ["dimensions"]
        occ_z = min(z_index, occ_dimz - 1)
        occupied = occ["layers"][occ_z] == 1  # 1=occupied wall/obstacle cell, 0=free, 2=outlet
        # mask free cells so only walls/obstacles are drawn, on top of the concentration layer
        occ_overlay = np.ma.masked_where(~occupied, occupied)
        occ_extent = [occ["minCoord"][0], occ["maxCoord"][0], occ["minCoord"][1], occ["maxCoord"][1]]
        ax.imshow(
            occ_overlay, origin="lower", extent=occ_extent, cmap="gray_r",
            vmin=0, vmax=1, alpha=0.85, aspect="equal", interpolation="none",
        )
    elif auto_detecting:
        print("warning: no OccupancyGrid3D.csv found for this scenario; plotting concentrations only "
              "(pass --occupancy to point at one explicitly, or run preprocessing first)")

    src = r["sourcePosition"]
    ax.plot(src[0], src[1], marker="x", markersize=12, markeredgewidth=2.5, color="red", label="source")
    ax.legend(loc="upper right")

    iter_match = ITERATION_RE.search(os.path.basename(iteration_path))
    iter_label = iter_match.group(1) if iter_match else "?"
    # mode is always stated in the title so a filament scatter can never be mistaken for a
    # concentration heatmap (or vice versa) at a glance
    ax.set_title(f"iteration {iter_label}  (z-layer {z_index}/{dimz - 1}, mode: {r['mode']})")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    fig.tight_layout()

    if out_path:
        fig.savefig(out_path, dpi=150)
        print(f"wrote {out_path}")
    if show:
        plt.show()
    else:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("iteration_file", help="path to an iteration_N result file")
    parser.add_argument("--z", type=int, default=0, help="grid layer to plot for 3D scenarios (default: 0)")
    parser.add_argument("--out", default=None, help="also save the plot to this image path (e.g. heatmap.png)")
    parser.add_argument("--no-show", action="store_true", help="don't open an interactive window, only save/print")
    occ_group = parser.add_mutually_exclusive_group()
    occ_group.add_argument("--occupancy", default=None,
                            help="path to OccupancyGrid3D.csv (default: auto-located from the scenario layout)")
    occ_group.add_argument("--no-occupancy", action="store_true", help="don't overlay walls/obstacles")
    args = parser.parse_args()

    if args.no_occupancy:
        occupancy_path = None
    elif args.occupancy:
        occupancy_path = args.occupancy
    else:
        occupancy_path = "auto"
    plot_result(args.iteration_file, z_index=args.z, out_path=args.out, show=not args.no_show,
                occupancy_path=occupancy_path)


if __name__ == "__main__":
    main()
