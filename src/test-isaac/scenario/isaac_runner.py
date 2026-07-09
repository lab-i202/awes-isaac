# scenario/isaac_runner.py
#
# Isaac Sim runner.
#
# This module imports Isaac Sim. Import it only after the GUI closes.

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from isaacsim import SimulationApp


def run_isaac_sim(
    profile: dict[str, Any],
    project_root: Path,
    profile_path: Path,
) -> None:
    launch_config = dict(profile["launch_config"])

    simulation_app = SimulationApp(launch_config)

    try:
        import carb
        import omni.replicator.core as rep

        from scenario.scene_builder import (
            clear_stage,
            create_environment,
            create_lights,
            update_motion,
        )

        settings = carb.settings.get_settings()

        output_root = project_root / profile.get("output", {}).get("root", "render_outputs")
        output_root.mkdir(parents=True, exist_ok=True)

        profile_name = profile["profile_name"]
        output_dir = output_root / profile_name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Keeps Replicator images and metrics under the same project output tree.
        settings.set("/omni/replicator/backends/disk/root_dir", str(output_root.resolve()))

        print("=" * 100)
        print("Isaac Sim run")
        print("=" * 100)
        print(f"Profile name: {profile_name}")
        print(f"Profile JSON: {profile_path}")
        print(f"Renderer: {launch_config.get('renderer')}")
        print(f"Resolution: {profile['capture']['resolution']}")
        print(f"RT subframes: {profile['capture']['rt_subframes']}")
        print(f"Output directory: {output_dir.resolve()}")
        print("=" * 100)

        try:
            simulation_app.reset_render_settings()
        except Exception as exc:
            print(f"[warning] reset_render_settings failed: {exc}")

        apply_carb_settings(settings=settings, settings_dict=profile.get("carb_settings", {}))

        stage = clear_stage()
        create_environment(stage)
        create_lights(stage)

        camera, render_product, writer = create_camera_and_writer(
            rep=rep,
            profile_name=profile_name,
            output_dir_name=profile_name,
            camera_cfg=profile["camera"],
            capture_cfg=profile["capture"],
        )

        scene_cfg = profile["scene"]
        capture_cfg = profile["capture"]
        diagnostics_cfg = profile.get("diagnostics", {})

        warmup_frames = int(scene_cfg.get("warmup_frames", 20))
        num_frames = int(scene_cfg.get("num_frames", 90))
        rt_subframes = int(capture_cfg.get("rt_subframes", 1))

        print_every_n_frames = int(diagnostics_cfg.get("print_every_n_frames", 10))
        rolling_window_frames = int(diagnostics_cfg.get("rolling_window_frames", 30))
        gpu_telemetry = bool(diagnostics_cfg.get("gpu_telemetry", False))

        print(f"Warmup frames: {warmup_frames}")
        print(f"Frames captured: {num_frames}")
        print(f"GPU telemetry: {gpu_telemetry}")

        print("Starting warmup...")
        warmup_start = time.perf_counter()
        for _ in range(warmup_frames):
            simulation_app.update()
        print(f"Warmup completed in {time.perf_counter() - warmup_start:.3f} seconds")

        frame_metrics: list[dict[str, Any]] = []
        gpu_metrics: list[dict[str, Any]] = []
        gpu_query_overhead_seconds = 0.0

        run_start = time.perf_counter()

        for frame_index in range(num_frames):
            frame_start = time.perf_counter()

            motion_start = time.perf_counter()
            update_motion(
                stage=stage,
                frame_index=frame_index,
                total_frames=num_frames,
                scene_cfg=scene_cfg,
            )
            motion_seconds = time.perf_counter() - motion_start

            sim_update_start = time.perf_counter()
            simulation_app.update()
            sim_update_seconds = time.perf_counter() - sim_update_start

            capture_start = time.perf_counter()
            rep.orchestrator.step(
                rt_subframes=rt_subframes,
                pause_timeline=True,
                delta_time=0.0,
                wait_for_render=True,
            )
            capture_seconds = time.perf_counter() - capture_start

            frame_total_seconds = time.perf_counter() - frame_start

            frame_metric = {
                "frame": frame_index,
                "motion_update_seconds": motion_seconds,
                "simulation_update_seconds": sim_update_seconds,
                "capture_seconds": capture_seconds,
                "frame_total_seconds": frame_total_seconds,
                "capture_fps": safe_inverse(capture_seconds),
                "frame_fps": safe_inverse(frame_total_seconds),
            }
            frame_metrics.append(frame_metric)

            should_print = (
                frame_index == 0
                or (frame_index + 1) % print_every_n_frames == 0
                or frame_index == num_frames - 1
            )

            if should_print:
                gpu_stats = None
                gpu_query_seconds = 0.0

                if gpu_telemetry:
                    gpu_query_start = time.perf_counter()
                    gpu_stats = query_nvidia_smi()
                    gpu_query_seconds = time.perf_counter() - gpu_query_start
                    gpu_query_overhead_seconds += gpu_query_seconds

                    if gpu_stats is not None:
                        gpu_metrics.append(
                            {
                                "frame": frame_index,
                                "query_seconds": gpu_query_seconds,
                                **gpu_stats,
                            }
                        )

                elapsed = time.perf_counter() - run_start
                elapsed_minus_gpu_query = elapsed - gpu_query_overhead_seconds
                completed_frames = frame_index + 1
                overall_fps = (
                    completed_frames / elapsed_minus_gpu_query
                    if elapsed_minus_gpu_query > 0
                    else None
                )

                rolling_window = frame_metrics[-rolling_window_frames:]
                rolling_seconds = sum(item["frame_total_seconds"] for item in rolling_window)
                rolling_fps = (
                    len(rolling_window) / rolling_seconds if rolling_seconds > 0 else None
                )

                print(
                    f"[frame {frame_index + 1:04d}/{num_frames:04d}] "
                    f"capture={capture_seconds:.4f}s "
                    f"capture_fps={safe_inverse(capture_seconds):.2f} "
                    f"frame_total={frame_total_seconds:.4f}s "
                    f"frame_fps={safe_inverse(frame_total_seconds):.2f} "
                    f"rolling_fps={format_optional_float(rolling_fps)} "
                    f"overall_fps={format_optional_float(overall_fps)} "
                    f"motion={motion_seconds:.5f}s "
                    f"sim_update={sim_update_seconds:.5f}s "
                    f"{format_gpu_stats(gpu_stats)}",
                    flush=True,
                )

        flush_start = time.perf_counter()
        rep.orchestrator.wait_until_complete()
        writer_flush_seconds = time.perf_counter() - flush_start

        total_run_seconds = time.perf_counter() - run_start
        total_run_seconds_excluding_gpu_query = total_run_seconds - gpu_query_overhead_seconds

        metrics = build_metrics(
            profile=profile,
            profile_path=profile_path,
            frame_metrics=frame_metrics,
            gpu_metrics=gpu_metrics,
            writer_flush_seconds=writer_flush_seconds,
            total_run_seconds=total_run_seconds,
            total_run_seconds_excluding_gpu_query=total_run_seconds_excluding_gpu_query,
            gpu_query_overhead_seconds=gpu_query_overhead_seconds,
        )

        metrics_path = output_dir / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as file:
            json.dump(metrics, file, indent=2)

        print("=" * 100)
        print("Performance summary")
        print("=" * 100)
        print(f"Average capture FPS: {metrics['average_capture_fps']:.2f}")
        print(f"Average total frame FPS: {metrics['average_frame_fps']:.2f}")
        print(f"Overall measured frame FPS: {metrics['overall_measured_frame_fps']:.2f}")
        print(f"Average motion update seconds/frame: {metrics['average_motion_update_seconds']:.6f}")
        print(f"Average simulation update seconds/frame: {metrics['average_simulation_update_seconds']:.6f}")
        print(f"Writer flush seconds: {writer_flush_seconds:.6f}")
        print(f"Metrics saved: {metrics_path.resolve()}")
        print("=" * 100)

        try:
            writer.detach()
        except Exception as exc:
            print(f"[warning] writer.detach failed: {exc}")

        try:
            render_product.destroy()
        except Exception as exc:
            print(f"[warning] render_product.destroy failed: {exc}")

    finally:
        simulation_app.close()


def apply_carb_settings(settings: Any, settings_dict: dict[str, Any]) -> None:
    for key, value in settings_dict.items():
        settings.set(key, value)
        print(f"[carb] {key} = {value}")


def create_camera_and_writer(
    rep: Any,
    profile_name: str,
    output_dir_name: str,
    camera_cfg: dict[str, Any],
    capture_cfg: dict[str, Any],
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
        output_dir=output_dir_name,
        rgb=bool(capture_cfg.get("rgb", True)),
        semantic_segmentation=bool(capture_cfg.get("semantic_segmentation", False)),
        bounding_box_2d_tight=bool(capture_cfg.get("bounding_box_2d_tight", False)),
    )
    writer.attach([render_product])

    return camera, render_product, writer


def safe_inverse(value: float) -> float:
    if value <= 0:
        return 0.0
    return 1.0 / value


def format_optional_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def query_nvidia_smi() -> dict[str, Any] | None:
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


def format_gpu_stats(gpu_stats: dict[str, Any] | None) -> str:
    if gpu_stats is None:
        return "gpu=n/a"

    return (
        f"gpu={gpu_stats.get('gpu_util_percent')}% "
        f"vram={gpu_stats.get('memory_used_mib')}/{gpu_stats.get('memory_total_mib')}MiB "
        f"temp={gpu_stats.get('temperature_c')}C "
        f"power={gpu_stats.get('power_draw_w')}/{gpu_stats.get('power_limit_w')}W"
    )


def build_metrics(
    profile: dict[str, Any],
    profile_path: Path,
    frame_metrics: list[dict[str, Any]],
    gpu_metrics: list[dict[str, Any]],
    writer_flush_seconds: float,
    total_run_seconds: float,
    total_run_seconds_excluding_gpu_query: float,
    gpu_query_overhead_seconds: float,
) -> dict[str, Any]:
    average_capture_seconds = average([item["capture_seconds"] for item in frame_metrics])
    average_frame_seconds = average([item["frame_total_seconds"] for item in frame_metrics])
    average_motion_seconds = average([item["motion_update_seconds"] for item in frame_metrics])
    average_sim_update_seconds = average([item["simulation_update_seconds"] for item in frame_metrics])

    overall_measured_frame_fps = (
        len(frame_metrics) / total_run_seconds_excluding_gpu_query
        if total_run_seconds_excluding_gpu_query > 0
        else 0.0
    )

    return {
        "profile_name": profile["profile_name"],
        "profile_path": str(profile_path),
        "launch_config": profile.get("launch_config", {}),
        "carb_settings": profile.get("carb_settings", {}),
        "capture": profile.get("capture", {}),
        "scene": profile.get("scene", {}),
        "score": profile.get("score", {}),
        "num_frames": len(frame_metrics),
        "average_capture_seconds": average_capture_seconds,
        "average_capture_fps": safe_inverse(average_capture_seconds),
        "average_frame_total_seconds": average_frame_seconds,
        "average_frame_fps": safe_inverse(average_frame_seconds),
        "overall_measured_frame_fps": overall_measured_frame_fps,
        "average_motion_update_seconds": average_motion_seconds,
        "average_simulation_update_seconds": average_sim_update_seconds,
        "writer_flush_seconds": writer_flush_seconds,
        "total_run_seconds": total_run_seconds,
        "total_run_seconds_excluding_gpu_query": total_run_seconds_excluding_gpu_query,
        "gpu_query_overhead_seconds": gpu_query_overhead_seconds,
        "gpu_metrics": gpu_metrics,
        "frames": frame_metrics,
    }


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
