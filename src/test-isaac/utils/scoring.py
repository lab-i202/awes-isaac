# utils/scoring.py
#
# Heuristic smoothness and quality estimates.
#
# These numbers are not measured FPS. They are only a fast user-facing guide.

from __future__ import annotations

import math
from typing import Any


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def score_profile_options(options: dict[str, Any]) -> dict[str, Any]:
    renderer = options.get("renderer", "RealTimePathTracing")
    width = int(options.get("width", 1280))
    height = int(options.get("height", 720))
    rt_subframes = max(1, int(options.get("rt_subframes", 1)))
    path_spp = max(1, int(options.get("path_spp", 1)))
    max_bounces = max(1, int(options.get("max_bounces", 1)))
    dlss_exec_mode = int(options.get("dlss_exec_mode", 1))
    semantic = bool(options.get("semantic_segmentation", False))
    boxes = bool(options.get("bounding_box_2d_tight", False))

    pixels = width * height
    base_pixels = 1280 * 720
    resolution_factor = pixels / base_pixels

    if renderer == "MinimalRendering":
        smoothness = 95.0
        quality = 20.0
        notes = ["RTX Minimal is fastest, but it is not a photorealism test."]
    elif renderer == "RaytracedLighting":
        smoothness = 75.0
        quality = 38.0
        notes = ["Legacy RaytracedLighting is mainly a compatibility baseline."]
    elif renderer == "RealTimePathTracing":
        smoothness = 70.0
        quality = 68.0
        notes = ["RTX Real-Time 2.0 is the practical robotics/synthetic-data starting point."]
    elif renderer == "PathTracing":
        smoothness = 35.0
        quality = 90.0
        notes = ["Path tracing gives the best visual reference, but it is slow."]
    else:
        smoothness = 50.0
        quality = 50.0
        notes = ["Unknown renderer. Scores are unreliable."]

    # Higher resolution reduces smoothness and increases useful visual detail.
    smoothness -= 16.0 * math.log2(max(1.0, resolution_factor))
    quality += 8.0 * math.log2(max(1.0, resolution_factor))

    # More subframes reduce smoothness but help temporal/render stability.
    smoothness -= 7.0 * math.log2(rt_subframes)
    quality += 4.0 * math.log2(rt_subframes)

    if renderer == "PathTracing":
        smoothness -= 6.0 * math.log2(path_spp)
        smoothness -= 3.0 * math.log2(max_bounces)
        quality += 5.0 * math.log2(path_spp)
        quality += 3.0 * math.log2(max_bounces)

    # DLSS mode: 0 performance, 1 balanced, 2 quality.
    if dlss_exec_mode == 0:
        smoothness += 8.0
        quality -= 5.0
        notes.append("DLSS Performance favors smoothness over image quality.")
    elif dlss_exec_mode == 2:
        smoothness -= 4.0
        quality += 5.0
        notes.append("DLSS Quality favors image quality over speed.")

    if semantic:
        smoothness -= 5.0
        notes.append("Semantic segmentation adds annotation overhead.")

    if boxes:
        smoothness -= 3.0
        notes.append("2D bounding boxes add annotation overhead.")

    if rt_subframes == 1:
        notes.append("rt_subframes=1 should be fastest, but image stability may be worse.")
    elif rt_subframes >= 16:
        notes.append("High rt_subframes improves accumulation but can be slow.")

    smoothness = clamp(smoothness)
    quality = clamp(quality)

    return {
        "smoothness_score": smoothness,
        "quality_score": quality,
        "smoothness_label": label_score(smoothness),
        "quality_label": label_score(quality),
        "notes": notes,
    }


def label_score(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium-high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "very low"
