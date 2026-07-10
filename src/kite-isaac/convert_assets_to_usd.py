# convert_assets_to_usd.py
#
# Convert registered mesh assets to USD for use in Isaac Sim.
#
# This script is intentionally separate from main.py.
# It does NOT run the tethered glider simulation.
# It does NOT modify the camera/capture pipeline.
#
# First target:
#   assets/bixler_free3d/bixler.fbx
#
# Output:
#   assets/converted/bixler_free3d/bixler_fbx.usd
#   assets/converted/asset_conversion_report.json
#
# Run from the kite-isaac project root:
#
#   python convert_assets_to_usd.py --asset-id bixler_free3d
#
# Re-run and overwrite converted USD:
#
#   python convert_assets_to_usd.py --asset-id bixler_free3d --force

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from utils.asset_io import (
    collect_registry_status,
    get_asset_converted_usd_path,
    get_asset_entry,
    get_asset_source_path,
    is_usd_path,
    load_asset_registry,
)


PROJECT_ROOT = Path(__file__).parent.resolve()
REGISTRY_PATH = PROJECT_ROOT / "assets" / "asset_registry.json"
CONVERSION_REPORT_PATH = PROJECT_ROOT / "assets" / "converted" / "asset_conversion_report.json"

ISAAC_SIM_CONFIG = {
    "headless": False,
    "renderer": "RealTimePathTracing",
    "width": 1280,
    "height": 720,
    "anti_aliasing": 3,
    "sync_loads": True,
}


@dataclass
class AssetConversionRecord:
    asset_id: str
    display_name: str
    source_path: str
    output_usd_path: str
    status: str
    message: str


@dataclass
class AssetConversionReport:
    ok: bool
    created_utc: str
    project_root: str
    records: list[AssetConversionRecord]
    registry_status_after: list[dict[str, Any]]


def main() -> int:
    args = parse_args()

    registry = load_asset_registry(PROJECT_ROOT, REGISTRY_PATH)
    assets = registry["assets"]

    selected_asset_ids = resolve_selected_asset_ids(
        available_asset_ids=sorted(assets.keys()),
        requested_asset_ids=args.asset_id,
    )

    conversion_plan = []
    preflight_errors: list[str] = []

    for asset_id in selected_asset_ids:
        asset_entry = get_asset_entry(registry, asset_id)
        source_path = get_asset_source_path(PROJECT_ROOT, asset_entry)
        output_usd_path = get_asset_converted_usd_path(PROJECT_ROOT, asset_entry)

        if not bool(asset_entry.get("conversion", {}).get("enabled", True)):
            conversion_plan.append((asset_id, asset_entry, source_path, output_usd_path, "disabled"))
            continue

        if not source_path.exists() or not source_path.is_file():
            preflight_errors.append(f"Missing source file for asset '{asset_id}': {source_path}")
            continue

        if is_usd_path(source_path):
            conversion_plan.append((asset_id, asset_entry, source_path, output_usd_path, "already_usd"))
            continue

        if output_usd_path.exists() and output_usd_path.is_file() and not args.force:
            conversion_plan.append((asset_id, asset_entry, source_path, output_usd_path, "skip_existing"))
            continue

        conversion_plan.append((asset_id, asset_entry, source_path, output_usd_path, "convert"))

    if preflight_errors:
        for error in preflight_errors:
            print(f"[error] {error}")
        return 2

    records: list[AssetConversionRecord] = []
    needs_isaac = any(item[4] == "convert" for item in conversion_plan)

    if not needs_isaac:
        for asset_id, asset_entry, source_path, output_usd_path, action in conversion_plan:
            records.append(make_non_conversion_record(asset_id, asset_entry, source_path, output_usd_path, action))

        report = build_report(records, registry)
        write_report(report)
        print_report(report)
        return 0 if report.ok else 1

    from isaacsim import SimulationApp

    simulation_app = SimulationApp(
        {
            **ISAAC_SIM_CONFIG,
            "headless": bool(args.headless),
        }
    )

    exit_code = 1

    try:
        enable_asset_converter_extension(simulation_app)

        import omni.kit.asset_converter as asset_converter

        for asset_id, asset_entry, source_path, output_usd_path, action in conversion_plan:
            if action != "convert":
                records.append(make_non_conversion_record(asset_id, asset_entry, source_path, output_usd_path, action))
                continue

            output_usd_path.parent.mkdir(parents=True, exist_ok=True)

            if args.force and output_usd_path.exists():
                output_usd_path.unlink()

            print("=" * 100)
            print(f"Converting asset: {asset_id}")
            print(f"Source: {source_path}")
            print(f"Output: {output_usd_path}")
            print("=" * 100)

            success, message = run_asset_conversion(
                asset_converter_module=asset_converter,
                asset_entry=asset_entry,
                source_path=source_path,
                output_usd_path=output_usd_path,
            )

            # Give Kit one frame to flush file operations and extension state.
            simulation_app.update()

            records.append(
                AssetConversionRecord(
                    asset_id=asset_id,
                    display_name=str(asset_entry.get("display_name", asset_id)),
                    source_path=str(source_path),
                    output_usd_path=str(output_usd_path),
                    status="converted" if success else "failed",
                    message=message,
                )
            )

        # Important: write the report before closing SimulationApp. In some
        # Isaac Sim/Kit runs, SimulationApp.close() can terminate the process
        # before code placed after close() has a chance to execute.
        report = build_report(records, registry)
        write_report(report)
        print_report(report)
        exit_code = 0 if report.ok else 1

    finally:
        simulation_app.close()

    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert registered mesh assets to USD.")
    parser.add_argument(
        "--asset-id",
        action="append",
        default=None,
        help="Asset ID from assets/asset_registry.json. May be repeated. Default: all assets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing converted USD files.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Isaac Sim without opening the UI while converting.",
    )
    return parser.parse_args()


def resolve_selected_asset_ids(available_asset_ids: list[str], requested_asset_ids: list[str] | None) -> list[str]:
    if not requested_asset_ids:
        return available_asset_ids

    missing = sorted(set(requested_asset_ids) - set(available_asset_ids))
    if missing:
        raise KeyError(
            "Requested asset IDs are not present in the registry: "
            f"{missing}. Available: {available_asset_ids}"
        )

    return requested_asset_ids


def enable_asset_converter_extension(simulation_app: Any) -> None:
    try:
        from isaacsim.core.utils.extensions import enable_extension

        enable_extension("omni.kit.asset_converter")
        simulation_app.update()
    except Exception as exc:
        print(f"[warning] Could not explicitly enable omni.kit.asset_converter: {exc}")
        print("[warning] Continuing; the extension may already be loaded by the Isaac experience.")


def make_non_conversion_record(
    asset_id: str,
    asset_entry: dict[str, Any],
    source_path: Path,
    output_usd_path: Path,
    action: str,
) -> AssetConversionRecord:
    if action == "skip_existing":
        status = "skipped"
        message = "Converted USD already exists. Use --force to overwrite."
    elif action == "already_usd":
        status = "skipped"
        message = "Source is already USD. No conversion needed."
    elif action == "disabled":
        status = "skipped"
        message = "Conversion is disabled for this asset in the registry."
    else:
        status = "skipped"
        message = f"No conversion action taken: {action}"

    return AssetConversionRecord(
        asset_id=asset_id,
        display_name=str(asset_entry.get("display_name", asset_id)),
        source_path=str(source_path),
        output_usd_path=str(output_usd_path),
        status=status,
        message=message,
    )


def run_asset_conversion(
    asset_converter_module: Any,
    asset_entry: dict[str, Any],
    source_path: Path,
    output_usd_path: Path,
) -> tuple[bool, str]:
    async def _convert() -> tuple[bool, str]:
        task_manager = asset_converter_module.get_instance()
        context = build_asset_converter_context(asset_converter_module, asset_entry)

        progress_callback = make_progress_callback(source_path.name)

        task = task_manager.create_converter_task(
            str(source_path),
            str(output_usd_path),
            progress_callback,
            context,
        )

        success = await task.wait_until_finished()

        if not success:
            status = safe_call(task, "get_status", default="unknown_status")
            error_message = safe_call(task, "get_error_message", default="unknown_error")
            return False, f"Converter failed. status={status}; error={error_message}"

        if not output_usd_path.exists() or not output_usd_path.is_file():
            return False, "Converter reported success, but output USD file was not found."

        return True, "Conversion succeeded."

    loop = asyncio.get_event_loop()
    return loop.run_until_complete(_convert())


def build_asset_converter_context(asset_converter_module: Any, asset_entry: dict[str, Any]) -> Any:
    conversion_cfg = asset_entry.get("conversion", {})

    context = asset_converter_module.AssetConverterContext()

    set_context_attr(context, "ignore_materials", False)
    set_context_attr(context, "ignore_animations", bool(conversion_cfg.get("ignore_animations", True)))
    set_context_attr(context, "ignore_camera", bool(conversion_cfg.get("ignore_cameras", True)))
    set_context_attr(context, "ignore_light", bool(conversion_cfg.get("ignore_lights", True)))
    set_context_attr(context, "single_mesh", bool(conversion_cfg.get("single_mesh", False)))
    set_context_attr(context, "smooth_normals", bool(conversion_cfg.get("smooth_normals", True)))
    set_context_attr(context, "export_preview_surface", bool(conversion_cfg.get("export_preview_surface", True)))
    set_context_attr(context, "use_meter_as_world_unit", bool(conversion_cfg.get("use_meter_as_world_unit", True)))
    set_context_attr(context, "convert_fbx_to_z_up", bool(conversion_cfg.get("convert_fbx_to_z_up", True)))
    set_context_attr(context, "keep_all_materials", bool(conversion_cfg.get("keep_all_materials", True)))
    set_context_attr(context, "merge_all_meshes", bool(conversion_cfg.get("merge_all_meshes", False)))
    set_context_attr(context, "baking_scales", bool(conversion_cfg.get("baking_scales", False)))

    return context


def set_context_attr(context: Any, name: str, value: Any) -> None:
    try:
        setattr(context, name, value)
    except Exception as exc:
        print(f"[warning] Could not set AssetConverterContext.{name}={value!r}: {exc}")


def make_progress_callback(label: str) -> Callable[[int, int], None]:
    last_printed_percent = {"value": -1}

    def progress_callback(current_step: int, total: int) -> None:
        if total <= 0:
            print(f"[{label}] conversion step {current_step}")
            return

        percent = int(round(100.0 * float(current_step) / float(total)))
        if percent != last_printed_percent["value"]:
            last_printed_percent["value"] = percent
            print(f"[{label}] {current_step}/{total} ({percent}%)")

    return progress_callback


def safe_call(obj: Any, method_name: str, default: Any) -> Any:
    method = getattr(obj, method_name, None)
    if method is None:
        return default

    try:
        return method()
    except Exception:
        return default


def build_report(records: list[AssetConversionRecord], registry: dict[str, Any]) -> AssetConversionReport:
    ok = all(record.status in {"converted", "skipped"} for record in records)

    return AssetConversionReport(
        ok=ok,
        created_utc=datetime.now(timezone.utc).isoformat(),
        project_root=str(PROJECT_ROOT),
        records=records,
        registry_status_after=collect_registry_status(PROJECT_ROOT, registry),
    )


def write_report(report: AssetConversionReport) -> None:
    CONVERSION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(CONVERSION_REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(asdict(report), file, indent=2)


def print_report(report: AssetConversionReport) -> None:
    print("=" * 100)
    print("Asset conversion report")
    print("=" * 100)
    print(f"Status: {'PASS' if report.ok else 'FAIL'}")
    print(f"Report: {CONVERSION_REPORT_PATH}")
    print("=" * 100)

    for record in report.records:
        print(f"Asset: {record.asset_id}")
        print(f"  Source: {record.source_path}")
        print(f"  Output: {record.output_usd_path}")
        print(f"  Status: {record.status}")
        print(f"  Message: {record.message}")

    print("=" * 100)


if __name__ == "__main__":
    raise SystemExit(main())
