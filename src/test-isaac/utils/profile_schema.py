# utils/profile_schema.py
#
# Default profile values and conversion from GUI options to Isaac Sim profile JSON.
#
# This file must not import Isaac Sim.

from __future__ import annotations

from copy import deepcopy
from typing import Any


BASE_OPTIONS: dict[str, Any] = {
    "profile_name": "rtx_realtime_2_balanced",
    "preset_name": "RTX Real-Time 2.0 - balanced",
    "renderer": "RealTimePathTracing",
    "width": 1280,
    "height": 720,
    "resolution": [1280, 720],
    "dlss_mode_name": "Balanced",
    "dlss_exec_mode": 1,
    "rt_subframes": 8,
    "duration_frames": 90,
    "warmup_frames": 20,
    "path_spp": 16,
    "path_total_spp": 16,
    "max_bounces": 4,
    "headless": False,
    "denoiser": True,
    "rgb": True,
    "semantic_segmentation": False,
    "bounding_box_2d_tight": False,
    "gpu_telemetry": True,
    "drone_motion_radius": 1.2,
    "drone_motion_height": 1.2,
    "object_motion_amplitude": 1.5,
    "camera_position": [4.2, -5.2, 2.8],
    "camera_look_at": [0.0, 0.0, 0.85],
    "focal_length": 35.0,
}


PRESETS: dict[str, dict[str, Any]] = {
    "RTX Minimal - fastest sanity check": {
        **BASE_OPTIONS,
        "profile_name": "rtx_minimal_fast",
        "preset_name": "RTX Minimal - fastest sanity check",
        "renderer": "MinimalRendering",
        "dlss_mode_name": "Performance",
        "dlss_exec_mode": 0,
        "rt_subframes": 1,
        "width": 1280,
        "height": 720,
        "resolution": [1280, 720],
        "headless": False,
        "path_spp": 1,
        "path_total_spp": 1,
        "max_bounces": 1,
    },
    "RTX Real-Time 2.0 - performance": {
        **BASE_OPTIONS,
        "profile_name": "rtx_realtime_2_performance",
        "preset_name": "RTX Real-Time 2.0 - performance",
        "renderer": "RealTimePathTracing",
        "dlss_mode_name": "Performance",
        "dlss_exec_mode": 0,
        "rt_subframes": 4,
        "width": 1280,
        "height": 720,
        "resolution": [1280, 720],
    },
    "RTX Real-Time 2.0 - balanced": {
        **BASE_OPTIONS,
        "profile_name": "rtx_realtime_2_balanced",
        "preset_name": "RTX Real-Time 2.0 - balanced",
        "renderer": "RealTimePathTracing",
        "dlss_mode_name": "Balanced",
        "dlss_exec_mode": 1,
        "rt_subframes": 8,
        "width": 1280,
        "height": 720,
        "resolution": [1280, 720],
    },
    "RTX Real-Time 2.0 - quality": {
        **BASE_OPTIONS,
        "profile_name": "rtx_realtime_2_quality",
        "preset_name": "RTX Real-Time 2.0 - quality",
        "renderer": "RealTimePathTracing",
        "dlss_mode_name": "Quality",
        "dlss_exec_mode": 2,
        "rt_subframes": 12,
        "width": 1920,
        "height": 1080,
        "resolution": [1920, 1080],
    },
    "RTX Interactive Path Tracing - preview": {
        **BASE_OPTIONS,
        "profile_name": "pathtracing_preview",
        "preset_name": "RTX Interactive Path Tracing - preview",
        "renderer": "PathTracing",
        "dlss_mode_name": "Quality",
        "dlss_exec_mode": 2,
        "rt_subframes": 16,
        "width": 1280,
        "height": 720,
        "resolution": [1280, 720],
        "path_spp": 16,
        "path_total_spp": 16,
        "max_bounces": 4,
    },
    "RTX Interactive Path Tracing - quality": {
        **BASE_OPTIONS,
        "profile_name": "pathtracing_quality",
        "preset_name": "RTX Interactive Path Tracing - quality",
        "renderer": "PathTracing",
        "dlss_mode_name": "Quality",
        "dlss_exec_mode": 2,
        "rt_subframes": 32,
        "width": 1920,
        "height": 1080,
        "resolution": [1920, 1080],
        "path_spp": 64,
        "path_total_spp": 64,
        "max_bounces": 8,
    },
}


def get_default_options() -> dict[str, Any]:
    return deepcopy(PRESETS["RTX Real-Time 2.0 - balanced"])


def build_profile_from_options(
    options: dict[str, Any],
    score: dict[str, Any],
) -> dict[str, Any]:
    width = int(options["width"])
    height = int(options["height"])
    renderer = options["renderer"]

    launch_config: dict[str, Any] = {
        "headless": bool(options["headless"]),
        "renderer": renderer,
        "width": width,
        "height": height,
        "anti_aliasing": 3,
        "sync_loads": True,
    }

    if renderer == "MinimalRendering":
        launch_config["minimal_shading_mode"] = 2
        launch_config["anti_aliasing"] = 0

    if renderer == "PathTracing":
        launch_config["samples_per_pixel_per_frame"] = int(options["path_spp"])
        launch_config["denoiser"] = bool(options["denoiser"])
        launch_config["max_bounces"] = int(options["max_bounces"])

    carb_settings: dict[str, Any] = {
        "/rtx/rendermode": renderer,
        "/rtx/post/dlss/execMode": int(options["dlss_exec_mode"]),
    }

    if renderer == "MinimalRendering":
        carb_settings["/rtx/minimal/mode"] = 2

    if renderer == "PathTracing":
        carb_settings["/rtx/pathtracing/spp"] = int(options["path_spp"])
        carb_settings["/rtx/pathtracing/totalSpp"] = int(options["path_total_spp"])
        carb_settings["/rtx/pathtracing/maxBounces"] = int(options["max_bounces"])

    return {
        "profile_name": options["profile_name"],
        "options": deepcopy(options),
        "score": deepcopy(score),
        "launch_config": launch_config,
        "carb_settings": carb_settings,
        "scene": {
            "num_frames": int(options["duration_frames"]),
            "warmup_frames": int(options["warmup_frames"]),
            "drone_motion_radius": float(options["drone_motion_radius"]),
            "drone_motion_height": float(options["drone_motion_height"]),
            "object_motion_amplitude": float(options["object_motion_amplitude"]),
        },
        "camera": {
            "position": list(options["camera_position"]),
            "look_at": list(options["camera_look_at"]),
            "focal_length": float(options["focal_length"]),
        },
        "capture": {
            "resolution": [width, height],
            "rt_subframes": int(options["rt_subframes"]),
            "rgb": bool(options["rgb"]),
            "semantic_segmentation": bool(options["semantic_segmentation"]),
            "bounding_box_2d_tight": bool(options["bounding_box_2d_tight"]),
        },
        "diagnostics": {
            "gpu_telemetry": bool(options["gpu_telemetry"]),
            "print_every_n_frames": 10,
            "rolling_window_frames": 30,
        },
        "output": {
            "root": "render_outputs",
        },
    }
