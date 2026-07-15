#!/usr/bin/env python3
"""Parse GADEN filament_simulator 'iteration_N' result files (pure Python, no ROS/colcon needed).

Format (gaden_core RunningSimulation::SaveResults / PlaybackSimulation::LoadLogfile):
  header (only in "modern" >=3.0 files):
    b"GADEN_RESULT\\x00"   13 bytes
    compression mode       1 byte  (0=uncompressed, 1=zlib, 2=libbsc)
    uncompressed size       8 bytes (uint64)
  payload (zlib-compressed; libbsc payloads need the C++ `decompress` tool instead):
    versionMajor            int32
    versionMinor             int32
    Description:
      dimensions.xyz        3x int32
      minCoord.xyz            3x float32
      maxCoord.xyz            3x float32
      cellSize                float32
    GasSource:
      sourceType             (uint64 length + bytes)
      [type-specific extra fields: box=vec3, line=vec3, sphere=float, cylinder=2 floats, point=none]
      sourcePosition           3x float32
      gasType                  int32
    constants: totalMolesInFilament, numMolesAllGasesIncm3    2x float32
    windIndex                int32
    mode                     (uint64 length + bytes) "filaments" or "concentrations"
    payload vector:
      count                  uint64
      - filaments:      count * (x,y,z,sigma float32)
      - concentrations:  count * float32   (one value per cell, x-fastest/then y/then z, i.e.
                                             index = x + y*dimx + z*dimx*dimy)

Usage:
  Inspect a single iteration file (prints a summary only):
    python3 inspect_result_file.py path/to/result/iteration_100

  Export a whole 'result' directory to a single human-readable CSV, written as
  "results_readable.csv" next to (i.e. as a sibling of) the result directory:
    python3 inspect_result_file.py path/to/result [--step 10] [--out path.csv]

  --step controls how many of the iteration files are actually decompressed/exported:
  with the default of 10, only iteration_0, iteration_10, iteration_20, ... are read.
  Use --step 1 to export every iteration.
"""
import argparse
import csv
import os
import re
import struct
import sys
import zlib

SOURCE_EXTRA = {
    "point": 0,
    "box": 12,      # vec3 size
    "line": 12,     # vec3 lineEnd
    "sphere": 4,    # float radius
    "cylinder": 8,  # float radius + float height
}

ITERATION_RE = re.compile(r"^iteration_(\d+)$")


def read_len_prefixed_string(buf, off):
    (n,) = struct.unpack_from("<Q", buf, off)
    off += 8
    s = buf[off:off + n].decode()
    return s, off + n


def parse(path):
    with open(path, "rb") as f:
        raw = f.read()

    if raw[:12] == b"GADEN_RESULT":
        mode_byte = raw[13]
        (uncompressed_size,) = struct.unpack_from("<Q", raw, 14)
        payload = raw[22:]
        if mode_byte == 0:
            data = payload[:uncompressed_size]
        elif mode_byte == 1:
            data = zlib.decompress(payload)
        else:
            raise RuntimeError(
                "LIBBSC-compressed payload; use the compiled `decompress` tool "
                "(build/gaden_common/third_party/gaden_core/utils/decompress/decompress) instead"
            )
    else:
        # old pre-3.0 files: no header, straight zlib the whole thing
        data = zlib.decompress(raw)

    result = {}
    off = 0
    result["versionMajor"], result["versionMinor"] = struct.unpack_from("<ii", data, off)
    off += 8

    dimx, dimy, dimz = struct.unpack_from("<iii", data, off); off += 12
    minc = struct.unpack_from("<fff", data, off); off += 12
    maxc = struct.unpack_from("<fff", data, off); off += 12
    (cellSize,) = struct.unpack_from("<f", data, off); off += 4
    result.update(dimensions=(dimx, dimy, dimz), minCoord=minc, maxCoord=maxc, cellSize=cellSize)

    source_type, off = read_len_prefixed_string(data, off)
    off += SOURCE_EXTRA[source_type]
    src_pos = struct.unpack_from("<fff", data, off); off += 12
    (gas_type,) = struct.unpack_from("<i", data, off); off += 4
    result.update(sourceType=source_type, sourcePosition=src_pos, gasType=gas_type)

    total_moles, moles_cm3 = struct.unpack_from("<ff", data, off); off += 8
    result.update(totalMolesInFilament=total_moles, numMolesAllGasesIncm3=moles_cm3)

    (wind_index,) = struct.unpack_from("<i", data, off); off += 4
    result["windIndex"] = wind_index

    payload_mode, off = read_len_prefixed_string(data, off)
    result["mode"] = payload_mode

    (count,) = struct.unpack_from("<Q", data, off); off += 8
    if payload_mode == "concentrations":
        values = struct.unpack_from(f"<{count}f", data, off)
        result["concentrations"] = values
        assert count == dimx * dimy * dimz, "count doesn't match dimx*dimy*dimz"
    elif payload_mode == "filaments":
        filaments = []
        for i in range(count):
            x, y, z, sigma = struct.unpack_from("<ffff", data, off + i * 16)
            filaments.append((x, y, z, sigma))
        result["filaments"] = filaments
    else:
        raise RuntimeError(f"unknown payload mode '{payload_mode}'")

    return result


def print_summary(r):
    print(f"gaden version   : {r['versionMajor']}.{r['versionMinor']}")
    print(f"grid dimensions : {r['dimensions']} ({r['dimensions'][0]*r['dimensions'][1]*r['dimensions'][2]} cells)")
    print(f"bounds          : {r['minCoord']} -> {r['maxCoord']}  (cellSize={r['cellSize']})")
    print(f"source          : type={r['sourceType']} pos={r['sourcePosition']} gasType={r['gasType']}")
    print(f"wind index      : {r['windIndex']}")
    print(f"payload mode    : {r['mode']}")

    if r["mode"] == "concentrations":
        vals = r["concentrations"]
        nonzero = [v for v in vals if v > 0]
        print(f"concentration stats (ppm): min={min(vals):.4g} max={max(vals):.4g} "
              f"mean={sum(vals)/len(vals):.4g} nonzero_cells={len(nonzero)}/{len(vals)}")
    else:
        print(f"num active filaments: {len(r['filaments'])}")
        if r["filaments"]:
            print(f"first filament (x,y,z,sigma): {r['filaments'][0]}")


def list_iterations(result_dir, step):
    entries = []
    for name in os.listdir(result_dir):
        m = ITERATION_RE.match(name)
        if m:
            entries.append((int(m.group(1)), os.path.join(result_dir, name)))
    entries.sort(key=lambda e: e[0])
    return [(n, p) for n, p in entries if n % step == 0]


def export_readable(result_dir, step=10, out_path=None):
    """Decompress every `step`-th iteration file in `result_dir` and write them all
    into a single CSV, in long/tidy format (one row per cell or per filament per
    iteration). CSV was chosen because it's plain text (openable/greppable without
    any tool), loads into pandas/Excel/etc. with zero extra parsing code, and a
    tidy one-row-per-value layout works whether the grid size changes or not."""
    result_dir = os.path.normpath(result_dir)
    iterations = list_iterations(result_dir, step)
    if not iterations:
        raise RuntimeError(f"no iteration_N files found in '{result_dir}' matching step={step}")

    if out_path is None:
        out_path = os.path.join(os.path.dirname(result_dir), "results_readable.csv")

    first = parse(iterations[0][1])
    mode = first["mode"]
    dimx, dimy, dimz = first["dimensions"]
    minc = first["minCoord"]
    cellSize = first["cellSize"]

    with open(out_path, "w", newline="") as f:
        f.write(f"# gaden_version: {first['versionMajor']}.{first['versionMinor']}\n")
        f.write(f"# grid_dimensions: {dimx} {dimy} {dimz}\n")
        f.write(f"# bounds_min: {minc[0]} {minc[1]} {minc[2]}\n")
        f.write(f"# bounds_max: {first['maxCoord'][0]} {first['maxCoord'][1]} {first['maxCoord'][2]}\n")
        f.write(f"# cell_size: {cellSize}\n")
        f.write(f"# source_type: {first['sourceType']}\n")
        f.write(f"# source_position: {first['sourcePosition'][0]} {first['sourcePosition'][1]} {first['sourcePosition'][2]}\n")
        f.write(f"# gas_type: {first['gasType']}\n")
        f.write(f"# mode: {mode}\n")
        f.write(f"# step: {step}\n")

        writer = csv.writer(f)
        if mode == "concentrations":
            writer.writerow(["iteration", "cell_x", "cell_y", "cell_z", "x_m", "y_m", "z_m", "concentration_ppm"])
        else:
            writer.writerow(["iteration", "filament_index", "x_m", "y_m", "z_m", "sigma"])

        for n, path in iterations:
            r = first if path == iterations[0][1] else parse(path)
            if r["mode"] != mode:
                raise RuntimeError(f"iteration_{n} has mode '{r['mode']}', expected '{mode}' "
                                    "(mixed-mode result directories are not supported)")

            if mode == "concentrations":
                vals = r["concentrations"]
                nonzero = sum(1 for v in vals if v > 0)
                print(f"iteration_{n}: min={min(vals):.4g} max={max(vals):.4g} "
                      f"mean={sum(vals)/len(vals):.4g} nonzero={nonzero}/{len(vals)}")
                for idx, v in enumerate(vals):
                    cz, rem = divmod(idx, dimx * dimy)
                    cy, cx = divmod(rem, dimx)
                    x_m = minc[0] + (cx + 0.5) * cellSize
                    y_m = minc[1] + (cy + 0.5) * cellSize
                    z_m = minc[2] + (cz + 0.5) * cellSize
                    writer.writerow([n, cx, cy, cz, f"{x_m:.4f}", f"{y_m:.4f}", f"{z_m:.4f}", f"{v:.6g}"])
            else:
                filaments = r["filaments"]
                print(f"iteration_{n}: {len(filaments)} active filaments")
                for i, (x, y, z, sigma) in enumerate(filaments):
                    writer.writerow([n, i, f"{x:.4f}", f"{y:.4f}", f"{z:.4f}", f"{sigma:.4f}"])

    print(f"\nwrote {len(iterations)} iterations (step={step}) to '{out_path}'")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="a 'result' directory to export, or a single iteration_N file to inspect")
    parser.add_argument("--step", type=int, default=10,
                         help="when exporting a directory, only decompress every Nth iteration (default: 10)")
    parser.add_argument("--out", default=None,
                         help="output CSV path (default: 'results_readable.csv' next to the result directory)")
    args = parser.parse_args()

    if args.step < 1:
        parser.error("--step must be >= 1")

    if os.path.isdir(args.path):
        export_readable(args.path, step=args.step, out_path=args.out)
    else:
        print_summary(parse(args.path))


if __name__ == "__main__":
    main()
