#!/usr/bin/env python3
"""
Dual GIF Builder GUI

Purpose:
- Take two image folders.
- Output two GIF files.
- Persist all GUI fields to a JSON config file.
- Reload previous settings automatically.
- Apply shared GIF settings to both outputs.

Dependency:
    pip install pillow
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps


APP_NAME = "Dual GIF Builder"
CONFIG_FILENAME = "dual_gif_builder_config.json"

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".gif",
}

MIN_RELIABLE_GIF_DURATION_MS = 20


DEFAULT_CONFIG: dict[str, Any] = {
    "input_folder_1": "",
    "output_gif_1": "",
    "input_folder_2": "",
    "output_gif_2": "",
    "duration_ms": "20",
    "loop_count": "0",
    "frame_stride": "1",
    "recursive": False,
    "resize_to_first_frame": True,
    "natural_sort": True,
    "quality_percent": "100",
    "background_color": "#FFFFFF",
    "extensions": ".png,.jpg,.jpeg,.bmp,.webp,.tif,.tiff,.gif",
}


def get_config_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / CONFIG_FILENAME

    return Path(__file__).resolve().parent / CONFIG_FILENAME


def load_config() -> dict[str, Any]:
    config_path = get_config_path()

    if not config_path.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with config_path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)

        if not isinstance(loaded, dict):
            return DEFAULT_CONFIG.copy()

        config = DEFAULT_CONFIG.copy()
        config.update(loaded)

        # Backward compatibility with the older one-folder version.
        if not config.get("input_folder_1") and loaded.get("input_folder"):
            config["input_folder_1"] = loaded.get("input_folder", "")

        if not config.get("output_gif_1") and loaded.get("output_gif"):
            config["output_gif_1"] = loaded.get("output_gif", "")

        return config

    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any]) -> None:
    config_path = get_config_path()

    with config_path.open("w", encoding="utf-8") as file:
        json.dump(config, file, indent=2, ensure_ascii=False)


def natural_key(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def parse_extensions(raw: str) -> set[str]:
    extensions: set[str] = set()

    for item in raw.split(","):
        ext = item.strip().lower()

        if not ext:
            continue

        if not ext.startswith("."):
            ext = "." + ext

        extensions.add(ext)

    return extensions


def parse_hex_color(value: str) -> tuple[int, int, int]:
    value = value.strip()

    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("Background color must be a hex color like #FFFFFF.")

    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
    )


def clamp_quality_percent(value: int) -> int:
    return max(10, min(100, value))


def quality_percent_to_palette_colors(quality_percent: int) -> int:
    quality_percent = clamp_quality_percent(quality_percent)

    min_colors = 32
    max_colors = 256

    if quality_percent == 100:
        return max_colors

    normalized = (quality_percent - 10) / 90.0
    colors = round(min_colors + normalized * (max_colors - min_colors))

    return max(2, min(256, colors))


def resize_by_quality(img: Image.Image, quality_percent: int) -> Image.Image:
    quality_percent = clamp_quality_percent(quality_percent)

    if quality_percent >= 100:
        return img

    scale = quality_percent / 100.0
    new_width = max(1, round(img.width * scale))
    new_height = max(1, round(img.height * scale))

    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def collect_image_paths(
    input_folder: Path,
    output_gif: Path,
    extensions: set[str],
    recursive: bool,
    natural_sort_enabled: bool,
    frame_stride: int,
) -> list[Path]:
    if recursive:
        candidates = [path for path in input_folder.rglob("*") if path.is_file()]
    else:
        candidates = [path for path in input_folder.iterdir() if path.is_file()]

    image_paths: list[Path] = []
    output_resolved = output_gif.resolve() if output_gif else None

    for path in candidates:
        if path.suffix.lower() not in extensions:
            continue

        if output_resolved is not None and path.resolve() == output_resolved:
            continue

        image_paths.append(path)

    if natural_sort_enabled:
        image_paths.sort(key=natural_key)
    else:
        image_paths.sort(key=lambda path: path.name.lower())

    if frame_stride < 1:
        frame_stride = 1

    return image_paths[::frame_stride]


def load_frame_as_rgb(
    path: Path,
    target_size: tuple[int, int] | None,
    resize_to_first_frame: bool,
    quality_percent: int,
    background_rgb: tuple[int, int, int],
) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)

        has_transparency = (
            img.mode in ("RGBA", "LA")
            or (img.mode == "P" and "transparency" in img.info)
        )

        if has_transparency:
            rgba = img.convert("RGBA")
            background = Image.new("RGBA", rgba.size, background_rgb + (255,))
            background.alpha_composite(rgba)
            img = background.convert("RGB")
        else:
            img = img.convert("RGB")

        if target_size is not None and resize_to_first_frame:
            img = img.resize(target_size, Image.Resampling.LANCZOS)

        img = resize_by_quality(img, quality_percent)

        return img.copy()


def quantize_frames(
    frames: list[Image.Image],
    quality_percent: int,
) -> list[Image.Image]:
    palette_colors = quality_percent_to_palette_colors(quality_percent)

    quantized_frames: list[Image.Image] = []

    for frame in frames:
        quantized = frame.quantize(
            colors=palette_colors,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        quantized_frames.append(quantized)

    return quantized_frames


def build_gif(
    input_folder: Path,
    output_gif: Path,
    duration_ms: int,
    loop_count: int,
    frame_stride: int,
    recursive: bool,
    resize_to_first_frame: bool,
    natural_sort_enabled: bool,
    quality_percent: int,
    extensions_raw: str,
    background_color: str,
) -> tuple[int, int]:
    if not input_folder.exists() or not input_folder.is_dir():
        raise ValueError(f"Input folder does not exist or is not a folder:\n{input_folder}")

    if not str(output_gif):
        raise ValueError("Output GIF path is empty.")

    if output_gif.suffix.lower() != ".gif":
        raise ValueError(f"Output file must end with .gif:\n{output_gif}")

    if duration_ms <= 0:
        raise ValueError("Frame duration must be greater than 0 ms.")

    effective_duration_ms = max(duration_ms, MIN_RELIABLE_GIF_DURATION_MS)

    if loop_count < 0:
        raise ValueError("Loop count must be 0 or greater. Use 0 for infinite looping.")

    if frame_stride < 1:
        raise ValueError("Frame stride must be 1 or greater.")

    quality_percent = clamp_quality_percent(quality_percent)

    extensions = parse_extensions(extensions_raw)

    if not extensions:
        raise ValueError("At least one image extension is required.")

    unsupported = extensions - SUPPORTED_EXTENSIONS

    if unsupported:
        raise ValueError(
            "Unsupported extensions: "
            + ", ".join(sorted(unsupported))
            + "\nSupported extensions: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )

    background_rgb = parse_hex_color(background_color)

    image_paths = collect_image_paths(
        input_folder=input_folder,
        output_gif=output_gif,
        extensions=extensions,
        recursive=recursive,
        natural_sort_enabled=natural_sort_enabled,
        frame_stride=frame_stride,
    )

    if not image_paths:
        raise ValueError(f"No supported image files were found in:\n{input_folder}")

    output_gif.parent.mkdir(parents=True, exist_ok=True)

    rgb_frames: list[Image.Image] = []
    target_size: tuple[int, int] | None = None

    for index, path in enumerate(image_paths):
        if index == 0:
            with Image.open(path) as first_original:
                first_original = ImageOps.exif_transpose(first_original)
                target_size = first_original.size

            first_frame = load_frame_as_rgb(
                path=path,
                target_size=None,
                resize_to_first_frame=False,
                quality_percent=quality_percent,
                background_rgb=background_rgb,
            )

            rgb_frames.append(first_frame)

        else:
            frame = load_frame_as_rgb(
                path=path,
                target_size=target_size,
                resize_to_first_frame=resize_to_first_frame,
                quality_percent=quality_percent,
                background_rgb=background_rgb,
            )

            rgb_frames.append(frame)

    if not rgb_frames:
        raise ValueError("No frames were loaded.")

    quantized_frames = quantize_frames(
        frames=rgb_frames,
        quality_percent=quality_percent,
    )

    try:
        quantized_frames[0].save(
            output_gif,
            save_all=True,
            append_images=quantized_frames[1:],
            duration=effective_duration_ms,
            loop=loop_count,
            optimize=True,
        )
    finally:
        for frame in rgb_frames:
            frame.close()

        for frame in quantized_frames:
            frame.close()

    return len(image_paths), effective_duration_ms


class DualGifBuilderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title(APP_NAME)
        self.geometry("940x680")
        self.minsize(900, 640)

        self.config_data = load_config()
        self.pending_save_job: str | None = None

        self.input_folder_1_var = tk.StringVar(value=str(self.config_data["input_folder_1"]))
        self.output_gif_1_var = tk.StringVar(value=str(self.config_data["output_gif_1"]))

        self.input_folder_2_var = tk.StringVar(value=str(self.config_data["input_folder_2"]))
        self.output_gif_2_var = tk.StringVar(value=str(self.config_data["output_gif_2"]))

        self.duration_ms_var = tk.StringVar(value=str(self.config_data["duration_ms"]))
        self.loop_count_var = tk.StringVar(value=str(self.config_data["loop_count"]))
        self.frame_stride_var = tk.StringVar(value=str(self.config_data["frame_stride"]))
        self.extensions_var = tk.StringVar(value=str(self.config_data["extensions"]))
        self.background_color_var = tk.StringVar(value=str(self.config_data["background_color"]))

        self.recursive_var = tk.BooleanVar(value=bool(self.config_data["recursive"]))
        self.resize_to_first_frame_var = tk.BooleanVar(
            value=bool(self.config_data["resize_to_first_frame"])
        )
        self.natural_sort_var = tk.BooleanVar(value=bool(self.config_data["natural_sort"]))

        try:
            initial_quality = int(float(str(self.config_data["quality_percent"])))
        except ValueError:
            initial_quality = 100

        initial_quality = clamp_quality_percent(initial_quality)

        self.quality_percent_var = tk.DoubleVar(value=float(initial_quality))
        self.quality_label_var = tk.StringVar(
            value=self.format_quality_label(initial_quality)
        )

        self.duration_warning_var = tk.StringVar(value=self.format_duration_warning())
        self.status_var = tk.StringVar(value=f"Config file: {get_config_path()}")

        self._build_ui()
        self._register_config_traces()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    @staticmethod
    def format_quality_label(quality_percent: int) -> str:
        palette_colors = quality_percent_to_palette_colors(quality_percent)
        return f"{quality_percent}% quality, approx. {palette_colors} GIF colors"

    def format_duration_warning(self) -> str:
        try:
            duration = int(self.duration_ms_var.get().strip())
        except ValueError:
            return "GIF timing note: use 20 ms or higher for reliable playback."

        if duration < MIN_RELIABLE_GIF_DURATION_MS:
            return (
                f"Warning: {duration} ms is too low for reliable GIF playback. "
                f"The saved GIF will use {MIN_RELIABLE_GIF_DURATION_MS} ms."
            )

        fps = 1000.0 / duration
        return f"Approx. playback rate: {fps:.1f} FPS before viewer/browser clamping."

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        root.columnconfigure(1, weight=1)

        job_1_frame = ttk.LabelFrame(root, text="GIF Job 1", padding=12)
        job_1_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        job_1_frame.columnconfigure(1, weight=1)

        ttk.Label(job_1_frame, text="Input folder 1").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(job_1_frame, textvariable=self.input_folder_1_var).grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            job_1_frame,
            text="Browse",
            command=lambda: self.browse_input_folder(
                self.input_folder_1_var,
                self.output_gif_1_var,
                default_output_name="output_1.gif",
            ),
        ).grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=6)

        ttk.Label(job_1_frame, text="Output GIF 1").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(job_1_frame, textvariable=self.output_gif_1_var).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            job_1_frame,
            text="Browse",
            command=lambda: self.browse_output_gif(
                self.output_gif_1_var,
                default_output_name="output_1.gif",
            ),
        ).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=6)

        job_2_frame = ttk.LabelFrame(root, text="GIF Job 2", padding=12)
        job_2_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        job_2_frame.columnconfigure(1, weight=1)

        ttk.Label(job_2_frame, text="Input folder 2").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(job_2_frame, textvariable=self.input_folder_2_var).grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            job_2_frame,
            text="Browse",
            command=lambda: self.browse_input_folder(
                self.input_folder_2_var,
                self.output_gif_2_var,
                default_output_name="output_2.gif",
            ),
        ).grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=6)

        ttk.Label(job_2_frame, text="Output GIF 2").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(job_2_frame, textvariable=self.output_gif_2_var).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            job_2_frame,
            text="Browse",
            command=lambda: self.browse_output_gif(
                self.output_gif_2_var,
                default_output_name="output_2.gif",
            ),
        ).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=6)

        settings_frame = ttk.LabelFrame(root, text="Shared GIF Settings", padding=12)
        settings_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        settings_frame.columnconfigure(1, weight=1)

        ttk.Label(settings_frame, text="Frame duration, ms").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(settings_frame, textvariable=self.duration_ms_var, width=16).grid(
            row=0, column=1, sticky="w", pady=6
        )
        ttk.Label(settings_frame, textvariable=self.duration_warning_var).grid(
            row=0, column=1, sticky="w", padx=(130, 0), pady=6
        )

        ttk.Label(settings_frame, text="Loop count").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(settings_frame, textvariable=self.loop_count_var, width=16).grid(
            row=1, column=1, sticky="w", pady=6
        )
        ttk.Label(settings_frame, text="0 = loop forever").grid(
            row=1, column=1, sticky="w", padx=(130, 0), pady=6
        )

        ttk.Label(settings_frame, text="Frame stride").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(settings_frame, textvariable=self.frame_stride_var, width=16).grid(
            row=2, column=1, sticky="w", pady=6
        )
        ttk.Label(
            settings_frame,
            text="1 = every frame, 2 = every 2nd frame, 3 = every 3rd frame",
        ).grid(row=2, column=1, sticky="w", padx=(130, 0), pady=6)

        ttk.Label(settings_frame, text="Image extensions").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(settings_frame, textvariable=self.extensions_var).grid(
            row=3, column=1, sticky="ew", pady=6
        )

        ttk.Label(settings_frame, text="Transparency background").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(settings_frame, textvariable=self.background_color_var, width=16).grid(
            row=4, column=1, sticky="w", pady=6
        )
        ttk.Label(settings_frame, text="Example: #FFFFFF").grid(
            row=4, column=1, sticky="w", padx=(130, 0), pady=6
        )

        ttk.Label(settings_frame, text="Quality / size percentage").grid(
            row=5, column=0, sticky="w", padx=(0, 8), pady=6
        )

        quality_frame = ttk.Frame(settings_frame)
        quality_frame.grid(row=5, column=1, sticky="ew", pady=6)
        quality_frame.columnconfigure(0, weight=1)

        quality_scale = ttk.Scale(
            quality_frame,
            from_=10,
            to=100,
            orient="horizontal",
            variable=self.quality_percent_var,
            command=self.on_quality_slider_changed,
        )
        quality_scale.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(
            quality_frame,
            textvariable=self.quality_label_var,
            width=34,
        ).grid(row=0, column=1, sticky="e")

        ttk.Label(
            settings_frame,
            text="Lower value = smaller dimensions and fewer GIF colors. 100% = original dimensions and 256 colors.",
        ).grid(row=6, column=1, sticky="w", pady=(0, 6))

        options_frame = ttk.LabelFrame(root, text="Options", padding=12)
        options_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        options_frame.columnconfigure(0, weight=1)
        options_frame.columnconfigure(1, weight=1)
        options_frame.columnconfigure(2, weight=1)

        ttk.Checkbutton(
            options_frame,
            text="Search subfolders",
            variable=self.recursive_var,
        ).grid(row=0, column=0, sticky="w")

        ttk.Checkbutton(
            options_frame,
            text="Resize all frames to first image size",
            variable=self.resize_to_first_frame_var,
        ).grid(row=0, column=1, sticky="w")

        ttk.Checkbutton(
            options_frame,
            text="Natural sort filenames",
            variable=self.natural_sort_var,
        ).grid(row=0, column=2, sticky="w")

        button_frame = ttk.Frame(root)
        button_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)
        button_frame.columnconfigure(4, weight=1)

        ttk.Button(
            button_frame,
            text="Save Settings",
            command=self.save_current_config,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ttk.Button(
            button_frame,
            text="Build GIF 1",
            command=lambda: self.on_build_single_gif(job_index=1),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Button(
            button_frame,
            text="Build GIF 2",
            command=lambda: self.on_build_single_gif(job_index=2),
        ).grid(row=0, column=2, sticky="ew", padx=6)

        ttk.Button(
            button_frame,
            text="Build BOTH GIFs",
            command=self.on_build_both_gifs,
        ).grid(row=0, column=3, sticky="ew", padx=6)

        ttk.Button(
            button_frame,
            text="Quit",
            command=self.on_close,
        ).grid(row=0, column=4, sticky="ew", padx=(6, 0))

        ttk.Separator(root).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 8))

        ttk.Label(root, textvariable=self.status_var, wraplength=880).grid(
            row=6, column=0, columnspan=3, sticky="w"
        )

    def _register_config_traces(self) -> None:
        variables = [
            self.input_folder_1_var,
            self.output_gif_1_var,
            self.input_folder_2_var,
            self.output_gif_2_var,
            self.duration_ms_var,
            self.loop_count_var,
            self.frame_stride_var,
            self.extensions_var,
            self.background_color_var,
            self.recursive_var,
            self.resize_to_first_frame_var,
            self.natural_sort_var,
            self.quality_percent_var,
        ]

        for variable in variables:
            variable.trace_add("write", lambda *_: self.on_any_field_changed())

    def on_any_field_changed(self) -> None:
        self.duration_warning_var.set(self.format_duration_warning())
        self.quality_label_var.set(self.format_quality_label(self.get_quality_percent()))
        self.schedule_save_config()

    def on_quality_slider_changed(self, _value: str | None = None) -> None:
        quality_percent = self.get_quality_percent()
        self.quality_label_var.set(self.format_quality_label(quality_percent))
        self.schedule_save_config()

    def get_quality_percent(self) -> int:
        return clamp_quality_percent(round(float(self.quality_percent_var.get())))

    def get_frame_stride(self) -> int:
        raw = self.frame_stride_var.get().strip()
        return max(1, int(raw))

    def schedule_save_config(self) -> None:
        if self.pending_save_job is not None:
            self.after_cancel(self.pending_save_job)

        self.pending_save_job = self.after(500, self.save_current_config_silent)

    def current_config(self) -> dict[str, Any]:
        return {
            "input_folder_1": self.input_folder_1_var.get().strip(),
            "output_gif_1": self.output_gif_1_var.get().strip(),
            "input_folder_2": self.input_folder_2_var.get().strip(),
            "output_gif_2": self.output_gif_2_var.get().strip(),
            "duration_ms": self.duration_ms_var.get().strip(),
            "loop_count": self.loop_count_var.get().strip(),
            "frame_stride": self.frame_stride_var.get().strip(),
            "recursive": self.recursive_var.get(),
            "resize_to_first_frame": self.resize_to_first_frame_var.get(),
            "natural_sort": self.natural_sort_var.get(),
            "quality_percent": str(self.get_quality_percent()),
            "background_color": self.background_color_var.get().strip(),
            "extensions": self.extensions_var.get().strip(),
        }

    def save_current_config_silent(self) -> None:
        self.pending_save_job = None

        try:
            save_config(self.current_config())
        except Exception:
            pass

    def save_current_config(self) -> None:
        try:
            save_config(self.current_config())
            self.status_var.set(f"Settings saved to: {get_config_path()}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to save settings:\n\n{exc}")

    def browse_input_folder(
        self,
        input_var: tk.StringVar,
        output_var: tk.StringVar,
        default_output_name: str,
    ) -> None:
        initial_dir = input_var.get().strip()

        if not initial_dir or not Path(initial_dir).exists():
            initial_dir = str(Path.home())

        folder = filedialog.askdirectory(
            title="Select input images folder",
            initialdir=initial_dir,
            mustexist=True,
        )

        if folder:
            input_var.set(folder)

            if not output_var.get().strip():
                output_var.set(str(Path(folder) / default_output_name))

    def browse_output_gif(
        self,
        output_var: tk.StringVar,
        default_output_name: str,
    ) -> None:
        current_output = output_var.get().strip()
        initial_dir = Path(current_output).parent if current_output else Path.home()

        if not initial_dir.exists():
            initial_dir = Path.home()

        output = filedialog.asksaveasfilename(
            title="Save GIF as",
            initialdir=str(initial_dir),
            initialfile=default_output_name,
            defaultextension=".gif",
            filetypes=[("GIF files", "*.gif")],
        )

        if output:
            output_var.set(output)

    def get_job_paths(self, job_index: int) -> tuple[Path, Path]:
        if job_index == 1:
            input_folder = Path(self.input_folder_1_var.get().strip()).expanduser()
            output_gif = Path(self.output_gif_1_var.get().strip()).expanduser()
            return input_folder, output_gif

        if job_index == 2:
            input_folder = Path(self.input_folder_2_var.get().strip()).expanduser()
            output_gif = Path(self.output_gif_2_var.get().strip()).expanduser()
            return input_folder, output_gif

        raise ValueError(f"Invalid job index: {job_index}")

    def build_job(self, job_index: int) -> tuple[int, int, Path]:
        input_folder, output_gif = self.get_job_paths(job_index)

        duration_ms = int(self.duration_ms_var.get().strip())
        loop_count = int(self.loop_count_var.get().strip())
        frame_stride = self.get_frame_stride()
        quality_percent = self.get_quality_percent()

        frame_count, effective_duration_ms = build_gif(
            input_folder=input_folder,
            output_gif=output_gif,
            duration_ms=duration_ms,
            loop_count=loop_count,
            frame_stride=frame_stride,
            recursive=self.recursive_var.get(),
            resize_to_first_frame=self.resize_to_first_frame_var.get(),
            natural_sort_enabled=self.natural_sort_var.get(),
            quality_percent=quality_percent,
            extensions_raw=self.extensions_var.get(),
            background_color=self.background_color_var.get(),
        )

        return frame_count, effective_duration_ms, output_gif

    def on_build_single_gif(self, job_index: int) -> None:
        try:
            self.save_current_config_silent()

            self.status_var.set(f"Building GIF {job_index}...")
            self.update_idletasks()

            frame_count, effective_duration_ms, output_gif = self.build_job(job_index)
            approx_fps = 1000.0 / effective_duration_ms

            self.status_var.set(
                f"Done. Built GIF {job_index} with {frame_count} frames, "
                f"{effective_duration_ms} ms/frame, approx. {approx_fps:.1f} FPS: "
                f"{output_gif}"
            )

            messagebox.showinfo(
                APP_NAME,
                f"GIF {job_index} created successfully.\n\n"
                f"Frames used: {frame_count}\n"
                f"Saved duration: {effective_duration_ms} ms\n"
                f"Approx. FPS: {approx_fps:.1f}\n"
                f"Output:\n{output_gif}",
            )

        except Exception as exc:
            error_details = traceback.format_exc()
            self.status_var.set(f"Failed to build GIF {job_index}.")
            messagebox.showerror(
                APP_NAME,
                f"Failed to build GIF {job_index}:\n\n{exc}\n\nDetails:\n{error_details}",
            )

    def on_build_both_gifs(self) -> None:
        try:
            self.save_current_config_silent()

            results: list[tuple[int, int, int, Path]] = []

            for job_index in (1, 2):
                self.status_var.set(f"Building GIF {job_index} of 2...")
                self.update_idletasks()

                frame_count, effective_duration_ms, output_gif = self.build_job(job_index)
                results.append((job_index, frame_count, effective_duration_ms, output_gif))

            lines = []

            for job_index, frame_count, effective_duration_ms, output_gif in results:
                approx_fps = 1000.0 / effective_duration_ms

                lines.append(
                    f"GIF {job_index}: {frame_count} frames, "
                    f"{effective_duration_ms} ms/frame, approx. {approx_fps:.1f} FPS\n"
                    f"{output_gif}"
                )

            message = "\n\n".join(lines)

            self.status_var.set("Done. Built both GIFs.")
            messagebox.showinfo(
                APP_NAME,
                f"Both GIFs created successfully.\n\n{message}",
            )

        except Exception as exc:
            error_details = traceback.format_exc()
            self.status_var.set("Failed to build both GIFs.")
            messagebox.showerror(
                APP_NAME,
                f"Failed to build both GIFs:\n\n{exc}\n\nDetails:\n{error_details}",
            )

    def on_close(self) -> None:
        try:
            if self.pending_save_job is not None:
                self.after_cancel(self.pending_save_job)
                self.pending_save_job = None

            save_config(self.current_config())
        except Exception:
            pass

        self.destroy()


def main() -> None:
    app = DualGifBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()