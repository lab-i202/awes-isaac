# utils/profile_io.py
#
# JSON profile loading and saving.
#
# This file must not import Isaac Sim.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def ensure_profiles_dir(project_root: Path) -> Path:
    profiles_dir = project_root / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return profiles_dir


def sanitize_profile_name(name: str) -> str:
    cleaned = name.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_\-]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip("_")

    if not cleaned:
        raise ValueError("Profile name becomes empty after sanitization.")

    return cleaned


def save_profile(profiles_dir: Path, profile: dict[str, Any]) -> Path:
    profile_name = profile.get("profile_name", "")
    file_stem = sanitize_profile_name(profile_name)

    profile = dict(profile)
    profile["profile_name"] = file_stem
    profile["saved_at_utc"] = datetime.now(timezone.utc).isoformat()

    profile_path = profiles_dir / f"{file_stem}.json"

    with open(profile_path, "w", encoding="utf-8") as file:
        json.dump(profile, file, indent=2)

    return profile_path


def load_profile(profile_path: Path) -> dict[str, Any]:
    with open(profile_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Profile JSON must contain an object: {profile_path}")

    return data


def list_profile_paths(profiles_dir: Path) -> list[Path]:
    if not profiles_dir.exists():
        return []

    return sorted(profiles_dir.glob("*.json"))
