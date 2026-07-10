# validate_outputs.py
#
# Offline validator for the tethered-glider synthetic dataset.
#
# Purpose:
#   Check that a generated two-camera RGB dataset is internally consistent.
#
# This script does NOT import Isaac Sim.
# This script does NOT modify image files.
# This script only reads the output folder and writes a validation report.
#
# Run:
#
#   python validate_outputs.py
#
# Default dataset checked:
#
#   outputs/tethered_glider_basic/
#
# Expected structure:
#
#   outputs/tethered_glider_basic/
#   ├── camera_rig_metadata.json
#   ├── capture_manifest.csv
#   ├── camera_main/
#   │   ├── rgb_0000.png
#   │   ├── rgb_0001.png
#   │   └── ...
#   └── camera_secondary/
#       ├── rgb_0000.png
#       ├── rgb_0001.png
#       └── ...
#
# Optional configuration:
#   Edit DATASET_DIR below if you want to validate a different output folder.

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).parent.resolve()
DATASET_DIR = PROJECT_ROOT / "outputs" / "tethered_glider_basic"

CAMERA_NAMES = [
    "camera_main",
    "camera_secondary",
]

IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
}

RGB_FILENAME_PATTERN = re.compile(r"^rgb_(\d+)\.(png|jpg|jpeg)$", re.IGNORECASE)
CAMERA_PARAMS_PATTERN = re.compile(r"^camera_params_.*\.json$", re.IGNORECASE)

REPORT_FILENAME = "validation_report.json"


@dataclass
class ValidationResult:
    ok: bool
    dataset_dir: str
    created_utc: str
    errors: list[str]
    warnings: list[str]
    summary: dict[str, Any]


def main() -> int:
    result = validate_dataset(DATASET_DIR)

    print("=" * 100)
    print("Dataset validation")
    print("=" * 100)
    print(f"Dataset directory: {result.dataset_dir}")
    print(f"Status: {'PASS' if result.ok else 'FAIL'}")
    print("=" * 100)

    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"  - {error}")
        print("=" * 100)

    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
        print("=" * 100)

    print("Summary:")
    print(json.dumps(result.summary, indent=2))
    print("=" * 100)

    report_path = DATASET_DIR / REPORT_FILENAME

    if DATASET_DIR.exists() and DATASET_DIR.is_dir():
        write_validation_report(report_path, result)
        print(f"Validation report written: {report_path}")

    return 0 if result.ok else 1


def validate_dataset(dataset_dir: Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    summary: dict[str, Any] = {}

    dataset_dir = dataset_dir.resolve()

    if not dataset_dir.exists():
        errors.append(f"Dataset directory does not exist: {dataset_dir}")
        return build_result(dataset_dir, errors, warnings, summary)

    if not dataset_dir.is_dir():
        errors.append(f"Dataset path is not a directory: {dataset_dir}")
        return build_result(dataset_dir, errors, warnings, summary)

    metadata_path = dataset_dir / "camera_rig_metadata.json"
    manifest_path = dataset_dir / "capture_manifest.csv"

    metadata = load_json_file(metadata_path, errors)
    manifest_rows = load_manifest(manifest_path, errors)

    summary["metadata_path"] = str(metadata_path)
    summary["manifest_path"] = str(manifest_path)
    summary["camera_names"] = CAMERA_NAMES

    validate_metadata(metadata, metadata_path, errors, warnings, summary)
    validate_camera_folders(dataset_dir, errors, warnings, summary)
    validate_manifest(dataset_dir, manifest_rows, errors, warnings, summary)
    validate_camera_frame_alignment(dataset_dir, errors, warnings, summary)
    validate_no_camera_params_spam(dataset_dir, errors, warnings, summary)

    return build_result(dataset_dir, errors, warnings, summary)


def build_result(
    dataset_dir: Path,
    errors: list[str],
    warnings: list[str],
    summary: dict[str, Any],
) -> ValidationResult:
    return ValidationResult(
        ok=len(errors) == 0,
        dataset_dir=str(dataset_dir),
        created_utc=datetime.now(timezone.utc).isoformat(),
        errors=errors,
        warnings=warnings,
        summary=summary,
    )


def load_json_file(path: Path, errors: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        errors.append(f"Missing JSON file: {path}")
        return None

    if not path.is_file():
        errors.append(f"Expected file but found non-file path: {path}")
        return None

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON file: {path}; error: {exc}")
        return None

    if not isinstance(data, dict):
        errors.append(f"Expected JSON object in {path}, got {type(data).__name__}")
        return None

    return data


def load_manifest(path: Path, errors: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        errors.append(f"Missing manifest file: {path}")
        return []

    if not path.is_file():
        errors.append(f"Expected file but found non-file path: {path}")
        return []

    try:
        with open(path, "r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
    except csv.Error as exc:
        errors.append(f"Invalid CSV manifest: {path}; error: {exc}")
        return []

    return rows


def validate_metadata(
    metadata: dict[str, Any] | None,
    metadata_path: Path,
    errors: list[str],
    warnings: list[str],
    summary: dict[str, Any],
) -> None:
    if metadata is None:
        return

    required_keys = [
        "created_utc",
        "scene_name",
        "num_frames",
        "time_step_s",
        "camera_rig",
        "camera_definitions",
    ]

    for key in required_keys:
        if key not in metadata:
            errors.append(f"Missing metadata key '{key}' in {metadata_path}")

    num_frames = metadata.get("num_frames")
    time_step_s = metadata.get("time_step_s")
    camera_definitions = metadata.get("camera_definitions")

    if not isinstance(num_frames, int) or isinstance(num_frames, bool) or num_frames <= 0:
        errors.append("camera_rig_metadata.json: num_frames must be a positive integer.")
    else:
        summary["metadata_num_frames"] = num_frames

    if not isinstance(time_step_s, (int, float)) or isinstance(time_step_s, bool) or time_step_s <= 0:
        errors.append("camera_rig_metadata.json: time_step_s must be a positive number.")
    else:
        summary["metadata_time_step_s"] = float(time_step_s)

    if not isinstance(camera_definitions, list):
        errors.append("camera_rig_metadata.json: camera_definitions must be a list.")
        return

    camera_names_found = []

    for camera_def in camera_definitions:
        if not isinstance(camera_def, dict):
            errors.append("camera_rig_metadata.json: each camera definition must be an object.")
            continue

        name = camera_def.get("name")
        output_folder = camera_def.get("output_folder")
        position = camera_def.get("position")

        if not isinstance(name, str):
            errors.append("camera_rig_metadata.json: camera definition missing string 'name'.")
        else:
            camera_names_found.append(name)

        if not isinstance(output_folder, str):
            errors.append(f"camera_rig_metadata.json: camera '{name}' missing string 'output_folder'.")

        if not is_vector3(position):
            errors.append(f"camera_rig_metadata.json: camera '{name}' has invalid position.")

    for expected_name in CAMERA_NAMES:
        if expected_name not in camera_names_found:
            warnings.append(
                f"camera_rig_metadata.json does not list expected camera name: {expected_name}"
            )

    summary["metadata_camera_names_found"] = camera_names_found


def validate_camera_folders(
    dataset_dir: Path,
    errors: list[str],
    warnings: list[str],
    summary: dict[str, Any],
) -> None:
    camera_summary: dict[str, Any] = {}

    for camera_name in CAMERA_NAMES:
        camera_dir = dataset_dir / camera_name

        if not camera_dir.exists():
            errors.append(f"Missing camera folder: {camera_dir}")
            continue

        if not camera_dir.is_dir():
            errors.append(f"Camera path exists but is not a folder: {camera_dir}")
            continue

        image_files = list_rgb_images(camera_dir)
        frame_indices = extract_frame_indices(image_files, errors, camera_name)

        camera_summary[camera_name] = {
            "folder": str(camera_dir),
            "num_rgb_images": len(image_files),
            "first_image": image_files[0].name if image_files else None,
            "last_image": image_files[-1].name if image_files else None,
            "first_frame_index": frame_indices[0] if frame_indices else None,
            "last_frame_index": frame_indices[-1] if frame_indices else None,
        }

        if len(image_files) == 0:
            errors.append(f"No RGB images found in {camera_dir}")

        if not is_contiguous_zero_based_sequence(frame_indices):
            errors.append(
                f"{camera_name}: RGB frame indices are not a contiguous zero-based sequence."
            )

        non_rgb_images = [
            path.name
            for path in camera_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and not RGB_FILENAME_PATTERN.match(path.name)
        ]

        if non_rgb_images:
            warnings.append(
                f"{camera_name}: found image files that do not match rgb_XXXX.png pattern: "
                f"{non_rgb_images[:10]}"
            )

    summary["camera_folders"] = camera_summary


def validate_manifest(
    dataset_dir: Path,
    manifest_rows: list[dict[str, str]],
    errors: list[str],
    warnings: list[str],
    summary: dict[str, Any],
) -> None:
    if not manifest_rows:
        errors.append("Manifest is empty or missing.")
        return

    required_columns = [
        "camera_name",
        "frame_index",
        "sim_timestamp_s",
        "saved_filename",
        "relative_path",
        "renamed_after_capture",
    ]

    manifest_columns = set(manifest_rows[0].keys())

    for column in required_columns:
        if column not in manifest_columns:
            errors.append(f"Manifest missing required column: {column}")

    if errors:
        return

    rows_by_camera: dict[str, list[dict[str, str]]] = {
        camera_name: [] for camera_name in CAMERA_NAMES
    }

    unexpected_camera_names: set[str] = set()

    for row_index, row in enumerate(manifest_rows):
        camera_name = row.get("camera_name", "")

        if camera_name not in rows_by_camera:
            unexpected_camera_names.add(camera_name)
            continue

        rows_by_camera[camera_name].append(row)

        relative_path = row.get("relative_path", "")
        absolute_image_path = dataset_dir / relative_path

        if not absolute_image_path.exists():
            errors.append(
                f"Manifest row {row_index}: referenced image does not exist: {absolute_image_path}"
            )

        if not absolute_image_path.is_file():
            errors.append(
                f"Manifest row {row_index}: referenced path is not a file: {absolute_image_path}"
            )

        try:
            frame_index = int(row.get("frame_index", ""))
        except ValueError:
            errors.append(f"Manifest row {row_index}: frame_index is not an integer.")
            continue

        expected_filename = row.get("saved_filename", "")
        if expected_filename and absolute_image_path.name != expected_filename:
            errors.append(
                f"Manifest row {row_index}: saved_filename does not match relative_path basename. "
                f"saved_filename={expected_filename}, basename={absolute_image_path.name}"
            )

        try:
            float(row.get("sim_timestamp_s", ""))
        except ValueError:
            errors.append(f"Manifest row {row_index}: sim_timestamp_s is not numeric.")

        renamed_value = row.get("renamed_after_capture", "").lower()
        if renamed_value not in {"true", "false"}:
            errors.append(
                f"Manifest row {row_index}: renamed_after_capture must be true or false."
            )

    if unexpected_camera_names:
        errors.append(f"Manifest contains unexpected camera names: {sorted(unexpected_camera_names)}")

    manifest_summary: dict[str, Any] = {}

    for camera_name, rows in rows_by_camera.items():
        frame_indices = []
        timestamps = []

        for row in rows:
            try:
                frame_indices.append(int(row["frame_index"]))
                timestamps.append(float(row["sim_timestamp_s"]))
            except ValueError:
                continue

        manifest_summary[camera_name] = {
            "num_rows": len(rows),
            "first_frame_index": frame_indices[0] if frame_indices else None,
            "last_frame_index": frame_indices[-1] if frame_indices else None,
            "first_timestamp_s": timestamps[0] if timestamps else None,
            "last_timestamp_s": timestamps[-1] if timestamps else None,
        }

        if not is_contiguous_zero_based_sequence(frame_indices):
            errors.append(
                f"Manifest rows for {camera_name} are not a contiguous zero-based sequence."
            )

        if not is_monotonic_non_decreasing(timestamps):
            errors.append(f"Manifest timestamps for {camera_name} are not monotonic.")

    summary["manifest"] = {
        "num_rows_total": len(manifest_rows),
        "by_camera": manifest_summary,
    }


def validate_camera_frame_alignment(
    dataset_dir: Path,
    errors: list[str],
    warnings: list[str],
    summary: dict[str, Any],
) -> None:
    camera_frames: dict[str, list[int]] = {}

    for camera_name in CAMERA_NAMES:
        camera_dir = dataset_dir / camera_name

        if not camera_dir.exists() or not camera_dir.is_dir():
            continue

        image_files = list_rgb_images(camera_dir)
        frame_indices = extract_frame_indices(image_files, errors, camera_name)

        camera_frames[camera_name] = frame_indices

    if len(camera_frames) != len(CAMERA_NAMES):
        return

    main_frames = camera_frames["camera_main"]
    secondary_frames = camera_frames["camera_secondary"]

    if len(main_frames) != len(secondary_frames):
        errors.append(
            "Camera frame count mismatch: "
            f"camera_main={len(main_frames)}, camera_secondary={len(secondary_frames)}"
        )
        return

    if main_frames != secondary_frames:
        errors.append(
            "Camera frame indices do not match exactly between camera_main and camera_secondary."
        )
        return

    summary["stereo_alignment"] = {
        "num_synchronized_frames": len(main_frames),
        "first_frame_index": main_frames[0] if main_frames else None,
        "last_frame_index": main_frames[-1] if main_frames else None,
    }


def validate_no_camera_params_spam(
    dataset_dir: Path,
    errors: list[str],
    warnings: list[str],
    summary: dict[str, Any],
) -> None:
    camera_params_files = [
        path
        for path in dataset_dir.rglob("*.json")
        if CAMERA_PARAMS_PATTERN.match(path.name)
    ]

    summary["num_camera_params_json_files"] = len(camera_params_files)

    if camera_params_files:
        warnings.append(
            "Found per-frame camera_params JSON files. This is not fatal, but usually unwanted. "
            f"Count: {len(camera_params_files)}"
        )


def list_rgb_images(camera_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in camera_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and RGB_FILENAME_PATTERN.match(path.name)
        ],
        key=lambda path: extract_rgb_index(path.name),
    )


def extract_rgb_index(filename: str) -> int:
    match = RGB_FILENAME_PATTERN.match(filename)

    if not match:
        return -1

    return int(match.group(1))


def extract_frame_indices(
    image_files: list[Path],
    errors: list[str],
    camera_name: str,
) -> list[int]:
    indices: list[int] = []

    for image_path in image_files:
        match = RGB_FILENAME_PATTERN.match(image_path.name)

        if not match:
            errors.append(f"{camera_name}: invalid RGB filename: {image_path.name}")
            continue

        indices.append(int(match.group(1)))

    return indices


def is_contiguous_zero_based_sequence(values: list[int]) -> bool:
    if not values:
        return False

    return values == list(range(len(values)))


def is_monotonic_non_decreasing(values: list[float]) -> bool:
    if not values:
        return False

    previous = values[0]

    for value in values[1:]:
        if value < previous:
            return False

        previous = value

    return True


def is_vector3(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False

    return all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def write_validation_report(report_path: Path, result: ValidationResult) -> None:
    with open(report_path, "w", encoding="utf-8") as file:
        json.dump(asdict(result), file, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())