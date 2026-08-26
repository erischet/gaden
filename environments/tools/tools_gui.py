#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Tkinter front-end for every script in this folder.

Every tool script here (see README.md) is invoked identically, through
`uv run <script>.py ...` - each one declares its own dependencies inline
(PEP 723, empty list for the ones that need nothing beyond the standard
library), so uv resolves/caches whatever's needed automatically regardless of
which script is being run. This GUI relies on exactly that uniformity: one
subprocess-invocation path (["uv", "run", script, *args]) covers all of them,
with no special-casing for which ones need extra packages.

Three tabs:
  - New scenario: the create_scenario_from_raw_data.py -> generate_walls_and_
    obstacles.py -> update_scenario_models.py pipeline (same steps
    create_scenario.py chains on the command line), plus an optional
    polymesh_to_stl.py pre-step for raw OpenFOAM exports that don't have an
    "_inner.stl" yet.
  - Existing scenario tools: re-run individual steps (regenerate walls/
    obstacles, sync models, detect/apply wind resolution) against a scenario
    picked from a dropdown.
  - Results: inspect_result_file.py / plot_result_heatmap.py against a result
    file or directory.

No extra dependencies - tkinter ships with the system python3 that every
other script in this folder already targets.

Usage:

    uv run environments/tools/tools_gui.py
"""

import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

TOOLS_DIR = Path(__file__).resolve().parent
SCENARIOS_ROOT = TOOLS_DIR.parent / "scenarios"


def list_scenarios():
    if not SCENARIOS_ROOT.is_dir():
        return []
    return sorted(p.name for p in SCENARIOS_ROOT.iterdir() if p.is_dir() and not p.name.startswith("."))


def list_configs(scenario_dir: Path):
    configs_root = scenario_dir / "environment_configurations"
    if not configs_root.is_dir():
        return []
    return sorted(p.name for p in configs_root.iterdir() if p.is_dir() and not p.name.startswith("."))


def find_inner_stl(scenario_dir: Path):
    matches = sorted((scenario_dir / "cad_models").glob("*_inner.stl"))
    return matches[0] if matches else None


def uv_run(script_name: str, *args) -> list:
    return ["uv", "run", str(TOOLS_DIR / script_name), *[str(a) for a in args]]


class JobRunner:
    """Runs a sequence of (description, cmd) subprocess steps on a background
    thread, streaming combined stdout/stderr into a queue the GUI thread polls.
    Stops at the first non-zero exit."""

    def __init__(self, on_line, on_done):
        self.on_line = on_line
        self.on_done = on_done
        self._queue = queue.Queue()
        self._process = None
        self._busy = False

    @property
    def busy(self):
        return self._busy

    def start(self, steps, root):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._run, args=(steps,), daemon=True).start()
        root.after(50, self._poll, root)

    def cancel(self):
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()

    def _run(self, steps):
        ok = True
        for description, cmd in steps:
            self._queue.put(f"\n=== {description} ===\n$ {' '.join(str(c) for c in cmd)}\n")
            try:
                self._process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
                )
            except OSError as e:
                self._queue.put(f"error: failed to start step: {e}\n")
                ok = False
                break
            for line in self._process.stdout:
                self._queue.put(line)
            returncode = self._process.wait()
            if returncode != 0:
                self._queue.put(f"error: step failed ({description}), exit code {returncode}\n")
                ok = False
                break
        self._process = None
        self._busy = False
        self._queue.put(None)
        self._queue.put(ok)

    def _poll(self, root):
        done = False
        result = True
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    done = True
                    continue
                if done:
                    result = item
                    break
                self.on_line(item)
        except queue.Empty:
            pass
        if done:
            self.on_done(result)
        else:
            root.after(50, self._poll, root)


class ToolsGUI(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.pack(fill=tk.BOTH, expand=True)
        self.runner = JobRunner(self._log, self._on_job_done)
        self._buttons = []

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.X)
        self._build_new_scenario_tab(notebook)
        self._build_existing_scenario_tab(notebook)
        self._build_results_tab(notebook)

        ttk.Label(self, text="Log").pack(anchor="w", pady=(8, 0))
        self.log = scrolledtext.ScrolledText(self, height=18, state="disabled")
        self.log.pack(fill=tk.BOTH, expand=True)

        self.cancel_button = ttk.Button(self, text="Cancel running step", command=self.runner.cancel)
        self.cancel_button.pack(anchor="e", pady=(4, 0))

    # ---- New scenario tab ----------------------------------------------

    def _build_new_scenario_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="New scenario")
        tab.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(tab, text="Optional: convert OpenFOAM polyMesh -> STL first", font=("", 9, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w"
        )
        row += 1
        self.polymesh_dir_var = tk.StringVar()
        ttk.Label(tab, text="Folder to search for polyMesh dirs:").grid(row=row, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.polymesh_dir_var).grid(row=row, column=1, sticky="ew", padx=4)
        ttk.Button(tab, text="Browse...", command=lambda: self._browse_dir(self.polymesh_dir_var)).grid(row=row, column=2)
        row += 1
        convert_button = ttk.Button(tab, text="Convert polyMesh to STL", command=self._run_polymesh_convert)
        convert_button.grid(row=row, column=1, sticky="w", pady=(2, 0))
        self._buttons.append(convert_button)

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)

        row += 1
        ttk.Label(tab, text="Create scenario from raw CFD export", font=("", 9, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w"
        )

        self.raw_dir_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.thickness_var = tk.StringVar(value="0.2")
        self.force_var = tk.BooleanVar()

        row += 1
        ttk.Label(tab, text="Raw CFD export folder:").grid(row=row, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.raw_dir_var).grid(row=row, column=1, sticky="ew", padx=4)
        ttk.Button(tab, text="Browse...", command=lambda: self._browse_dir(self.raw_dir_var, self.name_var)).grid(
            row=row, column=2
        )

        row += 1
        ttk.Label(tab, text="Scenario name:").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(tab, textvariable=self.name_var).grid(row=row, column=1, sticky="ew", padx=4, pady=(6, 0))

        row += 1
        ttk.Label(tab, text="Wall thickness (m):").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(tab, textvariable=self.thickness_var, width=10).grid(row=row, column=1, sticky="w", padx=4, pady=(6, 0))

        row += 1
        ttk.Checkbutton(tab, text="Overwrite if scenario already exists", variable=self.force_var).grid(
            row=row, column=1, sticky="w", pady=(6, 0)
        )

        row += 1
        run_button = ttk.Button(tab, text="Create scenario", command=self._run_new_scenario)
        run_button.grid(row=row, column=1, sticky="w", pady=(10, 0))
        self._buttons.append(run_button)

    def _browse_dir(self, target_var, name_var=None):
        path = filedialog.askdirectory(title="Select folder")
        if path:
            target_var.set(path)
            if name_var is not None and not name_var.get():
                name_var.set(Path(path).name)

    def _run_polymesh_convert(self):
        folder = self.polymesh_dir_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Missing input", "Choose a valid folder to search for polyMesh directories first.")
            return
        cmd = uv_run("polymesh_to_stl.py", "--search-root", folder)
        self._clear_log()
        self._set_busy(True)
        self.runner.start([("Convert polyMesh to STL", cmd)], self.winfo_toplevel())

    def _run_new_scenario(self):
        raw_dir = self.raw_dir_var.get().strip()
        name = self.name_var.get().strip()
        if not raw_dir or not Path(raw_dir).is_dir():
            messagebox.showerror("Missing input", "Choose a valid raw CFD export folder first.")
            return
        if not name:
            messagebox.showerror("Missing input", "Enter a name for the new scenario.")
            return
        try:
            thickness = float(self.thickness_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Wall thickness must be a number.")
            return

        scenario_dir = SCENARIOS_ROOT / name
        create_args = [raw_dir, name]
        if self.force_var.get():
            create_args.append("--force")
        create_cmd = uv_run("create_scenario_from_raw_data.py", *create_args)

        inner_stl = scenario_dir / "cad_models" / f"{name}_inner.stl"
        walls_cmd = uv_run("generate_walls_and_obstacles.py", inner_stl, "--thickness", thickness)

        update_cmd = uv_run("update_scenario_models.py", scenario_dir)

        steps = [
            ("1/3 scaffold scenario from raw data", create_cmd),
            ("2/3 generate walls and obstacles", walls_cmd),
            ("3/3 sync config.yaml models", update_cmd),
        ]
        self._clear_log()
        self._set_busy(True)
        self.runner.start(steps, self.winfo_toplevel())

    # ---- Existing scenario tab -----------------------------------------

    def _build_existing_scenario_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Existing scenario tools")
        tab.columnconfigure(1, weight=1)

        self.scenario_var = tk.StringVar()
        self.config_var = tk.StringVar()
        self.thickness2_var = tk.StringVar(value="0.2")

        row = 0
        ttk.Label(tab, text="Scenario:").grid(row=row, column=0, sticky="w")
        self.scenario_combo = ttk.Combobox(tab, textvariable=self.scenario_var, state="readonly")
        self.scenario_combo.grid(row=row, column=1, sticky="ew", padx=4)
        self.scenario_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_configs())
        ttk.Button(tab, text="Refresh", command=self._refresh_scenarios).grid(row=row, column=2)

        row += 1
        ttk.Label(tab, text="Config (blank = all):").grid(row=row, column=0, sticky="w", pady=(6, 0))
        self.config_combo = ttk.Combobox(tab, textvariable=self.config_var)
        self.config_combo.grid(row=row, column=1, sticky="ew", padx=4, pady=(6, 0))

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)

        row += 1
        ttk.Label(tab, text="Wall thickness (m):").grid(row=row, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.thickness2_var, width=10).grid(row=row, column=1, sticky="w", padx=4)
        regen_button = ttk.Button(tab, text="Regenerate walls && obstacles", command=self._run_regenerate_walls)
        regen_button.grid(row=row, column=2)
        self._buttons.append(regen_button)

        row += 1
        sync_button = ttk.Button(tab, text="Sync config.yaml models", command=self._run_sync_models)
        sync_button.grid(row=row, column=1, sticky="w", pady=(8, 0))
        self._buttons.append(sync_button)

        row += 1
        detect_button = ttk.Button(tab, text="Detect wind resolution", command=lambda: self._run_detect_wind(apply=False))
        detect_button.grid(row=row, column=1, sticky="w", pady=(8, 0))
        self._buttons.append(detect_button)
        apply_button = ttk.Button(tab, text="Detect && apply cell_size", command=lambda: self._run_detect_wind(apply=True))
        apply_button.grid(row=row, column=2, pady=(8, 0))
        self._buttons.append(apply_button)

        self._refresh_scenarios()

    def _refresh_scenarios(self):
        scenarios = list_scenarios()
        self.scenario_combo["values"] = scenarios
        if scenarios and self.scenario_var.get() not in scenarios:
            self.scenario_var.set(scenarios[0])
        self._refresh_configs()

    def _refresh_configs(self):
        name = self.scenario_var.get()
        if not name:
            self.config_combo["values"] = []
            return
        self.config_combo["values"] = list_configs(SCENARIOS_ROOT / name)

    def _selected_scenario_dir(self):
        name = self.scenario_var.get().strip()
        if not name:
            messagebox.showerror("Missing input", "Choose a scenario first.")
            return None
        scenario_dir = SCENARIOS_ROOT / name
        if not scenario_dir.is_dir():
            messagebox.showerror("Not found", f"{scenario_dir} does not exist.")
            return None
        return scenario_dir

    def _run_regenerate_walls(self):
        scenario_dir = self._selected_scenario_dir()
        if scenario_dir is None:
            return
        inner_stl = find_inner_stl(scenario_dir)
        if inner_stl is None:
            messagebox.showerror("Not found", f"No *_inner.stl found under {scenario_dir / 'cad_models'}.")
            return
        try:
            thickness = float(self.thickness2_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Wall thickness must be a number.")
            return
        cmd = uv_run("generate_walls_and_obstacles.py", inner_stl, "--thickness", thickness)
        self._clear_log()
        self._set_busy(True)
        self.runner.start([("Regenerate walls and obstacles", cmd)], self.winfo_toplevel())

    def _run_sync_models(self):
        scenario_dir = self._selected_scenario_dir()
        if scenario_dir is None:
            return
        args = [scenario_dir]
        config = self.config_var.get().strip()
        if config:
            args += ["--config", config]
        cmd = uv_run("update_scenario_models.py", *args)
        self._clear_log()
        self._set_busy(True)
        self.runner.start([("Sync config.yaml models", cmd)], self.winfo_toplevel())

    def _run_detect_wind(self, apply: bool):
        scenario_dir = self._selected_scenario_dir()
        if scenario_dir is None:
            return
        args = [scenario_dir]
        config = self.config_var.get().strip()
        if config:
            args += ["--config", config]
        if apply:
            args.append("--apply")
        cmd = uv_run("detect_wind_resolution.py", *args)
        self._clear_log()
        self._set_busy(True)
        self.runner.start([("Detect wind resolution" + (" (apply)" if apply else ""), cmd)], self.winfo_toplevel())

    # ---- Results tab ------------------------------------------------------

    def _build_results_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Results")
        tab.columnconfigure(1, weight=1)

        self.result_path_var = tk.StringVar()
        self.plot_z_var = tk.StringVar(value="0")
        self.plot_out_var = tk.StringVar()
        self.plot_no_show_var = tk.BooleanVar()
        self.plot_no_occupancy_var = tk.BooleanVar()

        row = 0
        ttk.Label(tab, text="Result file or 'result' directory:").grid(row=row, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.result_path_var).grid(row=row, column=1, sticky="ew", padx=4)
        browse_frame = ttk.Frame(tab)
        browse_frame.grid(row=row, column=2)
        ttk.Button(browse_frame, text="File...", command=self._browse_result_file).pack(side=tk.LEFT)
        ttk.Button(browse_frame, text="Folder...", command=lambda: self._browse_dir(self.result_path_var)).pack(
            side=tk.LEFT
        )

        row += 1
        inspect_button = ttk.Button(tab, text="Inspect / export CSV", command=self._run_inspect)
        inspect_button.grid(row=row, column=1, sticky="w", pady=(8, 0))
        self._buttons.append(inspect_button)

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)

        row += 1
        ttk.Label(tab, text="Plot heatmap (single iteration file only)", font=("", 9, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w"
        )

        row += 1
        ttk.Label(tab, text="Z layer:").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(tab, textvariable=self.plot_z_var, width=6).grid(row=row, column=1, sticky="w", padx=4, pady=(6, 0))

        row += 1
        ttk.Checkbutton(
            tab, text="Save to file instead of opening a window", variable=self.plot_no_show_var
        ).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(tab, text="Output image (if saving):").grid(row=row, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.plot_out_var).grid(row=row, column=1, sticky="ew", padx=4)
        ttk.Button(tab, text="Browse...", command=self._browse_plot_out).grid(row=row, column=2)

        row += 1
        ttk.Checkbutton(tab, text="Don't overlay walls/obstacles", variable=self.plot_no_occupancy_var).grid(
            row=row, column=1, sticky="w"
        )

        row += 1
        plot_button = ttk.Button(tab, text="Plot heatmap", command=self._run_plot_heatmap)
        plot_button.grid(row=row, column=1, sticky="w", pady=(8, 0))
        self._buttons.append(plot_button)

    def _browse_result_file(self):
        path = filedialog.askopenfilename(title="Select an iteration_N result file")
        if path:
            self.result_path_var.set(path)

    def _browse_plot_out(self):
        path = filedialog.asksaveasfilename(title="Save heatmap image as", defaultextension=".png")
        if path:
            self.plot_out_var.set(path)

    def _run_inspect(self):
        path = self.result_path_var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showerror("Missing input", "Choose a valid result file or directory first.")
            return
        cmd = uv_run("inspect_result_file.py", path)
        self._clear_log()
        self._set_busy(True)
        self.runner.start([("Inspect result", cmd)], self.winfo_toplevel())

    def _run_plot_heatmap(self):
        path = self.result_path_var.get().strip()
        if not path or not Path(path).is_file():
            messagebox.showerror("Missing input", "Choose a single result iteration file first (not a directory).")
            return
        try:
            z = int(self.plot_z_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Z layer must be an integer.")
            return
        args = [path, "--z", z]
        if self.plot_no_show_var.get():
            args.append("--no-show")
            out = self.plot_out_var.get().strip()
            if out:
                args += ["--out", out]
        if self.plot_no_occupancy_var.get():
            args.append("--no-occupancy")
        cmd = uv_run("plot_result_heatmap.py", *args)
        self._clear_log()
        self._set_busy(True)
        self.runner.start([("Plot heatmap", cmd)], self.winfo_toplevel())

    # ---- shared -----------------------------------------------------------

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for button in self._buttons:
            button.configure(state=state)

    def _on_job_done(self, ok):
        self._set_busy(False)
        self._refresh_scenarios()
        if ok:
            self._log("\ndone.\n")
        else:
            self._log("\nfailed - see log above.\n")
            messagebox.showerror("Step failed", "A step failed - see the log for details.")


def main():
    if shutil.which("uv") is None:
        print(
            "error: uv not found on PATH (see environments/tools/README.md, "
            "or https://docs.astral.sh/uv/getting-started/installation/)",
            file=sys.stderr,
        )
        sys.exit(1)
    root = tk.Tk()
    root.title("GADEN scenario tools")
    root.geometry("800x680")
    ToolsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
