#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy",
# ]
# ///
"""Convert OpenFOAM polyMesh boundary surfaces into STL files.

Reads the ASCII points/faces/neighbour files of an OpenFOAM polyMesh
directory, keeps only the boundary (non-internal) faces, fan-triangulates
each polygon, and writes a binary STL one level up from the polyMesh
directory (never inside it), e.g. case/constant/polyMesh -> case/constant/
<name>.stl - the same "STL sits next to, not inside, its source data" layout
used by the cad_models folders elsewhere in this project.

Needs numpy, declared inline above (PEP 723) and resolved/cached automatically
by uv (https://docs.astral.sh/uv/) - run with `uv run polymesh_to_stl.py ...`,
or directly as `./polymesh_to_stl.py ...` (the shebang already invokes uv).
"""

import argparse
import re
import struct
import sys
from pathlib import Path

import numpy as np

_HEADER_RE = re.compile(r'FoamFile\s*\{.*?\}', re.S)
_COMMENT_RE = re.compile(r'/\*.*?\*/|//.*')
_FACE_RE = re.compile(r'(\d+)\(([^)]*)\)')


def _strip_header(text: str) -> str:
    text = _COMMENT_RE.sub('', text)
    text = _HEADER_RE.sub('', text)
    return text


def _read_list_body(path: Path) -> tuple[int, str]:
    text = _strip_header(path.read_text())
    m = re.search(r'(\d+)\s*\(', text)
    if not m:
        raise ValueError(f"could not find a list in {path}")
    count = int(m.group(1))
    start = m.end()
    depth, i = 1, start
    while depth:
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
        i += 1
    return count, text[start:i - 1]


def read_points(path: Path) -> np.ndarray:
    count, body = _read_list_body(path)
    flat = np.fromstring(body.replace('(', ' ').replace(')', ' '), sep=' ')
    return flat.reshape(count, 3)


def read_faces(path: Path) -> list[list[int]]:
    count, body = _read_list_body(path)
    faces = [[int(x) for x in m.group(2).split()] for m in _FACE_RE.finditer(body)]
    if len(faces) != count:
        raise ValueError(f"expected {count} faces in {path}, parsed {len(faces)}")
    return faces


def read_label_list(path: Path) -> list[int]:
    count, body = _read_list_body(path)
    vals = [int(x) for x in body.split()]
    if len(vals) != count:
        raise ValueError(f"expected {count} entries in {path}, parsed {len(vals)}")
    return vals


def triangulate(points: np.ndarray, faces: list[list[int]]) -> np.ndarray:
    triangles = []
    for face in faces:
        verts = points[face]
        for i in range(1, len(verts) - 1):
            triangles.append((verts[0], verts[i], verts[i + 1]))
    return np.array(triangles, dtype=np.float32)


def write_stl_binary(path: Path, triangles: np.ndarray, name: str) -> None:
    with open(path, 'wb') as f:
        f.write(name.encode('ascii', 'replace')[:80].ljust(80, b'\0'))
        f.write(struct.pack('<I', len(triangles)))
        for a, b, c in triangles:
            normal = np.cross(b - a, c - a)
            norm = np.linalg.norm(normal)
            if norm > 1e-12:
                normal = normal / norm
            f.write(struct.pack('<3f', *normal))
            f.write(struct.pack('<3f', *a))
            f.write(struct.pack('<3f', *b))
            f.write(struct.pack('<3f', *c))
            f.write(struct.pack('<H', 0))


def convert(polymesh_dir: Path, output_path: Path) -> int:
    points = read_points(polymesh_dir / 'points')
    faces = read_faces(polymesh_dir / 'faces')
    n_internal = len(read_label_list(polymesh_dir / 'neighbour'))

    boundary_faces = faces[n_internal:]
    triangles = triangulate(points, boundary_faces)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_stl_binary(output_path, triangles, name=output_path.stem)
    return len(triangles)


SCRIPT_DIR = Path(__file__).resolve().parent


def find_polymesh_dirs(root: Path):
    return sorted(p for p in root.rglob('*') if p.is_dir() and p.name == 'polyMesh')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'polymesh_dirs', nargs='*', type=Path,
        help="OpenFOAM polyMesh directories to convert. If omitted, every "
             "'polyMesh' directory under --search-root is converted.",
    )
    parser.add_argument(
        '--search-root', type=Path, default=SCRIPT_DIR,
        help="Where to look for polyMesh directories when none are given "
             "explicitly (default: this script's own directory).",
    )
    parser.add_argument(
        '-o', '--output-dir', type=Path, default=None,
        help="Directory to write every .stl into, flat. Default: the parent "
             "directory of each polyMesh folder (i.e. one level up, never "
             "inside polyMesh itself), e.g. "
             "case/constant/polyMesh -> case/constant/<name>.stl.",
    )
    args = parser.parse_args()

    dirs = args.polymesh_dirs or find_polymesh_dirs(args.search_root)
    if not dirs:
        print(f"no polyMesh directories found under {args.search_root}", file=sys.stderr)
        sys.exit(1)

    for d in dirs:
        if not d.is_dir():
            print(f"skip: {d} is not a directory", file=sys.stderr)
            continue
        name = d.parent.name if d.name == 'polyMesh' else d.name
        out_dir = args.output_dir if args.output_dir is not None else d.parent
        out = out_dir / f"{name}.stl"
        n_tri = convert(d, out)
        print(f"{d} -> {out}  ({n_tri} triangles)")


if __name__ == '__main__':
    main()
