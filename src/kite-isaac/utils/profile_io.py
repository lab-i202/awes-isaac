# utils/profile_io.py
#
# JSON loading, saving, validation, and schema normalization for tethered glider
# scene profiles.
#
# This module must not import Isaac Sim.

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any


CAMERA_ORIENTATION_MODES = [
    "look_at_target",
    "parallel_manual",
]

GLIDER_ASSET_MODES = [
    "proxy",
    "usd_reference",
]

ENVIRONMENT_MODES = [
    "plain_debug",
    "external_usd",
]

RENDER_PROFILE_NAMES = [
    "debug_fast",
    "vision_fast",
    "vision_balanced",
    "vision_quality",
    "publication_quality",
    "custom",
]

DEFAULT_RENDER = {
    "profile": "vision_balanced",
    "headless": False,
    "hide_ui": False,
    "disable_viewport_updates": False,
    "renderer": "RealTimePathTracing",
    "width": 1280,
    "height": 720,
    "anti_aliasing": 3,
    "dlss_mode": 1,
    "samples_per_pixel_per_frame": 64,
    "max_bounces": 4,
    "denoiser": True,
    "sync_loads": True,
}

DEFAULT_TETHER_VISUAL = {
    "enabled": True,
    "radius_m": 0.015,
    "diffuse_color": [0.02, 0.02, 0.02],
    "roughness": 0.5,
    "metallic": 0.0,
}

DEFAULT_ENVIRONMENT = {
    # plain_debug keeps the original simple ground/backdrop environment.
    # external_usd references assets/environments/<asset_id>/scene.usda or the
    # path selected through metadata.json. The entire referenced environment is
    # loaded under /World/Environment, so this transform moves/rotates/scales it
    # as one block.
    "mode": "plain_debug",
    "asset_id": "flatland_trees_cloudy_01",
    "translation_m": [0.0, 0.0, 0.0],
    "rotation_xyz_deg": [0.0, 0.0, 0.0],
    "uniform_scale": 1.0,
    # For plain_debug this should stay true. For external_usd, leave false if
    # the environment already contains a Dome Light / HDRI / sun.
    "project_lights_enabled": True,
    "fallback_to_plain_debug": True,
}


DEFAULT_CAMERA_RIG = {
    "enabled": True,
    "show_markers": True,
    "show_frustums": False,
    "frustum_distance_m": 4.0,
    "frustum_line_width_m": 0.025,
    # look_at_target: Isaac/Replicator aims each camera at camera_rig.look_at.
    #   This is easy for detection/tracking but creates toe-in stereo.
    # parallel_manual: both cameras use explicit pitch/yaw/roll. This is better
    #   for stereo if both cameras share orientation and only differ by baseline.
    # Default is parallel_manual because this project will use stereo vision.
    "orientation_mode": "parallel_manual",
    "main_camera": {
        "label": "main_camera_blue",
        "marker_color": [0.05, 0.25, 0.95],
        "frustum_color": [0.05, 0.25, 0.95],
        "position": [-2.5, -12.0, 1.0],
        # GUI order: pitch, yaw, roll in degrees.
        # This default points a parallel stereo pair roughly toward the scene
        # center from a camera rig located at y=-12 m.
        "rotation_deg": [2.386, 90.0, 0.0],
    },
    "secondary_camera": {
        "label": "secondary_camera_orange",
        "marker_color": [0.95, 0.45, 0.05],
        "frustum_color": [0.95, 0.45, 0.05],
        "position_offset": [5.0, 0.0, 0.0],
        # Keep stereo cameras parallel by default.
        "rotation_offset_deg": [0.0, 0.0, 0.0],
    },
    "look_at": [0.0, 0.0, 1.5],
    "horizontal_fov_deg": 33.332,
    "horizontal_aperture_mm": 20.955,
    # focal_length is kept because Replicator uses it internally.
    # The GUI edits horizontal_fov_deg and this value is recomputed on save/load.
    "focal_length": 35.0,
    "resolution": [1280, 720],
}

DEFAULT_CAPTURE = {
    "enabled": True,
    "output_root": "outputs",
    "rgb": True,
    "rt_subframes": 1,
    "camera_params": False,
    # Keep BasicWriter filenames by default. Use capture_manifest.csv for time metadata.
    # Post-renaming can be enabled manually, but it is not recommended for datasets.
    "rename_after_capture": False,
}


DEFAULT_ANNOTATIONS = {
    # This block controls dataset ground-truth outputs.  The first target is
    # robust glider-centered annotation for single-camera detection/tracking.
    "enabled": True,
    "label_glider": True,
    "glider_class_name": "glider",
    "semantic_segmentation": True,
    "instance_segmentation": False,
    "binary_glider_mask": True,
    "bounding_box_2d_tight": True,
    "bounding_box_2d_loose": True,
    "bounding_box_3d": True,
    "distance_to_camera": False,
    "distance_to_image_plane": False,
    "debug_overlays": False,
    "raw_replicator_output": True,
}


DEFAULT_GLIDER_ASSET = {
    # proxy: use the procedural box-based glider.
    # usd_reference: reference a converted USD asset from assets/asset_registry.json.
    "mode": "usd_reference",
    "asset_id": "bixler_free3d",
    # The converted Bixler preview report measured max dimension 0.5207835137844086 m.
    # 1.5 / 0.5207835137844086 = 2.880275508531099.
    "uniform_scale": 2.880275508531099,
    # Candidate axis remap for the converted FBX: asset X appears to be wingspan,
    # asset Z appears to be fuselage length, asset Y appears to be thickness/up.
    # The scene expects: X=forward, Y=wingspan, Z=up.
    # This can still be tuned in the GUI after visual inspection.
    "rotation_xyz_deg": [90.0, 90.0, 0.0],
    "translation_offset_m": [0.0, 0.0, 0.0],
    "use_proxy_fallback": True,
    "material_override": {
        "enabled": True,
        # Deliberately not white. The Free3D Bixler import is effectively
        # untextured, so a visible debug color is more useful than white foam
        # while tuning scale and orientation.
        "diffuse_color": [1.0, 0.82, 0.05],
        "roughness": 0.55,
        "metallic": 0.0,
    },
}


# -----------------------------------------------------------------------------
# File IO
# -----------------------------------------------------------------------------


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
    validate_tethered_glider_profile(data)

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


def focal_length_mm_from_horizontal_fov(horizontal_fov_deg: float, horizontal_aperture_mm: float = 20.955) -> float:
    """Return focal length from horizontal field-of-view and horizontal aperture.

    Formula for a pinhole camera:
        focal = aperture / (2 * tan(fov / 2))
    """
    fov = float(horizontal_fov_deg)
    aperture = float(horizontal_aperture_mm)
    if not (0.1 < fov < 179.0):
        raise ValueError("horizontal_fov_deg must be between 0.1 and 179 degrees.")
    if aperture <= 0.0:
        raise ValueError("horizontal_aperture_mm must be greater than zero.")
    return aperture / (2.0 * math.tan(math.radians(fov) / 2.0))


def horizontal_fov_deg_from_focal_length(focal_length_mm: float, horizontal_aperture_mm: float = 20.955) -> float:
    """Return horizontal field-of-view from focal length and horizontal aperture."""
    focal = float(focal_length_mm)
    aperture = float(horizontal_aperture_mm)
    if focal <= 0.0:
        raise ValueError("focal_length must be greater than zero.")
    if aperture <= 0.0:
        raise ValueError("horizontal_aperture_mm must be greater than zero.")
    return math.degrees(2.0 * math.atan(aperture / (2.0 * focal)))

# -----------------------------------------------------------------------------
# Schema normalization
# -----------------------------------------------------------------------------


def normalize_profile_schema(profile: dict[str, Any]) -> None:
    profile["render"] = normalize_render(profile.get("render", {}))
    profile["tether_visual"] = normalize_tether_visual(profile.get("tether_visual", {}))
    profile["environment"] = normalize_environment(profile.get("environment", {}))
    profile["annotations"] = normalize_annotations(profile.get("annotations", {}))

    if "camera_rig" in profile and isinstance(profile["camera_rig"], dict):
        profile["camera_rig"] = normalize_camera_rig(profile["camera_rig"])

    if "capture" in profile and isinstance(profile["capture"], dict):
        profile["capture"] = normalize_capture(profile["capture"])

    profile["glider_asset"] = normalize_glider_asset(profile.get("glider_asset", {}))


def normalize_render(render: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_RENDER)
    if isinstance(render, dict):
        normalized.update(render)
    normalized["profile"] = str(normalized.get("profile", "vision_balanced"))
    normalized["headless"] = bool(normalized.get("headless", False))
    normalized["hide_ui"] = bool(normalized.get("hide_ui", False))
    normalized["disable_viewport_updates"] = bool(normalized.get("disable_viewport_updates", False))
    normalized["renderer"] = str(normalized.get("renderer", "RealTimePathTracing"))
    normalized["width"] = int(normalized.get("width", 1280))
    normalized["height"] = int(normalized.get("height", 720))
    normalized["anti_aliasing"] = int(normalized.get("anti_aliasing", 3))
    normalized["dlss_mode"] = int(normalized.get("dlss_mode", 1))
    normalized["samples_per_pixel_per_frame"] = int(normalized.get("samples_per_pixel_per_frame", 64))
    normalized["max_bounces"] = int(normalized.get("max_bounces", 4))
    normalized["denoiser"] = bool(normalized.get("denoiser", True))
    normalized["sync_loads"] = bool(normalized.get("sync_loads", True))
    return normalized


def normalize_tether_visual(tether_visual: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_TETHER_VISUAL)
    if isinstance(tether_visual, dict):
        normalized.update(tether_visual)
    normalized["enabled"] = bool(normalized.get("enabled", True))
    normalized["radius_m"] = float(normalized.get("radius_m", 0.015))
    normalized["roughness"] = float(normalized.get("roughness", 0.5))
    normalized["metallic"] = float(normalized.get("metallic", 0.0))
    if not isinstance(normalized.get("diffuse_color"), list) or len(normalized["diffuse_color"]) != 3:
        normalized["diffuse_color"] = copy.deepcopy(DEFAULT_TETHER_VISUAL["diffuse_color"])
    normalized["diffuse_color"] = [max(0.0, min(1.0, float(v))) for v in normalized["diffuse_color"]]
    return normalized


def normalize_environment(environment: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_ENVIRONMENT)

    if isinstance(environment, dict):
        normalized.update(environment)

    # Backward-friendly defaults. If the user selects an external environment,
    # assume its own lighting should be used unless explicitly overridden.
    if str(normalized.get("mode", "plain_debug")) == "external_usd" and "project_lights_enabled" not in environment:
        normalized["project_lights_enabled"] = False

    return normalized


def normalize_capture(capture: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_CAPTURE)

    # Backward compatibility: older profiles used timestamp_filenames.
    # Do not carry that value forward automatically; the safer dataset default is
    # no post-capture renaming. The new explicit key is rename_after_capture.
    legacy_capture = copy.deepcopy(capture)
    legacy_capture.pop("timestamp_filenames", None)

    normalized.update(legacy_capture)
    return normalized


def normalize_annotations(annotations: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_ANNOTATIONS)
    if isinstance(annotations, dict):
        normalized.update(annotations)

    for key in [
        "enabled",
        "label_glider",
        "semantic_segmentation",
        "instance_segmentation",
        "binary_glider_mask",
        "bounding_box_2d_tight",
        "bounding_box_2d_loose",
        "bounding_box_3d",
        "distance_to_camera",
        "distance_to_image_plane",
        "debug_overlays",
        "raw_replicator_output",
    ]:
        normalized[key] = bool(normalized.get(key, DEFAULT_ANNOTATIONS[key]))

    class_name = str(normalized.get("glider_class_name", "glider")).strip().lower()
    class_name = re.sub(r"[^a-z0-9_\-]+", "_", class_name)
    class_name = class_name.strip("_") or "glider"
    normalized["glider_class_name"] = class_name
    return normalized


def normalize_glider_asset(glider_asset: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_GLIDER_ASSET)

    if isinstance(glider_asset, dict):
        normalized.update(glider_asset)

        material_override = copy.deepcopy(DEFAULT_GLIDER_ASSET["material_override"])
        if isinstance(glider_asset.get("material_override"), dict):
            material_override.update(glider_asset["material_override"])
        normalized["material_override"] = material_override

    return normalized


def compute_parallel_rig_pitch_yaw_roll_deg(
    main_position: list[float],
    secondary_offset: list[float],
    look_at: list[float],
) -> list[float]:
    """
    Compute one visible pitch/yaw/roll value for a parallel stereo pair.

    The rotation is computed from the midpoint of the stereo baseline toward
    the look-at target, then applied to both cameras. This keeps both cameras
    parallel while roughly aiming the rig at the glider scene.
    """

    midpoint = [
        float(main_position[0]) + 0.5 * float(secondary_offset[0]),
        float(main_position[1]) + 0.5 * float(secondary_offset[1]),
        float(main_position[2]) + 0.5 * float(secondary_offset[2]),
    ]

    dx = float(look_at[0]) - midpoint[0]
    dy = float(look_at[1]) - midpoint[1]
    dz = float(look_at[2]) - midpoint[2]

    horizontal = math.sqrt(dx * dx + dy * dy)
    yaw_deg = 0.0 if horizontal < 1e-9 else math.degrees(math.atan2(dy, dx))
    pitch_deg = math.degrees(math.atan2(dz, horizontal))

    return [round(pitch_deg, 3), round(yaw_deg, 3), 0.0]


def is_zero_vector3(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(item, (int, float)) and abs(float(item)) < 1e-12 for item in value)
    )


def normalize_camera_rig(camera_rig: dict[str, Any]) -> dict[str, Any]:
    # Convert the previous boolean auto-aim field into an explicit mode.
    if "orientation_mode" not in camera_rig and "auto_aim_at_target" in camera_rig:
        camera_rig = copy.deepcopy(camera_rig)
        camera_rig["orientation_mode"] = (
            "look_at_target" if camera_rig.get("auto_aim_at_target", True) else "parallel_manual"
        )

    # New schema.
    if "main_camera" in camera_rig or "secondary_camera" in camera_rig:
        normalized = copy.deepcopy(DEFAULT_CAMERA_RIG)

        normalized["enabled"] = bool(camera_rig.get("enabled", normalized["enabled"]))
        normalized["show_markers"] = bool(camera_rig.get("show_markers", normalized["show_markers"]))
        normalized["show_frustums"] = bool(camera_rig.get("show_frustums", normalized.get("show_frustums", False)))
        normalized["frustum_distance_m"] = float(camera_rig.get("frustum_distance_m", normalized.get("frustum_distance_m", 4.0)))
        normalized["frustum_line_width_m"] = float(camera_rig.get("frustum_line_width_m", normalized.get("frustum_line_width_m", 0.025)))
        normalized["orientation_mode"] = str(
            camera_rig.get("orientation_mode", normalized["orientation_mode"])
        )

        if isinstance(camera_rig.get("main_camera"), dict):
            normalized["main_camera"].update(camera_rig["main_camera"])

        if isinstance(camera_rig.get("secondary_camera"), dict):
            normalized["secondary_camera"].update(camera_rig["secondary_camera"])

        if "look_at" in camera_rig:
            normalized["look_at"] = camera_rig["look_at"]

        if "horizontal_aperture_mm" in camera_rig:
            normalized["horizontal_aperture_mm"] = camera_rig["horizontal_aperture_mm"]

        if "horizontal_fov_deg" in camera_rig:
            normalized["horizontal_fov_deg"] = camera_rig["horizontal_fov_deg"]
            normalized["focal_length"] = focal_length_mm_from_horizontal_fov(
                float(normalized["horizontal_fov_deg"]),
                float(normalized.get("horizontal_aperture_mm", DEFAULT_CAMERA_RIG["horizontal_aperture_mm"])),
            )
        elif "focal_length" in camera_rig:
            normalized["focal_length"] = camera_rig["focal_length"]
            normalized["horizontal_fov_deg"] = horizontal_fov_deg_from_focal_length(
                float(normalized["focal_length"]),
                float(normalized.get("horizontal_aperture_mm", DEFAULT_CAMERA_RIG["horizontal_aperture_mm"])),
            )
        else:
            normalized["focal_length"] = focal_length_mm_from_horizontal_fov(
                float(normalized["horizontal_fov_deg"]),
                float(normalized.get("horizontal_aperture_mm", DEFAULT_CAMERA_RIG["horizontal_aperture_mm"])),
            )

        if "resolution" in camera_rig:
            normalized["resolution"] = camera_rig["resolution"]

        # Make old parallel_manual profiles usable: if all manual rotations are
        # still zero, initialize the main camera yaw/pitch visibly in the GUI.
        # This is not hidden auto-aim; the values are written into the profile.
        if (
            normalized["orientation_mode"] == "parallel_manual"
            and is_zero_vector3(normalized["main_camera"].get("rotation_deg"))
            and is_zero_vector3(normalized["secondary_camera"].get("rotation_offset_deg"))
        ):
            normalized["main_camera"]["rotation_deg"] = compute_parallel_rig_pitch_yaw_roll_deg(
                normalized["main_camera"]["position"],
                normalized["secondary_camera"]["position_offset"],
                normalized["look_at"],
            )
            normalized["secondary_camera"]["rotation_offset_deg"] = [0.0, 0.0, 0.0]

        return normalized

    # Legacy schema from the center/separation-axis version.
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
        normalized["show_markers"] = bool(camera_rig.get("show_markers", True))
        normalized["show_frustums"] = bool(camera_rig.get("show_frustums", False))
        normalized["frustum_distance_m"] = float(camera_rig.get("frustum_distance_m", normalized.get("frustum_distance_m", 4.0)))
        normalized["frustum_line_width_m"] = float(camera_rig.get("frustum_line_width_m", normalized.get("frustum_line_width_m", 0.025)))
        normalized["orientation_mode"] = "parallel_manual"
        normalized["main_camera"]["position"] = main_position
        normalized["secondary_camera"]["position_offset"] = secondary_offset
        normalized["main_camera"]["rotation_deg"] = compute_parallel_rig_pitch_yaw_roll_deg(
            main_position,
            secondary_offset,
            normalized["look_at"],
        )
        normalized["secondary_camera"]["rotation_offset_deg"] = [0.0, 0.0, 0.0]

        if "look_at" in camera_rig:
            normalized["look_at"] = camera_rig["look_at"]

        if "horizontal_aperture_mm" in camera_rig:
            normalized["horizontal_aperture_mm"] = camera_rig["horizontal_aperture_mm"]

        if "horizontal_fov_deg" in camera_rig:
            normalized["horizontal_fov_deg"] = camera_rig["horizontal_fov_deg"]
            normalized["focal_length"] = focal_length_mm_from_horizontal_fov(
                float(normalized["horizontal_fov_deg"]),
                float(normalized.get("horizontal_aperture_mm", DEFAULT_CAMERA_RIG["horizontal_aperture_mm"])),
            )
        elif "focal_length" in camera_rig:
            normalized["focal_length"] = camera_rig["focal_length"]
            normalized["horizontal_fov_deg"] = horizontal_fov_deg_from_focal_length(
                float(normalized["focal_length"]),
                float(normalized.get("horizontal_aperture_mm", DEFAULT_CAMERA_RIG["horizontal_aperture_mm"])),
            )
        else:
            normalized["focal_length"] = focal_length_mm_from_horizontal_fov(
                float(normalized["horizontal_fov_deg"]),
                float(normalized.get("horizontal_aperture_mm", DEFAULT_CAMERA_RIG["horizontal_aperture_mm"])),
            )

        if "resolution" in camera_rig:
            normalized["resolution"] = camera_rig["resolution"]

        return normalized

    return copy.deepcopy(DEFAULT_CAMERA_RIG)


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


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

    profile["render"] = normalize_render(profile.get("render", {}))
    validate_render(profile["render"])

    profile["tether_visual"] = normalize_tether_visual(profile.get("tether_visual", {}))
    validate_tether_visual(profile["tether_visual"])

    profile["environment"] = normalize_environment(profile.get("environment", {}))
    validate_environment(profile["environment"])

    profile["annotations"] = normalize_annotations(profile.get("annotations", {}))
    validate_annotations(profile["annotations"])

    if "camera_rig" in profile:
        profile["camera_rig"] = normalize_camera_rig(profile["camera_rig"])
        validate_camera_rig(profile["camera_rig"])

    if "capture" in profile:
        profile["capture"] = normalize_capture(profile["capture"])
        validate_capture(profile["capture"])

    profile["glider_asset"] = normalize_glider_asset(profile.get("glider_asset", {}))
    validate_glider_asset(profile["glider_asset"])


def validate_render(render: dict[str, Any]) -> None:
    if not isinstance(render, dict):
        raise ValueError("render must be a JSON object.")
    if render.get("profile") not in RENDER_PROFILE_NAMES:
        raise ValueError("render.profile must be one of: " + ", ".join(RENDER_PROFILE_NAMES))
    if int(render.get("width", 0)) <= 0 or int(render.get("height", 0)) <= 0:
        raise ValueError("render.width and render.height must be positive integers.")
    if int(render.get("anti_aliasing", 0)) < 0:
        raise ValueError("render.anti_aliasing must be non-negative.")
    if int(render.get("samples_per_pixel_per_frame", 0)) <= 0:
        raise ValueError("render.samples_per_pixel_per_frame must be positive.")
    if int(render.get("max_bounces", 0)) < 0:
        raise ValueError("render.max_bounces must be non-negative.")


def validate_tether_visual(tether_visual: dict[str, Any]) -> None:
    if not isinstance(tether_visual, dict):
        raise ValueError("tether_visual must be a JSON object.")
    if float(tether_visual.get("radius_m", 0.0)) <= 0.0:
        raise ValueError("tether_visual.radius_m must be greater than zero.")
    color = tether_visual.get("diffuse_color")
    if not isinstance(color, list) or len(color) != 3:
        raise ValueError("tether_visual.diffuse_color must be a 3-element list.")
    for value in color:
        if not (0.0 <= float(value) <= 1.0):
            raise ValueError("tether_visual.diffuse_color entries must be in [0, 1].")
    if float(tether_visual.get("roughness", 0.0)) < 0.0:
        raise ValueError("tether_visual.roughness must be non-negative.")
    if float(tether_visual.get("metallic", 0.0)) < 0.0:
        raise ValueError("tether_visual.metallic must be non-negative.")


def validate_environment(environment: dict[str, Any]) -> None:
    if not isinstance(environment, dict):
        raise ValueError("environment must be a JSON object.")

    required_keys = [
        "mode",
        "asset_id",
        "translation_m",
        "rotation_xyz_deg",
        "uniform_scale",
        "project_lights_enabled",
        "fallback_to_plain_debug",
    ]

    for key in required_keys:
        if key not in environment:
            raise ValueError(f"Missing environment key: {key}")

    if environment["mode"] not in ENVIRONMENT_MODES:
        raise ValueError("environment.mode must be one of: " + ", ".join(ENVIRONMENT_MODES))

    if not isinstance(environment["asset_id"], str):
        raise ValueError("environment.asset_id must be a string.")

    _validate_vector3(environment["translation_m"], "environment.translation_m")
    _validate_vector3(environment["rotation_xyz_deg"], "environment.rotation_xyz_deg")

    _require_number(environment, "uniform_scale")
    if float(environment["uniform_scale"]) <= 0.0:
        raise ValueError("environment.uniform_scale must be greater than zero.")

    if not isinstance(environment["project_lights_enabled"], bool):
        raise ValueError("environment.project_lights_enabled must be true or false.")

    if not isinstance(environment["fallback_to_plain_debug"], bool):
        raise ValueError("environment.fallback_to_plain_debug must be true or false.")


def validate_annotations(annotations: dict[str, Any]) -> None:
    if not isinstance(annotations, dict):
        raise ValueError("annotations must be a JSON object.")
    if not str(annotations.get("glider_class_name", "")).strip():
        raise ValueError("annotations.glider_class_name cannot be empty.")
    bool_keys = [
        "enabled",
        "label_glider",
        "semantic_segmentation",
        "instance_segmentation",
        "binary_glider_mask",
        "bounding_box_2d_tight",
        "bounding_box_2d_loose",
        "bounding_box_3d",
        "distance_to_camera",
        "distance_to_image_plane",
        "debug_overlays",
        "raw_replicator_output",
    ]
    for key in bool_keys:
        if not isinstance(annotations.get(key), bool):
            raise ValueError(f"annotations.{key} must be true or false.")


def validate_camera_rig(camera_rig: dict[str, Any]) -> None:
    if not isinstance(camera_rig, dict):
        raise ValueError("camera_rig must be a JSON object.")

    required_keys = [
        "enabled",
        "show_markers",
        "show_frustums",
        "frustum_distance_m",
        "frustum_line_width_m",
        "orientation_mode",
        "main_camera",
        "secondary_camera",
        "look_at",
        "horizontal_fov_deg",
        "horizontal_aperture_mm",
        "focal_length",
        "resolution",
    ]

    for key in required_keys:
        if key not in camera_rig:
            raise ValueError(f"Missing camera_rig key: {key}")

    if not isinstance(camera_rig["enabled"], bool):
        raise ValueError("camera_rig.enabled must be true or false.")

    if not isinstance(camera_rig["show_markers"], bool):
        raise ValueError("camera_rig.show_markers must be true or false.")

    if not isinstance(camera_rig["show_frustums"], bool):
        raise ValueError("camera_rig.show_frustums must be true or false.")

    _require_number(camera_rig, "frustum_distance_m")
    _require_number(camera_rig, "frustum_line_width_m")

    if float(camera_rig["frustum_distance_m"]) <= 0:
        raise ValueError("camera_rig.frustum_distance_m must be greater than zero.")

    if float(camera_rig["frustum_line_width_m"]) <= 0:
        raise ValueError("camera_rig.frustum_line_width_m must be greater than zero.")

    if camera_rig["orientation_mode"] not in CAMERA_ORIENTATION_MODES:
        raise ValueError(
            "camera_rig.orientation_mode must be one of: "
            + ", ".join(CAMERA_ORIENTATION_MODES)
        )

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

    if "marker_color" in main_camera:
        _validate_color3(main_camera["marker_color"], "camera_rig.main_camera.marker_color")

    if "marker_color" in secondary_camera:
        _validate_color3(secondary_camera["marker_color"], "camera_rig.secondary_camera.marker_color")

    if "frustum_color" in main_camera:
        _validate_color3(main_camera["frustum_color"], "camera_rig.main_camera.frustum_color")

    if "frustum_color" in secondary_camera:
        _validate_color3(secondary_camera["frustum_color"], "camera_rig.secondary_camera.frustum_color")

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

    _require_number(camera_rig, "horizontal_fov_deg")
    _require_number(camera_rig, "horizontal_aperture_mm")
    _require_number(camera_rig, "focal_length")

    if not (0.1 < float(camera_rig["horizontal_fov_deg"]) < 179.0):
        raise ValueError("camera_rig.horizontal_fov_deg must be between 0.1 and 179 degrees.")

    if float(camera_rig["horizontal_aperture_mm"]) <= 0:
        raise ValueError("camera_rig.horizontal_aperture_mm must be greater than zero.")

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



def _validate_color3(value: Any, name: str) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must be [r, g, b].")

    for index, item in enumerate(value):
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise ValueError(f"{name}[{index}] must be a number.")
        if float(item) < 0.0 or float(item) > 1.0:
            raise ValueError(f"{name}[{index}] must be between 0 and 1.")

def validate_glider_asset(glider_asset: dict[str, Any]) -> None:
    if not isinstance(glider_asset, dict):
        raise ValueError("glider_asset must be a JSON object.")

    required_keys = [
        "mode",
        "asset_id",
        "uniform_scale",
        "rotation_xyz_deg",
        "translation_offset_m",
        "use_proxy_fallback",
        "material_override",
    ]

    for key in required_keys:
        if key not in glider_asset:
            raise ValueError(f"Missing glider_asset key: {key}")

    if glider_asset["mode"] not in GLIDER_ASSET_MODES:
        raise ValueError(
            "glider_asset.mode must be one of: " + ", ".join(GLIDER_ASSET_MODES)
        )

    if not isinstance(glider_asset["asset_id"], str) or not glider_asset["asset_id"].strip():
        raise ValueError("glider_asset.asset_id must be a non-empty string.")

    _require_number(glider_asset, "uniform_scale")
    if float(glider_asset["uniform_scale"]) <= 0.0:
        raise ValueError("glider_asset.uniform_scale must be greater than zero.")

    _validate_vector3(glider_asset["rotation_xyz_deg"], "glider_asset.rotation_xyz_deg")
    _validate_vector3(glider_asset["translation_offset_m"], "glider_asset.translation_offset_m")

    if not isinstance(glider_asset["use_proxy_fallback"], bool):
        raise ValueError("glider_asset.use_proxy_fallback must be true or false.")

    material_override = glider_asset["material_override"]
    if not isinstance(material_override, dict):
        raise ValueError("glider_asset.material_override must be a JSON object.")

    if not isinstance(material_override.get("enabled"), bool):
        raise ValueError("glider_asset.material_override.enabled must be true or false.")

    color = material_override.get("diffuse_color")
    if not isinstance(color, list) or len(color) != 3:
        raise ValueError("glider_asset.material_override.diffuse_color must be [r, g, b].")

    for index, value in enumerate(color):
        if not _is_number(value):
            raise ValueError(
                f"glider_asset.material_override.diffuse_color[{index}] must be a number."
            )

        if float(value) < 0.0 or float(value) > 1.0:
            raise ValueError(
                f"glider_asset.material_override.diffuse_color[{index}] must be between 0 and 1."
            )

    for key in ["roughness", "metallic"]:
        _require_number(material_override, key)
        if float(material_override[key]) < 0.0 or float(material_override[key]) > 1.0:
            raise ValueError(f"glider_asset.material_override.{key} must be between 0 and 1.")


def validate_capture(capture: dict[str, Any]) -> None:
    if not isinstance(capture, dict):
        raise ValueError("capture must be a JSON object.")

    required_keys = [
        "enabled",
        "output_root",
        "rgb",
        "rt_subframes",
        "camera_params",
        "rename_after_capture",
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

    if not isinstance(capture["camera_params"], bool):
        raise ValueError("capture.camera_params must be true or false.")

    if not isinstance(capture["rename_after_capture"], bool):
        raise ValueError("capture.rename_after_capture must be true or false.")

    if not isinstance(capture["rt_subframes"], int) or isinstance(
        capture["rt_subframes"],
        bool,
    ):
        raise ValueError("capture.rt_subframes must be an integer.")

    if capture["rt_subframes"] <= 0:
        raise ValueError("capture.rt_subframes must be greater than zero.")


# -----------------------------------------------------------------------------
# Internal validators
# -----------------------------------------------------------------------------


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
