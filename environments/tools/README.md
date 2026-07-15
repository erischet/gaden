Tools for authoring GADEN scenarios.

## generate_walls_and_obstacles.py

Derives a walls STL and per-obstacle STLs from a scenario's `_inner.stl` mesh.
See the script's module docstring for details on the algorithm and assumptions.

Setup (one-time, isolated venv - keeps trimesh's numpy requirement away from the
system numpy that ROS2/cv_bridge depend on):

    python3 -m venv environments/tools/.venv
    environments/tools/.venv/bin/pip install trimesh manifold3d rtree shapely networkx scipy

Usage:

    environments/tools/.venv/bin/python environments/tools/generate_walls_and_obstacles.py \
        path/to/scenario_inner.stl [--thickness 0.2] [--output-dir DIR] [--prefix NAME]
