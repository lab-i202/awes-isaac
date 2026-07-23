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
        print(f"Show camera frustums in viewport: {camera_rig_cfg.get('show_frustums', False)}")
        print(f"Camera horizontal FOV deg: {float(camera_rig_cfg.get('horizontal_fov_deg', 0.0)):.3f}")
        print(f"Camera effective focal length mm: {float(camera_rig_cfg.get('focal_length', 0.0)):.3f}")
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
                write_camera_rig_layout_files(
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
    horizontal_aperture = float(camera_rig_cfg.get("horizontal_aperture_mm", 20.955))
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
                horizontal_aperture=horizontal_aperture,
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
                horizontal_aperture=horizontal_aperture,
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
    print(f"horizontal FOV deg: {float(camera_rig_cfg.get('horizontal_fov_deg', 0.0)):.3f}")
    print(f"horizontal aperture mm: {horizontal_aperture:.3f}")
    print(f"effective focal length mm: {focal_length:.3f}")
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
            "Camera frustum overlays are renderable debug geometry when camera_rig.show_frustums=true. Keep them off for clean RGB datasets.",
        ],
    }

    with open(output_dir / "camera_rig_metadata.json", "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)



def write_camera_rig_layout_files(
    output_dir: Path,
    scene_config: dict[str, Any],
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
) -> None:
    """Write camera layout diagnostics.

    The SVG is a diagnostic sheet, not decorative artwork. It writes the
    geometry needed to audit a stereo setup: world axes, camera coordinates,
    baseline, look-at/reference point, tether/orbit footprint, sampled glider
    positions, frustum footprints, and coverage checks.
    """

    diagnostics = build_camera_layout_diagnostics(scene_config, camera_rig_cfg, camera_defs)

    with open(output_dir / "camera_rig_layout.json", "w", encoding="utf-8") as file:
        json.dump(diagnostics, file, indent=2)

    svg = build_camera_layout_svg(diagnostics)
    (output_dir / "camera_rig_layout.svg").write_text(svg, encoding="utf-8")

    nearfield_svg = build_camera_nearfield_layout_svg(diagnostics)
    (output_dir / "camera_rig_layout_nearfield.svg").write_text(nearfield_svg, encoding="utf-8")

    summary_rows = diagnostics.get("summary_rows", [])
    if summary_rows:
        with open(output_dir / "camera_rig_layout_summary.csv", "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["quantity", "value", "unit"])
            writer.writeheader()
            writer.writerows(summary_rows)


def build_camera_layout_diagnostics(
    scene_config: dict[str, Any],
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute numeric stereo-layout diagnostics used by SVG/JSON outputs."""

    def v3(values: Any, fallback: list[float] | None = None) -> list[float]:
        if fallback is None:
            fallback = [0.0, 0.0, 0.0]
        try:
            return [float(values[0]), float(values[1]), float(values[2])]
        except Exception:
            return [float(fallback[0]), float(fallback[1]), float(fallback[2])]

    def add(a: list[float], b: list[float]) -> list[float]:
        return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]

    def sub(a: list[float], b: list[float]) -> list[float]:
        return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

    def scale(a: list[float], s: float) -> list[float]:
        return [a[0] * s, a[1] * s, a[2] * s]

    def dot(a: list[float], b: list[float]) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def cross(a: list[float], b: list[float]) -> list[float]:
        return [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]

    def norm(a: list[float]) -> float:
        return math.sqrt(max(dot(a, a), 0.0))

    def normalize(a: list[float], fallback: list[float] | None = None) -> list[float]:
        n = norm(a)
        if n < 1e-12:
            return list(fallback or [1.0, 0.0, 0.0])
        return [a[0] / n, a[1] / n, a[2] / n]

    def distance(a: list[float], b: list[float]) -> float:
        return norm(sub(a, b))

    def color_hex(values: Any, fallback: str) -> str:
        if isinstance(values, list) and len(values) == 3:
            return "#%02x%02x%02x" % tuple(
                max(0, min(255, int(round(float(v) * 255.0)))) for v in values
            )
        return fallback

    def camera_target(cam: dict[str, Any]) -> list[float]:
        cam_pos = v3(cam.get("position", [0.0, 0.0, 0.0]))
        if str(camera_rig_cfg.get("orientation_mode", "parallel_manual")) == "look_at_target":
            return look_at
        return v3(cam.get("parallel_look_at", [cam_pos[0] + 1.0, cam_pos[1], cam_pos[2]]))

    def camera_basis(cam: dict[str, Any]) -> dict[str, list[float]]:
        origin = v3(cam.get("position", [0.0, 0.0, 0.0]))
        target = camera_target(cam)
        forward = normalize(sub(target, origin), [1.0, 0.0, 0.0])
        world_up = [0.0, 0.0, 1.0]
        right = normalize(cross(forward, world_up), [1.0, 0.0, 0.0])
        up = normalize(cross(right, forward), [0.0, 0.0, 1.0])
        return {"origin": origin, "target": target, "forward": forward, "right": right, "up": up}

    def point_fov_status(point: list[float], basis: dict[str, list[float]]) -> dict[str, Any]:
        rel = sub(point, basis["origin"])
        depth = dot(rel, basis["forward"])
        if depth <= 1e-9:
            return {
                "inside": False,
                "depth_m": depth,
                "horizontal_angle_deg": None,
                "vertical_angle_deg": None,
                "reason": "behind_camera",
            }
        h_angle = math.degrees(math.atan2(dot(rel, basis["right"]), depth))
        v_angle = math.degrees(math.atan2(dot(rel, basis["up"]), depth))
        inside = abs(h_angle) <= hfov_deg / 2.0 and abs(v_angle) <= vfov_deg / 2.0
        return {
            "inside": bool(inside),
            "depth_m": depth,
            "horizontal_angle_deg": h_angle,
            "vertical_angle_deg": v_angle,
            "reason": "inside" if inside else "outside_fov",
        }

    def frustum_far_corners(basis: dict[str, list[float]]) -> list[list[float]]:
        origin = basis["origin"]
        center = add(origin, scale(basis["forward"], frustum_distance))
        half_w = math.tan(math.radians(hfov_deg) / 2.0) * frustum_distance
        half_h = math.tan(math.radians(vfov_deg) / 2.0) * frustum_distance
        return [
            add(add(center, scale(basis["right"], -half_w)), scale(basis["up"], half_h)),
            add(add(center, scale(basis["right"], half_w)), scale(basis["up"], half_h)),
            add(add(center, scale(basis["right"], half_w)), scale(basis["up"], -half_h)),
            add(add(center, scale(basis["right"], -half_w)), scale(basis["up"], -half_h)),
        ]

    def convex_hull_xy(points_xy: list[list[float]]) -> list[list[float]]:
        # Andrew monotonic chain. Returns ordered hull points for SVG polygon fill.
        pts = sorted({(round(float(p[0]), 10), round(float(p[1]), 10)) for p in points_xy})
        if len(pts) <= 1:
            return [[float(x), float(y)] for x, y in pts]

        def cross2(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        lower: list[tuple[float, float]] = []
        for p in pts:
            while len(lower) >= 2 and cross2(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        upper: list[tuple[float, float]] = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross2(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        hull = lower[:-1] + upper[:-1]
        return [[float(x), float(y)] for x, y in hull]

    def glider_position_at(theta: float) -> list[float]:
        return [
            anchor[0] + tether * math.cos(theta),
            anchor[1] + tether * math.sin(theta),
            float(scene_config.get("glider_height_m", anchor[2])),
        ]

    anchor = v3(scene_config.get("anchor_position", [0.0, 0.0, 0.0]))
    look_at = v3(camera_rig_cfg.get("look_at", [0.0, 0.0, 1.5]))
    tether = float(scene_config.get("tether_length_m", 0.0))
    glider_height = float(scene_config.get("glider_height_m", look_at[2]))
    angular_velocity = float(scene_config.get("angular_velocity_rad_s", 0.0))
    num_frames = int(scene_config.get("num_frames", 0))
    time_step_s = float(scene_config.get("time_step_s", 0.0))
    duration_s = max(0.0, (num_frames - 1) * time_step_s) if num_frames > 0 else 0.0

    resolution = camera_rig_cfg.get("resolution", [1280, 720])
    width_px = float(resolution[0]) if len(resolution) >= 1 else 1280.0
    height_px = float(resolution[1]) if len(resolution) >= 2 else 720.0
    aspect = width_px / max(height_px, 1.0)
    hfov_deg = float(camera_rig_cfg.get("horizontal_fov_deg", 33.332))
    vfov_deg = math.degrees(2.0 * math.atan(math.tan(math.radians(hfov_deg) / 2.0) / max(aspect, 1e-9)))
    frustum_distance = max(float(camera_rig_cfg.get("frustum_distance_m", 20.0)), 0.1)

    camera_items: list[dict[str, Any]] = []
    for cam in camera_defs:
        basis = camera_basis(cam)
        far_corners = frustum_far_corners(basis)
        origin = basis["origin"]
        target = basis["target"]
        frustum_xy_hull = convex_hull_xy([[origin[0], origin[1]]] + [[p[0], p[1]] for p in far_corners])
        anchor_delta = sub(anchor, origin)
        anchor_dx = float(anchor_delta[0])
        anchor_dy = float(anchor_delta[1])
        anchor_dz = float(anchor_delta[2])
        anchor_distance_xy = math.sqrt(anchor_dx * anchor_dx + anchor_dy * anchor_dy)
        anchor_distance_xyz = math.sqrt(anchor_dx * anchor_dx + anchor_dy * anchor_dy + anchor_dz * anchor_dz)
        camera_items.append(
            {
                "name": str(cam.get("name", "camera")),
                "position": origin,
                "target": target,
                "forward_unit": basis["forward"],
                "right_unit": basis["right"],
                "up_unit": basis["up"],
                "marker_color_hex": color_hex(cam.get("marker_color"), "#1f77b4"),
                "frustum_color_hex": color_hex(cam.get("frustum_color", cam.get("marker_color")), "#1f77b4"),
                "far_corners": far_corners,
                "frustum_xy_hull": frustum_xy_hull,
                "anchor_coverage": point_fov_status(anchor, basis),
                "look_at_coverage": point_fov_status(look_at, basis),
                "glider_start_coverage": point_fov_status(glider_position_at(0.0), basis),
                "anchor_relative": {
                    "dx_anchor_minus_camera_m": anchor_dx,
                    "dy_anchor_minus_camera_m": anchor_dy,
                    "dz_anchor_minus_camera_m": anchor_dz,
                    "distance_xy_m": anchor_distance_xy,
                    "distance_euclidean_m": anchor_distance_xyz,
                },
            }
        )

    glider_samples = []
    sample_count = 36
    for i in range(sample_count):
        theta = 2.0 * math.pi * i / sample_count
        p = glider_position_at(theta)
        sample = {"index": i, "theta_rad": theta, "position": p, "coverage": {}}
        for cam_item in camera_items:
            basis = {
                "origin": cam_item["position"],
                "forward": cam_item["forward_unit"],
                "right": cam_item["right_unit"],
                "up": cam_item["up_unit"],
            }
            sample["coverage"][cam_item["name"]] = point_fov_status(p, basis)["inside"]
        glider_samples.append(sample)

    baseline_vector = sub(camera_items[1]["position"], camera_items[0]["position"]) if len(camera_items) >= 2 else [0.0, 0.0, 0.0]
    baseline_distance = norm(baseline_vector)
    forward_angle_deg = 0.0
    if len(camera_items) >= 2:
        c = max(-1.0, min(1.0, dot(camera_items[0]["forward_unit"], camera_items[1]["forward_unit"])))
        forward_angle_deg = math.degrees(math.acos(c))

    for cam_item in camera_items:
        visible_samples = sum(1 for sample in glider_samples if sample["coverage"].get(cam_item["name"], False))
        cam_item["glider_orbit_samples_inside"] = visible_samples
        cam_item["glider_orbit_samples_total"] = sample_count
        cam_item["glider_orbit_coverage_fraction"] = visible_samples / sample_count

    both_visible = sum(
        1
        for sample in glider_samples
        if all(sample["coverage"].get(cam_item["name"], False) for cam_item in camera_items)
    )

    summary_rows = [
        {"quantity": "scene_name", "value": str(scene_config.get("scene_name", "")), "unit": ""},
        {"quantity": "orientation_mode", "value": str(camera_rig_cfg.get("orientation_mode", "")), "unit": ""},
        {"quantity": "resolution", "value": f"{int(width_px)} x {int(height_px)}", "unit": "px"},
        {"quantity": "horizontal_fov", "value": f"{hfov_deg:.6f}", "unit": "deg"},
        {"quantity": "vertical_fov_derived", "value": f"{vfov_deg:.6f}", "unit": "deg"},
        {"quantity": "frustum_distance", "value": f"{frustum_distance:.6f}", "unit": "m"},
        {"quantity": "baseline_distance", "value": f"{baseline_distance:.6f}", "unit": "m"},
        {"quantity": "baseline_vector_dx_dy_dz", "value": f"({baseline_vector[0]:.6f}, {baseline_vector[1]:.6f}, {baseline_vector[2]:.6f})", "unit": "m"},
        {"quantity": "inter_camera_forward_angle", "value": f"{forward_angle_deg:.6f}", "unit": "deg"},
        {"quantity": "anchor_position", "value": f"({anchor[0]:.6f}, {anchor[1]:.6f}, {anchor[2]:.6f})", "unit": "m"},
        {"quantity": "look_at_position", "value": f"({look_at[0]:.6f}, {look_at[1]:.6f}, {look_at[2]:.6f})", "unit": "m"},
        {"quantity": "tether_radius", "value": f"{tether:.6f}", "unit": "m"},
        {"quantity": "glider_height", "value": f"{glider_height:.6f}", "unit": "m"},
        {"quantity": "orbit_samples_visible_both_cameras", "value": f"{both_visible}/{sample_count}", "unit": "samples"},
    ]

    for cam_item in camera_items:
        p = cam_item["position"]
        f = cam_item["forward_unit"]
        summary_rows += [
            {"quantity": f"{cam_item['name']}_position", "value": f"({p[0]:.6f}, {p[1]:.6f}, {p[2]:.6f})", "unit": "m"},
            {"quantity": f"{cam_item['name']}_forward_unit", "value": f"({f[0]:.6f}, {f[1]:.6f}, {f[2]:.6f})", "unit": ""},
            {"quantity": f"{cam_item['name']}_anchor_inside_fov", "value": str(cam_item["anchor_coverage"]["inside"]), "unit": ""},
            {"quantity": f"{cam_item['name']}_look_at_inside_fov", "value": str(cam_item["look_at_coverage"]["inside"]), "unit": ""},
            {"quantity": f"{cam_item['name']}_orbit_samples_inside", "value": f"{cam_item['glider_orbit_samples_inside']}/{sample_count}", "unit": "samples"},
            {"quantity": f"{cam_item['name']}_anchor_dx", "value": f"{cam_item['anchor_relative']['dx_anchor_minus_camera_m']:.6f}", "unit": "m"},
            {"quantity": f"{cam_item['name']}_anchor_dy", "value": f"{cam_item['anchor_relative']['dy_anchor_minus_camera_m']:.6f}", "unit": "m"},
            {"quantity": f"{cam_item['name']}_anchor_distance_xy", "value": f"{cam_item['anchor_relative']['distance_xy_m']:.6f}", "unit": "m"},
            {"quantity": f"{cam_item['name']}_anchor_distance_euclidean", "value": f"{cam_item['anchor_relative']['distance_euclidean_m']:.6f}", "unit": "m"},
        ]

    return {
        "scene_name": str(scene_config.get("scene_name", "")),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "world_frame": {
            "x_axis": "world +X, drawn red in top-down layout",
            "y_axis": "world +Y, drawn green in top-down layout",
            "z_axis": "world +Z, up/out of the XY page",
        },
        "camera_model_note": (
            "Diagnostic pinhole-frustum geometry. The optical axis is the normalized vector from camera position "
            "to the Replicator look_at/parallel_look_at point. Horizontal FOV comes from the profile; vertical FOV "
            "is derived from horizontal FOV and image aspect ratio."
        ),
        "scene": {
            "anchor_position": anchor,
            "look_at": look_at,
            "tether_length_m": tether,
            "glider_height_m": glider_height,
            "angular_velocity_rad_s": angular_velocity,
            "duration_s": duration_s,
        },
        "camera_rig": {
            "orientation_mode": str(camera_rig_cfg.get("orientation_mode", "")),
            "resolution": [int(width_px), int(height_px)],
            "horizontal_fov_deg": hfov_deg,
            "vertical_fov_deg": vfov_deg,
            "focal_length_mm": float(camera_rig_cfg.get("focal_length", 0.0)),
            "horizontal_aperture_mm": float(camera_rig_cfg.get("horizontal_aperture_mm", 20.955)),
            "frustum_distance_m": frustum_distance,
            "baseline_vector_m": baseline_vector,
            "baseline_distance_m": baseline_distance,
            "inter_camera_forward_angle_deg": forward_angle_deg,
        },
        "cameras": camera_items,
        "glider_samples": glider_samples,
        "overlap": {
            "orbit_samples_visible_in_both_cameras": both_visible,
            "orbit_samples_total": sample_count,
            "orbit_coverage_fraction_both_cameras": both_visible / sample_count,
        },
        "summary_rows": summary_rows,
        "notes": [
            "The top-down frustum is the XY projection of the 3D viewing pyramid at the configured frustum distance.",
            "A point can be visible in RGB even if a previous 2D approximation failed to cover it; use the coverage booleans in this JSON for numeric diagnostics.",
            "In-scene frustum lines are renderable debug geometry. Keep them disabled for clean RGB datasets.",
        ],
    }


def build_camera_layout_svg(diagnostics: dict[str, Any]) -> str:
    """Build a publication-style stereo camera diagnostic SVG.

    The output is deliberately verbose: it is meant to be inspected, archived,
    and referenced when debugging stereo datasets, not merely previewed as a
    small decorative drawing.
    """

    def esc(text: Any) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def fmt3(values: list[float]) -> str:
        return f"({values[0]:.3f}, {values[1]:.3f}, {values[2]:.3f})"

    def xy(point: list[float]) -> list[float]:
        return [float(point[0]), float(point[1])]

    scene = diagnostics["scene"]
    rig = diagnostics["camera_rig"]
    cameras = diagnostics["cameras"]
    glider_samples = diagnostics["glider_samples"]

    width = 1800
    height = 1300
    top_panel = {"x": 60.0, "y": 125.0, "w": 1120.0, "h": 780.0}
    side_panel = {"x": 1220.0, "y": 125.0, "w": 520.0, "h": 330.0}
    table_panel = {"x": 1220.0, "y": 485.0, "w": 520.0, "h": 420.0}
    notes_panel = {"x": 60.0, "y": 945.0, "w": 1680.0, "h": 290.0}

    anchor = scene["anchor_position"]
    look_at = scene["look_at"]
    tether = float(scene["tether_length_m"])
    glider_height = float(scene["glider_height_m"])

    # Build top-down bounding box from all diagnostic points. This fixes the old
    # cropping/far-edge problem: the layout scale is driven by actual geometry,
    # not by a hand-picked viewport.
    top_points: list[list[float]] = [xy(anchor), xy(look_at)]
    if tether > 0.0:
        for k in range(96):
            theta = 2.0 * math.pi * k / 96.0
            top_points.append([anchor[0] + tether * math.cos(theta), anchor[1] + tether * math.sin(theta)])
    for cam in cameras:
        top_points.append(xy(cam["position"]))
        for p in cam["frustum_xy_hull"]:
            top_points.append([float(p[0]), float(p[1])])
        for p in cam["far_corners"]:
            top_points.append(xy(p))
    for sample in glider_samples:
        top_points.append(xy(sample["position"]))

    min_x = min(p[0] for p in top_points)
    max_x = max(p[0] for p in top_points)
    min_y = min(p[1] for p in top_points)
    max_y = max(p[1] for p in top_points)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    margin = 0.08 * max(span_x, span_y, 1.0)
    min_x -= margin
    max_x += margin
    min_y -= margin
    max_y += margin

    # Preserve metric aspect ratio.
    data_w = max_x - min_x
    data_h = max_y - min_y
    panel_ratio = top_panel["w"] / max(top_panel["h"], 1.0)
    data_ratio = data_w / max(data_h, 1e-9)
    if data_ratio > panel_ratio:
        target_h = data_w / panel_ratio
        extra = (target_h - data_h) / 2.0
        min_y -= extra
        max_y += extra
    else:
        target_w = data_h * panel_ratio
        extra = (target_w - data_w) / 2.0
        min_x -= extra
        max_x += extra

    def tx(x: float) -> float:
        return top_panel["x"] + (x - min_x) / max(max_x - min_x, 1e-9) * top_panel["w"]

    def ty(y: float) -> float:
        return top_panel["y"] + top_panel["h"] - (y - min_y) / max(max_y - min_y, 1e-9) * top_panel["h"]

    def top_poly(points: list[list[float]]) -> str:
        return " ".join(f"{tx(float(p[0])):.1f},{ty(float(p[1])):.1f}" for p in points)

    # Side view scaler: horizontal axis is forward ground range from camera_main;
    # vertical axis is Z. This is not a full epipolar plot; it is a practical
    # sanity check for height/pitch/frustum range.
    side_points: list[list[float]] = []
    cam0 = cameras[0]
    cam0_xy = xy(cam0["position"])
    f0 = [float(cam0["forward_unit"][0]), float(cam0["forward_unit"][1])]
    f0_norm = math.sqrt(f0[0] * f0[0] + f0[1] * f0[1])
    if f0_norm < 1e-9:
        f0 = [1.0, 0.0]
    else:
        f0 = [f0[0] / f0_norm, f0[1] / f0_norm]

    def range_from_cam0(point: list[float]) -> float:
        return (point[0] - cam0_xy[0]) * f0[0] + (point[1] - cam0_xy[1]) * f0[1]

    for cam in cameras:
        side_points.append([range_from_cam0(cam["position"]), cam["position"][2]])
        for p in cam["far_corners"]:
            side_points.append([range_from_cam0(p), p[2]])
    side_points.append([range_from_cam0(anchor), anchor[2]])
    side_points.append([range_from_cam0(look_at), look_at[2]])
    for sample in glider_samples:
        side_points.append([range_from_cam0(sample["position"]), sample["position"][2]])

    sr_min = min(p[0] for p in side_points)
    sr_max = max(p[0] for p in side_points)
    sz_min = min(p[1] for p in side_points)
    sz_max = max(p[1] for p in side_points)
    sr_margin = 0.08 * max(sr_max - sr_min, 1.0)
    sz_margin = 0.18 * max(sz_max - sz_min, 1.0)
    sr_min -= sr_margin
    sr_max += sr_margin
    sz_min = min(0.0, sz_min - sz_margin)
    sz_max += sz_margin

    def sx_side(r: float) -> float:
        return side_panel["x"] + (r - sr_min) / max(sr_max - sr_min, 1e-9) * side_panel["w"]

    def sz_side(z: float) -> float:
        return side_panel["y"] + side_panel["h"] - (z - sz_min) / max(sz_max - sz_min, 1e-9) * side_panel["h"]

    def side_point(point: list[float]) -> tuple[float, float]:
        return sx_side(range_from_cam0(point)), sz_side(point[2])

    elements: list[str] = []
    elements.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" style="background:white">'
    )
    elements.append('<rect width="100%" height="100%" fill="white"/>')
    elements.append(
        '<text x="60" y="45" font-size="28" font-family="Arial" font-weight="700">Stereo camera rig diagnostic</text>'
    )
    elements.append(
        f'<text x="60" y="75" font-size="15" font-family="Arial" fill="#555">Scene={esc(diagnostics["scene_name"])} | '
        f'orientation={esc(rig["orientation_mode"])} | HFOV={rig["horizontal_fov_deg"]:.2f}° | '
        f'VFOV={rig["vertical_fov_deg"]:.2f}° | frustum distance={rig["frustum_distance_m"]:.2f} m | '
        f'baseline={rig["baseline_distance_m"]:.3f} m</text>'
    )
    elements.append(
        '<text x="60" y="100" font-size="13" font-family="Arial" fill="#777">'
        'World frame: +X red, +Y green, +Z up/out of the top-down page. Camera optical axis is drawn from camera position toward the configured look_at/parallel_look_at point.</text>'
    )

    # Panel backgrounds.
    for panel, title in [(top_panel, "Top-down world XY layout"), (side_panel, "Side-view height sanity check"), (table_panel, "Numeric diagnostics")]:
        elements.append(
            f'<rect x="{panel["x"]}" y="{panel["y"]}" width="{panel["w"]}" height="{panel["h"]}" '
            'fill="#fbfbfb" stroke="#c9c9c9" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{panel["x"] + 14}" y="{panel["y"] + 28}" font-size="18" font-family="Arial" font-weight="700">{title}</text>'
        )

    # Top-down metric grid.
    extent = max(max_x - min_x, max_y - min_y)
    if extent <= 30:
        grid_step = 5.0
    elif extent <= 100:
        grid_step = 10.0
    elif extent <= 250:
        grid_step = 25.0
    else:
        grid_step = 50.0

    gx0 = math.floor(min_x / grid_step) * grid_step
    while gx0 <= max_x:
        color = "#e6e6e6" if abs(gx0) > 1e-9 else "#cc6666"
        sw = "1" if abs(gx0) > 1e-9 else "2"
        elements.append(f'<line x1="{tx(gx0):.1f}" y1="{top_panel["y"]:.1f}" x2="{tx(gx0):.1f}" y2="{top_panel["y"]+top_panel["h"]:.1f}" stroke="{color}" stroke-width="{sw}"/>')
        gx0 += grid_step
    gy0 = math.floor(min_y / grid_step) * grid_step
    while gy0 <= max_y:
        color = "#e6e6e6" if abs(gy0) > 1e-9 else "#66aa66"
        sw = "1" if abs(gy0) > 1e-9 else "2"
        elements.append(f'<line x1="{top_panel["x"]:.1f}" y1="{ty(gy0):.1f}" x2="{top_panel["x"]+top_panel["w"]:.1f}" y2="{ty(gy0):.1f}" stroke="{color}" stroke-width="{sw}"/>')
        gy0 += grid_step

    # World-frame axis inset.
    ax0 = top_panel["x"] + 70
    ay0 = top_panel["y"] + top_panel["h"] - 70
    elements.append(f'<line x1="{ax0}" y1="{ay0}" x2="{ax0+65}" y2="{ay0}" stroke="#cc3333" stroke-width="4" marker-end="url(#arrowRed)"/>')
    elements.append(f'<line x1="{ax0}" y1="{ay0}" x2="{ax0}" y2="{ay0-65}" stroke="#33aa33" stroke-width="4" marker-end="url(#arrowGreen)"/>')
    elements.append(f'<text x="{ax0+72}" y="{ay0+5}" font-size="14" font-family="Arial" fill="#cc3333">+X</text>')
    elements.append(f'<text x="{ax0-12}" y="{ay0-72}" font-size="14" font-family="Arial" fill="#33aa33">+Y</text>')
    elements.append(
        '<defs>'
        '<marker id="arrowRed" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#cc3333"/></marker>'
        '<marker id="arrowGreen" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#33aa33"/></marker>'
        '</defs>'
    )

    # Frustum footprints.
    for cam in cameras:
        color = cam["frustum_color_hex"]
        hull = cam["frustum_xy_hull"]
        elements.append(
            f'<polygon points="{top_poly(hull)}" fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="2.4"/>'
        )
        origin = cam["position"]
        far = cam["far_corners"]
        # Draw all corner rays and far rectangle projection to make the footprint clear.
        for corner in far:
            elements.append(
                f'<line x1="{tx(origin[0]):.1f}" y1="{ty(origin[1]):.1f}" '
                f'x2="{tx(corner[0]):.1f}" y2="{ty(corner[1]):.1f}" stroke="{color}" stroke-opacity="0.62" stroke-width="1.6"/>'
            )
        far_cycle = far + [far[0]]
        for p0, p1 in zip(far_cycle[:-1], far_cycle[1:]):
            elements.append(
                f'<line x1="{tx(p0[0]):.1f}" y1="{ty(p0[1]):.1f}" x2="{tx(p1[0]):.1f}" y2="{ty(p1[1]):.1f}" '
                f'stroke="{color}" stroke-width="2.2"/>'
            )
        target = cam["target"]
        center_end = [origin[0] + cam["forward_unit"][0] * rig["frustum_distance_m"], origin[1] + cam["forward_unit"][1] * rig["frustum_distance_m"], origin[2]]
        elements.append(
            f'<line x1="{tx(origin[0]):.1f}" y1="{ty(origin[1]):.1f}" x2="{tx(center_end[0]):.1f}" y2="{ty(center_end[1]):.1f}" '
            f'stroke="{color}" stroke-width="3" stroke-dasharray="7,5"/>'
        )

    # Tether/orbit footprint and glider samples.
    if tether > 0.0:
        rx = abs(tx(anchor[0] + tether) - tx(anchor[0]))
        ry = abs(ty(anchor[1] + tether) - ty(anchor[1]))
        elements.append(
            f'<ellipse cx="{tx(anchor[0]):.1f}" cy="{ty(anchor[1]):.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
            'fill="none" stroke="#333" stroke-width="2" stroke-dasharray="8,6"/>'
        )
        # Label details are shown in the near-field SVG and numeric table to avoid collisions in the full-range view.

    for sample in glider_samples:
        p = sample["position"]
        visible_both = all(sample["coverage"].get(cam["name"], False) for cam in cameras)
        fill = "#111111" if visible_both else "#ffffff"
        stroke = "#111111" if visible_both else "#888888"
        r = 4.0 if visible_both else 3.0
        elements.append(f'<circle cx="{tx(p[0]):.1f}" cy="{ty(p[1]):.1f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>')

    # Starting glider marker with velocity/tangent arrow.
    start_p = glider_samples[0]["position"] if glider_samples else [anchor[0] + tether, anchor[1], glider_height]
    start_theta = 0.0
    tangent = [-math.sin(start_theta), math.cos(start_theta)]
    arrow_len = max(0.7, tether * 0.20)
    elements.append(
        f'<polygon points="{tx(start_p[0]):.1f},{ty(start_p[1]):.1f} '
        f'{tx(start_p[0]-0.35):.1f},{ty(start_p[1]-0.22):.1f} '
        f'{tx(start_p[0]-0.35):.1f},{ty(start_p[1]+0.22):.1f}" fill="#ffd21f" stroke="#111" stroke-width="1.2"/>'
    )
    elements.append(
        f'<line x1="{tx(start_p[0]):.1f}" y1="{ty(start_p[1]):.1f}" '
        f'x2="{tx(start_p[0]+tangent[0]*arrow_len):.1f}" y2="{ty(start_p[1]+tangent[1]*arrow_len):.1f}" '
        'stroke="#111" stroke-width="2" marker-end="url(#arrowBlack)"/>'
    )
    elements.append(
        '<defs><marker id="arrowBlack" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#111"/></marker></defs>'
    )
    # The glider marker is shown without text in the full-range view; see near-field SVG for readable labels.

    # Anchor and look-at markers.
    elements.append(f'<circle cx="{tx(anchor[0]):.1f}" cy="{ty(anchor[1]):.1f}" r="7" fill="#222" stroke="white" stroke-width="1.5"/>')
    elements.append(f'<circle cx="{tx(look_at[0]):.1f}" cy="{ty(look_at[1]):.1f}" r="6" fill="#d62728" stroke="white" stroke-width="1.5"/>')

    # Cameras and baseline.
    if len(cameras) >= 2:
        p0 = cameras[0]["position"]
        p1 = cameras[1]["position"]
        elements.append(
            f'<line x1="{tx(p0[0]):.1f}" y1="{ty(p0[1]):.1f}" x2="{tx(p1[0]):.1f}" y2="{ty(p1[1]):.1f}" '
            'stroke="#111" stroke-width="2" stroke-dasharray="5,5"/>'
        )
        midx = (p0[0] + p1[0]) / 2.0
        midy = (p0[1] + p1[1]) / 2.0
        elements.append(
            f'<text x="{tx(midx)+8:.1f}" y="{ty(midy)-8:.1f}" font-size="14" font-family="Arial" font-weight="700">'
            f'baseline b = {rig["baseline_distance_m"]:.3f} m</text>'
        )

    for cam in cameras:
        p = cam["position"]
        color = cam["marker_color_hex"]
        x0, y0 = tx(p[0]), ty(p[1])
        label_y = y0 - 22 if y0 > top_panel["y"] + 45 else y0 + 32
        elements.append(
            f'<rect x="{x0-9:.1f}" y="{y0-9:.1f}" width="18" height="18" transform="rotate(45 {x0:.1f} {y0:.1f})" '
            f'fill="{color}" stroke="#111" stroke-width="1.2"/>'
        )
        elements.append(
            f'<text x="{x0+14:.1f}" y="{label_y:.1f}" font-size="14" font-family="Arial" font-weight="700">{esc(cam["name"])}</text>'
        )
        # Full-range view is often dominated by the far plane; keep coordinates in the numeric table to avoid label collisions.

    # Side-view panel: draw center optical rays and vertical far extents.
    elements.append(f'<line x1="{side_panel["x"]}" y1="{sz_side(0.0):.1f}" x2="{side_panel["x"]+side_panel["w"]}" y2="{sz_side(0.0):.1f}" stroke="#999" stroke-width="1.2"/>')
    elements.append(f'<text x="{side_panel["x"]+10}" y="{sz_side(0.0)-6:.1f}" font-size="12" font-family="Arial" fill="#555">z=0 ground/reference</text>')
    elements.append(f'<line x1="{sx_side(range_from_cam0(anchor)):.1f}" y1="{side_panel["y"]}" x2="{sx_side(range_from_cam0(anchor)):.1f}" y2="{side_panel["y"]+side_panel["h"]}" stroke="#ddd" stroke-width="1"/>')
    elements.append(f'<circle cx="{sx_side(range_from_cam0(anchor)):.1f}" cy="{sz_side(anchor[2]):.1f}" r="5" fill="#222"/>')
    elements.append(f'<circle cx="{sx_side(range_from_cam0(anchor)):.1f}" cy="{sz_side(glider_height):.1f}" r="5" fill="#ffd21f" stroke="#111"/>')
    elements.append(f'<text x="{sx_side(range_from_cam0(anchor))+8:.1f}" y="{sz_side(glider_height)-8:.1f}" font-size="12" font-family="Arial">glider height plane</text>')

    for cam in cameras:
        color = cam["frustum_color_hex"]
        p = cam["position"]
        cx, cz = side_point(p)
        far = cam["far_corners"]
        for corner in far:
            fx, fz = side_point(corner)
            elements.append(f'<line x1="{cx:.1f}" y1="{cz:.1f}" x2="{fx:.1f}" y2="{fz:.1f}" stroke="{color}" stroke-opacity="0.45" stroke-width="1.2"/>')
        target = cam["target"]
        # draw optical axis to frustum distance
        axis_end = [p[i] + cam["forward_unit"][i] * rig["frustum_distance_m"] for i in range(3)]
        ax, az = side_point(axis_end)
        elements.append(f'<line x1="{cx:.1f}" y1="{cz:.1f}" x2="{ax:.1f}" y2="{az:.1f}" stroke="{color}" stroke-width="2.5" stroke-dasharray="6,4"/>')
        elements.append(f'<rect x="{cx-6:.1f}" y="{cz-6:.1f}" width="12" height="12" fill="{cam["marker_color_hex"]}" stroke="#111"/>')
        elements.append(f'<text x="{cx+8:.1f}" y="{cz-8:.1f}" font-size="12" font-family="Arial">{esc(cam["name"])}</text>')

    # Numeric diagnostics table.
    rows = [
        ("camera_main", fmt3(cameras[0]["position"]), "m"),
        ("camera_secondary", fmt3(cameras[1]["position"]), "m") if len(cameras) > 1 else ("camera_secondary", "missing", ""),
        ("baseline b", f'{rig["baseline_distance_m"]:.3f}', "m"),
        ("baseline vector", fmt3(rig["baseline_vector_m"]), "m"),
        ("HFOV / VFOV", f'{rig["horizontal_fov_deg"]:.2f} / {rig["vertical_fov_deg"]:.2f}', "deg"),
        ("focal length", f'{rig["focal_length_mm"]:.3f}', "mm"),
        ("aperture", f'{rig["horizontal_aperture_mm"]:.3f}', "mm"),
        ("frustum distance", f'{rig["frustum_distance_m"]:.3f}', "m"),
        ("anchor", fmt3(anchor), "m"),
        ("look-at", fmt3(look_at), "m"),
        ("tether radius", f'{tether:.3f}', "m"),
        ("both-cam orbit coverage", f'{diagnostics["overlap"]["orbit_samples_visible_in_both_cameras"]}/{diagnostics["overlap"]["orbit_samples_total"]}', "samples"),
    ]

    x = table_panel["x"] + 16
    y = table_panel["y"] + 52
    row_step = 23
    for i, (name, value, unit) in enumerate(rows):
        yy = y + i * row_step
        fill = "#f5f5f5" if i % 2 == 0 else "#ffffff"
        elements.append(f'<rect x="{table_panel["x"]+8}" y="{yy-17}" width="{table_panel["w"]-16}" height="23" fill="{fill}"/>')
        elements.append(f'<text x="{x}" y="{yy}" font-size="12.5" font-family="Consolas, monospace" fill="#333">{esc(name)}</text>')
        elements.append(f'<text x="{x+205}" y="{yy}" font-size="12.5" font-family="Consolas, monospace" fill="#111">{esc(value)}</text>')
        elements.append(f'<text x="{x+450}" y="{yy}" font-size="12.5" font-family="Consolas, monospace" fill="#555">{esc(unit)}</text>')

    # Coverage status chips.
    cy = y + len(rows) * row_step + 22
    for idx, cam in enumerate(cameras):
        status = "OK" if cam["anchor_coverage"]["inside"] else "OUT"
        color = "#228B22" if status == "OK" else "#b00020"
        elements.append(f'<text x="{table_panel["x"]+16}" y="{cy+idx*20}" font-size="13" font-family="Arial" fill="{color}">{esc(cam["name"])} anchor in FOV: {status}; orbit samples: {cam["glider_orbit_samples_inside"]}/{cam["glider_orbit_samples_total"]}</text>')

    # Notes panel.
    elements.append(
        f'<rect x="{notes_panel["x"]}" y="{notes_panel["y"]}" width="{notes_panel["w"]}" height="{notes_panel["h"]}" fill="#fff" stroke="#c9c9c9"/>'
    )
    notes = [
        "How to read this diagram:",
        "1. The filled camera polygons are XY projections of the full 3D pinhole frustum at the configured frustum distance, not arbitrary triangles.",
        "2. The dashed circle is the tether/orbit footprint. Filled black orbit samples are inside both cameras; white samples are outside at least one camera.",
        "3. If the RGB images show the anchor/glider but this diagram says OUT, the camera model assumptions are inconsistent and camera_rig_metadata.json must be inspected.",
        "4. Renderable in-scene frustum lines are only viewport debug geometry. Keep them disabled for clean RGB datasets.",
    ]
    for i, note in enumerate(notes):
        elements.append(f'<text x="{notes_panel["x"]+18}" y="{notes_panel["y"]+34+i*28}" font-size="15" font-family="Arial" fill="#333">{esc(note)}</text>')

    # Legend.
    lx = notes_panel["x"] + 18
    ly = notes_panel["y"] + 185
    legend = [
        ("#222", "anchor / orbit center"),
        ("#d62728", "look-at/reference point"),
        ("#ffd21f", "glider at t=0"),
        ("#111", "orbit sample visible in both cameras"),
        ("#fff", "orbit sample not visible in at least one camera"),
    ]
    for i, (color, label) in enumerate(legend):
        xx = lx + i * 310
        elements.append(f'<circle cx="{xx}" cy="{ly}" r="7" fill="{color}" stroke="#111"/>')
        elements.append(f'<text x="{xx+14}" y="{ly+5}" font-size="13" font-family="Arial" fill="#333">{esc(label)}</text>')

    elements.append('</svg>')
    return "\n".join(elements)


def build_camera_nearfield_layout_svg(diagnostics: dict[str, Any]) -> str:
    """Build a near-field top-down SVG centered on the anchor/orbit.

    The full frustum sheet can become visually dominated by a large far-plane
    distance such as 200 m. This near-field drawing deliberately ignores the far
    end of the frustum and clips the rays to the local region around cameras,
    anchor, look-at point, and tether orbit. It answers: do the cameras cover the
    glider/anchor region now?
    """

    def esc(text: Any) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def fmt3(values: list[float]) -> str:
        return f"({values[0]:.3f}, {values[1]:.3f}, {values[2]:.3f})"

    scene = diagnostics["scene"]
    rig = diagnostics["camera_rig"]
    cameras = diagnostics["cameras"]
    glider_samples = diagnostics["glider_samples"]
    anchor = scene["anchor_position"]
    look_at = scene["look_at"]
    tether = float(scene["tether_length_m"])

    width = 1400
    height = 950
    panel_x, panel_y, panel_w, panel_h = 60.0, 120.0, 1280.0, 720.0

    # Choose a local ray length large enough to pass through the complete orbit,
    # but not the full far-plane distance. This makes the local geometry readable.
    max_cam_anchor = 0.0
    for cam in cameras:
        dx = float(cam["position"][0]) - float(anchor[0])
        dy = float(cam["position"][1]) - float(anchor[1])
        max_cam_anchor = max(max_cam_anchor, math.sqrt(dx * dx + dy * dy))
    local_ray_distance = min(
        max(float(rig.get("frustum_distance_m", 20.0)), 0.1),
        max(12.0, max_cam_anchor + tether + 6.0),
    )

    points: list[list[float]] = [[anchor[0], anchor[1]], [look_at[0], look_at[1]]]
    if tether > 0.0:
        for k in range(96):
            theta = 2.0 * math.pi * k / 96.0
            points.append([anchor[0] + tether * math.cos(theta), anchor[1] + tether * math.sin(theta)])
    for cam in cameras:
        points.append([cam["position"][0], cam["position"][1]])
        origin = cam["position"]
        for corner in cam["far_corners"]:
            direction = [corner[i] - origin[i] for i in range(3)]
            n = math.sqrt(direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2)
            if n < 1e-9:
                continue
            endpoint = [origin[i] + direction[i] / n * local_ray_distance for i in range(3)]
            points.append([endpoint[0], endpoint[1]])
    for sample in glider_samples:
        points.append([sample["position"][0], sample["position"][1]])

    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    margin = 0.12 * span
    min_x -= margin
    max_x += margin
    min_y -= margin
    max_y += margin

    # Preserve metric aspect ratio.
    data_w = max_x - min_x
    data_h = max_y - min_y
    panel_ratio = panel_w / panel_h
    data_ratio = data_w / max(data_h, 1e-9)
    if data_ratio > panel_ratio:
        target_h = data_w / panel_ratio
        extra = (target_h - data_h) / 2.0
        min_y -= extra
        max_y += extra
    else:
        target_w = data_h * panel_ratio
        extra = (target_w - data_w) / 2.0
        min_x -= extra
        max_x += extra

    def sx(x: float) -> float:
        return panel_x + (x - min_x) / max(max_x - min_x, 1e-9) * panel_w

    def sy(y: float) -> float:
        return panel_y + panel_h - (y - min_y) / max(max_y - min_y, 1e-9) * panel_h

    def poly(points_xy: list[list[float]]) -> str:
        return " ".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in points_xy)

    elements: list[str] = []
    elements.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="background:white">')
    elements.append('<rect width="100%" height="100%" fill="white"/>')
    elements.append('<text x="60" y="45" font-size="26" font-family="Arial" font-weight="700">Stereo camera near-field layout</text>')
    elements.append(
        f'<text x="60" y="76" font-size="14" font-family="Arial" fill="#555">Scene={esc(diagnostics["scene_name"])} | '
        f'local ray distance={local_ray_distance:.2f} m | baseline={float(rig["baseline_distance_m"]):.3f} m | '
        f'HFOV={float(rig["horizontal_fov_deg"]):.2f}° | VFOV={float(rig["vertical_fov_deg"]):.2f}°</text>'
    )
    elements.append(f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" fill="#fbfbfb" stroke="#c9c9c9"/>')

    # Grid and world axes.
    extent = max(max_x - min_x, max_y - min_y)
    grid_step = 2.0 if extent <= 25 else 5.0 if extent <= 70 else 10.0
    gx = math.floor(min_x / grid_step) * grid_step
    while gx <= max_x:
        color = "#cc6666" if abs(gx) < 1e-9 else "#e7e7e7"
        sw = "2" if abs(gx) < 1e-9 else "1"
        elements.append(f'<line x1="{sx(gx):.1f}" y1="{panel_y}" x2="{sx(gx):.1f}" y2="{panel_y+panel_h}" stroke="{color}" stroke-width="{sw}"/>')
        gx += grid_step
    gy = math.floor(min_y / grid_step) * grid_step
    while gy <= max_y:
        color = "#66aa66" if abs(gy) < 1e-9 else "#e7e7e7"
        sw = "2" if abs(gy) < 1e-9 else "1"
        elements.append(f'<line x1="{panel_x}" y1="{sy(gy):.1f}" x2="{panel_x+panel_w}" y2="{sy(gy):.1f}" stroke="{color}" stroke-width="{sw}"/>')
        gy += grid_step

    # Frustum local wedges.
    for cam in cameras:
        origin = cam["position"]
        local_endpoints = []
        for corner in cam["far_corners"]:
            direction = [corner[i] - origin[i] for i in range(3)]
            n = math.sqrt(direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2)
            if n < 1e-9:
                continue
            endpoint = [origin[i] + direction[i] / n * local_ray_distance for i in range(3)]
            local_endpoints.append([endpoint[0], endpoint[1]])
        wedge_points = [[origin[0], origin[1]]] + local_endpoints
        color = cam["frustum_color_hex"]
        elements.append(f'<polygon points="{poly(wedge_points)}" fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="2.2"/>')
        for endpoint in local_endpoints:
            elements.append(f'<line x1="{sx(origin[0]):.1f}" y1="{sy(origin[1]):.1f}" x2="{sx(endpoint[0]):.1f}" y2="{sy(endpoint[1]):.1f}" stroke="{color}" stroke-opacity="0.65"/>')
        axis_end = [origin[0] + cam["forward_unit"][0] * local_ray_distance, origin[1] + cam["forward_unit"][1] * local_ray_distance]
        elements.append(f'<line x1="{sx(origin[0]):.1f}" y1="{sy(origin[1]):.1f}" x2="{sx(axis_end[0]):.1f}" y2="{sy(axis_end[1]):.1f}" stroke="{color}" stroke-width="3" stroke-dasharray="7,5"/>')

    # Orbit and samples.
    if tether > 0.0:
        rx = abs(sx(anchor[0] + tether) - sx(anchor[0]))
        ry = abs(sy(anchor[1] + tether) - sy(anchor[1]))
        elements.append(f'<ellipse cx="{sx(anchor[0]):.1f}" cy="{sy(anchor[1]):.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="none" stroke="#333" stroke-dasharray="8,6" stroke-width="2"/>')
    for sample in glider_samples:
        p = sample["position"]
        visible_both = all(sample["coverage"].get(cam["name"], False) for cam in cameras)
        fill = "#111" if visible_both else "#fff"
        elements.append(f'<circle cx="{sx(p[0]):.1f}" cy="{sy(p[1]):.1f}" r="4" fill="{fill}" stroke="#111"/>')

    # Marker labels.
    elements.append(f'<circle cx="{sx(anchor[0]):.1f}" cy="{sy(anchor[1]):.1f}" r="7" fill="#222" stroke="white" stroke-width="1.5"/>')
    elements.append(f'<text x="{sx(anchor[0])+10:.1f}" y="{sy(anchor[1])-10:.1f}" font-size="14" font-family="Arial">Anchor {esc(fmt3(anchor))}</text>')
    elements.append(f'<circle cx="{sx(look_at[0]):.1f}" cy="{sy(look_at[1]):.1f}" r="6" fill="#d62728" stroke="white" stroke-width="1.5"/>')
    elements.append(f'<text x="{sx(look_at[0])+10:.1f}" y="{sy(look_at[1])+18:.1f}" font-size="14" font-family="Arial" fill="#b00000">Look-at {esc(fmt3(look_at))}</text>')

    if len(cameras) >= 2:
        p0 = cameras[0]["position"]
        p1 = cameras[1]["position"]
        elements.append(f'<line x1="{sx(p0[0]):.1f}" y1="{sy(p0[1]):.1f}" x2="{sx(p1[0]):.1f}" y2="{sy(p1[1]):.1f}" stroke="#111" stroke-dasharray="5,5" stroke-width="2"/>')
        elements.append(f'<text x="{sx((p0[0]+p1[0])/2)+8:.1f}" y="{sy((p0[1]+p1[1])/2)-8:.1f}" font-size="14" font-family="Arial" font-weight="700">b={float(rig["baseline_distance_m"]):.3f} m</text>')

    for cam in cameras:
        p = cam["position"]
        x0, y0 = sx(p[0]), sy(p[1])
        elements.append(f'<rect x="{x0-9:.1f}" y="{y0-9:.1f}" width="18" height="18" transform="rotate(45 {x0:.1f} {y0:.1f})" fill="{cam["marker_color_hex"]}" stroke="#111"/>')
        elements.append(f'<text x="{x0+14:.1f}" y="{y0-12:.1f}" font-size="14" font-family="Arial" font-weight="700">{esc(cam["name"])}</text>')
        status = "anchor OK" if cam["anchor_coverage"]["inside"] else "anchor OUT"
        status_color = "#228B22" if cam["anchor_coverage"]["inside"] else "#b00020"
        elements.append(f'<text x="{x0+14:.1f}" y="{y0+6:.1f}" font-size="12" font-family="Arial" fill="{status_color}">{status}; orbit {cam["glider_orbit_samples_inside"]}/{cam["glider_orbit_samples_total"]}</text>')

    # Axis inset.
    ax0 = panel_x + 60
    ay0 = panel_y + panel_h - 55
    elements.append(f'<line x1="{ax0}" y1="{ay0}" x2="{ax0+55}" y2="{ay0}" stroke="#cc3333" stroke-width="4"/>')
    elements.append(f'<line x1="{ax0}" y1="{ay0}" x2="{ax0}" y2="{ay0-55}" stroke="#33aa33" stroke-width="4"/>')
    elements.append(f'<text x="{ax0+62}" y="{ay0+5}" font-size="14" font-family="Arial" fill="#cc3333">+X</text>')
    elements.append(f'<text x="{ax0-12}" y="{ay0-62}" font-size="14" font-family="Arial" fill="#33aa33">+Y</text>')

    elements.append('<text x="60" y="885" font-size="14" font-family="Arial" fill="#555">Near-field view is intentionally clipped to the cameras/anchor/orbit region. Use camera_rig_layout.svg for the full far-plane footprint.</text>')
    elements.append('</svg>')
    return "\n".join(elements)

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
            "marker_color": main_camera.get("marker_color", [0.05, 0.25, 0.95]),
            "frustum_color": main_camera.get("frustum_color", main_camera.get("marker_color", [0.05, 0.25, 0.95])),
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
            "marker_color": secondary_camera.get("marker_color", [0.95, 0.45, 0.05]),
            "frustum_color": secondary_camera.get("frustum_color", secondary_camera.get("marker_color", [0.95, 0.45, 0.05])),
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

    main_color = tuple(float(value) for value in camera_defs[0].get("marker_color", [0.05, 0.25, 0.95]))
    secondary_color = tuple(float(value) for value in camera_defs[1].get("marker_color", [0.95, 0.45, 0.05]))

    main_material = make_material(
        stage,
        "/World/Materials/MainCameraMarkerMat",
        color=main_color,
        roughness=0.45,
        metallic=0.0,
    )

    secondary_material = make_material(
        stage,
        "/World/Materials/SecondaryCameraMarkerMat",
        color=secondary_color,
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

    main_frustum_color = tuple(float(value) for value in camera_defs[0].get("frustum_color", camera_defs[0].get("marker_color", [0.05, 0.25, 0.95])))
    secondary_frustum_color = tuple(float(value) for value in camera_defs[1].get("frustum_color", camera_defs[1].get("marker_color", [0.95, 0.45, 0.05])))

    main_frustum_material = make_material(
        stage,
        "/World/Materials/MainCameraFrustumMat",
        color=main_frustum_color,
        roughness=0.5,
        metallic=0.0,
    )

    secondary_frustum_material = make_material(
        stage,
        "/World/Materials/SecondaryCameraFrustumMat",
        color=secondary_frustum_color,
        roughness=0.5,
        metallic=0.0,
    )

    marker_materials = [main_material, secondary_material]
    frustum_materials = [main_frustum_material, secondary_frustum_material]

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

    if bool(camera_rig_cfg.get("show_frustums", False)):
        create_camera_frustum_overlays(
            stage=stage,
            camera_rig_cfg=camera_rig_cfg,
            camera_defs=camera_defs,
            materials=frustum_materials,
        )

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


def normalize_vec3(vec: list[float] | tuple[float, float, float]) -> list[float]:
    norm = math.sqrt(float(vec[0]) ** 2 + float(vec[1]) ** 2 + float(vec[2]) ** 2)
    if norm < 1e-12:
        return [0.0, 0.0, 0.0]
    return [float(vec[0]) / norm, float(vec[1]) / norm, float(vec[2]) / norm]


def cross_vec3(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def create_camera_frustum_overlays(
    stage: Usd.Stage,
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
    materials: list[UsdShade.Material],
) -> None:
    """Create renderable camera-frustum debug wireframes.

    These are deliberately renderable because they are viewport/debug objects.
    Keep camera_rig.show_frustums=false for clean RGB capture datasets.
    """

    UsdGeom.Xform.Define(stage, "/World/CameraFrustums")

    resolution = camera_rig_cfg.get("resolution", [1280, 720])
    aspect = float(resolution[0]) / max(float(resolution[1]), 1.0)
    horizontal_fov_rad = math.radians(float(camera_rig_cfg.get("horizontal_fov_deg", 33.332)))
    far_distance = float(camera_rig_cfg.get("frustum_distance_m", 4.0))
    far_distance = max(far_distance, 0.1)
    line_width = float(camera_rig_cfg.get("frustum_line_width_m", 0.025))
    line_width = max(line_width, 0.001)

    half_width = math.tan(horizontal_fov_rad / 2.0) * far_distance
    half_height = half_width / max(aspect, 1e-6)

    for index, camera_def in enumerate(camera_defs):
        origin = [float(value) for value in camera_def["position"]]
        if str(camera_rig_cfg.get("orientation_mode", "parallel_manual")) == "look_at_target":
            target = [float(value) for value in camera_rig_cfg.get("look_at", [0.0, 0.0, 1.5])]
        else:
            target = [float(value) for value in camera_def.get("parallel_look_at", [origin[0] + 1.0, origin[1], origin[2]])]

        forward = normalize_vec3([target[0] - origin[0], target[1] - origin[1], target[2] - origin[2]])
        world_up = [0.0, 0.0, 1.0]
        right = normalize_vec3(cross_vec3(forward, world_up))
        if abs(right[0]) + abs(right[1]) + abs(right[2]) < 1e-9:
            right = [1.0, 0.0, 0.0]
        up = normalize_vec3(cross_vec3(right, forward))

        center = [origin[i] + forward[i] * far_distance for i in range(3)]
        corners = [
            [center[i] - right[i] * half_width + up[i] * half_height for i in range(3)],
            [center[i] + right[i] * half_width + up[i] * half_height for i in range(3)],
            [center[i] + right[i] * half_width - up[i] * half_height for i in range(3)],
            [center[i] - right[i] * half_width - up[i] * half_height for i in range(3)],
        ]

        base_path = f"/World/CameraFrustums/{camera_def['name']}"
        UsdGeom.Xform.Define(stage, base_path)
        material = materials[index]
        create_polyline_curve(stage, f"{base_path}/FarRectangle", corners + [corners[0]], material, width=line_width)
        for corner_index, corner in enumerate(corners):
            create_polyline_curve(stage, f"{base_path}/Ray{corner_index}", [origin, corner], material, width=line_width)


def create_polyline_curve(
    stage: Usd.Stage,
    path: str,
    points: list[list[float]],
    material: UsdShade.Material,
    width: float = 0.02,
) -> None:
    curve = UsdGeom.BasisCurves.Define(stage, path)
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr([len(points)])
    curve.CreatePointsAttr([Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])) for p in points])
    curve.CreateWidthsAttr([float(width)] * len(points))
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


# -----------------------------------------------------------------------------
# v20 override: camera layout diagnostics must not mix XY plan geometry with the
# projected vertical extent of the 3-D camera pyramid.  The functions below
# intentionally override the v19 definitions above.  They generate separate,
# explicit views:
#   - Pure XY plan view: horizontal FOV only, no vertical far-plane rectangle.
#   - Pure elevation view: depth-versus-Z vertical FOV only.
# This avoids the misleading 2-D/3-D hybrid plot from v19.
# -----------------------------------------------------------------------------


def write_camera_rig_layout_files(
    output_dir: Path,
    scene_config: dict[str, Any],
    camera_rig_cfg: dict[str, Any],
    camera_defs: list[dict[str, Any]],
) -> None:
    """Write camera layout diagnostics with separated plan/elevation geometry."""

    diagnostics = build_camera_layout_diagnostics(scene_config, camera_rig_cfg, camera_defs)

    with open(output_dir / "camera_rig_layout.json", "w", encoding="utf-8") as file:
        json.dump(diagnostics, file, indent=2)

    plan_roi_svg = build_camera_layout_plan_svg(diagnostics, view_mode="roi")
    plan_full_svg = build_camera_layout_plan_svg(diagnostics, view_mode="full")
    elevation_svg = build_camera_layout_elevation_svg(diagnostics)

    # Primary filename kept for backward compatibility.  It is now the clean
    # pure top-down XY plan view, not the old 3-D-projected far-plane layout.
    (output_dir / "camera_rig_layout.svg").write_text(plan_roi_svg, encoding="utf-8")
    (output_dir / "camera_rig_layout_plan_roi.svg").write_text(plan_roi_svg, encoding="utf-8")
    (output_dir / "camera_rig_layout_plan_full.svg").write_text(plan_full_svg, encoding="utf-8")
    (output_dir / "camera_rig_layout_elevation.svg").write_text(elevation_svg, encoding="utf-8")

    # Backward-compatible alias used by older dashboard versions.
    (output_dir / "camera_rig_layout_nearfield.svg").write_text(plan_roi_svg, encoding="utf-8")

    summary_rows = diagnostics.get("summary_rows", [])
    if summary_rows:
        with open(output_dir / "camera_rig_layout_summary.csv", "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["quantity", "value", "unit"])
            writer.writeheader()
            writer.writerows(summary_rows)


def build_camera_layout_plan_svg(diagnostics: dict[str, Any], view_mode: str = "roi") -> str:
    """Build a pure top-down world-XY plan-view SVG.

    This diagram uses only horizontal FOV in the XY plane.  It deliberately does
    not draw the 3-D far-plane rectangle or vertical-FOV corner rays, because
    projecting those into XY creates the misleading box/parallelogram seen in
    v19.
    """

    def esc(text: Any) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def fmt3(values: list[float]) -> str:
        return f"({values[0]:.3f}, {values[1]:.3f}, {values[2]:.3f})"

    def norm2(v: list[float]) -> float:
        return math.sqrt(v[0] * v[0] + v[1] * v[1])

    def unit2(v: list[float], fallback: list[float] | None = None) -> list[float]:
        n = norm2(v)
        if n < 1e-12:
            return list(fallback or [1.0, 0.0])
        return [v[0] / n, v[1] / n]

    def rotate2(v: list[float], angle_deg: float) -> list[float]:
        a = math.radians(angle_deg)
        c = math.cos(a)
        s = math.sin(a)
        return [c * v[0] - s * v[1], s * v[0] + c * v[1]]

    def add2(a: list[float], b: list[float]) -> list[float]:
        return [a[0] + b[0], a[1] + b[1]]

    def scale2(a: list[float], s: float) -> list[float]:
        return [a[0] * s, a[1] * s]

    def dist2(a: list[float], b: list[float]) -> float:
        return norm2([a[0] - b[0], a[1] - b[1]])

    scene = diagnostics["scene"]
    rig = diagnostics["camera_rig"]
    cameras = diagnostics["cameras"]
    glider_samples = diagnostics["glider_samples"]

    anchor = scene["anchor_position"]
    look_at = scene["look_at"]
    tether = float(scene["tether_length_m"])
    hfov_deg = float(rig["horizontal_fov_deg"])
    configured_distance = max(float(rig["frustum_distance_m"]), 0.1)

    # Compute a readable ROI ray length.  It must cover cameras + orbit region,
    # but need not extend to 200 m if that would destroy the local geometry view.
    orbit_xy = [[sample["position"][0], sample["position"][1]] for sample in glider_samples]
    orbit_xy.append([anchor[0], anchor[1]])
    orbit_xy.append([look_at[0], look_at[1]])
    max_cam_to_roi = 1.0
    for cam in cameras:
        cxy = [cam["position"][0], cam["position"][1]]
        for pxy in orbit_xy:
            max_cam_to_roi = max(max_cam_to_roi, dist2(cxy, pxy))
    roi_distance = max_cam_to_roi * 1.35
    plot_distance = configured_distance if view_mode == "full" else min(configured_distance, roi_distance)
    plot_distance = max(plot_distance, 1.0)

    # Top-down horizontal wedges.
    camera_plan: list[dict[str, Any]] = []
    for cam in cameras:
        origin = [float(cam["position"][0]), float(cam["position"][1])]
        forward_xy = unit2([float(cam["forward_unit"][0]), float(cam["forward_unit"][1])], [0.0, -1.0])
        left_dir = rotate2(forward_xy, hfov_deg / 2.0)
        right_dir = rotate2(forward_xy, -hfov_deg / 2.0)
        axis_end = add2(origin, scale2(forward_xy, plot_distance))
        left_end = add2(origin, scale2(left_dir, plot_distance))
        right_end = add2(origin, scale2(right_dir, plot_distance))
        heading_deg = math.degrees(math.atan2(forward_xy[1], forward_xy[0]))
        base_midpoint = [(left_end[0] + right_end[0]) / 2.0, (left_end[1] + right_end[1]) / 2.0]
        triangle_height = dist2(origin, base_midpoint)
        anchor_dx = float(anchor[0] - origin[0])
        anchor_dy = float(anchor[1] - origin[1])
        anchor_dz = float(anchor[2] - cam["position"][2])
        anchor_distance_xy = math.sqrt(anchor_dx * anchor_dx + anchor_dy * anchor_dy)
        anchor_distance_euclidean = math.sqrt(anchor_dx * anchor_dx + anchor_dy * anchor_dy + anchor_dz * anchor_dz)
        camera_plan.append(
            {
                "source": cam,
                "origin": origin,
                "forward_xy": forward_xy,
                "axis_end": axis_end,
                "left_end": left_end,
                "right_end": right_end,
                "base_midpoint": base_midpoint,
                "triangle_height_m": triangle_height,
                "heading_deg": heading_deg,
                "anchor_dx_m": anchor_dx,
                "anchor_dy_m": anchor_dy,
                "anchor_dz_m": anchor_dz,
                "anchor_distance_xy_m": anchor_distance_xy,
                "anchor_distance_euclidean_m": anchor_distance_euclidean,
            }
        )

    width = 1800
    height = 1180
    plot = {"x": 70.0, "y": 135.0, "w": 1180.0, "h": 870.0}
    info = {"x": 1285.0, "y": 135.0, "w": 445.0, "h": 870.0}

    points: list[list[float]] = [[anchor[0], anchor[1]], [look_at[0], look_at[1]]]
    if tether > 0.0:
        for k in range(160):
            theta = 2.0 * math.pi * k / 160.0
            points.append([anchor[0] + tether * math.cos(theta), anchor[1] + tether * math.sin(theta)])
    for sample in glider_samples:
        points.append([sample["position"][0], sample["position"][1]])
    for item in camera_plan:
        points.extend([item["origin"], item["left_end"], item["right_end"], item["axis_end"]])

    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    margin = 0.12 * span
    min_x -= margin
    max_x += margin
    min_y -= margin
    max_y += margin

    # Preserve metric aspect ratio.
    data_w = max_x - min_x
    data_h = max_y - min_y
    panel_ratio = plot["w"] / plot["h"]
    data_ratio = data_w / max(data_h, 1e-9)
    if data_ratio > panel_ratio:
        target_h = data_w / panel_ratio
        extra = (target_h - data_h) / 2.0
        min_y -= extra
        max_y += extra
    else:
        target_w = data_h * panel_ratio
        extra = (target_w - data_w) / 2.0
        min_x -= extra
        max_x += extra

    def sx(x: float) -> float:
        return plot["x"] + (x - min_x) / max(max_x - min_x, 1e-9) * plot["w"]

    def sy(y: float) -> float:
        return plot["y"] + plot["h"] - (y - min_y) / max(max_y - min_y, 1e-9) * plot["h"]

    def poly(points_xy: list[list[float]]) -> str:
        return " ".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in points_xy)

    extent = max(max_x - min_x, max_y - min_y)
    if extent <= 25:
        grid_step = 2.0
    elif extent <= 60:
        grid_step = 5.0
    elif extent <= 150:
        grid_step = 10.0
    elif extent <= 350:
        grid_step = 25.0
    else:
        grid_step = 50.0

    elements: list[str] = []
    elements.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="background:white">')
    elements.append('<rect width="100%" height="100%" fill="white"/>')
    elements.append('<defs>')
    elements.append('<marker id="arrowX" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#c62828"/></marker>')
    elements.append('<marker id="arrowY" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#2e7d32"/></marker>')
    elements.append('</defs>')
    elements.append('<text x="70" y="48" font-size="28" font-family="Arial" font-weight="700">Stereo camera plan-view diagnostic</text>')
    mode_label = "full configured frustum distance" if view_mode == "full" else "region of interest, rays clipped for readability"
    elements.append(
        f'<text x="70" y="78" font-size="15" font-family="Arial" fill="#555">Pure world XY top-down view. Uses horizontal FOV only; vertical FOV is intentionally excluded. View mode: {esc(mode_label)}.</text>'
    )
    elements.append(
        f'<text x="70" y="103" font-size="14" font-family="Arial" fill="#777">Scene={esc(diagnostics["scene_name"])} | HFOV={hfov_deg:.2f}° | plot ray distance={plot_distance:.2f} m | configured frustum distance={configured_distance:.2f} m | baseline={float(rig["baseline_distance_m"]):.3f} m</text>'
    )

    elements.append(f'<rect x="{plot["x"]}" y="{plot["y"]}" width="{plot["w"]}" height="{plot["h"]}" fill="#fbfbfb" stroke="#bdbdbd"/>')
    elements.append(f'<rect x="{info["x"]}" y="{info["y"]}" width="{info["w"]}" height="{info["h"]}" fill="#fbfbfb" stroke="#bdbdbd"/>')
    elements.append(f'<text x="{info["x"] + 18}" y="{info["y"] + 32}" font-size="19" font-family="Arial" font-weight="700">Numeric details</text>')

    # Grid.
    gx = math.floor(min_x / grid_step) * grid_step
    while gx <= max_x + 1e-9:
        color = "#c62828" if abs(gx) < 1e-9 else "#e5e5e5"
        sw = "1.8" if abs(gx) < 1e-9 else "1"
        elements.append(f'<line x1="{sx(gx):.1f}" y1="{plot["y"]:.1f}" x2="{sx(gx):.1f}" y2="{plot["y"] + plot["h"]:.1f}" stroke="{color}" stroke-width="{sw}" opacity="0.8"/>')
        gx += grid_step
    gy = math.floor(min_y / grid_step) * grid_step
    while gy <= max_y + 1e-9:
        color = "#2e7d32" if abs(gy) < 1e-9 else "#e5e5e5"
        sw = "1.8" if abs(gy) < 1e-9 else "1"
        elements.append(f'<line x1="{plot["x"]:.1f}" y1="{sy(gy):.1f}" x2="{plot["x"] + plot["w"]:.1f}" y2="{sy(gy):.1f}" stroke="{color}" stroke-width="{sw}" opacity="0.8"/>')
        gy += grid_step

    # Frustum wedges: pure XY horizontal field of view.
    for item in camera_plan:
        cam = item["source"]
        color = cam["frustum_color_hex"]
        wedge = [item["origin"], item["left_end"], item["right_end"]]
        elements.append(f'<polygon points="{poly(wedge)}" fill="{color}" fill-opacity="0.13" stroke="{color}" stroke-width="2.8"/>')
        elements.append(f'<line x1="{sx(item["origin"][0]):.1f}" y1="{sy(item["origin"][1]):.1f}" x2="{sx(item["axis_end"][0]):.1f}" y2="{sy(item["axis_end"][1]):.1f}" stroke="{color}" stroke-width="3" stroke-dasharray="8,5"/>')
        elements.append(f'<line x1="{sx(item["left_end"][0]):.1f}" y1="{sy(item["left_end"][1]):.1f}" x2="{sx(item["right_end"][0]):.1f}" y2="{sy(item["right_end"][1]):.1f}" stroke="{color}" stroke-width="2" stroke-opacity="0.7"/>')
        elements.append(f'<line x1="{sx(item["origin"][0]):.1f}" y1="{sy(item["origin"][1]):.1f}" x2="{sx(item["base_midpoint"][0]):.1f}" y2="{sy(item["base_midpoint"][1]):.1f}" stroke="{color}" stroke-width="2" stroke-dasharray="4,4" stroke-opacity="0.95"/>')
        h_label_x = (sx(item["origin"][0]) + sx(item["base_midpoint"][0])) / 2.0 + 6.0
        h_label_y = (sy(item["origin"][1]) + sy(item["base_midpoint"][1])) / 2.0 - 6.0
        elements.append(f'<text x="{h_label_x:.1f}" y="{h_label_y:.1f}" font-size="12" font-family="Arial" fill="{color}" font-weight="700">h_tri={item["triangle_height_m"]:.2f} m</text>')

    # Tether/orbit footprint.
    if tether > 0.0:
        rx = abs(sx(anchor[0] + tether) - sx(anchor[0]))
        ry = abs(sy(anchor[1] + tether) - sy(anchor[1]))
        elements.append(f'<ellipse cx="{sx(anchor[0]):.1f}" cy="{sy(anchor[1]):.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="none" stroke="#222" stroke-width="2.3" stroke-dasharray="8,6"/>')
        elements.append(f'<text x="{sx(anchor[0] + tether) + 10:.1f}" y="{sy(anchor[1]) - 10:.1f}" font-size="13" font-family="Arial" fill="#222">glider orbit r={tether:.2f} m</text>')

    # Sampled glider positions.
    for sample in glider_samples:
        p = sample["position"]
        visible_both = all(sample["coverage"].get(cam["name"], False) for cam in cameras)
        fill = "#111" if visible_both else "white"
        stroke = "#111" if visible_both else "#777"
        elements.append(f'<circle cx="{sx(p[0]):.1f}" cy="{sy(p[1]):.1f}" r="4.6" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')

    # Explicit glider initial marker.
    if glider_samples:
        gp = glider_samples[0]["position"]
        elements.append(f'<circle cx="{sx(gp[0]):.1f}" cy="{sy(gp[1]):.1f}" r="8" fill="#ffeb3b" stroke="#111" stroke-width="2"/>')
        elements.append(f'<text x="{sx(gp[0]) + 10:.1f}" y="{sy(gp[1]) + 4:.1f}" font-size="14" font-family="Arial" font-weight="700">glider start</text>')

    # Anchor and look-at.
    elements.append(f'<circle cx="{sx(anchor[0]):.1f}" cy="{sy(anchor[1]):.1f}" r="7" fill="#d62728" stroke="white" stroke-width="2"/>')
    elements.append(f'<text x="{sx(anchor[0]) + 10:.1f}" y="{sy(anchor[1]) - 10:.1f}" font-size="14" font-family="Arial" font-weight="700">Anchor {esc(fmt3(anchor))}</text>')
    elements.append(f'<circle cx="{sx(look_at[0]):.1f}" cy="{sy(look_at[1]):.1f}" r="6" fill="#111" stroke="white" stroke-width="1.5"/>')
    elements.append(f'<text x="{sx(look_at[0]) + 10:.1f}" y="{sy(look_at[1]) + 20:.1f}" font-size="14" font-family="Arial">Look-at {esc(fmt3(look_at))}</text>')

    # Baseline and camera markers.
    if len(cameras) >= 2:
        p0 = cameras[0]["position"]
        p1 = cameras[1]["position"]
        mx = (p0[0] + p1[0]) / 2.0
        my = (p0[1] + p1[1]) / 2.0
        elements.append(f'<line x1="{sx(p0[0]):.1f}" y1="{sy(p0[1]):.1f}" x2="{sx(p1[0]):.1f}" y2="{sy(p1[1]):.1f}" stroke="#111" stroke-width="2.2" stroke-dasharray="5,5"/>')
        elements.append(f'<text x="{sx(mx) + 8:.1f}" y="{sy(my) - 10:.1f}" font-size="15" font-family="Arial" font-weight="700">baseline b={float(rig["baseline_distance_m"]):.3f} m</text>')

    for item in camera_plan:
        cam = item["source"]
        p = cam["position"]
        x0 = sx(p[0])
        y0 = sy(p[1])
        elements.append(f'<rect x="{x0 - 9:.1f}" y="{y0 - 9:.1f}" width="18" height="18" transform="rotate(45 {x0:.1f} {y0:.1f})" fill="{cam["marker_color_hex"]}" stroke="#111" stroke-width="1.4"/>')
        elements.append(f'<text x="{x0 + 14:.1f}" y="{y0 - 30:.1f}" font-size="14" font-family="Arial" font-weight="700">{esc(cam["name"])} </text>')
        elements.append(f'<text x="{x0 + 14:.1f}" y="{y0 - 14:.1f}" font-size="12" font-family="Arial">pos {esc(fmt3(p))}</text>')
        elements.append(f'<text x="{x0 + 14:.1f}" y="{y0 + 2:.1f}" font-size="12" font-family="Arial">XY heading {item["heading_deg"]:.1f}°; orbit {cam["glider_orbit_samples_inside"]}/{cam["glider_orbit_samples_total"]}</text>')
        elements.append(f'<text x="{x0 + 14:.1f}" y="{y0 + 18:.1f}" font-size="12" font-family="Arial">h_tri={item["triangle_height_m"]:.2f} m; d_E={item["anchor_distance_euclidean_m"]:.2f} m</text>')
        elements.append(f'<text x="{x0 + 14:.1f}" y="{y0 + 34:.1f}" font-size="12" font-family="Arial">Δx(A-C)={item["anchor_dx_m"]:.2f} m; Δy(A-C)={item["anchor_dy_m"]:.2f} m</text>')

    # Axis inset, clearly not a 3-D camera view.
    ax0 = plot["x"] + 70
    ay0 = plot["y"] + plot["h"] - 70
    elements.append(f'<line x1="{ax0}" y1="{ay0}" x2="{ax0 + 70}" y2="{ay0}" stroke="#c62828" stroke-width="4" marker-end="url(#arrowX)"/>')
    elements.append(f'<line x1="{ax0}" y1="{ay0}" x2="{ax0}" y2="{ay0 - 70}" stroke="#2e7d32" stroke-width="4" marker-end="url(#arrowY)"/>')
    elements.append(f'<text x="{ax0 + 78}" y="{ay0 + 5}" font-size="15" font-family="Arial" fill="#c62828">world +X</text>')
    elements.append(f'<text x="{ax0 - 16}" y="{ay0 - 80}" font-size="15" font-family="Arial" fill="#2e7d32">world +Y</text>')
    elements.append(f'<text x="{ax0 - 4}" y="{ay0 + 28}" font-size="12" font-family="Arial" fill="#555">+Z is out of this XY page</text>')

    # Numeric side panel.
    y = info["y"] + 65
    row_h = 22
    details = [
        ("Scene", diagnostics["scene_name"], ""),
        ("Orientation mode", rig["orientation_mode"], ""),
        ("Resolution", f"{rig['resolution'][0]} × {rig['resolution'][1]}", "px"),
        ("Horizontal FOV", f"{float(rig['horizontal_fov_deg']):.3f}", "deg"),
        ("Vertical FOV", f"{float(rig['vertical_fov_deg']):.3f}", "deg, not drawn here"),
        ("Focal length", f"{float(rig['focal_length_mm']):.3f}", "mm"),
        ("Horizontal aperture", f"{float(rig['horizontal_aperture_mm']):.3f}", "mm"),
        ("Baseline", f"{float(rig['baseline_distance_m']):.3f}", "m"),
        ("Baseline vector", f"({rig['baseline_vector_m'][0]:.3f}, {rig['baseline_vector_m'][1]:.3f}, {rig['baseline_vector_m'][2]:.3f})", "m"),
        ("Anchor", fmt3(anchor), "m"),
        ("Look-at", fmt3(look_at), "m"),
        ("Tether/orbit radius", f"{tether:.3f}", "m"),
        ("Glider height", f"{float(scene['glider_height_m']):.3f}", "m"),
        ("Both-camera orbit samples", f"{diagnostics['overlap']['orbit_samples_visible_in_both_cameras']}/{diagnostics['overlap']['orbit_samples_total']}", ""),
    ]
    for name, value, unit in details:
        elements.append(f'<text x="{info["x"] + 18}" y="{y:.1f}" font-size="13" font-family="Arial" fill="#333"><tspan font-weight="700">{esc(name)}:</tspan> {esc(value)} {esc(unit)}</text>')
        y += row_h

    y += 10
    elements.append(f'<text x="{info["x"] + 18}" y="{y:.1f}" font-size="15" font-family="Arial" font-weight="700">Cameras</text>')
    y += 22
    for cam, item in zip(cameras, camera_plan):
        p = cam["position"]
        f = cam["forward_unit"]
        elements.append(f'<text x="{info["x"] + 18}" y="{y:.1f}" font-size="12" font-family="Arial"><tspan font-weight="700">{esc(cam["name"])}:</tspan> pos {esc(fmt3(p))}</text>')
        y += 17
        elements.append(f'<text x="{info["x"] + 18}" y="{y:.1f}" font-size="12" font-family="Arial" fill="#555">forward {esc(fmt3(f))}; anchor inside={esc(cam["anchor_coverage"]["inside"])} </text>')
        y += 17
        elements.append(f'<text x="{info["x"] + 18}" y="{y:.1f}" font-size="12" font-family="Arial" fill="#321">plot h_tri={item["triangle_height_m"]:.3f} m; d_E={item["anchor_distance_euclidean_m"]:.3f} m; d_XY={item["anchor_distance_xy_m"]:.3f} m</text>')
        y += 17
        elements.append(f'<text x="{info["x"] + 18}" y="{y:.1f}" font-size="12" font-family="Arial" fill="#333">Δx(anchor-camera)={item["anchor_dx_m"]:.3f} m; Δy(anchor-camera)={item["anchor_dy_m"]:.3f} m; Δz={item["anchor_dz_m"]:.3f} m</text>')
        y += 26

    elements.append(f'<text x="70" y="1045" font-size="14" font-family="Arial" fill="#555">Important: this SVG is a pure XY plan view. It draws only horizontal FOV wedges. Use camera_rig_layout_elevation.svg for vertical FOV and camera pitch/height checks.</text>')
    if view_mode != "full" and plot_distance < configured_distance - 1e-9:
        elements.append(f'<text x="70" y="1070" font-size="14" font-family="Arial" fill="#b26a00">Rays are clipped to {plot_distance:.2f} m for readability. The configured frustum distance is {configured_distance:.2f} m. Use camera_rig_layout_plan_full.svg to inspect the full footprint.</text>')
    elements.append('</svg>')
    return "\n".join(elements)


def build_camera_layout_elevation_svg(diagnostics: dict[str, Any]) -> str:
    """Build a pure depth-versus-Z elevation diagnostic SVG.

    Each camera is shown in its own longitudinal vertical plane.  This diagram
    uses vertical FOV only.  It does not show horizontal coverage; that is the
    purpose of the plan-view SVG.
    """

    def esc(text: Any) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def dot(a: list[float], b: list[float]) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def sub(a: list[float], b: list[float]) -> list[float]:
        return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

    scene = diagnostics["scene"]
    rig = diagnostics["camera_rig"]
    cameras = diagnostics["cameras"]
    glider_samples = diagnostics["glider_samples"]

    width = 1800
    height = 1050
    margin_x = 70.0
    top_y = 135.0
    panel_gap = 45.0
    panel_w = (width - 2.0 * margin_x - panel_gap) / 2.0
    panel_h = 720.0
    vfov_deg = float(rig["vertical_fov_deg"])
    configured_distance = max(float(rig["frustum_distance_m"]), 0.1)

    elements: list[str] = []
    elements.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="background:white">')
    elements.append('<rect width="100%" height="100%" fill="white"/>')
    elements.append('<text x="70" y="48" font-size="28" font-family="Arial" font-weight="700">Stereo camera elevation diagnostic</text>')
    elements.append('<text x="70" y="78" font-size="15" font-family="Arial" fill="#555">Pure vertical view in each camera longitudinal plane. Uses vertical FOV only; horizontal FOV is intentionally excluded.</text>')
    elements.append(f'<text x="70" y="103" font-size="14" font-family="Arial" fill="#777">Scene={esc(diagnostics["scene_name"])} | VFOV={vfov_deg:.2f}° | configured frustum distance={configured_distance:.2f} m</text>')

    for cam_index, cam in enumerate(cameras):
        panel_x = margin_x + cam_index * (panel_w + panel_gap)
        panel_y = top_y
        origin = cam["position"]
        forward = cam["forward_unit"]
        up = cam["up_unit"]
        color = cam["frustum_color_hex"]

        points: list[tuple[float, float, str]] = []
        for label, p in [("anchor", scene["anchor_position"]), ("look-at", scene["look_at"])]:
            depth = dot(sub(p, origin), forward)
            points.append((depth, float(p[2]), label))
        for sample in glider_samples:
            p = sample["position"]
            depth = dot(sub(p, origin), forward)
            points.append((depth, float(p[2]), "glider"))

        # Vertical FOV boundary endpoints at configured distance.
        center = [origin[i] + forward[i] * configured_distance for i in range(3)]
        half_h = math.tan(math.radians(vfov_deg) / 2.0) * configured_distance
        top = [center[i] + up[i] * half_h for i in range(3)]
        bottom = [center[i] - up[i] * half_h for i in range(3)]
        fov_points = [(0.0, float(origin[2])), (configured_distance, float(top[2])), (configured_distance, float(bottom[2]))]

        min_d = min([0.0, configured_distance] + [p[0] for p in points])
        max_d = max([configured_distance] + [p[0] for p in points])
        min_z = min([0.0, origin[2], top[2], bottom[2]] + [p[1] for p in points])
        max_z = max([origin[2], top[2], bottom[2]] + [p[1] for p in points])
        d_margin = 0.08 * max(max_d - min_d, 1.0)
        z_margin = 0.15 * max(max_z - min_z, 1.0)
        min_d -= d_margin
        max_d += d_margin
        min_z -= z_margin
        max_z += z_margin

        def sx(depth: float) -> float:
            return panel_x + (depth - min_d) / max(max_d - min_d, 1e-9) * panel_w

        def sy(z: float) -> float:
            return panel_y + panel_h - (z - min_z) / max(max_z - min_z, 1e-9) * panel_h

        elements.append(f'<rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" fill="#fbfbfb" stroke="#bdbdbd"/>')
        elements.append(f'<text x="{panel_x + 16}" y="{panel_y + 30}" font-size="18" font-family="Arial" font-weight="700">{esc(cam["name"])}: depth vs Z</text>')
        elements.append(f'<text x="{panel_x + 16}" y="{panel_y + 52}" font-size="12" font-family="Arial" fill="#555">camera position=({origin[0]:.3f}, {origin[1]:.3f}, {origin[2]:.3f}) m</text>')

        # Grid.
        d_step = 5.0 if max_d - min_d <= 80 else 10.0 if max_d - min_d <= 180 else 25.0
        z_step = 1.0 if max_z - min_z <= 15 else 2.0
        d = math.ceil(min_d / d_step) * d_step
        while d <= max_d + 1e-9:
            elements.append(f'<line x1="{sx(d):.1f}" y1="{panel_y}" x2="{sx(d):.1f}" y2="{panel_y + panel_h}" stroke="#e5e5e5"/>')
            d += d_step
        z = math.ceil(min_z / z_step) * z_step
        while z <= max_z + 1e-9:
            sw = "1.8" if abs(z) < 1e-9 else "1"
            col = "#777" if abs(z) < 1e-9 else "#e5e5e5"
            elements.append(f'<line x1="{panel_x}" y1="{sy(z):.1f}" x2="{panel_x + panel_w}" y2="{sy(z):.1f}" stroke="{col}" stroke-width="{sw}"/>')
            z += z_step

        # FOV triangle.
        elements.append(
            f'<polygon points="{sx(0.0):.1f},{sy(origin[2]):.1f} {sx(configured_distance):.1f},{sy(top[2]):.1f} {sx(configured_distance):.1f},{sy(bottom[2]):.1f}" '
            f'fill="{color}" fill-opacity="0.12" stroke="{color}" stroke-width="2.5"/>'
        )
        center_depth = configured_distance
        center_z = center[2]
        elements.append(f'<line x1="{sx(0.0):.1f}" y1="{sy(origin[2]):.1f}" x2="{sx(center_depth):.1f}" y2="{sy(center_z):.1f}" stroke="{color}" stroke-width="3" stroke-dasharray="8,5"/>')

        # Camera marker.
        cx = sx(0.0)
        cy = sy(origin[2])
        elements.append(f'<rect x="{cx - 8:.1f}" y="{cy - 8:.1f}" width="16" height="16" transform="rotate(45 {cx:.1f} {cy:.1f})" fill="{cam["marker_color_hex"]}" stroke="#111"/>')
        elements.append(f'<text x="{cx + 12:.1f}" y="{cy - 10:.1f}" font-size="13" font-family="Arial" font-weight="700">camera</text>')

        # Points.
        for depth, zval, label in points:
            if depth < min_d or depth > max_d:
                continue
            if label == "glider":
                elements.append(f'<circle cx="{sx(depth):.1f}" cy="{sy(zval):.1f}" r="3.2" fill="#111" opacity="0.65"/>')
            elif label == "anchor":
                elements.append(f'<circle cx="{sx(depth):.1f}" cy="{sy(zval):.1f}" r="7" fill="#d62728" stroke="white" stroke-width="1.5"/>')
                elements.append(f'<text x="{sx(depth)+9:.1f}" y="{sy(zval)-8:.1f}" font-size="13" font-family="Arial">anchor</text>')
            else:
                elements.append(f'<circle cx="{sx(depth):.1f}" cy="{sy(zval):.1f}" r="6" fill="#444" stroke="white" stroke-width="1.5"/>')
                elements.append(f'<text x="{sx(depth)+9:.1f}" y="{sy(zval)+17:.1f}" font-size="13" font-family="Arial">look-at</text>')

        elements.append(f'<text x="{panel_x + 18}" y="{panel_y + panel_h + 32}" font-size="13" font-family="Arial" fill="#555">x-axis: depth along this camera optical axis [m]; y-axis: world Z [m].</text>')

    elements.append('<text x="70" y="975" font-size="14" font-family="Arial" fill="#555">Important: this SVG is an elevation diagnostic. It does not answer horizontal overlap; use camera_rig_layout_plan_roi.svg for that.</text>')
    elements.append('</svg>')
    return "\n".join(elements)
