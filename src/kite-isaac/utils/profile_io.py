# utils/profile_io.py
#
# JSON loading, saving, validation, and small schema normalization
# for tethered glider scene profiles.
#
# This module must not import Isaac Sim.

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any


DEFAULT_CAMERA_RIG = {
    "enabled": True,
    "auto_aim_at_target": True,
    "main_camera": {
        "position": [0.0, -12.0, 0.0],
        "rotation_deg": [0.0, 0.0, 0.0],
    },
    "secondary_camera": {
        "position_offset": [5.0, 0.0, 0.0],
        "rotation_offset_deg": [0.0, 0.0, 0.0],
    },
    "look_at": [0.0, 0.0, 1.5],
    "focal_length": 35.0,
    "resolution": [1280, 720],
}

DEFAULT_CAPTURE = {
    "enabled": True,
    "output_root": "outputs",
    "rgb": True,
    "rt_subframes": 1,
}


def load_json_profile(profile_path: Path) -> dict[str, Any]:
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile file does not exist: {profile_path}")

    if not profile_path.is_file():
        raise ValueError(f"Profile path is not a file: {profile_path}")

    try:
        with open(profile_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON profile: {profile_path}\n"
            f"JSON error: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Profile must be a JSON object, but got {type(data).__name__}: "
            f"{profile_path}"
        )

    normalize_profile_schema(data)

    return data


def save_json_profile(profile_path: Path, profile: dict[str, Any]) -> None:
    profile_path.parent.mkdir(parents=True, exist_ok=True)

    normalize_profile_schema(profile)
    validate_tethered_glider_profile(profile)

    with open(profile_path, "w", encoding="utf-8") as file:
        json.dump(profile, file, indent=2)


def default_profile_path(project_root: Path, scene_name: str) -> Path:
    safe_name = sanitize_filename(scene_name)
    return project_root / "profiles" / f"{safe_name}.json"


def sanitize_filename(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_\-]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip("_")

    if not cleaned:
        raise ValueError("Scene name cannot be empty.")

    return cleaned


def normalize_profile_schema(profile: dict[str, Any]) -> None:
    """
    Keep old JSON profiles usable.

    Step 5 changed the camera schema from:
      center_position + separation_axis + separation_m

    to:
      main_camera.position + secondary_camera.position_offset

    This function upgrades the old format in memory.
    """

    if "camera_rig" in profile and isinstance(profile["camera_rig"], dict):
        profile["camera_rig"] = normalize_camera_rig(profile["camera_rig"])

    if "capture" in profile and isinstance(profile["capture"], dict):
        profile["capture"] = normalize_capture(profile["capture"])


def normalize_capture(capture: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_CAPTURE)
    normalized.update(capture)
    return normalized


def normalize_camera_rig(camera_rig: dict[str, Any]) -> dict[str, Any]:
    # New schema already present.
    if "main_camera" in camera_rig or "secondary_camera" in camera_rig:
        normalized = copy.deepcopy(DEFAULT_CAMERA_RIG)

        normalized["enabled"] = bool(camera_rig.get("enabled", normalized["enabled"]))
        normalized["auto_aim_at_target"] = bool(
            camera_rig.get(
                "auto_aim_at_target",
                normalized["auto_aim_at_target"],
            )
        )

        if isinstance(camera_rig.get("main_camera"), dict):
            normalized["main_camera"].update(camera_rig["main_camera"])

        if isinstance(camera_rig.get("secondary_camera"), dict):
            normalized["secondary_camera"].update(camera_rig["secondary_camera"])

        if "look_at" in camera_rig:
            normalized["look_at"] = camera_rig["look_at"]

        if "focal_length" in camera_rig:
            normalized["focal_length"] = camera_rig["focal_length"]

        if "resolution" in camera_rig:
            normalized["resolution"] = camera_rig["resolution"]

        return normalized

    # Legacy Step 5 schema:
    # center_position + separation_axis + separation_m.
    if (
        "center_position" in camera_rig
        and "separation_axis" in camera_rig
        and "separation_m" in camera_rig
    ):
        center = [float(value) for value in camera_rig["center_position"]]
        axis = [float(value) for value in camera_rig["separation_axis"]]
        separation_m = float(camera_rig["separation_m"])

        axis_norm = math.sqrt(axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2)

        if axis_norm <= 0:
            axis = [1.0, 0.0, 0.0]
            axis_norm = 1.0

        unit_axis = [
            axis[0] / axis_norm,
            axis[1] / axis_norm,
            axis[2] / axis_norm,
        ]

        half = 0.5 * separation_m

        main_position = [
            center[0] - half * unit_axis[0],
            center[1] - half * unit_axis[1],
            center[2] - half * unit_axis[2],
        ]

        secondary_offset = [
            separation_m * unit_axis[0],
            separation_m * unit_axis[1],
            separation_m * unit_axis[2],
        ]

        normalized = copy.deepcopy(DEFAULT_CAMERA_RIG)
        normalized["enabled"] = bool(camera_rig.get("enabled", True))
        normalized["auto_aim_at_target"] = True
        normalized["main_camera"]["position"] = main_position
        normalized["secondary_camera"]["position_offset"] = secondary_offset

        if "look_at" in camera_rig:
            normalized["look_at"] = camera_rig["look_at"]

        if "focal_length" in camera_rig:
            normalized["focal_length"] = camera_rig["focal_length"]

        if "resolution" in camera_rig:
            normalized["resolution"] = camera_rig["resolution"]

        return normalized

    return copy.deepcopy(DEFAULT_CAMERA_RIG)


def validate_tethered_glider_profile(profile: dict[str, Any]) -> None:
    required_top_level_keys = [
        "scene_name",
        "anchor_position",
        "tether_length_m",
        "glider_height_m",
        "angular_velocity_rad_s",
        "num_frames",
        "time_step_s",
        "glider",
    ]

    for key in required_top_level_keys:
        if key not in profile:
            raise ValueError(f"Missing required profile key: {key}")

    if not isinstance(profile["scene_name"], str):
        raise ValueError("scene_name must be a string.")

    if not profile["scene_name"].strip():
        raise ValueError("scene_name cannot be empty.")

    _validate_vector3(profile["anchor_position"], "anchor_position")

    _require_number(profile, "tether_length_m")
    _require_number(profile, "glider_height_m")
    _require_number(profile, "angular_velocity_rad_s")
    _require_number(profile, "time_step_s")
    _require_positive_int(profile, "num_frames")

    if profile["tether_length_m"] <= 0:
        raise ValueError("tether_length_m must be greater than zero.")

    if profile["time_step_s"] <= 0:
        raise ValueError("time_step_s must be greater than zero.")

    glider = profile["glider"]
    if not isinstance(glider, dict):
        raise ValueError("glider must be a JSON object.")

    required_glider_keys = [
        "wingspan_m",
        "length_m",
        "body_width_m",
        "body_height_m",
        "wing_chord_m",
        "wing_thickness_m",
    ]

    for key in required_glider_keys:
        _require_number(glider, key)

        if glider[key] <= 0:
            raise ValueError(f"glider.{key} must be greater than zero.")

    if "camera_rig" in profile:
        profile["camera_rig"] = normalize_camera_rig(profile["camera_rig"])
        validate_camera_rig(profile["camera_rig"])

    if "capture" in profile:
        profile["capture"] = normalize_capture(profile["capture"])
        validate_capture(profile["capture"])


def validate_camera_rig(camera_rig: dict[str, Any]) -> None:
    if not isinstance(camera_rig, dict):
        raise ValueError("camera_rig must be a JSON object.")

    required_keys = [
        "enabled",
        "auto_aim_at_target",
        "main_camera",
        "secondary_camera",
        "look_at",
        "focal_length",
        "resolution",
    ]

    for key in required_keys:
        if key not in camera_rig:
            raise ValueError(f"Missing camera_rig key: {key}")

    if not isinstance(camera_rig["enabled"], bool):
        raise ValueError("camera_rig.enabled must be true or false.")

    if not isinstance(camera_rig["auto_aim_at_target"], bool):
        raise ValueError("camera_rig.auto_aim_at_target must be true or false.")

    main_camera = camera_rig["main_camera"]
    secondary_camera = camera_rig["secondary_camera"]

    if not isinstance(main_camera, dict):
        raise ValueError("camera_rig.main_camera must be a JSON object.")

    if not isinstance(secondary_camera, dict):
        raise ValueError("camera_rig.secondary_camera must be a JSON object.")

    for key in ["position", "rotation_deg"]:
        if key not in main_camera:
            raise ValueError(f"Missing camera_rig.main_camera key: {key}")

    for key in ["position_offset", "rotation_offset_deg"]:
        if key not in secondary_camera:
            raise ValueError(f"Missing camera_rig.secondary_camera key: {key}")

    _validate_vector3(main_camera["position"], "camera_rig.main_camera.position")
    _validate_vector3(
        main_camera["rotation_deg"],
        "camera_rig.main_camera.rotation_deg",
    )

    _validate_vector3(
        secondary_camera["position_offset"],
        "camera_rig.secondary_camera.position_offset",
    )
    _validate_vector3(
        secondary_camera["rotation_offset_deg"],
        "camera_rig.secondary_camera.rotation_offset_deg",
    )

    _validate_vector3(camera_rig["look_at"], "camera_rig.look_at")

    _require_number(camera_rig, "focal_length")

    if camera_rig["focal_length"] <= 0:
        raise ValueError("camera_rig.focal_length must be greater than zero.")

    resolution = camera_rig["resolution"]
    if not isinstance(resolution, list) or len(resolution) != 2:
        raise ValueError("camera_rig.resolution must be [width, height].")

    for index, value in enumerate(resolution):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"camera_rig.resolution[{index}] must be an integer.")

        if value <= 0:
            raise ValueError(f"camera_rig.resolution[{index}] must be greater than zero.")


def validate_capture(capture: dict[str, Any]) -> None:
    if not isinstance(capture, dict):
        raise ValueError("capture must be a JSON object.")

    required_keys = [
        "enabled",
        "output_root",
        "rgb",
        "rt_subframes",
    ]

    for key in required_keys:
        if key not in capture:
            raise ValueError(f"Missing capture key: {key}")

    if not isinstance(capture["enabled"], bool):
        raise ValueError("capture.enabled must be true or false.")

    if not isinstance(capture["output_root"], str):
        raise ValueError("capture.output_root must be a string.")

    if not capture["output_root"].strip():
        raise ValueError("capture.output_root cannot be empty.")

    if not isinstance(capture["rgb"], bool):
        raise ValueError("capture.rgb must be true or false.")

    if not isinstance(capture["rt_subframes"], int) or isinstance(
        capture["rt_subframes"],
        bool,
    ):
        raise ValueError("capture.rt_subframes must be an integer.")

    if capture["rt_subframes"] <= 0:
        raise ValueError("capture.rt_subframes must be greater than zero.")


def _validate_vector3(value: Any, name: str) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(
            f"{name} must be a list with exactly 3 numbers, "
            f"for example [0.0, 0.0, 0.0]."
        )

    for index, item in enumerate(value):
        if not _is_number(item):
            raise ValueError(f"{name}[{index}] must be a number.")


def _require_number(data: dict[str, Any], key: str) -> None:
    value = data.get(key)

    if not _is_number(value):
        raise ValueError(f"{key} must be a number.")


def _require_positive_int(data: dict[str, Any], key: str) -> None:
    value = data.get(key)

    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer.")

    if value <= 0:
        raise ValueError(f"{key} must be greater than zero.")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)