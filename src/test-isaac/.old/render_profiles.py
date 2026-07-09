# render_profile_test.py
#
# Run with Isaac Sim's Python:
#
#   /path/to/isaac-sim/python.sh render_profile_test.py
#
# No argparse.
# No command-line profile selection.
#
# Select the profile in render_profiles.yaml:
#
#   run:
#     active_profile: "rtx_realtime_2_balanced"
#
# To print available profiles without launching Isaac Sim:
#
#   run:
#     list_profiles_only: true
#
# To validate the selected profile without launching Isaac Sim:
#
#   run:
#     validate_config_only: true
#
# This script:
#   1. Loads the active profile from YAML.
#   2. Can print the available profiles before launching Isaac Sim.
#   3. Starts Isaac Sim with the active profile's launch_config.
#   4. Builds a simple scene.
#   5. Moves a quadcopter-like object and a target object.
#   6. Captures RGB frames with Replicator.
#   7. Saves metrics.json for comparison.
#
# This is not a drone dynamics simulator.
# It is a rendering-profile comparison harness.

import json
import math
import subprocess
import time
from copy import deepcopy
from pathlib import Path


CONFIG_PATH = "render_profiles.yaml"

import os
os.system("cls" if os.name == "nt" else "clear")  # Clear the terminal for better readability

ROOT_PATH = Path(__file__).parent.resolve()
CONFIG_PATH = ROOT_PATH / CONFIG_PATH


duration_frames = 90 #replaces num_frames in the yaml file, to make it easier to change the duration of the test without editing the yaml file.


#available profiles:
  # Available profiles:
  #   rtx_minimal_textured
  #   legacy_raytraced_lighting
  #   rtx_realtime_2_performance
  #   rtx_realtime_2_balanced
  #   rtx_realtime_2_quality
  #   pathtracing_preview
  #   pathtracing_quality
  #   pathtracing_ultra

desired_profile = "rtx_realtime_2_balanced" #change this to the desired profile for testing

#terminal performance print settings
print_fps_every_n_frames = 10
fps_rolling_window_frames = 30
print_detailed_timing = True

# GPU telemetry.
# This uses nvidia-smi. It is useful for diagnosis, but querying nvidia-smi has overhead.
# Keep it enabled for debugging. Disable it for clean benchmark numbers.
enable_gpu_telemetry = True
print_gpu_every_n_frames = 10

print(f"Using desired profile: {desired_profile}")
try:
    input(f"Press Enter to continue with this profile, or Ctrl+C to abort...")
except KeyboardInterrupt:
    print("Aborted by user.")
    exit(0)
#end-try-except





def load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required. Install it in the Isaac Sim Python environment. "
            "Example: /path/to/isaac-sim/python.sh -m pip install pyyaml"
        ) from exc

    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def get_available_profiles(config: dict) -> list[str]:
    profiles = config.get("profiles", {})
    profile_order = config.get("profile_order", [])

    ordered = [name for name in profile_order if name in profiles]
    missing_from_order = sorted(name for name in profiles.keys() if name not in ordered)

    return ordered + missing_from_order


def print_profile_catalog(config: dict) -> None:
    profiles = config.get("profiles", {})
    families = config.get("renderer_families", {})
    available_profiles = get_available_profiles(config)

    print("=" * 100)
    print("Available Isaac Sim rendering profiles")
    print("=" * 100)
    print("Speed and quality use a 1 to 5 relative scale.")
    print("Speed 5 means fastest. Quality 5 means highest visual quality.")
    print("=" * 100)

    header = (
        f"{'profile':32} "
        f"{'renderer':22} "
        f"{'speed':>5} "
        f"{'quality':>7} "
        f"{'recommended use'}"
    )
    print(header)
    print("-" * len(header))

    for profile_name in available_profiles:
        profile = profiles[profile_name]
        family_name = profile.get("family", "")
        family = families.get(family_name, {})
        renderer = profile.get("launch_config", {}).get(
            "renderer",
            family.get("isaac_renderer", "unknown"),
        )

        recommended_for = profile.get("recommended_for", [])
        recommended_use = recommended_for[0] if recommended_for else profile.get(
            "description",
            "",
        )

        print(
            f"{profile_name:32} "
            f"{renderer:22} "
            f"{profile.get('relative_speed', '?'):>5} "
            f"{profile.get('relative_quality', '?'):>7} "
            f"{recommended_use}"
        )

    print("=" * 100)
    print("Recommended sequence:")
    print("  1. rtx_minimal_textured - confirm the script and writer work.")
    print("  2. rtx_realtime_2_balanced - main practical baseline.")
    print("  3. rtx_realtime_2_quality - higher-quality real-time output.")
    print("  4. pathtracing_quality - high-quality reference.")
    print("=" * 100)
    print("Change run.active_profile in render_profiles.yaml to one of the names above.")
    print("=" * 100)


def print_selected_profile_details(
    config: dict,
    profile_name: str,
    profile: dict,
    output_dir: Path | None = None,
) -> None:
    families = config.get("renderer_families", {})
    family_name = profile.get("family")
    family = families.get(family_name, {})

    renderer = profile.get("launch_config", {}).get(
        "renderer",
        family.get("isaac_renderer", "unknown"),
    )

    print("=" * 100)
    print(f"Selected profile: {profile_name}")
    print(f"User label: {profile.get('user_label', '')}")
    print(f"Renderer: {renderer}")
    print(f"Family: {family.get('display_name', family_name)}")
    print(f"Family summary: {family.get('summary', '')}")
    print(f"Description: {profile.get('description', '')}")
    print(f"Relative speed: {profile.get('relative_speed', '?')} / 5")
    print(f"Relative quality: {profile.get('relative_quality', '?')} / 5")

    if output_dir is not None:
        print(f"Output directory: {output_dir.resolve()}")

    print("-" * 100)

    recommended_for = profile.get("recommended_for", [])
    avoid_if = profile.get("avoid_if", [])
    comparison_notes = profile.get("comparison_notes", [])

    if recommended_for:
        print("Recommended for:")
        for item in recommended_for:
            print(f"  - {item}")

    if avoid_if:
        print("Avoid if:")
        for item in avoid_if:
            print(f"  - {item}")

    if comparison_notes:
        print("Comparison notes:")
        for item in comparison_notes:
            print(f"  - {item}")

    print("=" * 100)


def load_active_profile(config_path: str) -> tuple[dict, dict, str]:
    config = load_yaml(config_path)

    run_cfg = config.get("run", {})
    # active_profile_name = run_cfg.get("active_profile")
    active_profile_name = desired_profile #use the desired profile variable instead of the yaml file setting

    if not active_profile_name:
        raise ValueError("Missing run.active_profile in YAML.")

    profiles = config.get("profiles", {})
    if active_profile_name not in profiles:
        available = get_available_profiles(config)
        print_profile_catalog(config)
        raise ValueError(
            f"Unknown active profile: {active_profile_name}. "
            f"Use one of these profiles: {available}"
        )

    capture_defaults = config.get("capture_defaults", {})
    profile_cfg = deepcopy(profiles[active_profile_name])

    profile_cfg["capture"] = deep_merge(
        capture_defaults,
        profile_cfg.get("capture", {}),
    )

    profile_cfg["scene"] = deepcopy(config.get("scene", {}))
    profile_cfg["camera"] = deepcopy(config.get("camera", {}))
    profile_cfg["output_root"] = run_cfg.get("output_root", "./render_outputs")

    return config, profile_cfg, active_profile_name


def main_before_isaac_import() -> tuple[dict, dict, str] | None:
    config, profile, active_profile_name = load_active_profile(CONFIG_PATH)
    run_cfg = config.get("run", {})

    if bool(run_cfg.get("list_profiles_only", False)):
        print_profile_catalog(config)
        return None

    if bool(run_cfg.get("validate_config_only", False)):
        print_profile_catalog(config)
        print_selected_profile_details(
            config=config,
            profile_name=active_profile_name,
            profile=profile,
            output_dir=None,
        )
        print("Configuration validation completed. Isaac Sim was not launched.")
        return None

    return config, profile, active_profile_name


loaded = main_before_isaac_import()

if loaded is None:
    raise SystemExit(0)

config, profile, active_profile_name = loaded
launch_config = deepcopy(profile.get("launch_config", {}))

# Important:
# SimulationApp must be created before most Isaac Sim and Omniverse imports.
from isaacsim import SimulationApp  # noqa: E402

simulation_app = SimulationApp(launch_config)

# Isaac Sim and Omniverse imports must happen after SimulationApp creation.
import carb  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade  # noqa: E402


def apply_carb_settings(settings_dict: dict) -> None:
    settings = carb.settings.get_settings()

    for key, value in settings_dict.items():
        settings.set(key, value)
        print(f"[carb] {key} = {value}")


def clear_stage() -> Usd.Stage:
    context = omni.usd.get_context()
    context.new_stage()

    stage = context.get_stage()

    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")

    return stage


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
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")

    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*color)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(metallic))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))

    material.CreateSurfaceOutput().ConnectToSource(
        shader.ConnectableAPI(),
        "surface",
    )

    return material


def bind_material(prim: Usd.Prim, material: UsdShade.Material) -> None:
    UsdShade.MaterialBindingAPI.Apply(prim)
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def create_cube(
    stage: Usd.Stage,
    path: str,
    translation,
    scale,
    material: UsdShade.Material,
) -> Usd.Prim:
    cube = UsdGeom.Cube.Define(stage, path)
    set_xform(stage, path, translation=translation, scale=scale)
    bind_material(cube.GetPrim(), material)
    return cube.GetPrim()


def create_sphere(
    stage: Usd.Stage,
    path: str,
    translation,
    scale,
    material: UsdShade.Material,
) -> Usd.Prim:
    sphere = UsdGeom.Sphere.Define(stage, path)
    set_xform(stage, path, translation=translation, scale=scale)
    bind_material(sphere.GetPrim(), material)
    return sphere.GetPrim()


def create_cylinder(
    stage: Usd.Stage,
    path: str,
    translation,
    rotation_deg,
    scale,
    material: UsdShade.Material,
) -> Usd.Prim:
    cylinder = UsdGeom.Cylinder.Define(stage, path)
    set_xform(
        stage,
        path,
        translation=translation,
        rotation_deg=rotation_deg,
        scale=scale,
    )
    bind_material(cylinder.GetPrim(), material)
    return cylinder.GetPrim()


def create_lights(stage: Usd.Stage) -> None:
    UsdGeom.Xform.Define(stage, "/World/Lights")

    dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
    dome.CreateIntensityAttr(450.0)
    dome.CreateExposureAttr(0.0)

    sun = UsdLux.DistantLight.Define(stage, "/World/Lights/Sun")
    sun.CreateIntensityAttr(2500.0)
    sun.CreateAngleAttr(0.35)
    set_xform(stage, "/World/Lights/Sun", rotation_deg=(-45.0, 0.0, 35.0))

    softbox = UsdLux.RectLight.Define(stage, "/World/Lights/Softbox")
    softbox.CreateIntensityAttr(600.0)
    softbox.CreateWidthAttr(4.0)
    softbox.CreateHeightAttr(3.0)
    set_xform(
        stage,
        "/World/Lights/Softbox",
        translation=(0.0, -3.5, 4.0),
        rotation_deg=(-55.0, 0.0, 0.0),
    )


def create_environment(stage: Usd.Stage) -> None:
    UsdGeom.Xform.Define(stage, "/World/Materials")

    mat_floor = make_material(
        stage,
        "/World/Materials/FloorMat",
        color=(0.45, 0.45, 0.45),
        roughness=0.9,
    )
    mat_blue = make_material(
        stage,
        "/World/Materials/BlueMat",
        color=(0.1, 0.2, 0.9),
        roughness=0.4,
    )
    mat_red = make_material(
        stage,
        "/World/Materials/RedMat",
        color=(0.9, 0.1, 0.05),
        roughness=0.45,
    )
    mat_green = make_material(
        stage,
        "/World/Materials/GreenMat",
        color=(0.1, 0.8, 0.2),
        roughness=0.55,
    )
    mat_metal = make_material(
        stage,
        "/World/Materials/MetalMat",
        color=(0.75, 0.75, 0.72),
        roughness=0.18,
        metallic=1.0,
    )
    mat_dark = make_material(
        stage,
        "/World/Materials/DarkMat",
        color=(0.02, 0.02, 0.025),
        roughness=0.35,
    )
    mat_rotor = make_material(
        stage,
        "/World/Materials/RotorMat",
        color=(0.01, 0.01, 0.01),
        roughness=0.2,
    )
    mat_yellow = make_material(
        stage,
        "/World/Materials/YellowMat",
        color=(0.95, 0.8, 0.05),
        roughness=0.5,
    )

    create_cube(
        stage,
        "/World/Floor",
        translation=(0.0, 0.0, -0.03),
        scale=(8.0, 8.0, 0.03),
        material=mat_floor,
    )

    create_cube(
        stage,
        "/World/BlockBlue",
        translation=(-1.8, 0.7, 0.35),
        scale=(0.45, 0.45, 0.35),
        material=mat_blue,
    )

    create_cube(
        stage,
        "/World/BlockGreen",
        translation=(1.9, 0.5, 0.45),
        scale=(0.35, 0.65, 0.45),
        material=mat_green,
    )

    create_cube(
        stage,
        "/World/MetalPlate",
        translation=(0.0, 1.7, 0.05),
        scale=(1.8, 0.08, 0.05),
        material=mat_metal,
    )

    create_sphere(
        stage,
        "/World/MovingTarget",
        translation=(0.0, -0.4, 0.35),
        scale=(0.22, 0.22, 0.22),
        material=mat_red,
    )

    UsdGeom.Xform.Define(stage, "/World/Drone")

    create_cube(
        stage,
        "/World/Drone/Body",
        translation=(0.0, 0.0, 0.0),
        scale=(0.32, 0.18, 0.07),
        material=mat_dark,
    )

    create_cube(
        stage,
        "/World/Drone/ArmX",
        translation=(0.0, 0.0, 0.0),
        scale=(0.72, 0.035, 0.025),
        material=mat_dark,
    )

    create_cube(
        stage,
        "/World/Drone/ArmY",
        translation=(0.0, 0.0, 0.0),
        scale=(0.035, 0.72, 0.025),
        material=mat_dark,
    )

    rotor_locations = [
        ("RotorFL", (0.55, 0.55, 0.0)),
        ("RotorFR", (0.55, -0.55, 0.0)),
        ("RotorRL", (-0.55, 0.55, 0.0)),
        ("RotorRR", (-0.55, -0.55, 0.0)),
    ]

    for name, location in rotor_locations:
        create_cylinder(
            stage,
            f"/World/Drone/{name}",
            translation=location,
            rotation_deg=(0.0, 0.0, 0.0),
            scale=(0.18, 0.18, 0.015),
            material=mat_rotor,
        )

    create_sphere(
        stage,
        "/World/Drone/FrontMarker",
        translation=(0.42, 0.0, 0.05),
        scale=(0.055, 0.055, 0.055),
        material=mat_yellow,
    )


def update_motion(
    stage: Usd.Stage,
    frame_index: int,
    total_frames: int,
    scene_cfg: dict,
) -> None:
    t = frame_index / max(total_frames - 1, 1)
    angle = 2.0 * math.pi * t

    drone_radius = float(scene_cfg.get("drone_motion_radius", 1.2))
    drone_height = float(scene_cfg.get("drone_motion_height", 1.2))
    object_amplitude = float(scene_cfg.get("object_motion_amplitude", 1.5))

    drone_x = drone_radius * math.cos(angle)
    drone_y = drone_radius * math.sin(angle)
    drone_z = drone_height + 0.25 * math.sin(2.0 * angle)
    drone_yaw = math.degrees(angle) + 90.0

    set_xform(
        stage,
        "/World/Drone",
        translation=(drone_x, drone_y, drone_z),
        rotation_deg=(0.0, 0.0, drone_yaw),
        scale=(1.0, 1.0, 1.0),
    )

    target_x = object_amplitude * math.sin(angle)
    target_y = -0.4
    target_z = 0.35

    set_xform(
        stage,
        "/World/MovingTarget",
        translation=(target_x, target_y, target_z),
        rotation_deg=(0.0, 0.0, 0.0),
        scale=(0.22, 0.22, 0.22),
    )

    rotor_spin = frame_index * 45.0

    for rotor in ["RotorFL", "RotorFR", "RotorRL", "RotorRR"]:
        prim_path = f"/World/Drone/{rotor}"
        prim = stage.GetPrimAtPath(prim_path)

        if not prim.IsValid():
            continue

        translate_attr = prim.GetAttribute("xformOp:translate")
        current_translation = translate_attr.Get()

        set_xform(
            stage,
            prim_path,
            translation=tuple(current_translation),
            rotation_deg=(0.0, 0.0, rotor_spin),
            scale=(0.18, 0.18, 0.015),
        )


def create_camera_and_writer(
    profile_name: str,
    output_dir: str,
    camera_cfg: dict,
    capture_cfg: dict,
):
    resolution = tuple(capture_cfg.get("resolution", [1280, 720]))

    camera_position = tuple(camera_cfg.get("position", [4.2, -5.2, 2.8]))
    camera_look_at = tuple(camera_cfg.get("look_at", [0.0, 0.0, 0.85]))
    focal_length = float(camera_cfg.get("focal_length", 35.0))

    camera = rep.create.camera(
        position=camera_position,
        look_at=camera_look_at,
        focal_length=focal_length,
    )

    render_product = rep.create.render_product(
        camera,
        resolution,
        name=f"render_product_{profile_name}",
    )

    rep.orchestrator.set_capture_on_play(False)

    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(
        output_dir=output_dir,
        rgb=bool(capture_cfg.get("rgb", True)),
        semantic_segmentation=bool(capture_cfg.get("semantic_segmentation", False)),
        bounding_box_2d_tight=bool(capture_cfg.get("bounding_box_2d_tight", False)),
    )
    writer.attach([render_product])

    return camera, render_product, writer



def query_nvidia_smi() -> dict | None:
    command = [
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
        "--format=csv,noheader,nounits",
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None

    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = [part.strip() for part in line.split(",")]

    if len(parts) != 8:
        return None

    def to_float(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    def to_int(value: str) -> int | None:
        try:
            return int(float(value))
        except ValueError:
            return None

    return {
        "gpu_name": parts[0],
        "gpu_util_percent": to_int(parts[1]),
        "memory_util_percent": to_int(parts[2]),
        "memory_used_mib": to_int(parts[3]),
        "memory_total_mib": to_int(parts[4]),
        "temperature_c": to_int(parts[5]),
        "power_draw_w": to_float(parts[6]),
        "power_limit_w": to_float(parts[7]),
    }


def format_gpu_stats(gpu_stats: dict | None) -> str:
    if gpu_stats is None:
        return "gpu_stats=unavailable"

    return (
        f"gpu={gpu_stats.get('gpu_util_percent')}% "
        f"vram={gpu_stats.get('memory_used_mib')}/{gpu_stats.get('memory_total_mib')}MiB "
        f"temp={gpu_stats.get('temperature_c')}C "
        f"power={gpu_stats.get('power_draw_w')}/{gpu_stats.get('power_limit_w')}W"
    )

def main() -> int:
    output_root = Path(profile.get("output_root", "./render_outputs"))
    output_dir = output_root / active_profile_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print_profile_catalog(config)
    print_selected_profile_details(
        config=config,
        profile_name=active_profile_name,
        profile=profile,
        output_dir=output_dir,
    )

    stage = clear_stage()

    try:
        simulation_app.reset_render_settings()
    except Exception as exc:
        print(f"[warning] reset_render_settings failed: {exc}")

    apply_carb_settings(profile.get("carb_settings", {}))

    create_environment(stage)
    create_lights(stage)

    camera, render_product, writer = create_camera_and_writer(
        profile_name=active_profile_name,
        output_dir=str(output_dir),
        camera_cfg=profile.get("camera", {}),
        capture_cfg=profile.get("capture", {}),
    )

    scene_cfg = profile.get("scene", {})
    capture_cfg = profile.get("capture", {})

    warmup_frames = int(scene_cfg.get("warmup_frames", 20))
    # num_frames = int(scene_cfg.get("num_frames", 90))
    num_frames = duration_frames
    rt_subframes = int(capture_cfg.get("rt_subframes", 8))

    print(f"Warmup frames: {warmup_frames}")
    print(f"Captured frames: {num_frames}")
    print(f"RT subframes per capture: {rt_subframes}")
    print(f"FPS print interval: every {print_fps_every_n_frames} frames")
    print(f"FPS rolling window: {fps_rolling_window_frames} frames")
    print(f"GPU telemetry enabled: {enable_gpu_telemetry}")
    print(f"GPU telemetry print interval: every {print_gpu_every_n_frames} frames")
    print(
        "Timing note: increasing duration_frames increases the number of captured frames. "
        "It does not increase per-frame rendering quality by itself."
    )

    print("Starting warmup...")
    warmup_start_time = time.perf_counter()

    for _ in range(warmup_frames):
        simulation_app.update()

    warmup_elapsed_seconds = time.perf_counter() - warmup_start_time

    print(f"Warmup completed in {warmup_elapsed_seconds:.3f} seconds")

    frame_metrics = []
    gpu_metrics = []
    gpu_telemetry_overhead_seconds = 0.0
    nvidia_smi_warning_printed = False
    run_start_time = time.perf_counter()

    for frame_index in range(num_frames):
        frame_start_time = time.perf_counter()

        motion_start_time = time.perf_counter()
        update_motion(
            stage=stage,
            frame_index=frame_index,
            total_frames=num_frames,
            scene_cfg=scene_cfg,
        )
        motion_update_seconds = time.perf_counter() - motion_start_time

        sim_update_start_time = time.perf_counter()
        simulation_app.update()
        simulation_update_seconds = time.perf_counter() - sim_update_start_time

        capture_start_time = time.perf_counter()

        rep.orchestrator.step(
            rt_subframes=rt_subframes,
            pause_timeline=True,
            delta_time=0.0,
            wait_for_render=True,
        )

        capture_seconds = time.perf_counter() - capture_start_time
        frame_total_seconds = time.perf_counter() - frame_start_time

        capture_fps = 1.0 / capture_seconds if capture_seconds > 0 else None
        frame_fps = 1.0 / frame_total_seconds if frame_total_seconds > 0 else None

        frame_metrics.append(
            {
                "frame": frame_index,
                "motion_update_seconds": motion_update_seconds,
                "simulation_update_seconds": simulation_update_seconds,
                "capture_seconds": capture_seconds,
                "frame_total_seconds": frame_total_seconds,
                "capture_fps": capture_fps,
                "frame_fps": frame_fps,
                # Kept for backward compatibility with your previous metrics.json.
                "fps_equivalent": capture_fps,
            }
        )

        should_print = (
            frame_index == 0
            or (frame_index + 1) % print_fps_every_n_frames == 0
            or frame_index == num_frames - 1
        )

        if should_print:
            gpu_stats = None
            gpu_query_seconds = 0.0

            should_query_gpu = (
                enable_gpu_telemetry
                and (
                    frame_index == 0
                    or (frame_index + 1) % print_gpu_every_n_frames == 0
                    or frame_index == num_frames - 1
                )
            )

            if should_query_gpu:
                gpu_query_start_time = time.perf_counter()
                gpu_stats = query_nvidia_smi()
                gpu_query_seconds = time.perf_counter() - gpu_query_start_time
                gpu_telemetry_overhead_seconds += gpu_query_seconds

                if gpu_stats is None and not nvidia_smi_warning_printed:
                    print(
                        "[warning] nvidia-smi telemetry is unavailable. "
                        "GPU FPS timing still works.",
                        flush=True,
                    )
                    nvidia_smi_warning_printed = True

                if gpu_stats is not None:
                    gpu_metrics.append(
                        {
                            "frame": frame_index,
                            "query_seconds": gpu_query_seconds,
                            **gpu_stats,
                        }
                    )

            elapsed_run_seconds = time.perf_counter() - run_start_time
            completed_frames = frame_index + 1

            elapsed_run_seconds_excluding_gpu_query = (
                elapsed_run_seconds - gpu_telemetry_overhead_seconds
            )

            overall_frame_fps = (
                completed_frames / elapsed_run_seconds
                if elapsed_run_seconds > 0
                else None
            )

            overall_frame_fps_excluding_gpu_query = (
                completed_frames / elapsed_run_seconds_excluding_gpu_query
                if elapsed_run_seconds_excluding_gpu_query > 0
                else None
            )

            rolling_window = frame_metrics[-fps_rolling_window_frames:]

            rolling_capture_seconds = sum(
                item["capture_seconds"] for item in rolling_window
            )
            rolling_frame_seconds = sum(
                item["frame_total_seconds"] for item in rolling_window
            )

            rolling_capture_fps = (
                len(rolling_window) / rolling_capture_seconds
                if rolling_capture_seconds > 0
                else None
            )
            rolling_frame_fps = (
                len(rolling_window) / rolling_frame_seconds
                if rolling_frame_seconds > 0
                else None
            )

            gpu_text = format_gpu_stats(gpu_stats)

            if print_detailed_timing:
                print(
                    f"[frame {frame_index + 1:04d}/{num_frames:04d}] "
                    f"capture={capture_seconds:.4f}s "
                    f"capture_fps={capture_fps:.2f} "
                    f"frame_total={frame_total_seconds:.4f}s "
                    f"frame_fps={frame_fps:.2f} "
                    f"rolling_frame_fps={rolling_frame_fps:.2f} "
                    f"overall_frame_fps={overall_frame_fps_excluding_gpu_query:.2f} "
                    f"motion={motion_update_seconds:.5f}s "
                    f"sim_update={simulation_update_seconds:.5f}s "
                    f"{gpu_text} "
                    f"gpu_query={gpu_query_seconds:.4f}s",
                    flush=True,
                )
            else:
                print(
                    f"[frame {frame_index + 1:04d}/{num_frames:04d}] "
                    f"frame_fps={frame_fps:.2f} "
                    f"rolling_frame_fps={rolling_frame_fps:.2f} "
                    f"overall_frame_fps={overall_frame_fps_excluding_gpu_query:.2f} "
                    f"{gpu_text}",
                    flush=True,
                )

    wait_start_time = time.perf_counter()
    rep.orchestrator.wait_until_complete()
    writer_flush_seconds = time.perf_counter() - wait_start_time
    total_run_seconds = time.perf_counter() - run_start_time
    total_run_seconds_excluding_gpu_query = (
        total_run_seconds - gpu_telemetry_overhead_seconds
    )

    print(f"Writer flush time: {writer_flush_seconds:.3f} seconds")
    print(f"Total measured capture loop time: {total_run_seconds:.3f} seconds")
    print(f"GPU telemetry overhead time: {gpu_telemetry_overhead_seconds:.3f} seconds")

    family_name = profile.get("family")
    family_cfg = config.get("renderer_families", {}).get(family_name, {})

    average_capture_seconds = None
    average_frame_total_seconds = None
    average_motion_update_seconds = None
    average_simulation_update_seconds = None
    average_capture_fps = None
    average_frame_fps = None
    overall_measured_frame_fps = None

    if frame_metrics:
        average_capture_seconds = sum(
            item["capture_seconds"] for item in frame_metrics
        ) / len(frame_metrics)

        average_frame_total_seconds = sum(
            item["frame_total_seconds"] for item in frame_metrics
        ) / len(frame_metrics)

        average_motion_update_seconds = sum(
            item["motion_update_seconds"] for item in frame_metrics
        ) / len(frame_metrics)

        average_simulation_update_seconds = sum(
            item["simulation_update_seconds"] for item in frame_metrics
        ) / len(frame_metrics)

        average_capture_fps = (
            1.0 / average_capture_seconds
            if average_capture_seconds and average_capture_seconds > 0
            else None
        )

        average_frame_fps = (
            1.0 / average_frame_total_seconds
            if average_frame_total_seconds and average_frame_total_seconds > 0
            else None
        )

        overall_measured_frame_fps = (
            len(frame_metrics) / total_run_seconds_excluding_gpu_query
            if total_run_seconds_excluding_gpu_query > 0
            else None
        )

    print("=" * 100)
    print("Performance summary")
    print("=" * 100)
    print(f"Profile: {active_profile_name}")
    print(f"Frames captured: {num_frames}")
    print(f"Average capture seconds/frame: {average_capture_seconds:.6f}")
    print(f"Average capture FPS: {average_capture_fps:.2f}")
    print(f"Average total seconds/frame: {average_frame_total_seconds:.6f}")
    print(f"Average total frame FPS: {average_frame_fps:.2f}")
    print(f"Overall measured frame FPS: {overall_measured_frame_fps:.2f}")
    print(f"Average motion update seconds/frame: {average_motion_update_seconds:.6f}")
    print(f"Average simulation update seconds/frame: {average_simulation_update_seconds:.6f}")
    print(f"Writer flush seconds: {writer_flush_seconds:.6f}")
    print(f"GPU telemetry overhead seconds: {gpu_telemetry_overhead_seconds:.6f}")

    if gpu_metrics:
        avg_gpu_util = sum(
            item["gpu_util_percent"] for item in gpu_metrics
            if item.get("gpu_util_percent") is not None
        ) / max(
            1,
            len([item for item in gpu_metrics if item.get("gpu_util_percent") is not None]),
        )
        max_vram = max(
            item["memory_used_mib"] for item in gpu_metrics
            if item.get("memory_used_mib") is not None
        )
        print(f"Average sampled GPU utilization: {avg_gpu_util:.2f}%")
        print(f"Maximum sampled VRAM usage: {max_vram} MiB")

    print("=" * 100)

    metrics = {
        "active_profile": active_profile_name,
        "profile_user_label": profile.get("user_label", ""),
        "profile_description": profile.get("description", ""),
        "relative_speed": profile.get("relative_speed"),
        "relative_quality": profile.get("relative_quality"),
        "recommended_for": profile.get("recommended_for", []),
        "avoid_if": profile.get("avoid_if", []),
        "comparison_notes": profile.get("comparison_notes", []),
        "renderer_family_name": family_name,
        "renderer_family": family_cfg,
        "launch_config": launch_config,
        "carb_settings": profile.get("carb_settings", {}),
        "capture": profile.get("capture", {}),
        "scene": profile.get("scene", {}),
        "camera": profile.get("camera", {}),
        "num_frames": num_frames,
        "average_capture_seconds": average_capture_seconds,
        "average_capture_fps": average_capture_fps,
        "average_frame_total_seconds": average_frame_total_seconds,
        "average_frame_fps": average_frame_fps,
        "overall_measured_frame_fps": overall_measured_frame_fps,
        "average_motion_update_seconds": average_motion_update_seconds,
        "average_simulation_update_seconds": average_simulation_update_seconds,
        "writer_flush_seconds": writer_flush_seconds,
        "total_run_seconds": total_run_seconds,
        "total_run_seconds_excluding_gpu_query": total_run_seconds_excluding_gpu_query,
        "gpu_telemetry_enabled": enable_gpu_telemetry,
        "gpu_telemetry_overhead_seconds": gpu_telemetry_overhead_seconds,
        "gpu_metrics": gpu_metrics,
        "print_fps_every_n_frames": print_fps_every_n_frames,
        "fps_rolling_window_frames": fps_rolling_window_frames,
        "frames": frame_metrics,
    }

    metrics_path = output_dir / "metrics.json"

    with open(metrics_path, "w", encoding="utf-8") as file:
        json.dump(metrics, file, indent=2)

    print(f"Saved metrics: {metrics_path.resolve()}")

    try:
        writer.detach()
    except Exception as exc:
        print(f"[warning] writer.detach failed: {exc}")

    try:
        render_product.destroy()
    except Exception as exc:
        print(f"[warning] render_product.destroy failed: {exc}")

    simulation_app.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        simulation_app.close()
        raise
