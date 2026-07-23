# scenario/tethered_glider_scene.py
#
# Kinematic tethered-glider scene with optional two-camera RGB capture.
#
# This is intentionally not aerodynamic.
# This is intentionally not a cable-physics simulation.
#
# Purpose:
#   Make an aircraft-like visual target move around a central anchor while
#   exactly respecting a tether-length constraint in the XY plane.

from __future__ import annotations

import csv
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import carb
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

from utils.asset_io import (
    get_asset_converted_usd_path,
    get_asset_entry,
    get_environment_asset_entry,
    get_environment_scene_path,
    load_asset_registry,
)
from utils.profile_io import normalize_camera_rig, normalize_environment, sanitize_filename


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def run_tethered_glider_scene(
    simulation_app: Any,
    scene_config: dict[str, Any],
) -> None:
    stage = create_empty_stage()

    try:
        simulation_app.reset_render_settings()
    except Exception as exc:
        print(f"[warning] reset_render_settings failed: {exc}")

    configure_renderer()

    environment_cfg = normalize_environment(scene_config.get("environment", {}))
    scene_config["environment"] = environment_cfg

    if bool(environment_cfg.get("project_lights_enabled", True)):
        create_lights(stage)
    else:
        print("Project default lights disabled by environment.project_lights_enabled=false.")

    create_environment(stage, scene_config)
    create_anchor(stage, scene_config)
    create_tether_curve(stage)
    create_glider_visual(stage, scene_config)

    capture_cfg = scene_config.get("capture", {})
    camera_rig_cfg = normalize_camera_rig(scene_config.get("camera_rig", {}))

    capture_enabled = bool(capture_cfg.get("enabled", False))
    camera_enabled = bool(camera_rig_cfg.get("enabled", False))

    camera_defs = compute_two_camera_definitions(camera_rig_cfg)

    if camera_enabled:
        create_camera_markers(stage, camera_rig_cfg, camera_defs)

    rep = None
    render_products: list[Any] = []
    writers: list[Any] = []
    output_dir: Path | None = None

    if capture_enabled and camera_enabled:
        import omni.replicator.core as rep_module

        rep = rep_module
        render_products, writers, output_dir = create_two_camera_capture(
            rep=rep,
            scene_config=scene_config,
            camera_rig_cfg=camera_rig_cfg,
            camera_defs=camera_defs,
        )

    num_frames = int(scene_config["num_frames"])
    time_step_s = float(scene_config["time_step_s"])

    print("=" * 100)
    print("Tethered glider prototype")
    print("=" * 100)
    print(f"Anchor position: {scene_config['anchor_position']}")
    print(f"Tether length: {scene_config['tether_length_m']} m")
    print(f"Glider height: {scene_config['glider_height_m']} m")
    print(f"Angular velocity: {scene_config['angular_velocity_rad_s']} rad/s")
    print(f"Frames: {num_frames}")
    print(f"Time step: {time_step_s} s")
    print(f"Camera rig enabled: {camera_enabled}")
    print(f"Capture enabled: {capture_enabled}")
    print(f"Environment mode: {environment_cfg.get('mode', 'plain_debug')}")
    print(f"Environment asset id: {environment_cfg.get('asset_id', '')}")
    print(f"Environment translation m: {environment_cfg.get('translation_m', [0.0, 0.0, 0.0])}")
    print(f"Environment rotation XYZ deg: {environment_cfg.get('rotation_xyz_deg', [0.0, 0.0, 0.0])}")
    print(f"Environment uniform scale: {environment_cfg.get('uniform_scale', 1.0)}")

    if camera_enabled:
        print(f"Camera orientation mode: {camera_rig_cfg['orientation_mode']}")
        print(f"Show camera markers in viewport: {camera_rig_cfg.get('show_markers', True)}")
        print(f"Camera look_at target: {camera_rig_cfg['look_at']}")
        print(f"Main camera marker: BLUE")
        print(f"Secondary camera marker: ORANGE")
        print(f"Main camera position: {camera_defs[0]['position']}")
        print(f"Secondary camera position: {camera_defs[1]['position']}")
        print(f"Main camera pitch/yaw/roll deg: {camera_defs[0]['rotation_pyr_deg']}")
        print(f"Secondary camera pitch/yaw/roll deg: {camera_defs[1]['rotation_pyr_deg']}")
        print(f"Main camera computed look-at yaw deg: {camera_defs[0]['look_at_yaw_deg']:.3f}")
        print(f"Secondary camera computed look-at yaw deg: {camera_defs[1]['look_at_yaw_deg']:.3f}")
        print(f"Main camera computed look-at pitch deg: {camera_defs[0]['look_at_pitch_deg']:.3f}")
        print(f"Secondary camera computed look-at pitch deg: {camera_defs[1]['look_at_pitch_deg']:.3f}")

    if capture_enabled and camera_enabled:
        print(f"Capture output root: {capture_cfg['output_root']}")
        print(f"RT subframes: {capture_cfg['rt_subframes']}")
        print(f"Post-rename after capture: {capture_cfg.get('rename_after_capture', False)}")
    print("=" * 100)

    live_state_writer: LiveFrameStateCsvWriter | None = None
    live_last_frame_updater: LiveLastFrameImageUpdater | None = None

    if capture_enabled and camera_enabled and output_dir is not None:
        live_state_writer = LiveFrameStateCsvWriter(output_dir=output_dir)
        live_last_frame_updater = LiveLastFrameImageUpdater(output_dir=output_dir)

    try:
        # Keep camera marker geometry visible when the profile requests it.
        # Earlier patches hid /World/CameraMarkers during capture to avoid RGB
        # contamination. That made the camera models disappear from the viewport,
        # which is unacceptable while tuning a camera rig.
        #
        # Dataset contamination should be handled later with dedicated non-rendered
        # debug overlays or by disabling markers manually, not by secretly hiding
        # user-requested viewport geometry.
        if camera_enabled:
            print(
                "Camera markers visibility is controlled only by "
                "camera_rig.show_markers. No automatic hiding during capture."
            )

        if live_state_writer is not None:
            print(
                "Live frame_state.csv streaming is enabled. The dashboard can read "
                "telemetry while Isaac Sim is still capturing."
            )

        for frame_index in range(num_frames):
            sim_time_s = frame_index * time_step_s

            update_tethered_glider_motion(
                stage=stage,
                scene_config=scene_config,
                sim_time_s=sim_time_s,
            )

            simulation_app.update()

            if capture_enabled and camera_enabled and rep is not None:
                rep.orchestrator.step(
                    rt_subframes=int(capture_cfg.get("rt_subframes", 1)),
                    pause_timeline=True,
                    delta_time=0.0,
                    wait_for_render=True,
                )

            if live_state_writer is not None:
                live_state_writer.write_row(
                    compute_glider_state_row(
                        scene_config=scene_config,
                        frame_index=frame_index,
                        sim_time_s=sim_time_s,
                        manifest_map={},
                    )
                )

            if live_last_frame_updater is not None:
                live_last_frame_updater.update()

            if frame_index % 60 == 0:
                glider_position = compute_glider_position(scene_config, sim_time_s)
                constraint_error = compute_tether_constraint_error(
                    scene_config=scene_config,
                    glider_position=glider_position,
                )

                print(
                    f"[frame {frame_index:04d}] "
                    f"t={sim_time_s:.2f}s "
                    f"glider_pos=({glider_position[0]:.3f}, "
                    f"{glider_position[1]:.3f}, "
                    f"{glider_position[2]:.3f}) "
                    f"constraint_error={constraint_error:.6f} m",
                    flush=True,
                )

        if capture_enabled and camera_enabled and rep is not None:
            rep.orchestrator.wait_until_complete()

            if output_dir is not None:
                write_capture_metadata(
                    output_dir=output_dir,
                    scene_config=scene_config,
                    camera_rig_cfg=camera_rig_cfg,
                    camera_defs=camera_defs,
                )

                if bool(capture_cfg.get("rename_after_capture", False)):
                    timestamp_saved_rgb_images(
                        output_dir=output_dir,
                        num_frames=num_frames,
                        time_step_s=time_step_s,
                    )
                else:
                    write_basicwriter_capture_manifest(
                        output_dir=output_dir,
                        num_frames=num_frames,
                        time_step_s=time_step_s,
                    )

                # Close the live writer before finalizing frame_state.csv.
                # On Windows, rewriting a CSV while it is still open for live
                # streaming can fail or produce stale content.
                if live_state_writer is not None:
                    live_state_writer.close()
                    live_state_writer = None

                write_frame_state_csv(
                    output_dir=output_dir,
                    scene_config=scene_config,
                    num_frames=num_frames,
                    time_step_s=time_step_s,
                )
                write_last_frame_images(output_dir=output_dir)

    finally:
        if live_state_writer is not None:
            live_state_writer.close()

        cleanup_capture(render_products=render_products, writers=writers)

    print("Simulation finished.")


# -----------------------------------------------------------------------------
# Stage and renderer
# -----------------------------------------------------------------------------


def create_empty_stage() -> Usd.Stage:
    context = omni.usd.get_context()
    context.new_stage()

    stage = context.get_stage()

    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")

    return stage


def configure_renderer() -> None:
    settings = carb.settings.get_settings()
    settings.set("/rtx/rendermode", "RealTimePathTracing")
    settings.set("/rtx/post/dlss/execMode", 1)


# -----------------------------------------------------------------------------
# Capture
# -----------------------------------------------------------------------------


def create_two_camera_capture(
    rep: Any,
    scene_config: dict[str, Any],
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
) -> tuple[list[Any], list[Any], Path]:
    capture_cfg = scene_config["capture"]

    output_root = PROJECT_ROOT / capture_cfg.get("output_root", "outputs")
    output_root.mkdir(parents=True, exist_ok=True)

    scene_dir_name = sanitize_filename(str(scene_config["scene_name"]))
    output_dir = output_root / scene_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = carb.settings.get_settings()
    settings.set("/omni/replicator/backends/disk/root_dir", str(output_root.resolve()))

    focal_length = float(camera_rig_cfg["focal_length"])
    resolution = tuple(int(value) for value in camera_rig_cfg["resolution"])
    orientation_mode = str(camera_rig_cfg["orientation_mode"])
    look_at = tuple(float(value) for value in camera_rig_cfg["look_at"])

    cameras = []

    for camera_def in camera_defs:
        if orientation_mode == "look_at_target":
            camera = rep.create.camera(
                position=tuple(camera_def["position"]),
                look_at=look_at,
                focal_length=focal_length,
            )
        elif orientation_mode == "parallel_manual":
            # Do not pass Euler rotations directly here. Isaac/Replicator camera
            # axis conventions are easy to get wrong. Instead, use the visible
            # pitch/yaw values from the GUI to build a forward look direction,
            # then call Replicator's look_at using a point along that direction.
            # This keeps both stereo cameras parallel when their rotation offsets
            # are equal, while avoiding hidden yaw/pitch.
            camera = rep.create.camera(
                position=tuple(camera_def["position"]),
                look_at=tuple(camera_def["parallel_look_at"]),
                focal_length=focal_length,
            )
        else:
            raise ValueError(f"Unsupported camera orientation mode: {orientation_mode}")

        cameras.append(camera)

    render_product_main = rep.create.render_product(
        cameras[0],
        resolution,
        name="camera_main_render_product",
    )

    render_product_secondary = rep.create.render_product(
        cameras[1],
        resolution,
        name="camera_secondary_render_product",
    )

    rep.orchestrator.set_capture_on_play(False)

    writer_main = rep.WriterRegistry.get("BasicWriter")
    writer_main.initialize(
        output_dir=f"{scene_dir_name}/camera_main",
        rgb=bool(capture_cfg.get("rgb", True)),
        camera_params=bool(capture_cfg.get("camera_params", False)),
    )
    writer_main.attach([render_product_main])

    writer_secondary = rep.WriterRegistry.get("BasicWriter")
    writer_secondary.initialize(
        output_dir=f"{scene_dir_name}/camera_secondary",
        rgb=bool(capture_cfg.get("rgb", True)),
        camera_params=bool(capture_cfg.get("camera_params", False)),
    )
    writer_secondary.attach([render_product_secondary])

    print("=" * 100)
    print("Two-camera RGB capture")
    print("=" * 100)
    print("camera_main: BLUE marker")
    print("camera_secondary: ORANGE marker")
    print(f"camera_main position: {camera_defs[0]['position']}")
    print(f"camera_secondary position: {camera_defs[1]['position']}")
    print(f"orientation mode: {orientation_mode}")
    print(f"look_at target: {look_at}")
    print(f"resolution: {resolution}")
    print(f"output directory: {output_dir.resolve()}")
    print(f"main camera output: {(output_dir / 'camera_main').resolve()}")
    print(f"secondary camera output: {(output_dir / 'camera_secondary').resolve()}")
    print("=" * 100)

    return [render_product_main, render_product_secondary], [writer_main, writer_secondary], output_dir


def cleanup_capture(render_products: list[Any], writers: list[Any]) -> None:
    for writer in writers:
        try:
            writer.detach()
        except Exception as exc:
            print(f"[warning] writer.detach failed: {exc}")

    for render_product in render_products:
        try:
            render_product.destroy()
        except Exception as exc:
            print(f"[warning] render_product.destroy failed: {exc}")


def write_capture_metadata(
    output_dir: Path,
    scene_config: dict[str, Any],
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
) -> None:
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scene_name": scene_config["scene_name"],
        "num_frames": int(scene_config["num_frames"]),
        "time_step_s": float(scene_config["time_step_s"]),
        "camera_rig": camera_rig_cfg,
        "camera_definitions": camera_defs,
        "environment": scene_config.get("environment", {}),
        "notes": [
            "camera_main uses the blue marker and outputs to camera_main/.",
            "camera_secondary uses the orange marker and outputs to camera_secondary/.",
            "look_at_target is toe-in stereo. parallel_manual is better for basic rectified stereo assumptions.",
            "Camera debug markers are controlled only by camera_rig.show_markers; they are not automatically hidden during capture.",
        ],
    }

    with open(output_dir / "camera_rig_metadata.json", "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)


def load_capture_manifest_map(output_dir: Path) -> dict[str, dict[int, dict[str, str]]]:
    """Return manifest records by camera name and frame index.

    The manifest is the source of truth for the actual image filenames. It works
    both when BasicWriter names are kept and when post-capture timestamp renaming
    is enabled.
    """

    manifest_path = output_dir / "capture_manifest.csv"
    records: dict[str, dict[int, dict[str, str]]] = {
        "camera_main": {},
        "camera_secondary": {},
    }

    if not manifest_path.exists():
        return records

    with open(manifest_path, "r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            camera_name = str(row.get("camera_name", "")).strip()
            if camera_name not in records:
                continue

            try:
                frame_index = int(row.get("frame_index", ""))
            except ValueError:
                continue

            relative_path = str(row.get("relative_path", "")).strip()
            saved_filename = str(row.get("saved_filename", "")).strip()
            new_filename = str(row.get("new_filename", "")).strip()
            original_filename = str(row.get("original_filename", "")).strip()

            if not saved_filename:
                saved_filename = new_filename or original_filename or Path(relative_path).name

            records[camera_name][frame_index] = {
                "relative_path": relative_path,
                "filename": saved_filename,
            }

    return records


def default_rgb_relative_path(camera_name: str, frame_index: int) -> str:
    return f"{camera_name}/rgb_{frame_index:04d}.png"


def compute_glider_state_row(
    scene_config: dict[str, Any],
    frame_index: int,
    sim_time_s: float,
    manifest_map: dict[str, dict[int, dict[str, str]]],
) -> dict[str, Any]:
    anchor = [float(value) for value in scene_config["anchor_position"]]
    tether_length = float(scene_config["tether_length_m"])
    glider_height = float(scene_config["glider_height_m"])
    angular_velocity = float(scene_config["angular_velocity_rad_s"])
    theta = angular_velocity * sim_time_s

    glider_position = compute_glider_position(scene_config, sim_time_s)

    tangent_x = -math.sin(theta)
    tangent_y = math.cos(theta)
    yaw_deg = math.degrees(math.atan2(tangent_y, tangent_x))

    velocity_x = -tether_length * angular_velocity * math.sin(theta)
    velocity_y = tether_length * angular_velocity * math.cos(theta)
    velocity_z = 0.0
    linear_speed = math.sqrt(velocity_x * velocity_x + velocity_y * velocity_y + velocity_z * velocity_z)

    tether_vector_x = glider_position[0] - anchor[0]
    tether_vector_y = glider_position[1] - anchor[1]
    tether_vector_z = glider_position[2] - anchor[2]

    actual_horizontal_tether_length = math.sqrt(
        tether_vector_x * tether_vector_x + tether_vector_y * tether_vector_y
    )
    actual_3d_anchor_to_glider_length = math.sqrt(
        tether_vector_x * tether_vector_x
        + tether_vector_y * tether_vector_y
        + tether_vector_z * tether_vector_z
    )
    tether_constraint_error = abs(actual_horizontal_tether_length - tether_length)

    glider_asset_cfg = scene_config.get("glider_asset", {})
    glider_visual_mode = str(glider_asset_cfg.get("mode", "proxy"))
    glider_asset_id = str(glider_asset_cfg.get("asset_id", ""))

    environment_cfg = normalize_environment(scene_config.get("environment", {}))
    environment_mode = str(environment_cfg.get("mode", "plain_debug"))
    environment_asset_id = str(environment_cfg.get("asset_id", ""))
    environment_translation = [float(value) for value in environment_cfg.get("translation_m", [0.0, 0.0, 0.0])]
    environment_rotation = [float(value) for value in environment_cfg.get("rotation_xyz_deg", [0.0, 0.0, 0.0])]
    environment_uniform_scale = float(environment_cfg.get("uniform_scale", 1.0))
    environment_scene_path = ""
    if environment_mode == "external_usd" and environment_asset_id:
        try:
            environment_scene_path = str(get_environment_scene_path(PROJECT_ROOT, environment_asset_id))
        except Exception:
            environment_scene_path = "UNRESOLVED"

    main_record = manifest_map.get("camera_main", {}).get(frame_index, {})
    secondary_record = manifest_map.get("camera_secondary", {}).get(frame_index, {})

    camera_main_rgb = main_record.get(
        "relative_path",
        default_rgb_relative_path("camera_main", frame_index),
    )
    camera_secondary_rgb = secondary_record.get(
        "relative_path",
        default_rgb_relative_path("camera_secondary", frame_index),
    )

    return {
        "frame_index": frame_index,
        "sim_timestamp_s": f"{sim_time_s:.9f}",
        "glider_x_m": f"{glider_position[0]:.9f}",
        "glider_y_m": f"{glider_position[1]:.9f}",
        "glider_z_m": f"{glider_position[2]:.9f}",
        "glider_roll_deg": f"{0.0:.9f}",
        "glider_pitch_deg": f"{0.0:.9f}",
        "glider_yaw_deg": f"{yaw_deg:.9f}",
        "glider_vx_m_s": f"{velocity_x:.9f}",
        "glider_vy_m_s": f"{velocity_y:.9f}",
        "glider_vz_m_s": f"{velocity_z:.9f}",
        "linear_speed_m_s": f"{linear_speed:.9f}",
        "anchor_x_m": f"{anchor[0]:.9f}",
        "anchor_y_m": f"{anchor[1]:.9f}",
        "anchor_z_m": f"{anchor[2]:.9f}",
        "commanded_horizontal_tether_length_m": f"{tether_length:.9f}",
        "glider_height_command_m": f"{glider_height:.9f}",
        "actual_horizontal_tether_length_m": f"{actual_horizontal_tether_length:.9f}",
        "actual_3d_anchor_to_glider_length_m": f"{actual_3d_anchor_to_glider_length:.9f}",
        "tether_constraint_error_m": f"{tether_constraint_error:.12f}",
        "tether_vector_x_m": f"{tether_vector_x:.9f}",
        "tether_vector_y_m": f"{tether_vector_y:.9f}",
        "tether_vector_z_m": f"{tether_vector_z:.9f}",
        "angular_velocity_rad_s": f"{angular_velocity:.9f}",
        "theta_rad": f"{theta:.9f}",
        "theta_deg": f"{math.degrees(theta):.9f}",
        "camera_main_rgb": camera_main_rgb,
        "camera_secondary_rgb": camera_secondary_rgb,
        "glider_visual_mode": glider_visual_mode,
        "glider_asset_id": glider_asset_id,
        "motion_model": "kinematic_circle",
        "environment_mode": environment_mode,
        "environment_asset_id": environment_asset_id,
        "environment_scene_path": environment_scene_path,
        "environment_translation_x_m": f"{environment_translation[0]:.9f}",
        "environment_translation_y_m": f"{environment_translation[1]:.9f}",
        "environment_translation_z_m": f"{environment_translation[2]:.9f}",
        "environment_rotation_x_deg": f"{environment_rotation[0]:.9f}",
        "environment_rotation_y_deg": f"{environment_rotation[1]:.9f}",
        "environment_rotation_z_deg": f"{environment_rotation[2]:.9f}",
        "environment_uniform_scale": f"{environment_uniform_scale:.9f}",
    }



class LiveFrameStateCsvWriter:
    """Append frame_state.csv rows during capture.

    The previous v12 implementation wrote frame_state.csv only after the whole
    Isaac run finished. That made the Streamlit dashboard useless for telemetry
    during long captures. This writer opens frame_state.csv at the start of the
    run, writes one row per timestep, and flushes immediately so a separate
    dashboard process can read partial telemetry.

    At the end of the run, write_frame_state_csv() still rewrites the complete
    final CSV from the manifest. That final rewrite is deliberate: it preserves
    correct filenames even when post-capture renaming is enabled.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.path = output_dir / "frame_state.csv"
        self.file = None
        self.writer: csv.DictWriter | None = None
        self.fieldnames: list[str] | None = None
        self.rows_written = 0

    def write_row(self, row: dict[str, Any]) -> None:
        if self.writer is None:
            self.fieldnames = list(row.keys())
            self.file = open(self.path, "w", newline="", encoding="utf-8")
            self.writer = csv.DictWriter(self.file, fieldnames=self.fieldnames)
            self.writer.writeheader()

        self.writer.writerow(row)
        self.rows_written += 1

        # Flush every row. This is intentionally more important than raw speed:
        # the dashboard is a live monitor, not only an after-run report viewer.
        if self.file is not None:
            self.file.flush()

    def close(self) -> None:
        if self.file is not None:
            try:
                self.file.flush()
            finally:
                self.file.close()
                self.file = None

        if self.rows_written > 0:
            print(f"Live frame_state.csv rows streamed: {self.rows_written} -> {self.path}")


class LiveLastFrameImageUpdater:
    """Update camera_main/last_frame.png and camera_secondary/last_frame.png while capturing.

    The copy is atomic from the dashboard point of view: we copy to a temporary
    path and then replace last_frame.png. If the RGB file is still being written
    by Replicator, the copy may fail; that is non-fatal and the next frame will
    try again.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.last_copied_source_by_camera: dict[str, str] = {}

    def update(self) -> None:
        for camera_folder in ["camera_main", "camera_secondary"]:
            camera_dir = self.output_dir / camera_folder
            if not camera_dir.exists() or not camera_dir.is_dir():
                continue

            image_files = list_rgb_files_sorted(camera_dir)
            if not image_files:
                continue

            source_path = image_files[-1]
            if self.last_copied_source_by_camera.get(camera_folder) == source_path.name:
                continue

            destination_path = camera_dir / "last_frame.png"
            temp_path = camera_dir / ".last_frame.tmp.png"

            try:
                if source_path.stat().st_size <= 0:
                    continue

                shutil.copy2(source_path, temp_path)
                temp_path.replace(destination_path)
                self.last_copied_source_by_camera[camera_folder] = source_path.name
            except OSError:
                # Do not spam the console during live capture. The final
                # write_last_frame_images() call after capture remains the
                # authoritative last-frame copy.
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass


def write_frame_state_csv(
    output_dir: Path,
    scene_config: dict[str, Any],
    num_frames: int,
    time_step_s: float,
) -> None:
    manifest_map = load_capture_manifest_map(output_dir)

    rows = [
        compute_glider_state_row(
            scene_config=scene_config,
            frame_index=frame_index,
            sim_time_s=frame_index * time_step_s,
            manifest_map=manifest_map,
        )
        for frame_index in range(num_frames)
    ]

    if not rows:
        print("[warning] no frame state rows were generated.")
        return

    state_path = output_dir / "frame_state.csv"
    with open(state_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Frame state CSV written: {state_path}")


def list_rgb_files_sorted(camera_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in camera_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and path.name.lower().startswith("rgb_")
        ],
        key=lambda path: path.name.lower(),
    )


def write_last_frame_images(output_dir: Path) -> None:
    """Copy each camera's latest RGB image to last_frame.png.

    This is deliberately a copy, not a rename. It is a user-convenience preview
    file and does not affect capture_manifest.csv or frame_state.csv.
    """

    for camera_folder in ["camera_main", "camera_secondary"]:
        camera_dir = output_dir / camera_folder
        if not camera_dir.exists() or not camera_dir.is_dir():
            print(f"[warning] cannot write last_frame.png; missing camera folder: {camera_dir}")
            continue

        image_files = list_rgb_files_sorted(camera_dir)
        if not image_files:
            print(f"[warning] cannot write last_frame.png; no RGB files found in: {camera_dir}")
            continue

        source_path = image_files[-1]
        destination_path = camera_dir / "last_frame.png"
        shutil.copy2(source_path, destination_path)
        print(f"Last frame copied: {source_path.name} -> {destination_path}")


def write_basicwriter_capture_manifest(
    output_dir: Path,
    num_frames: int,
    time_step_s: float,
) -> None:
    """Write frame/time metadata without renaming BasicWriter image files."""

    camera_folders = [
        ("camera_main", "camera_main"),
        ("camera_secondary", "camera_secondary"),
    ]

    manifest_rows: list[dict[str, Any]] = []

    for folder_name, camera_name in camera_folders:
        camera_dir = output_dir / folder_name

        if not camera_dir.exists():
            print(f"[warning] camera output folder does not exist: {camera_dir}")
            continue

        image_files = [
            path
            for path in sorted(camera_dir.iterdir())
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and path.name.lower().startswith("rgb_")
        ]

        if not image_files:
            print(f"[warning] no BasicWriter RGB files found in: {camera_dir}")
            continue

        if len(image_files) != num_frames:
            print(
                "[warning] RGB image count does not match num_frames for "
                f"{camera_name}: files={len(image_files)}, num_frames={num_frames}"
            )

        for frame_index, image_path in enumerate(image_files):
            sim_timestamp_s = frame_index * time_step_s
            manifest_rows.append(
                {
                    "camera_name": camera_name,
                    "frame_index": frame_index,
                    "sim_timestamp_s": f"{sim_timestamp_s:.9f}",
                    "saved_filename": image_path.name,
                    "relative_path": str(image_path.relative_to(output_dir)).replace("\\", "/"),
                    "renamed_after_capture": "false",
                }
            )

    manifest_path = output_dir / "capture_manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "camera_name",
            "frame_index",
            "sim_timestamp_s",
            "saved_filename",
            "relative_path",
            "renamed_after_capture",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Capture manifest written without renaming images: {manifest_path}")


def timestamp_saved_rgb_images(
    output_dir: Path,
    num_frames: int,
    time_step_s: float,
) -> None:
    camera_folders = [
        ("camera_main", "camera_main"),
        ("camera_secondary", "camera_secondary"),
    ]

    manifest_rows: list[dict[str, Any]] = []

    for folder_name, camera_name in camera_folders:
        camera_dir = output_dir / folder_name

        if not camera_dir.exists():
            print(f"[warning] camera output folder does not exist: {camera_dir}")
            continue

        image_files = [
            path
            for path in sorted(camera_dir.iterdir())
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and path.name.lower().startswith("rgb_")
        ]

        if not image_files:
            print(f"[warning] no RGB image files found in: {camera_dir}")
            continue

        if len(image_files) != num_frames:
            print(
                "[warning] RGB image count does not match num_frames for "
                f"{camera_name}: files={len(image_files)}, num_frames={num_frames}"
            )

        temp_pairs: list[tuple[Path, Path, Path, int, float]] = []

        for frame_index, old_path in enumerate(image_files):
            sim_timestamp_s = frame_index * time_step_s
            timestamp_token = timestamp_token_from_seconds(sim_timestamp_s)
            new_name = (
                f"{camera_name}_frame_{frame_index:06d}_"
                f"t_{timestamp_token}s_rgb{old_path.suffix.lower()}"
            )
            new_path = camera_dir / new_name
            temp_path = camera_dir / f".__tmp__{old_path.name}"

            if new_path.exists() and new_path.resolve() != old_path.resolve():
                new_path.unlink()

            old_path.rename(temp_path)
            temp_pairs.append((temp_path, old_path, new_path, frame_index, sim_timestamp_s))

        for temp_path, old_path, new_path, frame_index, sim_timestamp_s in temp_pairs:
            temp_path.rename(new_path)
            manifest_rows.append(
                {
                    "camera_name": camera_name,
                    "frame_index": frame_index,
                    "sim_timestamp_s": f"{sim_timestamp_s:.9f}",
                    "original_filename": old_path.name,
                    "new_filename": new_path.name,
                    "relative_path": str(new_path.relative_to(output_dir)).replace("\\", "/"),
                    "renamed_after_capture": "true",
                }
            )

        print(f"Timestamped {len(temp_pairs)} RGB files in {camera_dir}")

    manifest_path = output_dir / "capture_manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "camera_name",
            "frame_index",
            "sim_timestamp_s",
            "original_filename",
            "new_filename",
            "relative_path",
            "renamed_after_capture",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Capture manifest written: {manifest_path}")


def timestamp_token_from_seconds(value_s: float) -> str:
    # Filename-safe fixed-point seconds. Example: 1.500000 -> 1p500000
    return f"{value_s:.6f}".replace(".", "p")


# -----------------------------------------------------------------------------
# Camera definitions and markers
# -----------------------------------------------------------------------------


def compute_two_camera_definitions(camera_rig_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    main_camera = camera_rig_cfg["main_camera"]
    secondary_camera = camera_rig_cfg["secondary_camera"]
    look_at = [float(value) for value in camera_rig_cfg["look_at"]]

    main_position = [float(value) for value in main_camera["position"]]
    main_rotation_pyr = [float(value) for value in main_camera["rotation_deg"]]

    secondary_position_offset = [float(value) for value in secondary_camera["position_offset"]]
    secondary_rotation_offset_pyr = [float(value) for value in secondary_camera["rotation_offset_deg"]]

    secondary_position = [
        main_position[0] + secondary_position_offset[0],
        main_position[1] + secondary_position_offset[1],
        main_position[2] + secondary_position_offset[2],
    ]

    secondary_rotation_pyr = [
        main_rotation_pyr[0] + secondary_rotation_offset_pyr[0],
        main_rotation_pyr[1] + secondary_rotation_offset_pyr[1],
        main_rotation_pyr[2] + secondary_rotation_offset_pyr[2],
    ]

    main_aim = compute_yaw_pitch_to_target_deg(main_position, look_at)
    secondary_aim = compute_yaw_pitch_to_target_deg(secondary_position, look_at)

    return [
        {
            "name": "camera_main",
            "marker_color": "blue",
            "output_folder": "camera_main",
            "position": main_position,
            "rotation_pyr_deg": main_rotation_pyr,
            "rotation_xyz_deg": pitch_yaw_roll_to_usd_xyz_rotation(main_rotation_pyr),
            "parallel_look_at": compute_parallel_manual_look_at_point(main_position, main_rotation_pyr),
            "look_at_yaw_deg": main_aim[0],
            "look_at_pitch_deg": main_aim[1],
        },
        {
            "name": "camera_secondary",
            "marker_color": "orange",
            "output_folder": "camera_secondary",
            "position": secondary_position,
            "rotation_pyr_deg": secondary_rotation_pyr,
            "rotation_xyz_deg": pitch_yaw_roll_to_usd_xyz_rotation(secondary_rotation_pyr),
            "parallel_look_at": compute_parallel_manual_look_at_point(secondary_position, secondary_rotation_pyr),
            "look_at_yaw_deg": secondary_aim[0],
            "look_at_pitch_deg": secondary_aim[1],
        },
    ]


def pitch_yaw_roll_to_usd_xyz_rotation(pitch_yaw_roll_deg: list[float]) -> tuple[float, float, float]:
    # GUI order is pitch, yaw, roll.
    # USD RotateXYZ order is X, Y, Z.
    # Simple convention used here:
    #   roll  -> X axis
    #   pitch -> Y axis
    #   yaw   -> Z axis
    pitch = float(pitch_yaw_roll_deg[0])
    yaw = float(pitch_yaw_roll_deg[1])
    roll = float(pitch_yaw_roll_deg[2])
    return roll, pitch, yaw


def compute_yaw_pitch_to_target_deg(position: list[float], target: list[float]) -> tuple[float, float]:
    dx = float(target[0]) - float(position[0])
    dy = float(target[1]) - float(position[1])
    dz = float(target[2]) - float(position[2])

    horizontal = math.sqrt(dx * dx + dy * dy)

    yaw_deg = 0.0 if horizontal < 1e-9 else math.degrees(math.atan2(dy, dx))
    pitch_deg = math.degrees(math.atan2(dz, horizontal))

    return yaw_deg, pitch_deg


def compute_parallel_manual_look_at_point(
    position: list[float],
    pitch_yaw_roll_deg: list[float],
    distance_m: float = 20.0,
) -> list[float]:
    """
    Convert visible GUI pitch/yaw into a point that Replicator can look at.

    We intentionally use Replicator's look_at call instead of direct Euler
    rotation for RGB capture. Direct camera Euler angles are convention-sensitive
    and caused captures that were visibly not aligned with the viewport markers.

    Roll is not represented by a look_at point. Keep roll at zero for the current
    stereo/detection prototype.
    """

    pitch_deg = float(pitch_yaw_roll_deg[0])
    yaw_deg = float(pitch_yaw_roll_deg[1])

    pitch_rad = math.radians(pitch_deg)
    yaw_rad = math.radians(yaw_deg)

    direction_x = math.cos(pitch_rad) * math.cos(yaw_rad)
    direction_y = math.cos(pitch_rad) * math.sin(yaw_rad)
    direction_z = math.sin(pitch_rad)

    return [
        float(position[0]) + distance_m * direction_x,
        float(position[1]) + distance_m * direction_y,
        float(position[2]) + distance_m * direction_z,
    ]


def create_camera_markers(
    stage: Usd.Stage,
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
) -> None:
    look_at = [float(value) for value in camera_rig_cfg["look_at"]]
    orientation_mode = str(camera_rig_cfg["orientation_mode"])

    UsdGeom.Xform.Define(stage, "/World/CameraMarkers")

    main_material = make_material(
        stage,
        "/World/Materials/MainCameraBlueMat",
        color=(0.05, 0.25, 0.95),
        roughness=0.45,
        metallic=0.0,
    )

    secondary_material = make_material(
        stage,
        "/World/Materials/SecondaryCameraOrangeMat",
        color=(0.95, 0.45, 0.05),
        roughness=0.45,
        metallic=0.0,
    )

    lens_material = make_material(
        stage,
        "/World/Materials/CameraLensBlackMat",
        color=(0.01, 0.01, 0.015),
        roughness=0.2,
        metallic=0.1,
    )

    marker_materials = [main_material, secondary_material]

    for index, camera_def in enumerate(camera_defs):
        position = camera_def["position"]

        if orientation_mode == "look_at_target":
            marker_rotation_xyz = (0.0, 0.0, float(camera_def["look_at_yaw_deg"]))
        else:
            marker_rotation_xyz = tuple(float(value) for value in camera_def["rotation_xyz_deg"])

        create_single_camera_marker(
            stage=stage,
            path=f"/World/CameraMarkers/{camera_def['name']}",
            position=position,
            rotation_xyz_deg=marker_rotation_xyz,
            body_material=marker_materials[index],
            lens_material=lens_material,
            is_main=(index == 0),
        )

        # Do not create a renderable aim-line from the camera to the target.
        # It is useful for debugging but can cross the camera frustum and contaminate RGB output.

    set_camera_markers_visible(
        stage=stage,
        visible=bool(camera_rig_cfg.get("show_markers", True)),
    )


def create_single_camera_marker(
    stage: Usd.Stage,
    path: str,
    position: list[float],
    rotation_xyz_deg: tuple[float, float, float],
    body_material: UsdShade.Material,
    lens_material: UsdShade.Material,
    is_main: bool,
) -> None:
    UsdGeom.Xform.Define(stage, path)

    set_xform(
        stage=stage,
        prim_path=path,
        translation=(position[0], position[1], position[2]),
        rotation_deg=rotation_xyz_deg,
        scale=(1.0, 1.0, 1.0),
    )

    create_box(
        stage=stage,
        path=f"{path}/BasePost",
        translation=(-0.55, 0.0, 0.16),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.10, 0.10, 0.32),
        material=body_material,
    )

    create_box(
        stage=stage,
        path=f"{path}/Body",
        translation=(-0.55, 0.0, 0.42),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.48, 0.28, 0.22),
        material=body_material,
    )

    create_box(
        stage=stage,
        path=f"{path}/Lens",
        translation=(-0.24, 0.0, 0.42),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.20, 0.13, 0.13),
        material=lens_material,
    )

    # Visual ID: main camera has one tall flag, secondary camera has two flags.
    if is_main:
        create_box(
            stage=stage,
            path=f"{path}/MainIdFlag",
            translation=(-0.85, 0.0, 0.72),
            rotation_deg=(0.0, 0.0, 0.0),
            dimensions=(0.12, 0.38, 0.08),
            material=body_material,
        )
    else:
        create_box(
            stage=stage,
            path=f"{path}/SecondaryIdFlagA",
            translation=(-0.85, 0.10, 0.72),
            rotation_deg=(0.0, 0.0, 0.0),
            dimensions=(0.12, 0.16, 0.08),
            material=body_material,
        )
        create_box(
            stage=stage,
            path=f"{path}/SecondaryIdFlagB",
            translation=(-0.85, -0.10, 0.72),
            rotation_deg=(0.0, 0.0, 0.0),
            dimensions=(0.12, 0.16, 0.08),
            material=body_material,
        )


def create_camera_aim_line(
    stage: Usd.Stage,
    path: str,
    start: list[float],
    end: list[float],
    material: UsdShade.Material,
) -> None:
    curve = UsdGeom.BasisCurves.Define(stage, path)
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr([2])
    curve.CreatePointsAttr(
        [
            Gf.Vec3f(float(start[0]), float(start[1]), float(start[2]) + 0.42),
            Gf.Vec3f(float(end[0]), float(end[1]), float(end[2])),
        ]
    )
    curve.CreateWidthsAttr([0.01, 0.01])
    bind_material(curve.GetPrim(), material)


# -----------------------------------------------------------------------------
# Scene geometry
# -----------------------------------------------------------------------------


def create_lights(stage: Usd.Stage) -> None:
    UsdGeom.Xform.Define(stage, "/World/Lights")

    dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
    dome.CreateIntensityAttr(1500.0)
    try:
        dome.CreateColorAttr(Gf.Vec3f(0.65, 0.78, 1.0))
    except Exception:
        pass

    sun = UsdLux.DistantLight.Define(stage, "/World/Lights/Sun")
    sun.CreateIntensityAttr(3500.0)
    sun.CreateAngleAttr(0.35)
    set_xform(stage, "/World/Lights/Sun", rotation_deg=(-45.0, 0.0, 35.0))


def create_environment(stage: Usd.Stage, scene_config: dict[str, Any]) -> None:
    environment_cfg = normalize_environment(scene_config.get("environment", {}))
    mode = str(environment_cfg.get("mode", "plain_debug"))

    if mode == "plain_debug":
        print("Environment mode: plain_debug")
        create_plain_debug_environment(stage)
        return

    if mode == "external_usd":
        try:
            create_external_usd_environment(stage, environment_cfg)
            return
        except Exception as exc:
            if bool(environment_cfg.get("fallback_to_plain_debug", True)):
                print(
                    "[warning] external USD environment load failed. "
                    "Falling back to plain_debug environment. "
                    f"Reason: {exc}"
                )
                create_plain_debug_environment(stage)
                return

            raise

    raise ValueError(f"Unsupported environment.mode: {mode}")


def create_external_usd_environment(stage: Usd.Stage, environment_cfg: dict[str, Any]) -> None:
    environment_id = str(environment_cfg.get("asset_id", "")).strip()
    if not environment_id:
        raise ValueError("environment.asset_id cannot be empty in external_usd mode.")

    environment_entry = get_environment_asset_entry(PROJECT_ROOT, environment_id)
    environment_scene_path = get_environment_scene_path(PROJECT_ROOT, environment_id)

    if not environment_scene_path.exists() or not environment_scene_path.is_file():
        raise FileNotFoundError(f"Environment scene file does not exist: {environment_scene_path}")

    environment_root_path = "/World/Environment"
    UsdGeom.Xform.Define(stage, environment_root_path)

    translation_m = tuple(float(value) for value in environment_cfg.get("translation_m", [0.0, 0.0, 0.0]))
    rotation_xyz_deg = tuple(float(value) for value in environment_cfg.get("rotation_xyz_deg", [0.0, 0.0, 0.0]))
    uniform_scale = float(environment_cfg.get("uniform_scale", 1.0))
    if uniform_scale <= 0.0:
        raise ValueError("environment.uniform_scale must be greater than zero.")

    set_xform(
        stage=stage,
        prim_path=environment_root_path,
        translation=translation_m,
        rotation_deg=rotation_xyz_deg,
        scale=(uniform_scale, uniform_scale, uniform_scale),
    )

    reference_path = str(environment_scene_path.resolve()).replace("\\", "/")
    reference_targets = get_environment_reference_targets(environment_scene_path)

    if not reference_targets:
        raise ValueError(
            "The external environment USD has no usable top-level prims to reference. "
            f"File: {environment_scene_path}"
        )

    for target in reference_targets:
        target_name = target["name"]
        source_prim_path = target["path"]
        child_path = f"{environment_root_path}/{target_name}"
        # Do not predefine a concrete prim type here. The referenced prim may be
        # a light, scope, material scope, or geometry Xform. Defining an Xform
        # locally can mask the referenced type composition.
        child_prim = stage.DefinePrim(child_path)
        child_prim.GetReferences().AddReference(reference_path, Sdf.Path(source_prim_path))

    print("Environment mode: external_usd")
    print(f"Environment asset id: {environment_id}")
    print(f"Environment display name: {environment_entry.get('display_name', environment_id)}")
    print(f"Environment USD path: {environment_scene_path}")
    print(f"Environment reference path: {reference_path}")
    print(f"Environment referenced prims: {[item['path'] for item in reference_targets]}")
    print(f"Environment translation m: {translation_m}")
    print(f"Environment rotation XYZ deg: {rotation_xyz_deg}")
    print(f"Environment uniform scale: {uniform_scale}")


def get_environment_reference_targets(environment_scene_path: Path) -> list[dict[str, str]]:
    """Return prim targets that should be referenced under /World/Environment.

    Exported Isaac/Omniverse stages often do not set a defaultPrim and may have
    several root prims. A direct file reference would then be fragile. This
    function opens the environment stage and references each root prim separately.

    If the stage has a single /World root, reference /World's children instead of
    nesting another /World under /World/Environment.
    """

    env_stage = Usd.Stage.Open(str(environment_scene_path))
    if env_stage is None:
        raise ValueError(f"Could not open environment USD for inspection: {environment_scene_path}")

    roots = [prim for prim in env_stage.GetPseudoRoot().GetChildren() if prim.IsActive()]

    if len(roots) == 1 and roots[0].GetName() == "World":
        candidates = [prim for prim in roots[0].GetChildren() if prim.IsActive()]
    else:
        candidates = roots

    targets: list[dict[str, str]] = []
    used_names: set[str] = set()

    for prim in candidates:
        name = sanitize_usd_identifier(prim.GetName())
        if not name:
            continue

        base_name = name
        suffix = 1
        while name in used_names:
            suffix += 1
            name = f"{base_name}_{suffix}"

        used_names.add(name)
        targets.append({"name": name, "path": str(prim.GetPath())})

    return targets


def sanitize_usd_identifier(value: str) -> str:
    cleaned = []
    for index, char in enumerate(str(value)):
        if char.isalnum() or char == "_":
            cleaned.append(char)
        else:
            cleaned.append("_")

    result = "".join(cleaned).strip("_")
    if not result:
        return "Prim"

    if result[0].isdigit():
        result = "Prim_" + result

    return result


def create_plain_debug_environment(stage: Usd.Stage) -> None:
    ground_material = make_material(
        stage,
        "/World/Materials/GroundMat",
        color=(0.55, 0.57, 0.55),
        roughness=0.9,
        metallic=0.0,
    )

    sky_material = make_material(
        stage,
        "/World/Materials/BigSkyBlueMat",
        color=(0.45, 0.68, 0.95),
        roughness=0.95,
        metallic=0.0,
    )

    create_box(
        stage=stage,
        path="/World/Ground",
        translation=(0.0, 0.0, -0.015),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(80.0, 80.0, 0.02),
        material=ground_material,
    )

    # Large open sky backdrop.
    # No roof/ceiling: a ceiling blocks dome/sun light and made the scene dark.
    # Keep only far vertical panels so the cameras see blue background without
    # enclosing the scene.
    create_box(
        stage=stage,
        path="/World/SkyBackdrop/BackWall",
        translation=(0.0, 50.0, 18.0),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(140.0, 0.10, 48.0),
        material=sky_material,
    )
    create_box(
        stage=stage,
        path="/World/SkyBackdrop/LeftWall",
        translation=(-70.0, 0.0, 18.0),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.10, 140.0, 48.0),
        material=sky_material,
    )
    create_box(
        stage=stage,
        path="/World/SkyBackdrop/RightWall",
        translation=(70.0, 0.0, 18.0),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.10, 140.0, 48.0),
        material=sky_material,
    )

def create_anchor(stage: Usd.Stage, scene_config: dict[str, Any]) -> None:
    anchor_position = tuple(scene_config["anchor_position"])

    UsdGeom.Xform.Define(stage, "/World/Anchor")

    anchor_material = make_material(
        stage,
        "/World/Materials/AnchorMat",
        color=(0.05, 0.05, 0.05),
        roughness=0.45,
        metallic=0.3,
    )

    mast = UsdGeom.Cylinder.Define(stage, "/World/Anchor/Mast")
    mast.CreateRadiusAttr(0.08)
    mast.CreateHeightAttr(1.2)
    bind_material(mast.GetPrim(), anchor_material)

    set_xform(
        stage,
        "/World/Anchor/Mast",
        translation=(anchor_position[0], anchor_position[1], 0.6),
        rotation_deg=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
    )

    hub = UsdGeom.Sphere.Define(stage, "/World/Anchor/Hub")
    hub.CreateRadiusAttr(0.18)
    bind_material(hub.GetPrim(), anchor_material)

    set_xform(
        stage,
        "/World/Anchor/Hub",
        translation=(anchor_position[0], anchor_position[1], anchor_position[2]),
        rotation_deg=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
    )


def create_tether_curve(stage: Usd.Stage) -> None:
    material = make_material(
        stage,
        "/World/Materials/TetherMat",
        color=(0.02, 0.02, 0.02),
        roughness=0.4,
        metallic=0.0,
    )

    curve = UsdGeom.BasisCurves.Define(stage, "/World/Tether")
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr([2])
    curve.CreatePointsAttr([Gf.Vec3f(0.0, 0.0, 0.0), Gf.Vec3f(1.0, 0.0, 0.0)])
    curve.CreateWidthsAttr([0.025, 0.025])

    bind_material(curve.GetPrim(), material)


def create_glider_visual(stage: Usd.Stage, scene_config: dict[str, Any]) -> None:
    glider_asset_cfg = scene_config.get("glider_asset", {})
    mode = str(glider_asset_cfg.get("mode", "proxy"))

    if mode == "proxy":
        print("Glider visual mode: proxy")
        create_proxy_glider(stage, scene_config)
        return

    if mode != "usd_reference":
        raise ValueError(f"Unsupported glider_asset.mode: {mode}")

    try:
        create_usd_reference_glider(stage, scene_config, glider_asset_cfg)
    except Exception as exc:
        if bool(glider_asset_cfg.get("use_proxy_fallback", True)):
            print(
                "[warning] USD glider asset load failed. Falling back to proxy glider. "
                f"Reason: {exc}"
            )
            create_proxy_glider(stage, scene_config)
            return

        raise


def create_usd_reference_glider(
    stage: Usd.Stage,
    scene_config: dict[str, Any],
    glider_asset_cfg: dict[str, Any],
) -> None:
    asset_id = str(glider_asset_cfg.get("asset_id", "")).strip()

    if not asset_id:
        raise ValueError("glider_asset.asset_id cannot be empty in usd_reference mode.")

    registry = load_asset_registry(PROJECT_ROOT)
    asset_entry = get_asset_entry(registry, asset_id)
    usd_path = get_asset_converted_usd_path(PROJECT_ROOT, asset_entry)

    if not usd_path.exists() or not usd_path.is_file():
        raise FileNotFoundError(f"Converted USD asset does not exist: {usd_path}")

    UsdGeom.Xform.Define(stage, "/World/Glider")
    asset_prim = UsdGeom.Xform.Define(stage, "/World/Glider/Asset").GetPrim()

    reference_path = str(usd_path.resolve()).replace("\\", "/")
    asset_prim.GetReferences().AddReference(reference_path)

    uniform_scale = float(glider_asset_cfg.get("uniform_scale", 1.0))
    if uniform_scale <= 0.0:
        raise ValueError("glider_asset.uniform_scale must be greater than zero.")

    rotation_xyz_deg = tuple(
        float(value) for value in glider_asset_cfg.get("rotation_xyz_deg", [0.0, 0.0, 0.0])
    )
    translation_offset_m = tuple(
        float(value) for value in glider_asset_cfg.get("translation_offset_m", [0.0, 0.0, 0.0])
    )

    set_xform(
        stage=stage,
        prim_path="/World/Glider/Asset",
        translation=translation_offset_m,
        rotation_deg=rotation_xyz_deg,
        scale=(uniform_scale, uniform_scale, uniform_scale),
    )

    material_override = glider_asset_cfg.get("material_override", {})
    if bool(material_override.get("enabled", False)):
        color = material_override.get("diffuse_color", [0.92, 0.92, 0.88])
        material = make_material(
            stage,
            "/World/Materials/GliderAssetOverrideMat",
            color=(float(color[0]), float(color[1]), float(color[2])),
            roughness=float(material_override.get("roughness", 0.55)),
            metallic=float(material_override.get("metallic", 0.0)),
        )
        bind_material_recursive(asset_prim, material)

    print("Glider visual mode: usd_reference")
    print(f"Glider asset id: {asset_id}")
    print(f"Glider USD path: {usd_path}")
    print(f"Glider USD reference path: {reference_path}")
    print(f"Glider USD uniform scale: {uniform_scale}")
    print(f"Glider USD child rotation XYZ deg: {rotation_xyz_deg}")
    print(f"Glider USD child translation offset m: {translation_offset_m}")


def create_proxy_glider(stage: Usd.Stage, scene_config: dict[str, Any]) -> None:
    glider_cfg = scene_config["glider"]

    wingspan = float(glider_cfg["wingspan_m"])
    length = float(glider_cfg["length_m"])
    body_width = float(glider_cfg["body_width_m"])
    body_height = float(glider_cfg["body_height_m"])
    wing_chord = float(glider_cfg["wing_chord_m"])
    wing_thickness = float(glider_cfg["wing_thickness_m"])

    UsdGeom.Xform.Define(stage, "/World/Glider")

    white = make_material(stage, "/World/Materials/GliderWhite", color=(0.92, 0.92, 0.88), roughness=0.55)
    black = make_material(stage, "/World/Materials/GliderBlack", color=(0.01, 0.01, 0.012), roughness=0.35)
    green = make_material(stage, "/World/Materials/GliderGreen", color=(0.2, 0.9, 0.05), roughness=0.45)

    create_box(stage, "/World/Glider/Fuselage", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (length, body_width, body_height), white)
    create_box(stage, "/World/Glider/MainWing", (0.03, 0.0, 0.035), (0.0, 0.0, 0.0), (wing_chord, wingspan, wing_thickness), white)
    create_box(stage, "/World/Glider/LeftGreenStripe", (0.05, 0.38, 0.055), (0.0, 0.0, 0.0), (0.04, 0.42, 0.012), green)
    create_box(stage, "/World/Glider/RightGreenStripe", (0.05, -0.38, 0.055), (0.0, 0.0, 0.0), (0.04, 0.42, 0.012), green)
    create_box(stage, "/World/Glider/TailPlane", (-0.40, 0.0, 0.04), (0.0, 0.0, 0.0), (0.14, 0.42, 0.02), white)
    create_box(stage, "/World/Glider/VerticalFin", (-0.42, 0.0, 0.13), (0.0, 0.0, 0.0), (0.12, 0.025, 0.22), white)
    create_box(stage, "/World/Glider/Canopy", (0.23, 0.0, 0.08), (0.0, 0.0, 0.0), (0.20, 0.08, 0.035), black)
    create_box(stage, "/World/Glider/NoseMarker", (0.46, 0.0, 0.0), (0.0, 0.0, 0.0), (0.035, 0.11, 0.11), black)


# -----------------------------------------------------------------------------
# Motion
# -----------------------------------------------------------------------------


def update_tethered_glider_motion(stage: Usd.Stage, scene_config: dict[str, Any], sim_time_s: float) -> None:
    anchor = scene_config["anchor_position"]
    glider_position = compute_glider_position(scene_config, sim_time_s)

    theta = float(scene_config["angular_velocity_rad_s"]) * sim_time_s

    tangent_x = -math.sin(theta)
    tangent_y = math.cos(theta)
    yaw_deg = math.degrees(math.atan2(tangent_y, tangent_x))

    set_xform(stage, "/World/Glider", translation=tuple(glider_position), rotation_deg=(0.0, 0.0, yaw_deg), scale=(1.0, 1.0, 1.0))

    tether = UsdGeom.BasisCurves(stage.GetPrimAtPath("/World/Tether"))
    tether.GetPointsAttr().Set(
        [
            Gf.Vec3f(float(anchor[0]), float(anchor[1]), float(anchor[2])),
            Gf.Vec3f(float(glider_position[0]), float(glider_position[1]), float(glider_position[2])),
        ]
    )


def compute_glider_position(scene_config: dict[str, Any], sim_time_s: float) -> tuple[float, float, float]:
    anchor = scene_config["anchor_position"]
    tether_length = float(scene_config["tether_length_m"])
    glider_height = float(scene_config["glider_height_m"])
    angular_velocity = float(scene_config["angular_velocity_rad_s"])

    theta = angular_velocity * sim_time_s

    x = float(anchor[0]) + tether_length * math.cos(theta)
    y = float(anchor[1]) + tether_length * math.sin(theta)
    z = float(anchor[2]) + glider_height

    return x, y, z


def compute_tether_constraint_error(scene_config: dict[str, Any], glider_position: tuple[float, float, float]) -> float:
    anchor = scene_config["anchor_position"]
    tether_length = float(scene_config["tether_length_m"])

    dx = glider_position[0] - float(anchor[0])
    dy = glider_position[1] - float(anchor[1])

    horizontal_distance = math.sqrt(dx * dx + dy * dy)

    return abs(horizontal_distance - tether_length)



def set_camera_markers_visible(stage: Usd.Stage, visible: bool) -> None:
    set_prim_visibility_recursive(
        prim=stage.GetPrimAtPath("/World/CameraMarkers"),
        visible=visible,
    )


def set_prim_visibility_recursive(prim: Usd.Prim, visible: bool) -> None:
    if not prim or not prim.IsValid():
        return

    visibility_token = UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible

    try:
        imageable = UsdGeom.Imageable(prim)
        imageable.CreateVisibilityAttr().Set(visibility_token)
    except Exception:
        pass

    for child in prim.GetChildren():
        set_prim_visibility_recursive(child, visible)


# -----------------------------------------------------------------------------
# USD helpers
# -----------------------------------------------------------------------------


def create_box(
    stage: Usd.Stage,
    path: str,
    translation,
    rotation_deg,
    dimensions,
    material: UsdShade.Material,
) -> Usd.Prim:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)

    set_xform(stage=stage, prim_path=path, translation=translation, rotation_deg=rotation_deg, scale=dimensions)
    bind_material(cube.GetPrim(), material)

    return cube.GetPrim()


def set_xform(
    stage: Usd.Stage,
    prim_path: str,
    translation=(0.0, 0.0, 0.0),
    rotation_deg=(0.0, 0.0, 0.0),
    scale=(1.0, 1.0, 1.0),
) -> None:
    prim = stage.GetPrimAtPath(prim_path)

    if not prim.IsValid():
        raise ValueError(f"Invalid prim path: {prim_path}")

    xformable = UsdGeom.Xformable(prim)
    xformable.ClearXformOpOrder()

    translate_op = xformable.AddTranslateOp()
    rotate_op = xformable.AddRotateXYZOp()
    scale_op = xformable.AddScaleOp()

    translate_op.Set(Gf.Vec3d(*translation))
    rotate_op.Set(Gf.Vec3f(*rotation_deg))
    scale_op.Set(Gf.Vec3f(*scale))


def make_material(
    stage: Usd.Stage,
    path: str,
    color=(0.8, 0.8, 0.8),
    roughness=0.5,
    metallic=0.0,
    opacity=1.0,
) -> UsdShade.Material:
    material_parent_path = "/".join(path.split("/")[:-1])

    if material_parent_path:
        UsdGeom.Xform.Define(stage, material_parent_path)

    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")

    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(metallic))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))

    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

    return material


def bind_material(prim: Usd.Prim, material: UsdShade.Material) -> None:
    UsdShade.MaterialBindingAPI.Apply(prim)
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def bind_material_recursive(prim: Usd.Prim, material: UsdShade.Material) -> None:
    if not prim or not prim.IsValid():
        return

    try:
        bind_material(prim, material)
    except Exception:
        pass

    for child in prim.GetChildren():
        bind_material_recursive(child, material)
