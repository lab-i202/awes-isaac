# gui/profile_gui.py
#
# Tkinter GUI for choosing rendering options.
#
# This file must not import Isaac Sim.
# Isaac Sim starts only after the GUI closes.

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any

from utils.profile_io import ensure_profiles_dir, load_profile, save_profile
from utils.profile_schema import PRESETS, build_profile_from_options, get_default_options
from utils.scoring import score_profile_options


RENDERER_OPTIONS = [
    "MinimalRendering",
    "RaytracedLighting",
    "RealTimePathTracing",
    "PathTracing",
]

RESOLUTION_OPTIONS = [
    "640x480",
    "1280x720",
    "1920x1080",
    "2560x1440",
]

DLSS_OPTIONS = {
    "Performance": 0,
    "Balanced": 1,
    "Quality": 2,
}


def run_profile_gui(project_root: Path) -> dict[str, Any] | None:
    app = ProfileGui(project_root=project_root)
    return app.run()


class ProfileGui:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.profiles_dir = ensure_profiles_dir(project_root)
        self.result: dict[str, Any] | None = None

        self.root = tk.Tk()
        self.root.title("Isaac Sim Rendering Profile")
        self.root.geometry("760x720")
        self.root.minsize(720, 640)

        self._build_variables()
        self._build_layout()
        self._load_options_into_gui(get_default_options())
        self._update_meters()

    def run(self) -> dict[str, Any] | None:
        self.root.mainloop()
        return self.result

    def _build_variables(self) -> None:
        self.profile_name = tk.StringVar()
        self.preset_name = tk.StringVar()

        self.renderer = tk.StringVar()
        self.resolution = tk.StringVar()
        self.dlss_mode = tk.StringVar()

        self.rt_subframes = tk.IntVar()
        self.duration_frames = tk.IntVar()
        self.warmup_frames = tk.IntVar()

        self.path_spp = tk.IntVar()
        self.path_total_spp = tk.IntVar()
        self.max_bounces = tk.IntVar()

        self.headless = tk.BooleanVar()
        self.denoiser = tk.BooleanVar()
        self.rgb = tk.BooleanVar()
        self.semantic_segmentation = tk.BooleanVar()
        self.bounding_box_2d_tight = tk.BooleanVar()
        self.gpu_telemetry = tk.BooleanVar()

        self.drone_motion_radius = tk.DoubleVar()
        self.drone_motion_height = tk.DoubleVar()
        self.object_motion_amplitude = tk.DoubleVar()

        self.camera_x = tk.DoubleVar()
        self.camera_y = tk.DoubleVar()
        self.camera_z = tk.DoubleVar()
        self.camera_look_x = tk.DoubleVar()
        self.camera_look_y = tk.DoubleVar()
        self.camera_look_z = tk.DoubleVar()
        self.focal_length = tk.DoubleVar()

        self.smoothness_text = tk.StringVar()
        self.quality_text = tk.StringVar()
        self.notes_text = tk.StringVar()

        self.smoothness_score = tk.DoubleVar(value=0.0)
        self.quality_score = tk.DoubleVar(value=0.0)

    def _build_layout(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        title = ttk.Label(
            main,
            text="Isaac Sim Rendering Profile Builder",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w", pady=(0, 8))

        subtitle = ttk.Label(
            main,
            text=(
                "Pick rendering options, save them to JSON, then run Isaac Sim. "
                "Scores are heuristic, not benchmark guarantees."
            ),
            wraplength=720,
        )
        subtitle.pack(anchor="w", pady=(0, 12))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)

        self.basic_tab = ttk.Frame(self.notebook, padding=12)
        self.scene_tab = ttk.Frame(self.notebook, padding=12)
        self.camera_tab = ttk.Frame(self.notebook, padding=12)

        self.notebook.add(self.basic_tab, text="Rendering")
        self.notebook.add(self.scene_tab, text="Scene")
        self.notebook.add(self.camera_tab, text="Camera")

        self._build_rendering_tab()
        self._build_scene_tab()
        self._build_camera_tab()

        meter_frame = ttk.LabelFrame(main, text="Expected result", padding=12)
        meter_frame.pack(fill="x", pady=(12, 8))

        ttk.Label(meter_frame, textvariable=self.smoothness_text).pack(anchor="w")
        ttk.Progressbar(
            meter_frame,
            maximum=100,
            variable=self.smoothness_score,
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(meter_frame, textvariable=self.quality_text).pack(anchor="w")
        ttk.Progressbar(
            meter_frame,
            maximum=100,
            variable=self.quality_score,
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(
            meter_frame,
            textvariable=self.notes_text,
            wraplength=700,
            justify="left",
        ).pack(anchor="w")

        button_frame = ttk.Frame(main)
        button_frame.pack(fill="x", pady=(8, 0))

        ttk.Button(button_frame, text="Load JSON Profile", command=self._on_load).pack(
            side="left"
        )
        ttk.Button(button_frame, text="Save JSON Only", command=self._on_save_only).pack(
            side="left",
            padx=(8, 0),
        )
        ttk.Button(button_frame, text="Run", command=self._on_run).pack(
            side="right"
        )
        ttk.Button(button_frame, text="Cancel", command=self._on_cancel).pack(
            side="right",
            padx=(0, 8),
        )

    def _build_rendering_tab(self) -> None:
        row = 0

        ttk.Label(self.basic_tab, text="Preset").grid(row=row, column=0, sticky="w")
        preset_box = ttk.Combobox(
            self.basic_tab,
            textvariable=self.preset_name,
            values=list(PRESETS.keys()),
            state="readonly",
            width=36,
        )
        preset_box.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        preset_box.bind("<<ComboboxSelected>>", self._on_preset_selected)
        row += 1

        ttk.Label(self.basic_tab, text="Profile name").grid(row=row, column=0, sticky="w")
        ttk.Entry(self.basic_tab, textvariable=self.profile_name, width=40).grid(
            row=row, column=1, sticky="ew", padx=8, pady=4
        )
        row += 1

        ttk.Label(self.basic_tab, text="Renderer").grid(row=row, column=0, sticky="w")
        renderer_box = ttk.Combobox(
            self.basic_tab,
            textvariable=self.renderer,
            values=RENDERER_OPTIONS,
            state="readonly",
            width=36,
        )
        renderer_box.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        renderer_box.bind("<<ComboboxSelected>>", self._on_any_change)
        row += 1

        ttk.Label(self.basic_tab, text="Resolution").grid(row=row, column=0, sticky="w")
        resolution_box = ttk.Combobox(
            self.basic_tab,
            textvariable=self.resolution,
            values=RESOLUTION_OPTIONS,
            state="readonly",
            width=36,
        )
        resolution_box.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        resolution_box.bind("<<ComboboxSelected>>", self._on_any_change)
        row += 1

        ttk.Label(self.basic_tab, text="DLSS mode").grid(row=row, column=0, sticky="w")
        dlss_box = ttk.Combobox(
            self.basic_tab,
            textvariable=self.dlss_mode,
            values=list(DLSS_OPTIONS.keys()),
            state="readonly",
            width=36,
        )
        dlss_box.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        dlss_box.bind("<<ComboboxSelected>>", self._on_any_change)
        row += 1

        self._add_spinbox(
            parent=self.basic_tab,
            row=row,
            label="RT subframes",
            variable=self.rt_subframes,
            from_=1,
            to=128,
            increment=1,
        )
        row += 1

        self._add_spinbox(
            parent=self.basic_tab,
            row=row,
            label="Path tracing spp",
            variable=self.path_spp,
            from_=1,
            to=512,
            increment=1,
        )
        row += 1

        self._add_spinbox(
            parent=self.basic_tab,
            row=row,
            label="Path tracing total spp",
            variable=self.path_total_spp,
            from_=1,
            to=512,
            increment=1,
        )
        row += 1

        self._add_spinbox(
            parent=self.basic_tab,
            row=row,
            label="Max bounces",
            variable=self.max_bounces,
            from_=1,
            to=32,
            increment=1,
        )
        row += 1

        check_frame = ttk.Frame(self.basic_tab)
        check_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self._add_check(check_frame, "Headless", self.headless)
        self._add_check(check_frame, "Denoiser", self.denoiser)
        self._add_check(check_frame, "RGB", self.rgb)
        self._add_check(check_frame, "Semantic segmentation", self.semantic_segmentation)
        self._add_check(check_frame, "2D tight boxes", self.bounding_box_2d_tight)
        self._add_check(check_frame, "GPU telemetry", self.gpu_telemetry)

        self.basic_tab.columnconfigure(1, weight=1)

    def _build_scene_tab(self) -> None:
        row = 0

        self._add_spinbox(
            parent=self.scene_tab,
            row=row,
            label="Duration frames",
            variable=self.duration_frames,
            from_=1,
            to=100000,
            increment=1,
        )
        row += 1

        self._add_spinbox(
            parent=self.scene_tab,
            row=row,
            label="Warmup frames",
            variable=self.warmup_frames,
            from_=0,
            to=10000,
            increment=1,
        )
        row += 1

        self._add_float_spinbox(
            parent=self.scene_tab,
            row=row,
            label="Drone motion radius",
            variable=self.drone_motion_radius,
            from_=0.0,
            to=100.0,
            increment=0.1,
        )
        row += 1

        self._add_float_spinbox(
            parent=self.scene_tab,
            row=row,
            label="Drone motion height",
            variable=self.drone_motion_height,
            from_=0.0,
            to=100.0,
            increment=0.1,
        )
        row += 1

        self._add_float_spinbox(
            parent=self.scene_tab,
            row=row,
            label="Object motion amplitude",
            variable=self.object_motion_amplitude,
            from_=0.0,
            to=100.0,
            increment=0.1,
        )

        self.scene_tab.columnconfigure(1, weight=1)

    def _build_camera_tab(self) -> None:
        row = 0

        for label, variable in [
            ("Camera X", self.camera_x),
            ("Camera Y", self.camera_y),
            ("Camera Z", self.camera_z),
            ("Look-at X", self.camera_look_x),
            ("Look-at Y", self.camera_look_y),
            ("Look-at Z", self.camera_look_z),
            ("Focal length", self.focal_length),
        ]:
            self._add_float_spinbox(
                parent=self.camera_tab,
                row=row,
                label=label,
                variable=variable,
                from_=-1000.0,
                to=1000.0,
                increment=0.1,
            )
            row += 1

        self.camera_tab.columnconfigure(1, weight=1)

    def _add_spinbox(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.IntVar,
        from_: int,
        to: int,
        increment: int,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        widget = ttk.Spinbox(
            parent,
            from_=from_,
            to=to,
            increment=increment,
            textvariable=variable,
            width=12,
            command=self._update_meters,
        )
        widget.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        widget.bind("<KeyRelease>", self._on_any_change)
        widget.bind("<FocusOut>", self._on_any_change)

    def _add_float_spinbox(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.DoubleVar,
        from_: float,
        to: float,
        increment: float,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        widget = ttk.Spinbox(
            parent,
            from_=from_,
            to=to,
            increment=increment,
            textvariable=variable,
            width=12,
            command=self._update_meters,
        )
        widget.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        widget.bind("<KeyRelease>", self._on_any_change)
        widget.bind("<FocusOut>", self._on_any_change)

    def _add_check(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.BooleanVar,
    ) -> None:
        check = ttk.Checkbutton(
            parent,
            text=label,
            variable=variable,
            command=self._update_meters,
        )
        check.pack(anchor="w")

    def _on_preset_selected(self, event: object | None = None) -> None:
        preset = PRESETS[self.preset_name.get()]
        self._load_options_into_gui(preset)

    def _on_any_change(self, event: object | None = None) -> None:
        self._update_meters()

    def _on_load(self) -> None:
        selected = filedialog.askopenfilename(
            title="Load JSON profile",
            initialdir=str(self.profiles_dir),
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")],
        )

        if not selected:
            return

        try:
            profile = load_profile(Path(selected))
            options = profile.get("options", profile)
            self._load_options_into_gui(options)
            messagebox.showinfo("Loaded", f"Loaded profile:\n{selected}")
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    def _on_save_only(self) -> None:
        try:
            profile, profile_path = self._save_current_profile()
            self.result = {
                "run_requested": False,
                "profile": profile,
                "profile_path": profile_path,
            }
            messagebox.showinfo("Saved", f"Profile saved:\n{profile_path}")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def _on_run(self) -> None:
        try:
            profile, profile_path = self._save_current_profile()
            self.result = {
                "run_requested": True,
                "profile": profile,
                "profile_path": profile_path,
            }
            self.root.destroy()
        except Exception as exc:
            messagebox.showerror("Run failed", str(exc))

    def _on_cancel(self) -> None:
        self.result = None
        self.root.destroy()

    def _save_current_profile(self) -> tuple[dict[str, Any], Path]:
        options = self._collect_options_from_gui()
        score = score_profile_options(options)
        profile = build_profile_from_options(options=options, score=score)

        profile_path = save_profile(
            profiles_dir=self.profiles_dir,
            profile=profile,
        )

        return profile, profile_path

    def _collect_options_from_gui(self) -> dict[str, Any]:
        width, height = self._parse_resolution(self.resolution.get())

        profile_name = self.profile_name.get().strip()
        if not profile_name:
            raise ValueError("Profile name cannot be empty.")

        return {
            "profile_name": profile_name,
            "renderer": self.renderer.get(),
            "width": width,
            "height": height,
            "resolution": [width, height],
            "dlss_mode_name": self.dlss_mode.get(),
            "dlss_exec_mode": DLSS_OPTIONS[self.dlss_mode.get()],
            "rt_subframes": int(self.rt_subframes.get()),
            "duration_frames": int(self.duration_frames.get()),
            "warmup_frames": int(self.warmup_frames.get()),
            "path_spp": int(self.path_spp.get()),
            "path_total_spp": int(self.path_total_spp.get()),
            "max_bounces": int(self.max_bounces.get()),
            "headless": bool(self.headless.get()),
            "denoiser": bool(self.denoiser.get()),
            "rgb": bool(self.rgb.get()),
            "semantic_segmentation": bool(self.semantic_segmentation.get()),
            "bounding_box_2d_tight": bool(self.bounding_box_2d_tight.get()),
            "gpu_telemetry": bool(self.gpu_telemetry.get()),
            "drone_motion_radius": float(self.drone_motion_radius.get()),
            "drone_motion_height": float(self.drone_motion_height.get()),
            "object_motion_amplitude": float(self.object_motion_amplitude.get()),
            "camera_position": [
                float(self.camera_x.get()),
                float(self.camera_y.get()),
                float(self.camera_z.get()),
            ],
            "camera_look_at": [
                float(self.camera_look_x.get()),
                float(self.camera_look_y.get()),
                float(self.camera_look_z.get()),
            ],
            "focal_length": float(self.focal_length.get()),
        }

    def _load_options_into_gui(self, options: dict[str, Any]) -> None:
        width = int(options.get("width", options.get("resolution", [1280, 720])[0]))
        height = int(options.get("height", options.get("resolution", [1280, 720])[1]))

        self.profile_name.set(options.get("profile_name", "custom_profile"))
        self.preset_name.set(options.get("preset_name", ""))

        self.renderer.set(options.get("renderer", "RealTimePathTracing"))
        self.resolution.set(f"{width}x{height}")

        dlss_mode_name = options.get("dlss_mode_name", "Balanced")
        if dlss_mode_name not in DLSS_OPTIONS:
            dlss_mode_name = "Balanced"
        self.dlss_mode.set(dlss_mode_name)

        self.rt_subframes.set(int(options.get("rt_subframes", 8)))
        self.duration_frames.set(int(options.get("duration_frames", 90)))
        self.warmup_frames.set(int(options.get("warmup_frames", 20)))

        self.path_spp.set(int(options.get("path_spp", 16)))
        self.path_total_spp.set(int(options.get("path_total_spp", 16)))
        self.max_bounces.set(int(options.get("max_bounces", 4)))

        self.headless.set(bool(options.get("headless", False)))
        self.denoiser.set(bool(options.get("denoiser", True)))
        self.rgb.set(bool(options.get("rgb", True)))
        self.semantic_segmentation.set(bool(options.get("semantic_segmentation", False)))
        self.bounding_box_2d_tight.set(bool(options.get("bounding_box_2d_tight", False)))
        self.gpu_telemetry.set(bool(options.get("gpu_telemetry", True)))

        self.drone_motion_radius.set(float(options.get("drone_motion_radius", 1.2)))
        self.drone_motion_height.set(float(options.get("drone_motion_height", 1.2)))
        self.object_motion_amplitude.set(float(options.get("object_motion_amplitude", 1.5)))

        camera_position = options.get("camera_position", [4.2, -5.2, 2.8])
        camera_look_at = options.get("camera_look_at", [0.0, 0.0, 0.85])

        self.camera_x.set(float(camera_position[0]))
        self.camera_y.set(float(camera_position[1]))
        self.camera_z.set(float(camera_position[2]))
        self.camera_look_x.set(float(camera_look_at[0]))
        self.camera_look_y.set(float(camera_look_at[1]))
        self.camera_look_z.set(float(camera_look_at[2]))
        self.focal_length.set(float(options.get("focal_length", 35.0)))

        self._update_meters()

    def _update_meters(self) -> None:
        try:
            options = self._collect_options_from_gui()
        except Exception:
            return

        score = score_profile_options(options)

        self.smoothness_score.set(score["smoothness_score"])
        self.quality_score.set(score["quality_score"])
        self.smoothness_text.set(
            f"Smoothness estimate: {score['smoothness_score']:.0f}/100 - {score['smoothness_label']}"
        )
        self.quality_text.set(
            f"Image quality estimate: {score['quality_score']:.0f}/100 - {score['quality_label']}"
        )
        self.notes_text.set(" ".join(score["notes"]))

    @staticmethod
    def _parse_resolution(value: str) -> tuple[int, int]:
        parts = value.lower().split("x")
        if len(parts) != 2:
            raise ValueError(f"Invalid resolution: {value}")
        return int(parts[0]), int(parts[1])
