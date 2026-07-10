# preview_converted_asset.py
#
# Standalone Isaac Sim/USD asset preview and inspection script.
#
# Purpose:
#   Load a converted USD asset in a clean stage before integrating it into the
#   moving tethered-glider scene. This catches bad scale, orientation, missing
#   geometry, bad pivot/origin, and material/texture issues early.
#
# This script does NOT modify main.py.
# This script does NOT modify scenario/tethered_glider_scene.py.
# This script does NOT generate synthetic RGB datasets.
#
# Typical run from project root:
#
#   python preview_converted_asset.py --asset-id bixler_free3d
#
# Direct USD path alternative:
#
#   python preview_converted_asset.py --usd-path assets/converted/bixler_free3d/bixler_fbx.usd
#
# Useful options:
#
#   --hold-seconds 0
#       Keep Isaac Sim open until you close the window or interrupt the script.
#
#   --hold-seconds 30
#       Keep Isaac Sim open for 30 seconds, then close automatically.
#
#   --target-wingspan-m 1.5
#       Assumes the longest bounding-box dimension should be about 1.5 m and
#       reports the uniform scale factor needed to reach that size.
#
# Output report:
#
#   assets/converted/asset_preview_report.json
#
# Output preview stage:
#
#   assets/converted/<asset_id>/<asset_id>_preview_stage.usda

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).parent.resolve()
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "assets" / "asset_registry.json"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "assets" / "converted" / "asset_preview_report.json"
DEFAULT_ASSET_ID = "bixler_free3d"
DEFAULT_USD_RELATIVE_PATH = Path("assets") / "converted" / "bixler_free3d" / "bixler_fbx.usd"


ISAAC_SIM_CONFIG = {
    "headless": False,
    "renderer": "RealTimePathTracing",
    "width": 1280,
    "height": 720,
    "anti_aliasing": 3,
    "sync_loads": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview and inspect a converted USD asset in a clean Isaac Sim stage."
    )

    parser.add_argument(
        "--asset-id",
        default=DEFAULT_ASSET_ID,
        help="Asset id from assets/asset_registry.json. Default: bixler_free3d.",
    )
    parser.add_argument(
        "--usd-path",
        default=None,
        help="Optional direct USD path. Overrides registry lookup when provided.",
    )
    parser.add_argument(
        "--registry-path",
        default=str(DEFAULT_REGISTRY_PATH),
        help="Path to asset registry JSON.",
    )
    parser.add_argument(
        "--report-path",
        default=str(DEFAULT_REPORT_PATH),
        help="Path where asset_preview_report.json will be written.",
    )
    parser.add_argument(
        "--target-wingspan-m",
        type=float,
        default=1.5,
        help=(
            "Expected approximate aircraft wingspan in meters. The script uses "
            "the longest bounding-box dimension as a rough wingspan proxy."
        ),
    )
    parser.add_argument(
        "--preview-scale",
        type=float,
        default=1.0,
        help="Uniform scale applied only in the preview stage. Default: 1.0.",
    )
    parser.add_argument(
        "--preview-rotation-deg",
        nargs=3,
        type=float,
        default=[0.0, 0.0, 0.0],
        metavar=("RX", "RY", "RZ"),
        help="XYZ rotation applied only in the preview stage. Default: 0 0 0.",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=120.0,
        help=(
            "How long to keep the Isaac Sim window open after loading. "
            "Use 0 to keep it open until manually closed/interrupted. Default: 120."
        ),
    )
    parser.add_argument(
        "--no-save-preview-stage",
        action="store_true",
        help="Do not export the generated preview stage USDA file.",
    )

    return parser.parse_args()


# -----------------------------------------------------------------------------
# Plain Python helpers. Safe before Isaac imports.
# -----------------------------------------------------------------------------


def resolve_path(path_text: str | Path, base_dir: Path = PROJECT_ROOT) -> Path:
    path = Path(path_text)

    if not path.is_absolute():
        path = base_dir / path

    return path.resolve()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {path}")

    if not path.is_file():
        raise ValueError(f"Expected JSON file but path is not a file: {path}")

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}, got {type(data).__name__}")

    return data


def find_asset_record(registry: dict[str, Any], asset_id: str) -> dict[str, Any] | None:
    """Support a few registry shapes so this script stays robust."""

    assets = registry.get("assets")

    if isinstance(assets, dict):
        record = assets.get(asset_id)
        return record if isinstance(record, dict) else None

    if isinstance(assets, list):
        for record in assets:
            if isinstance(record, dict) and record.get("asset_id") == asset_id:
                return record

    # Fallback: root-level dictionary keyed by asset id.
    record = registry.get(asset_id)
    if isinstance(record, dict):
        return record

    return None


def get_record_path(record: dict[str, Any], candidate_keys: list[str]) -> str | None:
    for key in candidate_keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value

    converted = record.get("converted")
    if isinstance(converted, dict):
        for key in candidate_keys:
            value = converted.get(key)
            if isinstance(value, str) and value.strip():
                return value

    return None


def resolve_usd_path(args: argparse.Namespace) -> tuple[str, Path, dict[str, Any] | None]:
    asset_id = str(args.asset_id)

    if args.usd_path:
        return asset_id, resolve_path(args.usd_path), None

    registry_path = resolve_path(args.registry_path)
    registry = load_json(registry_path)
    record = find_asset_record(registry, asset_id)

    if record is not None:
        usd_text = get_record_path(
            record,
            [
                "converted_usd_path",
                "usd_path",
                "output_usd_path",
                "converted_path",
            ],
        )

        if usd_text:
            return asset_id, resolve_path(usd_text), record

    # Explicit default fallback for the current Bixler asset workflow.
    return asset_id, resolve_path(DEFAULT_USD_RELATIVE_PATH), record


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


# -----------------------------------------------------------------------------
# Isaac/USD-dependent code. Import only after SimulationApp starts.
# -----------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    asset_id, usd_path, registry_record = resolve_usd_path(args)
    report_path = resolve_path(args.report_path)

    if not usd_path.exists():
        raise FileNotFoundError(f"Converted USD asset does not exist: {usd_path}")

    if not usd_path.is_file():
        raise ValueError(f"Converted USD path is not a file: {usd_path}")

    print("=" * 100)
    print("Converted asset preview")
    print("=" * 100)
    print(f"Asset id: {asset_id}")
    print(f"USD path: {usd_path}")
    print(f"Report path: {report_path}")
    print("=" * 100)

    from isaacsim import SimulationApp

    simulation_app = SimulationApp(ISAAC_SIM_CONFIG)

    try:
        import carb
        import omni.usd
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

        stage = create_preview_stage(omni.usd, UsdGeom)

        configure_renderer(carb)
        create_lights(stage, UsdLux, Gf)
        create_ground_and_axes(stage, UsdGeom, UsdShade, Sdf, Gf)

        asset_prim_path = "/World/PreviewAsset"
        add_usd_reference(
            stage=stage,
            UsdGeom=UsdGeom,
            asset_prim_path=asset_prim_path,
            usd_path=usd_path,
            rotation_xyz_deg=tuple(float(value) for value in args.preview_rotation_deg),
            uniform_scale=float(args.preview_scale),
        )

        # Give Kit/USD a few updates so the referenced asset loads before bbox inspection.
        for _ in range(15):
            simulation_app.update()

        bbox_info = compute_bbox_info(stage, Usd, UsdGeom, asset_prim_path)
        create_bbox_wireframe(stage, UsdGeom, UsdShade, Sdf, Gf, bbox_info)

        preview_stage_path = None
        if not bool(args.no_save_preview_stage):
            preview_stage_path = (
                PROJECT_ROOT
                / "assets"
                / "converted"
                / asset_id
                / f"{asset_id}_preview_stage.usda"
            ).resolve()
            preview_stage_path.parent.mkdir(parents=True, exist_ok=True)
            stage.GetRootLayer().Export(str(preview_stage_path))

        report = build_report(
            asset_id=asset_id,
            usd_path=usd_path,
            registry_record=registry_record,
            bbox_info=bbox_info,
            target_wingspan_m=float(args.target_wingspan_m),
            preview_scale=float(args.preview_scale),
            preview_rotation_deg=[float(value) for value in args.preview_rotation_deg],
            preview_stage_path=preview_stage_path,
        )

        write_json(report_path, report)
        print_preview_report(report, report_path)

        hold_window(simulation_app, float(args.hold_seconds))

    finally:
        simulation_app.close()

    return 0


def create_preview_stage(omni_usd: Any, UsdGeom: Any) -> Any:
    context = omni_usd.get_context()
    context.new_stage()
    stage = context.get_stage()

    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")

    return stage


def configure_renderer(carb: Any) -> None:
    settings = carb.settings.get_settings()
    settings.set("/rtx/rendermode", "RealTimePathTracing")
    settings.set("/rtx/post/dlss/execMode", 1)


def create_lights(stage: Any, UsdLux: Any, Gf: Any) -> None:
    UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight").CreateIntensityAttr(1200.0)

    sun = UsdLux.DistantLight.Define(stage, "/World/Lights/Sun")
    sun.CreateIntensityAttr(3000.0)
    sun.CreateAngleAttr(0.35)
    set_xform(
        stage=stage,
        UsdGeom_module=None,
        prim_path="/World/Lights/Sun",
        translation=(0.0, 0.0, 0.0),
        rotation_deg=(-45.0, 0.0, 35.0),
        scale=(1.0, 1.0, 1.0),
    )


def create_ground_and_axes(
    stage: Any,
    UsdGeom: Any,
    UsdShade: Any,
    Sdf: Any,
    Gf: Any,
) -> None:
    gray = make_material(stage, UsdShade, Sdf, Gf, "/World/Materials/GroundGray", (0.55, 0.55, 0.55))
    red = make_material(stage, UsdShade, Sdf, Gf, "/World/Materials/AxisRed_X", (0.9, 0.05, 0.05))
    green = make_material(stage, UsdShade, Sdf, Gf, "/World/Materials/AxisGreen_Y", (0.05, 0.75, 0.05))
    blue = make_material(stage, UsdShade, Sdf, Gf, "/World/Materials/AxisBlue_Z", (0.05, 0.15, 0.95))

    create_box(stage, UsdGeom, "/World/Ground", (0.0, 0.0, -0.01), (0.0, 0.0, 0.0), (8.0, 8.0, 0.02), gray)

    # Positive-axis markers. These are deliberately simple USD cubes.
    create_box(stage, UsdGeom, "/World/Axes/X_Positive_Red", (1.0, 0.0, 0.04), (0.0, 0.0, 0.0), (2.0, 0.035, 0.035), red)
    create_box(stage, UsdGeom, "/World/Axes/Y_Positive_Green", (0.0, 1.0, 0.07), (0.0, 0.0, 0.0), (0.035, 2.0, 0.035), green)
    create_box(stage, UsdGeom, "/World/Axes/Z_Positive_Blue", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.035, 0.035, 2.0), blue)


def add_usd_reference(
    stage: Any,
    UsdGeom: Any,
    asset_prim_path: str,
    usd_path: Path,
    rotation_xyz_deg: tuple[float, float, float],
    uniform_scale: float,
) -> None:
    if uniform_scale <= 0.0:
        raise ValueError("--preview-scale must be greater than zero.")

    xform = UsdGeom.Xform.Define(stage, asset_prim_path)
    prim = xform.GetPrim()

    # Use a normalized absolute path for robust Windows USD references.
    prim.GetReferences().AddReference(usd_path.as_posix())

    set_xform(
        stage=stage,
        UsdGeom_module=UsdGeom,
        prim_path=asset_prim_path,
        translation=(0.0, 0.0, 0.0),
        rotation_deg=rotation_xyz_deg,
        scale=(uniform_scale, uniform_scale, uniform_scale),
    )


def compute_bbox_info(stage: Any, Usd: Any, UsdGeom: Any, asset_prim_path: str) -> dict[str, Any]:
    prim = stage.GetPrimAtPath(asset_prim_path)

    if not prim or not prim.IsValid():
        raise ValueError(f"Invalid asset prim path: {asset_prim_path}")

    purposes = [
        UsdGeom.Tokens.default_,
        UsdGeom.Tokens.render,
        UsdGeom.Tokens.proxy,
        UsdGeom.Tokens.guide,
    ]

    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), purposes)
    world_bound = bbox_cache.ComputeWorldBound(prim)
    aligned_box = world_bound.ComputeAlignedBox()

    min_vec = aligned_box.GetMin()
    max_vec = aligned_box.GetMax()
    size_vec = aligned_box.GetSize()
    center_vec = aligned_box.GetMidpoint()

    min_xyz = vec3_to_list(min_vec)
    max_xyz = vec3_to_list(max_vec)
    size_xyz = vec3_to_list(size_vec)
    center_xyz = vec3_to_list(center_vec)

    max_dimension = max(size_xyz)
    min_dimension = min(size_xyz)

    geometry_prim_count = count_geometry_prims(prim, UsdGeom)

    warnings: list[str] = []

    if geometry_prim_count == 0:
        warnings.append("No mesh/geometry prims were found below /World/PreviewAsset.")

    if max_dimension <= 1e-6:
        warnings.append("Bounding box is effectively zero-sized. Asset may be invisible or unloaded.")

    if max_dimension > 100.0:
        warnings.append("Asset is extremely large in meters. Scale likely needs correction.")

    if 0.0 < max_dimension < 0.01:
        warnings.append("Asset is extremely small in meters. Scale likely needs correction.")

    origin_offset_m = math.sqrt(center_xyz[0] ** 2 + center_xyz[1] ** 2 + center_xyz[2] ** 2)

    if origin_offset_m > max(2.0 * max_dimension, 1.0):
        warnings.append(
            "Asset bounding-box center is far from the origin. Pivot/origin may need a translation offset."
        )

    return {
        "asset_prim_path": asset_prim_path,
        "geometry_prim_count": geometry_prim_count,
        "bbox_min_world_m": min_xyz,
        "bbox_max_world_m": max_xyz,
        "bbox_center_world_m": center_xyz,
        "bbox_size_world_m": size_xyz,
        "bbox_max_dimension_m": max_dimension,
        "bbox_min_dimension_m": min_dimension,
        "origin_to_bbox_center_distance_m": origin_offset_m,
        "warnings": warnings,
    }


def count_geometry_prims(root_prim: Any, UsdGeom: Any) -> int:
    count = 0

    for prim in root_prim.GetAllChildren():
        if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Gprim):
            count += 1

        count += count_geometry_prims(prim, UsdGeom)

    return count


def create_bbox_wireframe(
    stage: Any,
    UsdGeom: Any,
    UsdShade: Any,
    Sdf: Any,
    Gf: Any,
    bbox_info: dict[str, Any],
) -> None:
    min_xyz = bbox_info["bbox_min_world_m"]
    max_xyz = bbox_info["bbox_max_world_m"]

    if bbox_info["bbox_max_dimension_m"] <= 1e-9:
        return

    yellow = make_material(stage, UsdShade, Sdf, Gf, "/World/Materials/BBoxYellow", (1.0, 0.85, 0.05))

    x0, y0, z0 = min_xyz
    x1, y1, z1 = max_xyz

    corners = {
        "000": (x0, y0, z0),
        "100": (x1, y0, z0),
        "010": (x0, y1, z0),
        "110": (x1, y1, z0),
        "001": (x0, y0, z1),
        "101": (x1, y0, z1),
        "011": (x0, y1, z1),
        "111": (x1, y1, z1),
    }

    edges = [
        ("000", "100"),
        ("010", "110"),
        ("001", "101"),
        ("011", "111"),
        ("000", "010"),
        ("100", "110"),
        ("001", "011"),
        ("101", "111"),
        ("000", "001"),
        ("100", "101"),
        ("010", "011"),
        ("110", "111"),
    ]

    points = []
    counts = []

    for start_key, end_key in edges:
        points.append(Gf.Vec3f(*corners[start_key]))
        points.append(Gf.Vec3f(*corners[end_key]))
        counts.append(2)

    curve = UsdGeom.BasisCurves.Define(stage, "/World/PreviewAsset_BoundingBox")
    curve.CreateTypeAttr("linear")
    curve.CreateCurveVertexCountsAttr(counts)
    curve.CreatePointsAttr(points)
    curve.CreateWidthsAttr([0.01 for _ in points])
    bind_material(curve.GetPrim(), UsdShade, yellow)


def build_report(
    asset_id: str,
    usd_path: Path,
    registry_record: dict[str, Any] | None,
    bbox_info: dict[str, Any],
    target_wingspan_m: float,
    preview_scale: float,
    preview_rotation_deg: list[float],
    preview_stage_path: Path | None,
) -> dict[str, Any]:
    max_dimension = float(bbox_info["bbox_max_dimension_m"])

    if max_dimension > 1e-9 and target_wingspan_m > 0:
        recommended_uniform_scale_to_target_wingspan = target_wingspan_m / max_dimension
    else:
        recommended_uniform_scale_to_target_wingspan = None

    status = "pass" if not bbox_info["warnings"] else "needs_review"

    return {
        "ok": True,
        "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(PROJECT_ROOT),
        "asset_id": asset_id,
        "usd_path": str(usd_path),
        "usd_exists": usd_path.exists(),
        "registry_record_found": registry_record is not None,
        "preview_transform": {
            "uniform_scale": preview_scale,
            "rotation_xyz_deg": preview_rotation_deg,
        },
        "inspection": bbox_info,
        "target_wingspan_m": target_wingspan_m,
        "recommended_uniform_scale_to_target_wingspan": recommended_uniform_scale_to_target_wingspan,
        "preview_stage_path": str(preview_stage_path) if preview_stage_path else None,
        "notes": [
            "Use the bbox_size_world_m values to judge whether scale is plausible.",
            "Use the colored axes in the viewport to inspect orientation: X=red, Y=green, Z=blue.",
            "The yellow wireframe is the computed world-space bounding box.",
            "Do not integrate this asset into the moving glider scene until scale, orientation, and origin are acceptable.",
        ],
    }


def print_preview_report(report: dict[str, Any], report_path: Path) -> None:
    inspection = report["inspection"]

    print("=" * 100)
    print("Asset preview report")
    print("=" * 100)
    print(f"Status: {report['status'].upper()}")
    print(f"Report: {report_path}")
    print(f"USD: {report['usd_path']}")
    print(f"Geometry prim count: {inspection['geometry_prim_count']}")
    print(f"BBox min [m]: {inspection['bbox_min_world_m']}")
    print(f"BBox max [m]: {inspection['bbox_max_world_m']}")
    print(f"BBox center [m]: {inspection['bbox_center_world_m']}")
    print(f"BBox size [m]: {inspection['bbox_size_world_m']}")
    print(f"Max dimension [m]: {inspection['bbox_max_dimension_m']:.6f}")
    print(f"Origin to bbox center distance [m]: {inspection['origin_to_bbox_center_distance_m']:.6f}")
    print(f"Target wingspan [m]: {report['target_wingspan_m']}")
    print(f"Recommended uniform scale: {report['recommended_uniform_scale_to_target_wingspan']}")

    if report["preview_stage_path"]:
        print(f"Preview stage: {report['preview_stage_path']}")

    if inspection["warnings"]:
        print("Warnings:")
        for warning in inspection["warnings"]:
            print(f"  - {warning}")

    print("=" * 100)


def hold_window(simulation_app: Any, hold_seconds: float) -> None:
    if hold_seconds < 0.0:
        raise ValueError("--hold-seconds must be >= 0")

    if hold_seconds == 0.0:
        print("Preview window is open. Close Isaac Sim or press Ctrl+C to stop.")
        while simulation_app.is_running():
            simulation_app.update()
        return

    print(f"Preview window will stay open for {hold_seconds:.1f} seconds.")
    start_time = time.perf_counter()

    while simulation_app.is_running():
        simulation_app.update()
        elapsed = time.perf_counter() - start_time
        if elapsed >= hold_seconds:
            break


def vec3_to_list(value: Any) -> list[float]:
    return [float(value[0]), float(value[1]), float(value[2])]


# -----------------------------------------------------------------------------
# USD helper functions
# -----------------------------------------------------------------------------


def create_box(
    stage: Any,
    UsdGeom: Any,
    path: str,
    translation: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: Any,
) -> Any:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)

    set_xform(
        stage=stage,
        UsdGeom_module=UsdGeom,
        prim_path=path,
        translation=translation,
        rotation_deg=rotation_deg,
        scale=dimensions,
    )
    bind_material(cube.GetPrim(), None, material)

    return cube.GetPrim()


def set_xform(
    stage: Any,
    UsdGeom_module: Any,
    prim_path: str,
    translation: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> None:
    from pxr import Gf, UsdGeom

    prim = stage.GetPrimAtPath(prim_path)

    if not prim or not prim.IsValid():
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
    stage: Any,
    UsdShade: Any,
    Sdf: Any,
    Gf: Any,
    path: str,
    color: tuple[float, float, float],
    roughness: float = 0.55,
    metallic: float = 0.0,
) -> Any:
    parent_path = "/".join(path.split("/")[:-1])

    if parent_path:
        from pxr import UsdGeom

        UsdGeom.Xform.Define(stage, parent_path)

    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")

    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(metallic))

    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

    return material


def bind_material(prim: Any, UsdShade_unused: Any, material: Any) -> None:
    from pxr import UsdShade

    UsdShade.MaterialBindingAPI.Apply(prim)
    UsdShade.MaterialBindingAPI(prim).Bind(material)


if __name__ == "__main__":
    raise SystemExit(main())
