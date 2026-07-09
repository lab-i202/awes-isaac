# scenario/tethered_glider_scene.py
#
# Minimal kinematic tethered-glider scene.
#
# This is intentionally not aerodynamic.
# This is intentionally not a cable-physics simulation.
#
# Purpose:
#   Make an aircraft-like visual target move around a central anchor while
#   exactly respecting a tether-length constraint in the XY plane.

from __future__ import annotations

import math
from typing import Any

import carb
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade


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
    create_lights(stage)
    create_ground(stage)
    create_anchor(stage, scene_config)
    create_tether_curve(stage)
    create_proxy_glider(stage, scene_config)

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
    print("=" * 100)

    for frame_index in range(num_frames):
        sim_time_s = frame_index * time_step_s

        update_tethered_glider_motion(
            stage=stage,
            scene_config=scene_config,
            sim_time_s=sim_time_s,
        )

        simulation_app.update()

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

    print("Simulation finished.")


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


def create_lights(stage: Usd.Stage) -> None:
    UsdGeom.Xform.Define(stage, "/World/Lights")

    dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
    dome.CreateIntensityAttr(450.0)

    sun = UsdLux.DistantLight.Define(stage, "/World/Lights/Sun")
    sun.CreateIntensityAttr(2500.0)
    sun.CreateAngleAttr(0.35)
    set_xform(
        stage,
        "/World/Lights/Sun",
        rotation_deg=(-45.0, 0.0, 35.0),
    )


def create_ground(stage: Usd.Stage) -> None:
    material = make_material(
        stage,
        "/World/Materials/GroundMat",
        color=(0.35, 0.35, 0.35),
        roughness=0.9,
        metallic=0.0,
    )

    create_box(
        stage=stage,
        path="/World/Ground",
        translation=(0.0, 0.0, -0.03),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(14.0, 14.0, 0.04),
        material=material,
    )


def create_anchor(stage: Usd.Stage, scene_config: dict[str, Any]) -> None:
    anchor_position = tuple(scene_config["anchor_position"])

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
    curve.CreatePointsAttr(
        [
            Gf.Vec3f(0.0, 0.0, 0.0),
            Gf.Vec3f(1.0, 0.0, 0.0),
        ]
    )
    curve.CreateWidthsAttr([0.025, 0.025])

    bind_material(curve.GetPrim(), material)


def create_proxy_glider(stage: Usd.Stage, scene_config: dict[str, Any]) -> None:
    glider_cfg = scene_config["glider"]

    wingspan = float(glider_cfg["wingspan_m"])
    length = float(glider_cfg["length_m"])
    body_width = float(glider_cfg["body_width_m"])
    body_height = float(glider_cfg["body_height_m"])
    wing_chord = float(glider_cfg["wing_chord_m"])
    wing_thickness = float(glider_cfg["wing_thickness_m"])

    UsdGeom.Xform.Define(stage, "/World/Glider")

    white = make_material(
        stage,
        "/World/Materials/GliderWhite",
        color=(0.92, 0.92, 0.88),
        roughness=0.55,
        metallic=0.0,
    )
    black = make_material(
        stage,
        "/World/Materials/GliderBlack",
        color=(0.01, 0.01, 0.012),
        roughness=0.35,
        metallic=0.0,
    )
    green = make_material(
        stage,
        "/World/Materials/GliderGreen",
        color=(0.2, 0.9, 0.05),
        roughness=0.45,
        metallic=0.0,
    )

    # Body frame:
    #   +X forward
    #   +Y left wing
    #   +Z up
    # Parent origin is treated as approximate center of mass.

    create_box(
        stage=stage,
        path="/World/Glider/Fuselage",
        translation=(0.0, 0.0, 0.0),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(length, body_width, body_height),
        material=white,
    )

    create_box(
        stage=stage,
        path="/World/Glider/MainWing",
        translation=(0.03, 0.0, 0.035),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(wing_chord, wingspan, wing_thickness),
        material=white,
    )

    create_box(
        stage=stage,
        path="/World/Glider/LeftGreenStripe",
        translation=(0.05, 0.38, 0.055),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.04, 0.42, 0.012),
        material=green,
    )

    create_box(
        stage=stage,
        path="/World/Glider/RightGreenStripe",
        translation=(0.05, -0.38, 0.055),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.04, 0.42, 0.012),
        material=green,
    )

    create_box(
        stage=stage,
        path="/World/Glider/TailPlane",
        translation=(-0.40, 0.0, 0.04),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.14, 0.42, 0.02),
        material=white,
    )

    create_box(
        stage=stage,
        path="/World/Glider/VerticalFin",
        translation=(-0.42, 0.0, 0.13),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.12, 0.025, 0.22),
        material=white,
    )

    create_box(
        stage=stage,
        path="/World/Glider/Canopy",
        translation=(0.23, 0.0, 0.08),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.20, 0.08, 0.035),
        material=black,
    )

    create_box(
        stage=stage,
        path="/World/Glider/NoseMarker",
        translation=(0.46, 0.0, 0.0),
        rotation_deg=(0.0, 0.0, 0.0),
        dimensions=(0.035, 0.11, 0.11),
        material=black,
    )


def update_tethered_glider_motion(
    stage: Usd.Stage,
    scene_config: dict[str, Any],
    sim_time_s: float,
) -> None:
    anchor = scene_config["anchor_position"]
    glider_position = compute_glider_position(scene_config, sim_time_s)

    theta = float(scene_config["angular_velocity_rad_s"]) * sim_time_s

    # Tangent direction for counter-clockwise circular motion:
    #   position direction = [cos(theta), sin(theta)]
    #   velocity direction = [-sin(theta), cos(theta)]
    tangent_x = -math.sin(theta)
    tangent_y = math.cos(theta)
    yaw_deg = math.degrees(math.atan2(tangent_y, tangent_x))

    set_xform(
        stage,
        "/World/Glider",
        translation=tuple(glider_position),
        rotation_deg=(0.0, 0.0, yaw_deg),
        scale=(1.0, 1.0, 1.0),
    )

    tether = UsdGeom.BasisCurves(stage.GetPrimAtPath("/World/Tether"))
    tether.GetPointsAttr().Set(
        [
            Gf.Vec3f(float(anchor[0]), float(anchor[1]), float(anchor[2])),
            Gf.Vec3f(
                float(glider_position[0]),
                float(glider_position[1]),
                float(glider_position[2]),
            ),
        ]
    )


def compute_glider_position(
    scene_config: dict[str, Any],
    sim_time_s: float,
) -> tuple[float, float, float]:
    anchor = scene_config["anchor_position"]
    tether_length = float(scene_config["tether_length_m"])
    glider_height = float(scene_config["glider_height_m"])
    angular_velocity = float(scene_config["angular_velocity_rad_s"])

    theta = angular_velocity * sim_time_s

    x = float(anchor[0]) + tether_length * math.cos(theta)
    y = float(anchor[1]) + tether_length * math.sin(theta)
    z = float(anchor[2]) + glider_height

    return x, y, z


def compute_tether_constraint_error(
    scene_config: dict[str, Any],
    glider_position: tuple[float, float, float],
) -> float:
    anchor = scene_config["anchor_position"]
    tether_length = float(scene_config["tether_length_m"])

    dx = glider_position[0] - float(anchor[0])
    dy = glider_position[1] - float(anchor[1])

    horizontal_distance = math.sqrt(dx * dx + dy * dy)

    return abs(horizontal_distance - tether_length)


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

    set_xform(
        stage=stage,
        prim_path=path,
        translation=translation,
        rotation_deg=rotation_deg,
        scale=dimensions,
    )

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