# gui/scene_profile_gui.py
#
# Scrollable GUI for editing tethered glider scene JSON profiles.
#
# This file must not import Isaac Sim.
# The GUI runs first. Isaac Sim starts only after the GUI closes.

from __future__ import annotations

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
        self.initial_profile_path = initial_profile_path
        self.current_profile_path = initial_profile_path
        self.result: dict[str, Any] | None = None
        self.available_glider_asset_ids = self._load_available_glider_asset_ids()
        self.available_environment_asset_ids = self._load_available_environment_asset_ids()

        self.root = tk.Tk()
        self.root.title("Tethered Glider Scene Profile")
        self.root.geometry("780x700")
        self.root.minsize(720, 560)

        self._build_variables()
        self._build_layout()

        profile = load_json_profile(initial_profile_path)
        validate_tethered_glider_profile(profile)
        self._load_profile_into_gui(profile)

    def run(self) -> dict[str, Any] | None:
        self.root.mainloop()
        return self.result

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
        self.camera_orientation_mode = tk.StringVar()
        self.last_camera_orientation_snapshot: dict[str, Any] | None = None

        self.main_camera_x = tk.DoubleVar()
        self.main_camera_y = tk.DoubleVar()
        self.main_camera_z = tk.DoubleVar()

        self.main_pitch_deg = tk.DoubleVar()
        self.main_yaw_deg = tk.DoubleVar()
        self.main_roll_deg = tk.DoubleVar()

        self.secondary_offset_x = tk.DoubleVar()
        self.secondary_offset_y = tk.DoubleVar()
        self.secondary_offset_z = tk.DoubleVar()

        self.secondary_pitch_offset_deg = tk.DoubleVar()
        self.secondary_yaw_offset_deg = tk.DoubleVar()
        self.secondary_roll_offset_deg = tk.DoubleVar()

        self.camera_look_x = tk.DoubleVar()
        self.camera_look_y = tk.DoubleVar()
        self.camera_look_z = tk.DoubleVar()

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

        ttk.Label(parent, text="Scene name").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.scene_name, width=36).grid(
            row=row,
            column=1,
            sticky="ew",
            padx=8,
            pady=4,
        )
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

        self._add_float_row(parent, row, "Focal length", self.camera_focal_length)
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
        self.camera_orientation_mode.set(str(camera_rig["orientation_mode"]))

        main_camera = camera_rig["main_camera"]
        secondary_camera = camera_rig["secondary_camera"]

        main_position = main_camera["position"]
        main_rotation = main_camera["rotation_deg"]

        self.main_camera_x.set(float(main_position[0]))
        self.main_camera_y.set(float(main_position[1]))
        self.main_camera_z.set(float(main_position[2]))

        self.main_pitch_deg.set(float(main_rotation[0]))
        self.main_yaw_deg.set(float(main_rotation[1]))
        self.main_roll_deg.set(float(main_rotation[2]))

        secondary_position_offset = secondary_camera["position_offset"]
        secondary_rotation_offset = secondary_camera["rotation_offset_deg"]

        self.secondary_offset_x.set(float(secondary_position_offset[0]))
        self.secondary_offset_y.set(float(secondary_position_offset[1]))
        self.secondary_offset_z.set(float(secondary_position_offset[2]))

        self.secondary_pitch_offset_deg.set(float(secondary_rotation_offset[0]))
        self.secondary_yaw_offset_deg.set(float(secondary_rotation_offset[1]))
        self.secondary_roll_offset_deg.set(float(secondary_rotation_offset[2]))

        look_at = camera_rig["look_at"]
        resolution = camera_rig["resolution"]

        self.camera_look_x.set(float(look_at[0]))
        self.camera_look_y.set(float(look_at[1]))
        self.camera_look_z.set(float(look_at[2]))

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
                "orientation_mode": self.camera_orientation_mode.get().strip(),
                "main_camera": {
                    "label": "main_camera_blue",
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
                "focal_length": float(self.camera_focal_length.get()),
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
        self.camera_orientation_mode.set(str(default_rig["orientation_mode"]))

        self.main_camera_x.set(float(main_camera["position"][0]))
        self.main_camera_y.set(float(main_camera["position"][1]))
        self.main_camera_z.set(float(main_camera["position"][2]))
        self.main_pitch_deg.set(float(main_camera["rotation_deg"][0]))
        self.main_yaw_deg.set(float(main_camera["rotation_deg"][1]))
        self.main_roll_deg.set(float(main_camera["rotation_deg"][2]))

        self.secondary_offset_x.set(float(secondary_camera["position_offset"][0]))
        self.secondary_offset_y.set(float(secondary_camera["position_offset"][1]))
        self.secondary_offset_z.set(float(secondary_camera["position_offset"][2]))
        self.secondary_pitch_offset_deg.set(float(secondary_camera["rotation_offset_deg"][0]))
        self.secondary_yaw_offset_deg.set(float(secondary_camera["rotation_offset_deg"][1]))
        self.secondary_roll_offset_deg.set(float(secondary_camera["rotation_offset_deg"][2]))

        self.camera_look_x.set(float(default_rig["look_at"][0]))
        self.camera_look_y.set(float(default_rig["look_at"][1]))
        self.camera_look_z.set(float(default_rig["look_at"][2]))
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

        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    def _on_save(self) -> None:
        try:
            profile = self._collect_profile_from_gui()
            profile_path = default_profile_path(self.project_root, profile["scene_name"])
            save_json_profile(profile_path, profile)

            self.current_profile_path = profile_path
            self.status_text.set(f"Saved: {profile_path}")
            messagebox.showinfo("Saved", f"Profile saved:\n{profile_path}")

        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def _on_run(self) -> None:
        try:
            profile = self._collect_profile_from_gui()
            profile_path = default_profile_path(self.project_root, profile["scene_name"])
            save_json_profile(profile_path, profile)

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
