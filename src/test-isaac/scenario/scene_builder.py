# scenario/scene_builder.py
#
# Scene construction and animation helpers.
#
# This module imports pxr/Omniverse APIs, so import it only after SimulationApp exists.

from __future__ import annotations

import math
from typing import Any

import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade


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
    scene_cfg: dict[str, Any],
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
