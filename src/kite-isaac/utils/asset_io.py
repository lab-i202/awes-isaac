# utils/asset_io.py
#
# Asset registry helpers for the kite-isaac project.
#
# This module intentionally does NOT import Isaac Sim.
# It is safe to use from normal Python scripts and from the Tkinter GUI.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ASSET_REGISTRY_RELATIVE_PATH = Path("assets") / "asset_registry.json"
ENVIRONMENTS_RELATIVE_PATH = Path("assets") / "environments"



SUPPORTED_SOURCE_EXTENSIONS = {
    ".fbx",
    ".obj",
    ".stl",
    ".usd",
    ".usda",
    ".usdc",
}

USD_EXTENSIONS = {
    ".usd",
    ".usda",
    ".usdc",
}

ENVIRONMENT_SCENE_EXTENSIONS = {
    ".usd",
    ".usda",
    ".usdc",
}


class AssetRegistryError(ValueError):
    """Raised when the asset registry is invalid or unusable."""


def default_asset_registry_path(project_root: Path) -> Path:
    return project_root / ASSET_REGISTRY_RELATIVE_PATH


def load_asset_registry(project_root: Path, registry_path: Path | None = None) -> dict[str, Any]:
    resolved_registry_path = registry_path or default_asset_registry_path(project_root)
    resolved_registry_path = resolved_registry_path.resolve()

    if not resolved_registry_path.exists():
        raise FileNotFoundError(f"Asset registry does not exist: {resolved_registry_path}")

    if not resolved_registry_path.is_file():
        raise AssetRegistryError(f"Asset registry path is not a file: {resolved_registry_path}")

    try:
        with open(resolved_registry_path, "r", encoding="utf-8") as file:
            registry = json.load(file)
    except json.JSONDecodeError as exc:
        raise AssetRegistryError(
            f"Invalid JSON asset registry: {resolved_registry_path}\nJSON error: {exc}"
        ) from exc

    validate_asset_registry(registry)
    return registry


def save_asset_registry(project_root: Path, registry: dict[str, Any], registry_path: Path | None = None) -> None:
    validate_asset_registry(registry)

    resolved_registry_path = registry_path or default_asset_registry_path(project_root)
    resolved_registry_path = resolved_registry_path.resolve()
    resolved_registry_path.parent.mkdir(parents=True, exist_ok=True)

    with open(resolved_registry_path, "w", encoding="utf-8") as file:
        json.dump(registry, file, indent=2)


def validate_asset_registry(registry: dict[str, Any]) -> None:
    if not isinstance(registry, dict):
        raise AssetRegistryError(f"Asset registry must be a JSON object, got {type(registry).__name__}.")

    assets = registry.get("assets")
    if not isinstance(assets, dict):
        raise AssetRegistryError("Asset registry must contain an object key named 'assets'.")

    for asset_id, asset_entry in assets.items():
        validate_asset_entry(asset_id, asset_entry)


def validate_asset_entry(asset_id: str, asset_entry: Any) -> None:
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise AssetRegistryError("Asset IDs must be non-empty strings.")

    if not isinstance(asset_entry, dict):
        raise AssetRegistryError(f"Asset entry '{asset_id}' must be an object.")

    source = asset_entry.get("source")
    converted = asset_entry.get("converted")

    if not isinstance(source, dict):
        raise AssetRegistryError(f"Asset '{asset_id}' is missing object key 'source'.")

    if not isinstance(converted, dict):
        raise AssetRegistryError(f"Asset '{asset_id}' is missing object key 'converted'.")

    preferred_file = source.get("preferred_file")
    usd_file = converted.get("usd_file")

    if not isinstance(preferred_file, str) or not preferred_file.strip():
        raise AssetRegistryError(f"Asset '{asset_id}' must define source.preferred_file as a non-empty string.")

    if not isinstance(usd_file, str) or not usd_file.strip():
        raise AssetRegistryError(f"Asset '{asset_id}' must define converted.usd_file as a non-empty string.")

    preferred_suffix = Path(preferred_file).suffix.lower()
    usd_suffix = Path(usd_file).suffix.lower()

    if preferred_suffix not in SUPPORTED_SOURCE_EXTENSIONS:
        raise AssetRegistryError(
            f"Asset '{asset_id}' has unsupported preferred source extension '{preferred_suffix}'. "
            f"Supported: {sorted(SUPPORTED_SOURCE_EXTENSIONS)}"
        )

    if usd_suffix not in USD_EXTENSIONS:
        raise AssetRegistryError(
            f"Asset '{asset_id}' converted.usd_file must end in one of {sorted(USD_EXTENSIONS)}."
        )


def get_assets(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_asset_registry(registry)
    return registry["assets"]


def get_asset_entry(registry: dict[str, Any], asset_id: str) -> dict[str, Any]:
    assets = get_assets(registry)

    if asset_id not in assets:
        raise KeyError(f"Asset ID not found in registry: {asset_id}")

    return assets[asset_id]


def resolve_project_path(project_root: Path, path_value: str | Path) -> Path:
    path = Path(path_value)

    if path.is_absolute():
        return path.resolve()

    return (project_root / path).resolve()


def get_asset_source_path(project_root: Path, asset_entry: dict[str, Any]) -> Path:
    return resolve_project_path(project_root, asset_entry["source"]["preferred_file"])


def get_asset_converted_usd_path(project_root: Path, asset_entry: dict[str, Any]) -> Path:
    return resolve_project_path(project_root, asset_entry["converted"]["usd_file"])


def is_usd_path(path: Path) -> bool:
    return path.suffix.lower() in USD_EXTENSIONS


def get_asset_status(project_root: Path, asset_id: str, asset_entry: dict[str, Any]) -> dict[str, Any]:
    source_path = get_asset_source_path(project_root, asset_entry)
    converted_usd_path = get_asset_converted_usd_path(project_root, asset_entry)

    return {
        "asset_id": asset_id,
        "display_name": asset_entry.get("display_name", asset_id),
        "source_path": str(source_path),
        "source_exists": source_path.exists() and source_path.is_file(),
        "source_extension": source_path.suffix.lower(),
        "converted_usd_path": str(converted_usd_path),
        "converted_usd_exists": converted_usd_path.exists() and converted_usd_path.is_file(),
        "conversion_enabled": bool(asset_entry.get("conversion", {}).get("enabled", True)),
    }


def collect_registry_status(project_root: Path, registry: dict[str, Any]) -> list[dict[str, Any]]:
    assets = get_assets(registry)
    return [get_asset_status(project_root, asset_id, asset_entry) for asset_id, asset_entry in assets.items()]


# -----------------------------------------------------------------------------
# Environment asset discovery
# -----------------------------------------------------------------------------


def default_environments_root(project_root: Path) -> Path:
    return project_root / ENVIRONMENTS_RELATIVE_PATH


def discover_environment_assets(project_root: Path) -> list[dict[str, Any]]:
    """Discover local environment folders under assets/environments/.

    Expected minimal layout:
        assets/environments/<environment_id>/metadata.json
        assets/environments/<environment_id>/scene.usda

    metadata.json is optional for discovery, but strongly recommended.
    If it is missing, the folder name is used as asset_id/display_name and the
    first scene.usd/usda/usdc file is used.
    """

    environments_root = default_environments_root(project_root)
    if not environments_root.exists() or not environments_root.is_dir():
        return []

    entries: list[dict[str, Any]] = []

    for folder in sorted(environments_root.iterdir(), key=lambda item: item.name.lower()):
        if not folder.is_dir():
            continue

        asset_id = folder.name
        metadata_path = folder / "metadata.json"
        metadata: dict[str, Any] = {}

        if metadata_path.exists() and metadata_path.is_file():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except json.JSONDecodeError:
                metadata = {}

        asset_id = str(metadata.get("asset_id", asset_id)).strip() or folder.name
        display_name = str(metadata.get("display_name", asset_id)).strip() or asset_id
        scene_path_value = str(metadata.get("scene_path", "scene.usda")).strip() or "scene.usda"
        scene_path = resolve_project_path(project_root, ENVIRONMENTS_RELATIVE_PATH / folder.name / scene_path_value)

        if not scene_path.exists() or not scene_path.is_file():
            scene_candidates = [
                path for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in ENVIRONMENT_SCENE_EXTENSIONS
            ]
            if scene_candidates:
                scene_path = sorted(scene_candidates, key=lambda item: item.name.lower())[0].resolve()
                try:
                    scene_path_value = str(scene_path.relative_to(folder)).replace("\\", "/")
                except ValueError:
                    scene_path_value = scene_path.name

        try:
            relative_scene_path = str(scene_path.relative_to(project_root)).replace("\\", "/")
        except ValueError:
            relative_scene_path = str(scene_path).replace("\\", "/")

        entries.append(
            {
                "asset_id": asset_id,
                "display_name": display_name,
                "folder_name": folder.name,
                "folder_path": str(folder.resolve()),
                "metadata_path": str(metadata_path.resolve()),
                "scene_path": relative_scene_path,
                "scene_path_in_folder": scene_path_value.replace("\\", "/"),
                "scene_path_abs": str(scene_path.resolve()),
                "scene_exists": scene_path.exists() and scene_path.is_file(),
                "metadata": metadata,
            }
        )

    # Show usable assets first.
    return sorted(entries, key=lambda item: (not bool(item["scene_exists"]), str(item["asset_id"]).lower()))


def get_environment_asset_entry(project_root: Path, environment_id: str) -> dict[str, Any]:
    environment_id = str(environment_id).strip()
    if not environment_id:
        raise KeyError("Environment asset ID cannot be empty.")

    entries = discover_environment_assets(project_root)
    for entry in entries:
        if entry["asset_id"] == environment_id or entry["folder_name"] == environment_id:
            return entry

    raise KeyError(f"Environment asset ID not found under assets/environments: {environment_id}")


def get_environment_scene_path(project_root: Path, environment_id: str) -> Path:
    entry = get_environment_asset_entry(project_root, environment_id)
    return Path(str(entry["scene_path_abs"])).resolve()
