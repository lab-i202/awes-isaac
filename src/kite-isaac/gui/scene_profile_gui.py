# gui/scene_profile_gui.py
#
# Scrollable GUI for editing tethered glider scene JSON profiles.
#
# This file must not import Isaac Sim.
# The GUI runs first. Isaac Sim starts only after the GUI closes.

from __future__ import annotations

import json
import math
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Any

from utils.asset_io import collect_registry_status, discover_environment_assets, load_asset_registry

from utils.profile_io import (
    CAMERA_ORIENTATION_MODES,
    DEFAULT_CAMERA_RIG,
    DEFAULT_CAPTURE,
    DEFAULT_GLIDER_ASSET,
    DEFAULT_ENVIRONMENT,
    ENVIRONMENT_MODES,
    GLIDER_ASSET_MODES,
    normalize_environment,
    compute_parallel_rig_pitch_yaw_roll_deg,
    focal_length_mm_from_horizontal_fov,
    default_profile_path,
    load_json_profile,
    normalize_camera_rig,
    sanitize_filename,
    save_json_profile,
    validate_tethered_glider_profile,
)


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget):
        super().__init__(parent)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.content = ttk.Frame(self.canvas)

        self.content_window = self.canvas.create_window(
            (0, 0),
            window=self.content,
            anchor="nw",
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.content.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_content_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.content_window, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event: tk.Event) -> None:
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")


class SceneProfileGui:
    def __init__(self, project_root: Path, initial_profile_path: Path):
        self.project_root = project_root
        self.available_scene_profiles = self._load_available_scene_profiles()
        self.initial_profile_path = self._resolve_initial_profile_path(initial_profile_path)
        self.current_profile_path = self.initial_profile_path
        self.result: dict[str, Any] | None = None
        self.available_glider_asset_ids = self._load_available_glider_asset_ids()
        self.available_environment_asset_ids = self._load_available_environment_asset_ids()

        self.root = tk.Tk()
        self.root.title("Tethered Glider Scene Profile")
        self.root.geometry("780x700")
        self.root.minsize(720, 560)

        self._build_variables()
        self._build_layout()

        profile = load_json_profile(self.initial_profile_path)
        validate_tethered_glider_profile(profile)
        self._load_profile_into_gui(profile)

    def run(self) -> dict[str, Any] | None:
        self.root.mainloop()
        return self.result

    def _gui_state_path(self) -> Path:
        return self.project_root / "profiles" / "_gui_state.json"

    def _read_gui_state(self) -> dict[str, Any]:
        path = self._gui_state_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_gui_state(self, profile_path: Path, profile: dict[str, Any]) -> None:
        payload = {
            "last_profile_path": str(profile_path.resolve()),
            "last_scene_name": str(profile.get("scene_name", "")),
        }
        path = self._gui_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_available_scene_profiles(self) -> dict[str, Path]:
        profiles_dir = self.project_root / "profiles"
        mapping: dict[str, Path] = {}
        if not profiles_dir.exists():
            return mapping

        for path in sorted(profiles_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                scene_name = str(data.get("scene_name", "")).strip()
                if scene_name:
                    mapping[scene_name] = path.resolve()
        return mapping

    def _resolve_initial_profile_path(self, fallback_profile_path: Path) -> Path:
        state = self._read_gui_state()
        last_profile_raw = str(state.get("last_profile_path", "")).strip()
        if last_profile_raw:
            last_profile = Path(last_profile_raw)
            if last_profile.exists() and last_profile.is_file():
                return last_profile.resolve()

        last_scene_name = str(state.get("last_scene_name", "")).strip()
        if last_scene_name in self.available_scene_profiles:
            return self.available_scene_profiles[last_scene_name]

        return fallback_profile_path.resolve()

    def _refresh_scene_profile_list(self) -> None:
        self.available_scene_profiles = self._load_available_scene_profiles()
        values = sorted(self.available_scene_profiles.keys())
        if hasattr(self, "scene_selector_combo"):
            self.scene_selector_combo.configure(values=values, state="readonly" if values else "normal")

    def _load_available_glider_asset_ids(self) -> list[str]:
        try:
            registry = load_asset_registry(self.project_root)
            statuses = collect_registry_status(self.project_root, registry)
        except Exception:
            return []

        # The dropdown should show registry asset IDs, not arbitrary directory
        # names. A folder is only usable here after it is registered and has a
        # converted USD target path. Prefer converted assets first.
        converted_ids = [
            str(item["asset_id"])
            for item in statuses
            if bool(item.get("converted_usd_exists", False))
        ]
        other_ids = [
            str(item["asset_id"])
            for item in statuses
            if not bool(item.get("converted_usd_exists", False))
        ]

        return sorted(converted_ids) + sorted(other_ids)

    def _load_available_environment_asset_ids(self) -> list[str]:
        try:
            entries = discover_environment_assets(self.project_root)
        except Exception:
            return []

        usable_ids = [str(item["asset_id"]) for item in entries if bool(item.get("scene_exists", False))]
        unusable_ids = [str(item["asset_id"]) for item in entries if not bool(item.get("scene_exists", False))]
        return sorted(usable_ids) + sorted(unusable_ids)

    def _build_variables(self) -> None:
        self.scene_name = tk.StringVar()
        self.selected_scene_name = tk.StringVar()

        self.anchor_x = tk.DoubleVar()
        self.anchor_y = tk.DoubleVar()
        self.anchor_z = tk.DoubleVar()

        self.tether_length_m = tk.DoubleVar()
        self.glider_height_m = tk.DoubleVar()
        self.angular_velocity_rad_s = tk.DoubleVar()

        self.num_frames = tk.IntVar()
        self.time_step_s = tk.DoubleVar()

        self.environment_mode = tk.StringVar()
        self.environment_asset_id = tk.StringVar()
        self.environment_translation_x = tk.DoubleVar()
        self.environment_translation_y = tk.DoubleVar()
        self.environment_translation_z = tk.DoubleVar()
        self.environment_rotation_x_deg = tk.DoubleVar()
        self.environment_rotation_y_deg = tk.DoubleVar()
        self.environment_rotation_z_deg = tk.DoubleVar()
        self.environment_uniform_scale = tk.DoubleVar()
        self.environment_project_lights_enabled = tk.BooleanVar()
        self.environment_fallback_to_plain_debug = tk.BooleanVar()

        self.wingspan_m = tk.DoubleVar()
        self.length_m = tk.DoubleVar()
        self.body_width_m = tk.DoubleVar()
        self.body_height_m = tk.DoubleVar()
        self.wing_chord_m = tk.DoubleVar()
        self.wing_thickness_m = tk.DoubleVar()

        self.glider_asset_mode = tk.StringVar()
        self.glider_asset_id = tk.StringVar()
        self.glider_asset_uniform_scale = tk.DoubleVar()
        self.glider_asset_rotation_x_deg = tk.DoubleVar()
        self.glider_asset_rotation_y_deg = tk.DoubleVar()
        self.glider_asset_rotation_z_deg = tk.DoubleVar()
        self.glider_asset_offset_x = tk.DoubleVar()
        self.glider_asset_offset_y = tk.DoubleVar()
        self.glider_asset_offset_z = tk.DoubleVar()
        self.glider_asset_use_proxy_fallback = tk.BooleanVar()
        self.glider_asset_material_override = tk.BooleanVar()
        self.glider_asset_color_hex = tk.StringVar()

        self.camera_enabled = tk.BooleanVar()
        self.camera_show_markers = tk.BooleanVar()
        self.camera_show_frustums = tk.BooleanVar()
        self.camera_frustum_distance_m = tk.DoubleVar()
        self.camera_frustum_line_width_m = tk.DoubleVar()
        self.camera_orientation_mode = tk.StringVar()
        self.last_camera_orientation_snapshot: dict[str, Any] | None = None

        self.main_camera_x = tk.DoubleVar()
        self.main_camera_y = tk.DoubleVar()
        self.main_camera_z = tk.DoubleVar()
        self.main_camera_color_hex = tk.StringVar()
        self.main_frustum_color_hex = tk.StringVar()

        self.main_pitch_deg = tk.DoubleVar()
        self.main_yaw_deg = tk.DoubleVar()
        self.main_roll_deg = tk.DoubleVar()

        self.secondary_offset_x = tk.DoubleVar()
        self.secondary_offset_y = tk.DoubleVar()
        self.secondary_offset_z = tk.DoubleVar()
        self.secondary_camera_color_hex = tk.StringVar()
        self.secondary_frustum_color_hex = tk.StringVar()

        self.secondary_pitch_offset_deg = tk.DoubleVar()
        self.secondary_yaw_offset_deg = tk.DoubleVar()
        self.secondary_roll_offset_deg = tk.DoubleVar()

        self.camera_look_x = tk.DoubleVar()
        self.camera_look_y = tk.DoubleVar()
        self.camera_look_z = tk.DoubleVar()

        self.camera_horizontal_fov_deg = tk.DoubleVar()
        self.camera_horizontal_aperture_mm = tk.DoubleVar()
        self.camera_focal_length = tk.DoubleVar()
        self.camera_resolution_width = tk.IntVar()
        self.camera_resolution_height = tk.IntVar()

        self.capture_enabled = tk.BooleanVar()
        self.capture_output_root = tk.StringVar()
        self.capture_rgb = tk.BooleanVar()
        self.capture_camera_params = tk.BooleanVar()
        self.capture_rename_after_capture = tk.BooleanVar()
        self.capture_rt_subframes = tk.IntVar()

        self.status_text = tk.StringVar()

    def _build_layout(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        title = ttk.Label(
            main,
            text="Tethered Glider Scene Profile",
            font=("Segoe UI", 15, "bold"),
        )
        title.pack(anchor="w", pady=(0, 4))

        description = ttk.Label(
            main,
            text=(
                "Edit scene, glider, camera, and capture parameters. "
                "Tabs scroll. Buttons stay fixed at the bottom."
            ),
            wraplength=720,
        )
        description.pack(anchor="w", pady=(0, 8))

        bottom_panel = ttk.Frame(main)
        bottom_panel.pack(side="bottom", fill="x", pady=(8, 0))

        status = ttk.Label(
            bottom_panel,
            textvariable=self.status_text,
            wraplength=720,
            foreground="gray",
        )
        status.pack(anchor="w", pady=(0, 8))

        buttons = ttk.Frame(bottom_panel)
        buttons.pack(fill="x")

        ttk.Button(buttons, text="Load JSON", command=self._on_load).pack(side="left")
        ttk.Button(buttons, text="Save JSON", command=self._on_save).pack(
            side="left",
            padx=(8, 0),
        )

        ttk.Button(buttons, text="Run Isaac Sim", command=self._on_run).pack(
            side="right",
        )
        ttk.Button(buttons, text="Cancel", command=self._on_cancel).pack(
            side="right",
            padx=(0, 8),
        )

        notebook = ttk.Notebook(main)
        notebook.pack(side="top", fill="both", expand=True)

        scene_scroll = ScrollableFrame(notebook)
        environment_scroll = ScrollableFrame(notebook)
        glider_scroll = ScrollableFrame(notebook)
        camera_scroll = ScrollableFrame(notebook)
        capture_scroll = ScrollableFrame(notebook)

        notebook.add(scene_scroll, text="Scene")
        notebook.add(environment_scroll, text="Environment")
        notebook.add(glider_scroll, text="Glider")
        notebook.add(camera_scroll, text="Cameras")
        notebook.add(capture_scroll, text="Capture")

        self._build_scene_tab(scene_scroll.content)
        self._build_environment_tab(environment_scroll.content)
        self._build_glider_tab(glider_scroll.content)
        self._build_camera_tab(camera_scroll.content)
        self._build_capture_tab(capture_scroll.content)

    def _build_scene_tab(self, parent: ttk.Frame) -> None:
        parent.configure(padding=12)
        row = 0

        ttk.Label(parent, text="Existing scene profile").grid(row=row, column=0, sticky="w")
        scene_selector = ttk.Frame(parent)
        scene_selector.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        scene_selector.columnconfigure(0, weight=1)
        self.scene_selector_combo = ttk.Combobox(
            scene_selector,
            textvariable=self.selected_scene_name,
            values=sorted(self.available_scene_profiles.keys()),
            state="readonly" if self.available_scene_profiles else "normal",
            width=34,
        )
        self.scene_selector_combo.grid(row=0, column=0, sticky="ew")
        self.scene_selector_combo.bind("<<ComboboxSelected>>", self._on_scene_selected)
        ttk.Button(
            scene_selector,
            text="Refresh",
            command=self._on_refresh_scene_profiles,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        row += 1

        ttk.Label(parent, text="Scene name / new scene name").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.scene_name, width=36).grid(
            row=row,
            column=1,
            sticky="ew",
            padx=8,
            pady=4,
        )
        row += 1

        ttk.Label(
            parent,
            text=(
                "Pick an existing scene profile from the dropdown to load it. "
                "Edit the scene name textbox to save/run a new scene profile. "
                "The last run/saved profile is loaded by default the next time this GUI opens."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        self._add_float_row(parent, row, "Anchor X [m]", self.anchor_x)
        row += 1
        self._add_float_row(parent, row, "Anchor Y [m]", self.anchor_y)
        row += 1
        self._add_float_row(parent, row, "Anchor Z [m]", self.anchor_z)
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        self._add_float_row(parent, row, "Tether length [m]", self.tether_length_m)
        row += 1
        self._add_float_row(parent, row, "Glider height [m]", self.glider_height_m)
        row += 1
        self._add_float_row(parent, row, "Angular velocity [rad/s]", self.angular_velocity_rad_s)
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        self._add_int_row(parent, row, "Number of frames", self.num_frames)
        row += 1
        self._add_float_row(parent, row, "Time step [s]", self.time_step_s)

        parent.columnconfigure(1, weight=1)

    def _build_environment_tab(self, parent: ttk.Frame) -> None:
        parent.configure(padding=12)
        row = 0

        ttk.Label(parent, text="Environment selection", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        row += 1

        ttk.Label(parent, text="Environment mode").grid(row=row, column=0, sticky="w")
        mode_box = ttk.Combobox(
            parent,
            textvariable=self.environment_mode,
            values=ENVIRONMENT_MODES,
            state="readonly",
            width=24,
        )
        mode_box.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        mode_box.bind("<<ComboboxSelected>>", self._on_environment_mode_changed)
        row += 1

        ttk.Label(parent, text="External environment ID").grid(row=row, column=0, sticky="w")
        environment_selector = ttk.Frame(parent)
        environment_selector.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        environment_selector.columnconfigure(0, weight=1)

        self.environment_asset_combo = ttk.Combobox(
            environment_selector,
            textvariable=self.environment_asset_id,
            values=self.available_environment_asset_ids,
            state="readonly" if self.available_environment_asset_ids else "normal",
            width=34,
        )
        self.environment_asset_combo.grid(row=0, column=0, sticky="ew")

        ttk.Button(
            environment_selector,
            text="Refresh",
            command=self._on_refresh_environment_asset_list,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        row += 1

        ttk.Label(
            parent,
            text=(
                "plain_debug keeps the previous simple generated world. external_usd loads "
                "assets/environments/<id>/scene.usda or the scene_path from metadata.json under /World/Environment."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 10))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        ttk.Label(parent, text="Environment transform", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        row += 1

        self._add_float_row(parent, row, "Translation X [m]", self.environment_translation_x)
        row += 1
        self._add_float_row(parent, row, "Translation Y [m]", self.environment_translation_y)
        row += 1
        self._add_float_row(parent, row, "Translation Z [m]", self.environment_translation_z)
        row += 1

        self._add_float_row(parent, row, "Rotation X [deg]", self.environment_rotation_x_deg)
        row += 1
        self._add_float_row(parent, row, "Rotation Y [deg]", self.environment_rotation_y_deg)
        row += 1
        self._add_float_row(parent, row, "Rotation Z [deg]", self.environment_rotation_z_deg)
        row += 1

        self._add_float_row(parent, row, "Uniform scale", self.environment_uniform_scale)
        row += 1

        ttk.Checkbutton(
            parent,
            text="Use project default lights",
            variable=self.environment_project_lights_enabled,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row += 1

        ttk.Checkbutton(
            parent,
            text="Fallback to plain_debug if external environment fails",
            variable=self.environment_fallback_to_plain_debug,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(
            parent,
            text=(
                "Use the transform to move the whole imported scene relative to the glider anchor/camera rig. "
                "For external scenes that already contain HDRI/dome/sun lights, keep project default lights off."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))

        parent.columnconfigure(1, weight=1)

    def _build_glider_tab(self, parent: ttk.Frame) -> None:
        parent.configure(padding=12)
        row = 0

        ttk.Label(parent, text="Proxy glider dimensions", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        row += 1

        self._add_float_row(parent, row, "Wingspan [m]", self.wingspan_m)
        row += 1
        self._add_float_row(parent, row, "Length [m]", self.length_m)
        row += 1
        self._add_float_row(parent, row, "Body width [m]", self.body_width_m)
        row += 1
        self._add_float_row(parent, row, "Body height [m]", self.body_height_m)
        row += 1
        self._add_float_row(parent, row, "Wing chord [m]", self.wing_chord_m)
        row += 1
        self._add_float_row(parent, row, "Wing thickness [m]", self.wing_thickness_m)
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=12)
        row += 1

        ttk.Label(parent, text="Visual asset", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        row += 1

        ttk.Label(parent, text="Visual mode").grid(row=row, column=0, sticky="w")
        mode_box = ttk.Combobox(
            parent,
            textvariable=self.glider_asset_mode,
            values=GLIDER_ASSET_MODES,
            state="readonly",
            width=24,
        )
        mode_box.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        ttk.Label(parent, text="Asset ID").grid(row=row, column=0, sticky="w")
        asset_selector = ttk.Frame(parent)
        asset_selector.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        asset_selector.columnconfigure(0, weight=1)

        self.glider_asset_combo = ttk.Combobox(
            asset_selector,
            textvariable=self.glider_asset_id,
            values=self.available_glider_asset_ids,
            state="readonly" if self.available_glider_asset_ids else "normal",
            width=34,
        )
        self.glider_asset_combo.grid(row=0, column=0, sticky="ew")

        ttk.Button(
            asset_selector,
            text="Refresh",
            command=self._on_refresh_glider_asset_list,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        row += 1

        self._add_float_row(parent, row, "USD uniform scale", self.glider_asset_uniform_scale)
        row += 1
        self._add_float_row(parent, row, "USD rotation X [deg]", self.glider_asset_rotation_x_deg)
        row += 1
        self._add_float_row(parent, row, "USD rotation Y [deg]", self.glider_asset_rotation_y_deg)
        row += 1
        self._add_float_row(parent, row, "USD rotation Z [deg]", self.glider_asset_rotation_z_deg)
        row += 1
        self._add_float_row(parent, row, "USD offset X [m]", self.glider_asset_offset_x)
        row += 1
        self._add_float_row(parent, row, "USD offset Y [m]", self.glider_asset_offset_y)
        row += 1
        self._add_float_row(parent, row, "USD offset Z [m]", self.glider_asset_offset_z)
        row += 1

        ttk.Checkbutton(
            parent,
            text="Fallback to proxy if USD asset load fails",
            variable=self.glider_asset_use_proxy_fallback,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row += 1

        ttk.Checkbutton(
            parent,
            text="Override USD material with simple matte color",
            variable=self.glider_asset_material_override,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(parent, text="Glider color").grid(row=row, column=0, sticky="w")
        color_row = ttk.Frame(parent)
        color_row.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        self.glider_color_preview = tk.Label(
            color_row,
            textvariable=self.glider_asset_color_hex,
            width=12,
            relief="solid",
            borderwidth=1,
        )
        self.glider_color_preview.pack(side="left")
        ttk.Button(
            color_row,
            text="Pick color",
            command=self._on_pick_glider_asset_color,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            color_row,
            text="Visible yellow",
            command=self._on_set_glider_asset_visible_yellow,
        ).pack(side="left", padx=(8, 0))
        row += 1

        ttk.Label(
            parent,
            text=(
                "proxy uses the procedural box glider. usd_reference references the converted Bixler USD. "
                "The current Bixler conversion is one geometry prim with no trusted texture/materials, "
                "so per-part coloring is not available yet."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))

        parent.columnconfigure(1, weight=1)

    def _build_camera_tab(self, parent: ttk.Frame) -> None:
        parent.configure(padding=12)
        row = 0

        ttk.Checkbutton(
            parent,
            text="Enable two-camera rig",
            variable=self.camera_enabled,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1

        ttk.Checkbutton(
            parent,
            text="Show camera markers in Isaac viewport",
            variable=self.camera_show_markers,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1

        ttk.Checkbutton(
            parent,
            text="Show camera frustum rectangles/rays in Isaac viewport (debug only; visible in RGB if enabled)",
            variable=self.camera_show_frustums,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1

        self._add_float_row(parent, row, "Frustum distance [m]", self.camera_frustum_distance_m)
        row += 1
        self._add_float_row(parent, row, "Frustum line width [m]", self.camera_frustum_line_width_m)
        row += 1
        self._add_color_row(parent, row, "Main frustum color", self.main_frustum_color_hex, self._on_pick_main_frustum_color)
        row += 1
        self._add_color_row(parent, row, "Secondary frustum color", self.secondary_frustum_color_hex, self._on_pick_secondary_frustum_color)
        row += 1

        ttk.Label(parent, text="Orientation mode").grid(row=row, column=0, sticky="w")
        mode_box = ttk.Combobox(
            parent,
            textvariable=self.camera_orientation_mode,
            values=CAMERA_ORIENTATION_MODES,
            state="readonly",
            width=24,
        )
        mode_box.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        row += 1

        ttk.Label(
            parent,
            text=(
                "look_at_target aims each camera at the same target and is easy for detection/tracking. "
                "parallel_manual uses the explicit pitch/yaw/roll fields and is the safer starting point "
                "for stereo algorithms that expect parallel cameras."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        orientation_buttons = ttk.Frame(parent)
        orientation_buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Button(
            orientation_buttons,
            text="Set parallel yaw/pitch from look-at target",
            command=self._on_set_parallel_rotation_from_look_at,
        ).pack(side="left")

        ttk.Button(
            orientation_buttons,
            text="Undo yaw/pitch change",
            command=self._on_undo_camera_orientation_change,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            orientation_buttons,
            text="Reset stereo camera defaults",
            command=self._on_reset_stereo_camera_defaults,
        ).pack(side="left", padx=(8, 0))

        row += 1

        ttk.Label(parent, text="Main camera - blue marker", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        row += 1

        self._add_float_row(parent, row, "Main camera X [m]", self.main_camera_x)
        row += 1
        self._add_float_row(parent, row, "Main camera Y [m]", self.main_camera_y)
        row += 1
        self._add_float_row(parent, row, "Main camera Z [m]", self.main_camera_z)
        row += 1
        self._add_color_row(parent, row, "Main camera marker color", self.main_camera_color_hex, self._on_pick_main_camera_color)
        row += 1

        self._add_float_row(parent, row, "Main pitch [deg]", self.main_pitch_deg)
        row += 1
        self._add_float_row(parent, row, "Main yaw [deg]", self.main_yaw_deg)
        row += 1
        self._add_float_row(parent, row, "Main roll [deg]", self.main_roll_deg)
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        ttk.Label(
            parent,
            text="Secondary camera - orange marker - offset relative to main camera",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
        row += 1

        self._add_float_row(parent, row, "Secondary X offset [m]", self.secondary_offset_x)
        row += 1
        self._add_float_row(parent, row, "Secondary Y offset [m]", self.secondary_offset_y)
        row += 1
        self._add_float_row(parent, row, "Secondary Z offset [m]", self.secondary_offset_z)
        row += 1
        self._add_color_row(parent, row, "Secondary camera marker color", self.secondary_camera_color_hex, self._on_pick_secondary_camera_color)
        row += 1

        self._add_float_row(parent, row, "Secondary pitch offset [deg]", self.secondary_pitch_offset_deg)
        row += 1
        self._add_float_row(parent, row, "Secondary yaw offset [deg]", self.secondary_yaw_offset_deg)
        row += 1
        self._add_float_row(parent, row, "Secondary roll offset [deg]", self.secondary_roll_offset_deg)
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        ttk.Label(parent, text="Look-at target and optics", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        row += 1

        self._add_float_row(parent, row, "Look-at X [m]", self.camera_look_x)
        row += 1
        self._add_float_row(parent, row, "Look-at Y [m]", self.camera_look_y)
        row += 1
        self._add_float_row(parent, row, "Look-at Z [m]", self.camera_look_z)
        row += 1

        self._add_float_row(parent, row, "Horizontal FOV [deg]", self.camera_horizontal_fov_deg)
        row += 1
        self._add_float_row(parent, row, "Horizontal aperture [mm]", self.camera_horizontal_aperture_mm)
        row += 1
        self._add_float_row(parent, row, "Derived focal length [mm]", self.camera_focal_length)
        row += 1
        ttk.Button(
            parent,
            text="Recompute focal length from FOV",
            command=self._on_update_focal_from_fov,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1
        ttk.Label(
            parent,
            text=(
                "Edit horizontal FOV. The derived focal length is still saved because Replicator uses focal_length internally. "
                "Default USD/Replicator horizontal aperture is 20.955 mm. "
                "focal_length = horizontal_aperture / (2 * tan(horizontal_fov / 2))."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1
        self._add_int_row(parent, row, "Resolution width", self.camera_resolution_width)
        row += 1
        self._add_int_row(parent, row, "Resolution height", self.camera_resolution_height)

        parent.columnconfigure(1, weight=1)

    def _build_capture_tab(self, parent: ttk.Frame) -> None:
        parent.configure(padding=12)
        row = 0

        ttk.Checkbutton(parent, text="Enable capture", variable=self.capture_enabled).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        row += 1

        ttk.Checkbutton(parent, text="RGB images", variable=self.capture_rgb).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        row += 1

        ttk.Checkbutton(parent, text="Camera params metadata (usually off; creates one JSON per frame)", variable=self.capture_camera_params).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        row += 1

        ttk.Checkbutton(
            parent,
            text="Post-rename RGB files after capture (off recommended; use manifest instead)",
            variable=self.capture_rename_after_capture,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(parent, text="Output root folder").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.capture_output_root, width=36).grid(
            row=row,
            column=1,
            sticky="ew",
            padx=8,
            pady=4,
        )
        row += 1

        self._add_int_row(parent, row, "RT subframes", self.capture_rt_subframes)
        row += 1

        ttk.Button(
            parent,
            text="Check / Delete Output Folder",
            command=self._on_check_delete_output_folder,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 8))
        row += 1

        ttk.Label(
            parent,
            text=(
                "Output is organized as outputs/<scene>/camera_main/ and "
                "outputs/<scene>/camera_secondary/. Delete the scene folder before running "
                "if you do not want old frames mixed with new frames."
            ),
            wraplength=720,
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))

        parent.columnconfigure(1, weight=1)

    def _add_float_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.DoubleVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        spinbox = ttk.Spinbox(
            parent,
            from_=-10000.0,
            to=10000.0,
            increment=0.1,
            textvariable=variable,
            width=16,
        )
        spinbox.grid(row=row, column=1, sticky="w", padx=8, pady=4)

    def _add_int_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.IntVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        spinbox = ttk.Spinbox(
            parent,
            from_=1,
            to=1000000,
            increment=1,
            textvariable=variable,
            width=16,
        )
        spinbox.grid(row=row, column=1, sticky="w", padx=8, pady=4)

    def _add_color_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command: Any) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="w", padx=8, pady=4)

        def current_background() -> str:
            try:
                return self._normalize_hex_color(variable.get())
            except Exception:
                return "#ffffff"

        preview = tk.Label(
            frame,
            textvariable=variable,
            width=12,
            relief="solid",
            borderwidth=1,
            background=current_background(),
        )
        preview.pack(side="left")

        def update_preview(*_args: Any) -> None:
            preview.configure(background=current_background())

        variable.trace_add("write", update_preview)
        ttk.Button(frame, text="Pick", command=command).pack(side="left", padx=(6, 0))

    def _on_refresh_scene_profiles(self) -> None:
        self._refresh_scene_profile_list()
        self.status_text.set(
            "Loaded scene profiles: "
            + (", ".join(sorted(self.available_scene_profiles.keys())) if self.available_scene_profiles else "none")
        )

    def _on_scene_selected(self, _event: tk.Event | None = None) -> None:
        scene_name = self.selected_scene_name.get().strip()
        profile_path = self.available_scene_profiles.get(scene_name)
        if profile_path is None:
            return
        try:
            profile = load_json_profile(profile_path)
            validate_tethered_glider_profile(profile)
            self.current_profile_path = profile_path
            self._load_profile_into_gui(profile)
            self.status_text.set(f"Loaded scene profile: {profile_path}")
        except Exception as exc:
            messagebox.showerror("Load scene profile failed", str(exc))

    def _on_refresh_glider_asset_list(self) -> None:
        self.available_glider_asset_ids = self._load_available_glider_asset_ids()
        self.available_environment_asset_ids = self._load_available_environment_asset_ids()

        if hasattr(self, "glider_asset_combo"):
            self.glider_asset_combo.configure(
                values=self.available_glider_asset_ids,
                state="readonly" if self.available_glider_asset_ids else "normal",
            )

        current_asset_id = self.glider_asset_id.get().strip()
        if not current_asset_id and self.available_glider_asset_ids:
            self.glider_asset_id.set(self.available_glider_asset_ids[0])

        self.status_text.set(
            "Loaded glider asset IDs: "
            + (", ".join(self.available_glider_asset_ids) if self.available_glider_asset_ids else "none")
        )

    def _on_environment_mode_changed(self, _event: tk.Event | None = None) -> None:
        mode = self.environment_mode.get().strip()
        if mode == "external_usd":
            self.environment_project_lights_enabled.set(False)
            if not self.environment_asset_id.get().strip() and self.available_environment_asset_ids:
                self.environment_asset_id.set(self.available_environment_asset_ids[0])
            self.status_text.set("External USD environment selected. Project default lights were turned off.")
        elif mode == "plain_debug":
            self.environment_project_lights_enabled.set(True)
            self.status_text.set("plain_debug environment selected. Project default lights were turned on.")

    def _on_refresh_environment_asset_list(self) -> None:
        self.available_environment_asset_ids = self._load_available_environment_asset_ids()

        if hasattr(self, "environment_asset_combo"):
            self.environment_asset_combo.configure(
                values=self.available_environment_asset_ids,
                state="readonly" if self.available_environment_asset_ids else "normal",
            )

        current_asset_id = self.environment_asset_id.get().strip()
        if not current_asset_id and self.available_environment_asset_ids:
            self.environment_asset_id.set(self.available_environment_asset_ids[0])

        self.status_text.set(
            "Loaded environment asset IDs: "
            + (", ".join(self.available_environment_asset_ids) if self.available_environment_asset_ids else "none")
        )

    def _on_pick_glider_asset_color(self) -> None:
        current_hex = self.glider_asset_color_hex.get().strip() or "#ffd10d"
        selected = colorchooser.askcolor(color=current_hex, title="Pick glider material color")

        if selected is None or selected[1] is None:
            return

        self._set_glider_asset_color_hex(str(selected[1]))

    def _on_set_glider_asset_visible_yellow(self) -> None:
        self._set_glider_asset_color_hex("#ffd10d")

    def _set_glider_asset_color_hex(self, color_hex: str) -> None:
        normalized = self._normalize_hex_color(color_hex)
        self.glider_asset_color_hex.set(normalized)

        if hasattr(self, "glider_color_preview"):
            self.glider_color_preview.configure(background=normalized)

        self.status_text.set(f"Glider material color set to {normalized}")

    @staticmethod
    def _normalize_hex_color(color_hex: str) -> str:
        value = color_hex.strip()
        if not value.startswith("#"):
            value = "#" + value

        if len(value) != 7:
            raise ValueError(f"Expected color in #RRGGBB format, got: {color_hex}")

        int(value[1:3], 16)
        int(value[3:5], 16)
        int(value[5:7], 16)
        return value.lower()

    @staticmethod
    def _rgb_float_to_hex(rgb: list[float]) -> str:
        if len(rgb) != 3:
            return "#ffd10d"

        channels = []
        for value in rgb:
            clamped = max(0.0, min(1.0, float(value)))
            channels.append(int(round(clamped * 255.0)))

        return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"

    def _on_update_focal_from_fov(self) -> None:
        try:
            focal = focal_length_mm_from_horizontal_fov(
                float(self.camera_horizontal_fov_deg.get()),
                float(self.camera_horizontal_aperture_mm.get()),
            )
            self.camera_focal_length.set(float(focal))
            self.status_text.set(f"Derived focal length updated from horizontal FOV: {focal:.3f} mm")
        except Exception as exc:
            messagebox.showerror("FOV conversion failed", str(exc))

    def _on_pick_main_camera_color(self) -> None:
        color = colorchooser.askcolor(color=self.main_camera_color_hex.get(), title="Pick main camera marker color")
        if color and color[1]:
            self.main_camera_color_hex.set(self._normalize_hex_color(color[1]))

    def _on_pick_secondary_camera_color(self) -> None:
        color = colorchooser.askcolor(color=self.secondary_camera_color_hex.get(), title="Pick secondary camera marker color")
        if color and color[1]:
            self.secondary_camera_color_hex.set(self._normalize_hex_color(color[1]))

    def _on_pick_main_frustum_color(self) -> None:
        color = colorchooser.askcolor(color=self.main_frustum_color_hex.get(), title="Pick main camera frustum color")
        if color and color[1]:
            self.main_frustum_color_hex.set(self._normalize_hex_color(color[1]))

    def _on_pick_secondary_frustum_color(self) -> None:
        color = colorchooser.askcolor(color=self.secondary_frustum_color_hex.get(), title="Pick secondary camera frustum color")
        if color and color[1]:
            self.secondary_frustum_color_hex.set(self._normalize_hex_color(color[1]))

    @staticmethod
    def _hex_to_rgb_float(color_hex: str) -> list[float]:
        normalized = SceneProfileGui._normalize_hex_color(color_hex)
        return [
            int(normalized[1:3], 16) / 255.0,
            int(normalized[3:5], 16) / 255.0,
            int(normalized[5:7], 16) / 255.0,
        ]

    def _load_profile_into_gui(self, profile: dict[str, Any]) -> None:
        glider = profile["glider"]
        camera_rig = normalize_camera_rig(profile.get("camera_rig", DEFAULT_CAMERA_RIG))
        capture = dict(DEFAULT_CAPTURE)
        capture.update(profile.get("capture", {}))
        environment = normalize_environment(profile.get("environment", DEFAULT_ENVIRONMENT))
        glider_asset = dict(DEFAULT_GLIDER_ASSET)
        glider_asset.update(profile.get("glider_asset", {}))
        material_override = dict(DEFAULT_GLIDER_ASSET["material_override"])
        material_override.update(glider_asset.get("material_override", {}))
        glider_asset["material_override"] = material_override

        self.scene_name.set(str(profile["scene_name"]))
        self.selected_scene_name.set(str(profile["scene_name"]))

        anchor = profile["anchor_position"]
        self.anchor_x.set(float(anchor[0]))
        self.anchor_y.set(float(anchor[1]))
        self.anchor_z.set(float(anchor[2]))

        self.tether_length_m.set(float(profile["tether_length_m"]))
        self.glider_height_m.set(float(profile["glider_height_m"]))
        self.angular_velocity_rad_s.set(float(profile["angular_velocity_rad_s"]))

        self.num_frames.set(int(profile["num_frames"]))
        self.time_step_s.set(float(profile["time_step_s"]))

        self.environment_mode.set(str(environment.get("mode", DEFAULT_ENVIRONMENT["mode"])))
        self.environment_asset_id.set(str(environment.get("asset_id", DEFAULT_ENVIRONMENT["asset_id"])))
        environment_translation = environment.get("translation_m", DEFAULT_ENVIRONMENT["translation_m"])
        environment_rotation = environment.get("rotation_xyz_deg", DEFAULT_ENVIRONMENT["rotation_xyz_deg"])
        self.environment_translation_x.set(float(environment_translation[0]))
        self.environment_translation_y.set(float(environment_translation[1]))
        self.environment_translation_z.set(float(environment_translation[2]))
        self.environment_rotation_x_deg.set(float(environment_rotation[0]))
        self.environment_rotation_y_deg.set(float(environment_rotation[1]))
        self.environment_rotation_z_deg.set(float(environment_rotation[2]))
        self.environment_uniform_scale.set(float(environment.get("uniform_scale", DEFAULT_ENVIRONMENT["uniform_scale"])))
        self.environment_project_lights_enabled.set(bool(environment.get("project_lights_enabled", DEFAULT_ENVIRONMENT["project_lights_enabled"])))
        self.environment_fallback_to_plain_debug.set(bool(environment.get("fallback_to_plain_debug", DEFAULT_ENVIRONMENT["fallback_to_plain_debug"])))

        self.wingspan_m.set(float(glider["wingspan_m"]))
        self.length_m.set(float(glider["length_m"]))
        self.body_width_m.set(float(glider["body_width_m"]))
        self.body_height_m.set(float(glider["body_height_m"]))
        self.wing_chord_m.set(float(glider["wing_chord_m"]))
        self.wing_thickness_m.set(float(glider["wing_thickness_m"]))

        self.glider_asset_mode.set(str(glider_asset.get("mode", DEFAULT_GLIDER_ASSET["mode"])))
        self.glider_asset_id.set(str(glider_asset.get("asset_id", DEFAULT_GLIDER_ASSET["asset_id"])))
        self.glider_asset_uniform_scale.set(float(glider_asset.get("uniform_scale", DEFAULT_GLIDER_ASSET["uniform_scale"])))

        asset_rotation = glider_asset.get("rotation_xyz_deg", DEFAULT_GLIDER_ASSET["rotation_xyz_deg"])
        asset_offset = glider_asset.get("translation_offset_m", DEFAULT_GLIDER_ASSET["translation_offset_m"])

        self.glider_asset_rotation_x_deg.set(float(asset_rotation[0]))
        self.glider_asset_rotation_y_deg.set(float(asset_rotation[1]))
        self.glider_asset_rotation_z_deg.set(float(asset_rotation[2]))
        self.glider_asset_offset_x.set(float(asset_offset[0]))
        self.glider_asset_offset_y.set(float(asset_offset[1]))
        self.glider_asset_offset_z.set(float(asset_offset[2]))
        self.glider_asset_use_proxy_fallback.set(bool(glider_asset.get("use_proxy_fallback", True)))
        self.glider_asset_material_override.set(bool(material_override.get("enabled", True)))
        self._set_glider_asset_color_hex(
            self._rgb_float_to_hex(material_override.get("diffuse_color", [1.0, 0.82, 0.05]))
        )

        self.camera_enabled.set(bool(camera_rig["enabled"]))
        self.camera_show_markers.set(bool(camera_rig.get("show_markers", True)))
        self.camera_show_frustums.set(bool(camera_rig.get("show_frustums", False)))
        self.camera_frustum_distance_m.set(float(camera_rig.get("frustum_distance_m", 4.0)))
        self.camera_frustum_line_width_m.set(float(camera_rig.get("frustum_line_width_m", 0.025)))
        self.camera_orientation_mode.set(str(camera_rig["orientation_mode"]))

        main_camera = camera_rig["main_camera"]
        secondary_camera = camera_rig["secondary_camera"]

        main_position = main_camera["position"]
        main_rotation = main_camera["rotation_deg"]

        self.main_camera_x.set(float(main_position[0]))
        self.main_camera_y.set(float(main_position[1]))
        self.main_camera_z.set(float(main_position[2]))
        self.main_camera_color_hex.set(self._rgb_float_to_hex(main_camera.get("marker_color", [0.05, 0.25, 0.95])))
        self.main_frustum_color_hex.set(self._rgb_float_to_hex(main_camera.get("frustum_color", main_camera.get("marker_color", [0.05, 0.25, 0.95]))))

        self.main_pitch_deg.set(float(main_rotation[0]))
        self.main_yaw_deg.set(float(main_rotation[1]))
        self.main_roll_deg.set(float(main_rotation[2]))

        secondary_position_offset = secondary_camera["position_offset"]
        secondary_rotation_offset = secondary_camera["rotation_offset_deg"]

        self.secondary_offset_x.set(float(secondary_position_offset[0]))
        self.secondary_offset_y.set(float(secondary_position_offset[1]))
        self.secondary_offset_z.set(float(secondary_position_offset[2]))
        self.secondary_camera_color_hex.set(self._rgb_float_to_hex(secondary_camera.get("marker_color", [0.95, 0.45, 0.05])))
        self.secondary_frustum_color_hex.set(self._rgb_float_to_hex(secondary_camera.get("frustum_color", secondary_camera.get("marker_color", [0.95, 0.45, 0.05]))))

        self.secondary_pitch_offset_deg.set(float(secondary_rotation_offset[0]))
        self.secondary_yaw_offset_deg.set(float(secondary_rotation_offset[1]))
        self.secondary_roll_offset_deg.set(float(secondary_rotation_offset[2]))

        look_at = camera_rig["look_at"]
        resolution = camera_rig["resolution"]

        self.camera_look_x.set(float(look_at[0]))
        self.camera_look_y.set(float(look_at[1]))
        self.camera_look_z.set(float(look_at[2]))

        self.camera_horizontal_fov_deg.set(float(camera_rig.get("horizontal_fov_deg", 33.332)))
        self.camera_horizontal_aperture_mm.set(float(camera_rig.get("horizontal_aperture_mm", 20.955)))
        self.camera_focal_length.set(float(camera_rig["focal_length"]))

        self.camera_resolution_width.set(int(resolution[0]))
        self.camera_resolution_height.set(int(resolution[1]))

        self.capture_enabled.set(bool(capture.get("enabled", True)))
        self.capture_output_root.set(str(capture.get("output_root", "outputs")))
        self.capture_rgb.set(bool(capture.get("rgb", True)))
        self.capture_camera_params.set(bool(capture.get("camera_params", False)))
        self.capture_rename_after_capture.set(bool(capture.get("rename_after_capture", False)))
        self.capture_rt_subframes.set(int(capture.get("rt_subframes", 1)))

        self.status_text.set(f"Loaded: {self.current_profile_path}")

    def _collect_profile_from_gui(self) -> dict[str, Any]:
        profile = {
            "scene_name": self.scene_name.get().strip(),
            "anchor_position": [float(self.anchor_x.get()), float(self.anchor_y.get()), float(self.anchor_z.get())],
            "tether_length_m": float(self.tether_length_m.get()),
            "glider_height_m": float(self.glider_height_m.get()),
            "angular_velocity_rad_s": float(self.angular_velocity_rad_s.get()),
            "num_frames": int(self.num_frames.get()),
            "time_step_s": float(self.time_step_s.get()),
            "environment": {
                "mode": self.environment_mode.get().strip(),
                "asset_id": self.environment_asset_id.get().strip(),
                "translation_m": [
                    float(self.environment_translation_x.get()),
                    float(self.environment_translation_y.get()),
                    float(self.environment_translation_z.get()),
                ],
                "rotation_xyz_deg": [
                    float(self.environment_rotation_x_deg.get()),
                    float(self.environment_rotation_y_deg.get()),
                    float(self.environment_rotation_z_deg.get()),
                ],
                "uniform_scale": float(self.environment_uniform_scale.get()),
                "project_lights_enabled": bool(self.environment_project_lights_enabled.get()),
                "fallback_to_plain_debug": bool(self.environment_fallback_to_plain_debug.get()),
            },
            "glider": {
                "wingspan_m": float(self.wingspan_m.get()),
                "length_m": float(self.length_m.get()),
                "body_width_m": float(self.body_width_m.get()),
                "body_height_m": float(self.body_height_m.get()),
                "wing_chord_m": float(self.wing_chord_m.get()),
                "wing_thickness_m": float(self.wing_thickness_m.get()),
            },
            "glider_asset": {
                "mode": self.glider_asset_mode.get().strip(),
                "asset_id": self.glider_asset_id.get().strip(),
                "uniform_scale": float(self.glider_asset_uniform_scale.get()),
                "rotation_xyz_deg": [
                    float(self.glider_asset_rotation_x_deg.get()),
                    float(self.glider_asset_rotation_y_deg.get()),
                    float(self.glider_asset_rotation_z_deg.get()),
                ],
                "translation_offset_m": [
                    float(self.glider_asset_offset_x.get()),
                    float(self.glider_asset_offset_y.get()),
                    float(self.glider_asset_offset_z.get()),
                ],
                "use_proxy_fallback": bool(self.glider_asset_use_proxy_fallback.get()),
                "material_override": {
                    "enabled": bool(self.glider_asset_material_override.get()),
                    "diffuse_color": self._hex_to_rgb_float(self.glider_asset_color_hex.get()),
                    "roughness": 0.55,
                    "metallic": 0.0,
                },
            },
            "camera_rig": {
                "enabled": bool(self.camera_enabled.get()),
                "show_markers": bool(self.camera_show_markers.get()),
                "show_frustums": bool(self.camera_show_frustums.get()),
                "frustum_distance_m": float(self.camera_frustum_distance_m.get()),
                "frustum_line_width_m": float(self.camera_frustum_line_width_m.get()),
                "orientation_mode": self.camera_orientation_mode.get().strip(),
                "main_camera": {
                    "label": "main_camera_blue",
                    "marker_color": self._hex_to_rgb_float(self.main_camera_color_hex.get()),
                    "frustum_color": self._hex_to_rgb_float(self.main_frustum_color_hex.get()),
                    "position": [
                        float(self.main_camera_x.get()),
                        float(self.main_camera_y.get()),
                        float(self.main_camera_z.get()),
                    ],
                    "rotation_deg": [
                        float(self.main_pitch_deg.get()),
                        float(self.main_yaw_deg.get()),
                        float(self.main_roll_deg.get()),
                    ],
                },
                "secondary_camera": {
                    "label": "secondary_camera_orange",
                    "marker_color": self._hex_to_rgb_float(self.secondary_camera_color_hex.get()),
                    "frustum_color": self._hex_to_rgb_float(self.secondary_frustum_color_hex.get()),
                    "position_offset": [
                        float(self.secondary_offset_x.get()),
                        float(self.secondary_offset_y.get()),
                        float(self.secondary_offset_z.get()),
                    ],
                    "rotation_offset_deg": [
                        float(self.secondary_pitch_offset_deg.get()),
                        float(self.secondary_yaw_offset_deg.get()),
                        float(self.secondary_roll_offset_deg.get()),
                    ],
                },
                "look_at": [
                    float(self.camera_look_x.get()),
                    float(self.camera_look_y.get()),
                    float(self.camera_look_z.get()),
                ],
                "horizontal_fov_deg": float(self.camera_horizontal_fov_deg.get()),
                "horizontal_aperture_mm": float(self.camera_horizontal_aperture_mm.get()),
                "focal_length": focal_length_mm_from_horizontal_fov(
                    float(self.camera_horizontal_fov_deg.get()),
                    float(self.camera_horizontal_aperture_mm.get()),
                ),
                "resolution": [
                    int(self.camera_resolution_width.get()),
                    int(self.camera_resolution_height.get()),
                ],
            },
            "capture": {
                "enabled": bool(self.capture_enabled.get()),
                "output_root": self.capture_output_root.get().strip(),
                "rgb": bool(self.capture_rgb.get()),
                "camera_params": bool(self.capture_camera_params.get()),
                "rename_after_capture": bool(self.capture_rename_after_capture.get()),
                "rt_subframes": int(self.capture_rt_subframes.get()),
            },
            "future_wind": {
                "enabled": False,
                "mean_velocity_m_s": [0.0, 0.0, 0.0],
                "noise_std_m_s": 0.0,
                "note": "Reserved for later. Do not use yet.",
            },
        }

        validate_tethered_glider_profile(profile)
        return profile

    def _snapshot_camera_orientation(self) -> dict[str, Any]:
        return {
            "orientation_mode": self.camera_orientation_mode.get(),
            "main_pitch_deg": float(self.main_pitch_deg.get()),
            "main_yaw_deg": float(self.main_yaw_deg.get()),
            "main_roll_deg": float(self.main_roll_deg.get()),
            "secondary_pitch_offset_deg": float(self.secondary_pitch_offset_deg.get()),
            "secondary_yaw_offset_deg": float(self.secondary_yaw_offset_deg.get()),
            "secondary_roll_offset_deg": float(self.secondary_roll_offset_deg.get()),
        }

    def _restore_camera_orientation_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.camera_orientation_mode.set(str(snapshot["orientation_mode"]))
        self.main_pitch_deg.set(float(snapshot["main_pitch_deg"]))
        self.main_yaw_deg.set(float(snapshot["main_yaw_deg"]))
        self.main_roll_deg.set(float(snapshot["main_roll_deg"]))
        self.secondary_pitch_offset_deg.set(float(snapshot["secondary_pitch_offset_deg"]))
        self.secondary_yaw_offset_deg.set(float(snapshot["secondary_yaw_offset_deg"]))
        self.secondary_roll_offset_deg.set(float(snapshot["secondary_roll_offset_deg"]))

    def _on_set_parallel_rotation_from_look_at(self) -> None:
        try:
            main_position = [
                float(self.main_camera_x.get()),
                float(self.main_camera_y.get()),
                float(self.main_camera_z.get()),
            ]
            secondary_offset = [
                float(self.secondary_offset_x.get()),
                float(self.secondary_offset_y.get()),
                float(self.secondary_offset_z.get()),
            ]
            look_at = [
                float(self.camera_look_x.get()),
                float(self.camera_look_y.get()),
                float(self.camera_look_z.get()),
            ]

            pitch_yaw_roll = compute_parallel_rig_pitch_yaw_roll_deg(
                main_position=main_position,
                secondary_offset=secondary_offset,
                look_at=look_at,
            )

            self.last_camera_orientation_snapshot = self._snapshot_camera_orientation()

            self.camera_orientation_mode.set("parallel_manual")
            self.main_pitch_deg.set(float(pitch_yaw_roll[0]))
            self.main_yaw_deg.set(float(pitch_yaw_roll[1]))
            self.main_roll_deg.set(float(pitch_yaw_roll[2]))

            self.secondary_pitch_offset_deg.set(0.0)
            self.secondary_yaw_offset_deg.set(0.0)
            self.secondary_roll_offset_deg.set(0.0)

            self.status_text.set(
                "Set parallel camera orientation: "
                f"pitch={pitch_yaw_roll[0]:.3f} deg, "
                f"yaw={pitch_yaw_roll[1]:.3f} deg, "
                "roll=0.000 deg. Secondary rotation offsets were set to zero."
            )

        except Exception as exc:
            messagebox.showerror("Could not compute parallel orientation", str(exc))


    def _on_undo_camera_orientation_change(self) -> None:
        if self.last_camera_orientation_snapshot is None:
            messagebox.showinfo(
                "Nothing to undo",
                "No previous camera orientation change is stored for this GUI session.",
            )
            return

        self._restore_camera_orientation_snapshot(self.last_camera_orientation_snapshot)
        self.status_text.set("Restored previous camera orientation values.")
        self.last_camera_orientation_snapshot = None

    def _on_reset_stereo_camera_defaults(self) -> None:
        self.last_camera_orientation_snapshot = self._snapshot_camera_orientation()

        default_rig = normalize_camera_rig(DEFAULT_CAMERA_RIG)
        main_camera = default_rig["main_camera"]
        secondary_camera = default_rig["secondary_camera"]

        self.camera_enabled.set(bool(default_rig["enabled"]))
        self.camera_show_markers.set(bool(default_rig.get("show_markers", True)))
        self.camera_show_frustums.set(bool(default_rig.get("show_frustums", False)))
        self.camera_frustum_distance_m.set(float(default_rig.get("frustum_distance_m", 4.0)))
        self.camera_frustum_line_width_m.set(float(default_rig.get("frustum_line_width_m", 0.025)))
        self.camera_orientation_mode.set(str(default_rig["orientation_mode"]))

        self.main_camera_x.set(float(main_camera["position"][0]))
        self.main_camera_y.set(float(main_camera["position"][1]))
        self.main_camera_z.set(float(main_camera["position"][2]))
        self.main_camera_color_hex.set(self._rgb_float_to_hex(main_camera.get("marker_color", [0.05, 0.25, 0.95])))
        self.main_frustum_color_hex.set(self._rgb_float_to_hex(main_camera.get("frustum_color", main_camera.get("marker_color", [0.05, 0.25, 0.95]))))
        self.main_pitch_deg.set(float(main_camera["rotation_deg"][0]))
        self.main_yaw_deg.set(float(main_camera["rotation_deg"][1]))
        self.main_roll_deg.set(float(main_camera["rotation_deg"][2]))

        self.secondary_offset_x.set(float(secondary_camera["position_offset"][0]))
        self.secondary_offset_y.set(float(secondary_camera["position_offset"][1]))
        self.secondary_offset_z.set(float(secondary_camera["position_offset"][2]))
        self.secondary_camera_color_hex.set(self._rgb_float_to_hex(secondary_camera.get("marker_color", [0.95, 0.45, 0.05])))
        self.secondary_frustum_color_hex.set(self._rgb_float_to_hex(secondary_camera.get("frustum_color", secondary_camera.get("marker_color", [0.95, 0.45, 0.05]))))
        self.secondary_pitch_offset_deg.set(float(secondary_camera["rotation_offset_deg"][0]))
        self.secondary_yaw_offset_deg.set(float(secondary_camera["rotation_offset_deg"][1]))
        self.secondary_roll_offset_deg.set(float(secondary_camera["rotation_offset_deg"][2]))

        self.camera_look_x.set(float(default_rig["look_at"][0]))
        self.camera_look_y.set(float(default_rig["look_at"][1]))
        self.camera_look_z.set(float(default_rig["look_at"][2]))
        self.camera_horizontal_fov_deg.set(float(default_rig.get("horizontal_fov_deg", 33.332)))
        self.camera_horizontal_aperture_mm.set(float(default_rig.get("horizontal_aperture_mm", 20.955)))
        self.camera_focal_length.set(float(default_rig["focal_length"]))
        self.camera_resolution_width.set(int(default_rig["resolution"][0]))
        self.camera_resolution_height.set(int(default_rig["resolution"][1]))

        self.status_text.set("Reset stereo camera defaults. Use Undo to restore previous orientation values.")


    def _get_output_dir_from_gui(self) -> Path:
        scene_name = self.scene_name.get().strip()
        output_root_text = self.capture_output_root.get().strip()

        if not scene_name:
            raise ValueError("Scene name cannot be empty.")

        if not output_root_text:
            raise ValueError("Output root folder cannot be empty.")

        output_root = Path(output_root_text)
        if not output_root.is_absolute():
            output_root = self.project_root / output_root

        return (output_root / sanitize_filename(scene_name)).resolve()

    def _on_check_delete_output_folder(self) -> None:
        try:
            output_dir = self._get_output_dir_from_gui()

            if not output_dir.exists():
                messagebox.showinfo("Output folder check", f"Folder does not exist yet:\n{output_dir}")
                return

            if not output_dir.is_dir():
                messagebox.showerror("Output folder check", f"Path exists but is not a folder:\n{output_dir}")
                return

            delete = messagebox.askyesno(
                "Delete output folder?",
                (
                    "Output folder already exists:\n\n"
                    f"{output_dir}\n\n"
                    "Delete it now?\n\n"
                    "This permanently removes existing captured frames inside that folder."
                ),
            )

            if not delete:
                self.status_text.set(f"Output folder kept: {output_dir}")
                return

            shutil.rmtree(output_dir)
            self.status_text.set(f"Deleted output folder: {output_dir}")
            messagebox.showinfo("Deleted", f"Deleted output folder:\n{output_dir}")

        except Exception as exc:
            messagebox.showerror("Output folder check failed", str(exc))


    def _prompt_delete_output_folder_before_run(self) -> bool:
        """Return True if RUN should continue, False if cancelled."""
        output_dir = self._get_output_dir_from_gui()
        if not output_dir.exists():
            return True
        if not output_dir.is_dir():
            messagebox.showerror("Output folder problem", f"Path exists but is not a folder:\n{output_dir}")
            return False

        answer = messagebox.askyesnocancel(
            "Existing output folder",
            (
                "Output folder already exists:\n\n"
                f"{output_dir}\n\n"
                "Delete previous simulation outputs before running?\n\n"
                "Yes = delete old output folder and run.\n"
                "No = keep old files and run anyway.\n"
                "Cancel = do not run."
            ),
        )
        if answer is None:
            self.status_text.set("Run cancelled before Isaac Sim start.")
            return False
        if answer is True:
            shutil.rmtree(output_dir)
            self.status_text.set(f"Deleted old output folder before run: {output_dir}")
        else:
            self.status_text.set(f"Keeping existing output folder: {output_dir}")
        return True

    def _write_camera_layout_files_before_run(self, profile: dict[str, Any]) -> None:
        output_dir = self._get_output_dir_from_gui()
        output_dir.mkdir(parents=True, exist_ok=True)
        camera_rig = profile["camera_rig"]
        camera_defs = self._compute_camera_defs_for_diagram(camera_rig)
        payload = {
            "scene_name": profile["scene_name"],
            "anchor_position": profile["anchor_position"],
            "tether_length_m": profile["tether_length_m"],
            "camera_rig": camera_rig,
            "camera_definitions": camera_defs,
            "notes": [
                "Generated by the GUI immediately after clicking RUN.",
                "This is a top-down diagnostic, not a calibrated projection export.",
            ],
        }
        (output_dir / "camera_rig_layout.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (output_dir / "camera_rig_layout.svg").write_text(
            self._build_camera_layout_svg(profile, camera_rig, camera_defs),
            encoding="utf-8",
        )

    def _compute_camera_defs_for_diagram(self, camera_rig: dict[str, Any]) -> list[dict[str, Any]]:
        main = camera_rig["main_camera"]
        secondary = camera_rig["secondary_camera"]
        main_pos = [float(v) for v in main["position"]]
        offset = [float(v) for v in secondary["position_offset"]]
        secondary_pos = [main_pos[0] + offset[0], main_pos[1] + offset[1], main_pos[2] + offset[2]]
        main_rot = [float(v) for v in main["rotation_deg"]]
        secondary_rot_offset = [float(v) for v in secondary["rotation_offset_deg"]]
        secondary_rot = [main_rot[i] + secondary_rot_offset[i] for i in range(3)]
        return [
            {
                "name": "camera_main",
                "position": main_pos,
                "rotation_deg": main_rot,
                "marker_color": main.get("marker_color", [0.05, 0.25, 0.95]),
                "parallel_look_at": self._parallel_look_at(main_pos, main_rot),
            },
            {
                "name": "camera_secondary",
                "position": secondary_pos,
                "rotation_deg": secondary_rot,
                "marker_color": secondary.get("marker_color", [0.95, 0.45, 0.05]),
                "parallel_look_at": self._parallel_look_at(secondary_pos, secondary_rot),
            },
        ]

    @staticmethod
    def _parallel_look_at(position: list[float], pitch_yaw_roll_deg: list[float], distance_m: float = 20.0) -> list[float]:
        pitch = math.radians(float(pitch_yaw_roll_deg[0]))
        yaw = math.radians(float(pitch_yaw_roll_deg[1]))
        return [
            float(position[0]) + distance_m * math.cos(pitch) * math.cos(yaw),
            float(position[1]) + distance_m * math.cos(pitch) * math.sin(yaw),
            float(position[2]) + distance_m * math.sin(pitch),
        ]

    def _build_camera_layout_svg(self, profile: dict[str, Any], camera_rig: dict[str, Any], camera_defs: list[dict[str, Any]]) -> str:
        anchor = [float(v) for v in profile.get("anchor_position", [0.0, 0.0, 0.0])]
        look_at = [float(v) for v in camera_rig.get("look_at", [0.0, 0.0, 1.5])]
        tether = float(profile.get("tether_length_m", 0.0))
        hfov = float(camera_rig.get("horizontal_fov_deg", 33.332))
        mode = str(camera_rig.get("orientation_mode", "parallel_manual"))
        pts = [(anchor[0], anchor[1]), (look_at[0], look_at[1])]
        for cam in camera_defs:
            pts.append((float(cam["position"][0]), float(cam["position"][1])))
        if tether > 0:
            pts.extend([(anchor[0] - tether, anchor[1] - tether), (anchor[0] + tether, anchor[1] + tether)])
        min_x = min(x for x, _ in pts) - 2.0
        max_x = max(x for x, _ in pts) + 2.0
        min_y = min(y for _, y in pts) - 2.0
        max_y = max(y for _, y in pts) + 2.0
        width, height, pad = 1100, 720, 70
        def sx(x: float) -> float:
            return pad + (x - min_x) / max(max_x - min_x, 1e-6) * (width - 2 * pad)
        def sy(y: float) -> float:
            return height - (pad + (y - min_y) / max(max_y - min_y, 1e-6) * (height - 2 * pad))
        def color(values: Any) -> str:
            if isinstance(values, list) and len(values) == 3:
                return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(float(v) * 255)))) for v in values)
            return "#1f77b4"
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
        svg.append('<rect width="100%" height="100%" fill="white"/>')
        svg.append('<text x="30" y="35" font-size="22" font-family="Arial" fill="black">Stereo camera top-down layout</text>')
        svg.append('<text x="30" y="62" font-size="13" font-family="Arial" fill="#555">Generated by GUI at RUN. Units: meters in world XY.</text>')
        if tether > 0:
            rx = abs(sx(anchor[0] + tether) - sx(anchor[0]))
            ry = abs(sy(anchor[1] + tether) - sy(anchor[1]))
            svg.append(f'<ellipse cx="{sx(anchor[0]):.1f}" cy="{sy(anchor[1]):.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="none" stroke="#777" stroke-dasharray="6,6" stroke-width="2"/>')
        svg.append(f'<circle cx="{sx(anchor[0]):.1f}" cy="{sy(anchor[1]):.1f}" r="7" fill="#222"/>')
        svg.append(f'<text x="{sx(anchor[0])+10:.1f}" y="{sy(anchor[1])-10:.1f}" font-size="14" font-family="Arial">Anchor</text>')
        svg.append(f'<circle cx="{sx(look_at[0]):.1f}" cy="{sy(look_at[1]):.1f}" r="6" fill="#d62728"/>')
        svg.append(f'<text x="{sx(look_at[0])+10:.1f}" y="{sy(look_at[1])+5:.1f}" font-size="14" font-family="Arial">Look-at</text>')
        for cam in camera_defs:
            pos = [float(v) for v in cam["position"]]
            target = look_at if mode == "look_at_target" else [float(v) for v in cam["parallel_look_at"]]
            dx, dy = target[0] - pos[0], target[1] - pos[1]
            norm = math.sqrt(dx * dx + dy * dy) or 1.0
            ux, uy = dx / norm, dy / norm
            depth = min(max(norm * 0.45, 2.0), 10.0)
            spread = math.tan(math.radians(hfov) / 2.0) * depth
            cx, cy = pos[0] + ux * depth, pos[1] + uy * depth
            rx, ry = -uy, ux
            p1 = (pos[0], pos[1])
            p2 = (cx + rx * spread, cy + ry * spread)
            p3 = (cx - rx * spread, cy - ry * spread)
            c = color(cam.get("marker_color"))
            x0, y0 = sx(pos[0]), sy(pos[1])
            svg.append(f'<polygon points="{sx(p1[0]):.1f},{sy(p1[1]):.1f} {sx(p2[0]):.1f},{sy(p2[1]):.1f} {sx(p3[0]):.1f},{sy(p3[1]):.1f}" fill="{c}" fill-opacity="0.14" stroke="{c}" stroke-width="2"/>')
            svg.append(f'<rect x="{x0-8:.1f}" y="{y0-8:.1f}" width="16" height="16" transform="rotate(45 {x0:.1f} {y0:.1f})" fill="{c}" stroke="#111" stroke-width="1"/>')
            svg.append(f'<text x="{x0+12:.1f}" y="{y0-10:.1f}" font-size="14" font-family="Arial">{cam["name"]}</text>')
        svg.append('</svg>')
        return "\n".join(svg)

    def _on_load(self) -> None:
        selected_path = filedialog.askopenfilename(
            title="Load tethered glider JSON profile",
            initialdir=str(self.project_root / "profiles"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )

        if not selected_path:
            return

        try:
            profile_path = Path(selected_path).resolve()
            profile = load_json_profile(profile_path)
            validate_tethered_glider_profile(profile)

            self.current_profile_path = profile_path
            self._load_profile_into_gui(profile)
            self._write_gui_state(profile_path, profile)
            self._refresh_scene_profile_list()

        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    def _on_save(self) -> None:
        try:
            profile = self._collect_profile_from_gui()
            profile_path = default_profile_path(self.project_root, profile["scene_name"])
            save_json_profile(profile_path, profile)
            self._write_gui_state(profile_path, profile)
            self._refresh_scene_profile_list()

            self.current_profile_path = profile_path
            self.status_text.set(f"Saved: {profile_path}")
            messagebox.showinfo("Saved", f"Profile saved:\n{profile_path}")

        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def _on_run(self) -> None:
        try:
            profile = self._collect_profile_from_gui()

            if not self._prompt_delete_output_folder_before_run():
                return

            profile_path = default_profile_path(self.project_root, profile["scene_name"])
            save_json_profile(profile_path, profile)
            self._write_gui_state(profile_path, profile)
            self._refresh_scene_profile_list()
            self._write_camera_layout_files_before_run(profile)

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


def run_scene_profile_gui(project_root: Path, initial_profile_path: Path) -> dict[str, Any] | None:
    gui = SceneProfileGui(project_root=project_root, initial_profile_path=initial_profile_path)
    return gui.run()
