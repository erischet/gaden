#!/usr/bin/env python3
"""Parse a GADEN filament_simulator 'iteration_N' result file (pure Python, no ROS/colcon needed).

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
"""
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


def read_len_prefixed_string(buf, off):
    (n,) = struct.unpack_from("<Q", buf, off)
    off += 8
    s = buf[off:off + n].decode()
    return s, off + n


def parse(path):
    with open(path, "rb") as f:
        raw = f.read()

    off = 0
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


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path/to/iteration_N>")
        sys.exit(1)

    r = parse(sys.argv[1])
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
