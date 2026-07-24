# utils/run_logger.py
#
# Lightweight structured run logging and timing utilities.
# Pure Python. Does not import Isaac Sim.

from __future__ import annotations

import csv
import json
import logging
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_run_id(scene_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(scene_name).strip())
    safe = safe.strip("_") or "scene"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{safe}"


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def write(self, payload: dict[str, Any]) -> None:
        self._file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


class RunLogger:
    """Human-readable plus structured JSONL run logger."""

    def __init__(self, output_dir: Path, scene_name: str, run_id: str | None = None):
        self.output_dir = Path(output_dir)
        self.scene_name = str(scene_name)
        self.run_id = run_id or make_run_id(scene_name)
        self.logs_dir = self.output_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.run_log_path = self.logs_dir / "run.log"
        self.events_path = self.logs_dir / "events.jsonl"
        self.warnings_path = self.logs_dir / "warnings.jsonl"
        self.errors_path = self.logs_dir / "errors.jsonl"

        self._events = JsonlWriter(self.events_path)
        self._warnings = JsonlWriter(self.warnings_path)
        self._errors = JsonlWriter(self.errors_path)

        self._logger = logging.getLogger(f"kite_isaac.{self.run_id}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._logger.handlers.clear()

        fmt = logging.Formatter("%(asctime)sZ [%(levelname)s] %(module_name)s:%(event)s - %(message)s")
        handler = logging.FileHandler(self.run_log_path, mode="a", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        self._logger.addHandler(handler)

        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(fmt)
        self._logger.addHandler(stream)

    def event(
        self,
        level: str,
        module: str,
        event: str,
        message: str,
        *,
        frame_index: int | None = None,
        camera_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        level = str(level).upper()
        payload = {
            "timestamp_utc": utc_now_iso(),
            "level": level,
            "module": str(module),
            "event": str(event),
            "scene_name": self.scene_name,
            "run_id": self.run_id,
            "frame_index": frame_index,
            "camera_name": camera_name,
            "message": str(message),
            "details": details or {},
        }
        self._events.write(payload)
        if level == "WARNING":
            self._warnings.write(payload)
        elif level in {"ERROR", "CRITICAL"}:
            self._errors.write(payload)

        log_method = getattr(self._logger, level.lower(), self._logger.info)
        log_method(str(message), extra={"module_name": str(module), "event": str(event)})

    def info(self, module: str, event: str, message: str, **kwargs: Any) -> None:
        self.event("INFO", module, event, message, **kwargs)

    def warning(self, module: str, event: str, message: str, **kwargs: Any) -> None:
        self.event("WARNING", module, event, message, **kwargs)

    def error(self, module: str, event: str, message: str, **kwargs: Any) -> None:
        self.event("ERROR", module, event, message, **kwargs)

    def exception(self, module: str, event: str, message: str, exc: BaseException, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", {}) or {})
        details.update({
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
        })
        self.event("ERROR", module, event, message, details=details, **kwargs)

    def close(self) -> None:
        self._events.close()
        self._warnings.close()
        self._errors.close()
        for handler in list(self._logger.handlers):
            try:
                handler.close()
            except Exception:
                pass
            self._logger.removeHandler(handler)


@dataclass
class PerformanceRecorder:
    output_dir: Path
    scene_name: str
    run_id: str
    num_frames: int
    time_step_s: float
    camera_count: int
    performance_dir: Path = field(init=False)
    frame_timing_path: Path = field(init=False)
    module_timing_path: Path = field(init=False)
    summary_path: Path = field(init=False)
    _frame_file: Any = field(init=False, default=None)
    _frame_writer: Any = field(init=False, default=None)
    _module_rows: list[dict[str, Any]] = field(init=False, default_factory=list)
    _run_start_perf: float = field(init=False, default_factory=time.perf_counter)
    _run_start_utc: str = field(init=False, default_factory=utc_now_iso)
    _frame_count: int = 0
    _total_images_written: int = 0

    def __post_init__(self) -> None:
        self.performance_dir = Path(self.output_dir) / "performance"
        self.performance_dir.mkdir(parents=True, exist_ok=True)
        self.frame_timing_path = self.performance_dir / "frame_timing.csv"
        self.module_timing_path = self.performance_dir / "module_timing.csv"
        self.summary_path = self.performance_dir / "run_performance_summary.json"
        self._frame_file = self.frame_timing_path.open("w", newline="", encoding="utf-8")
        self._frame_writer = csv.DictWriter(
            self._frame_file,
            fieldnames=[
                "run_id",
                "scene_name",
                "frame_index",
                "sim_timestamp_s",
                "motion_update_ms",
                "simulation_update_ms",
                "capture_step_ms",
                "state_write_ms",
                "last_frame_update_ms",
                "frame_total_ms",
                "num_images_expected",
            ],
        )
        self._frame_writer.writeheader()
        self._frame_file.flush()

    @contextmanager
    def timed(self, module: str, event: str, frame_index: int | None = None) -> Iterator[None]:
        start = time.perf_counter()
        success = True
        error_message = ""
        try:
            yield
        except Exception as exc:
            success = False
            error_message = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._module_rows.append(
                {
                    "timestamp_utc": utc_now_iso(),
                    "run_id": self.run_id,
                    "scene_name": self.scene_name,
                    "frame_index": frame_index,
                    "module": module,
                    "event": event,
                    "elapsed_ms": f"{elapsed_ms:.6f}",
                    "success": success,
                    "error_message": error_message,
                }
            )

    def write_frame_timing(self, row: dict[str, Any]) -> None:
        row = dict(row)
        row.setdefault("run_id", self.run_id)
        row.setdefault("scene_name", self.scene_name)
        row.setdefault("num_images_expected", self.camera_count)
        self._frame_writer.writerow(row)
        self._frame_file.flush()
        self._frame_count += 1
        self._total_images_written += int(row.get("num_images_expected", 0) or 0)

    def close(self) -> None:
        try:
            if self._frame_file:
                self._frame_file.close()
        except Exception:
            pass

        if self._module_rows:
            with self.module_timing_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "timestamp_utc",
                        "run_id",
                        "scene_name",
                        "frame_index",
                        "module",
                        "event",
                        "elapsed_ms",
                        "success",
                        "error_message",
                    ],
                )
                writer.writeheader()
                writer.writerows(self._module_rows)

        wall_time_s = max(time.perf_counter() - self._run_start_perf, 1e-12)
        simulated_time_s = max((max(self.num_frames, 1) - 1) * float(self.time_step_s), 0.0)
        summary = {
            "created_utc": utc_now_iso(),
            "run_start_utc": self._run_start_utc,
            "scene_name": self.scene_name,
            "run_id": self.run_id,
            "num_frames_requested": int(self.num_frames),
            "frames_timed": int(self._frame_count),
            "time_step_s": float(self.time_step_s),
            "simulated_time_s": simulated_time_s,
            "wall_time_s": wall_time_s,
            "real_time_factor": simulated_time_s / wall_time_s if wall_time_s > 0.0 else None,
            "camera_count": int(self.camera_count),
            "expected_images_written": int(self._total_images_written),
            "capture_fps_per_camera": (self._frame_count / wall_time_s) if wall_time_s > 0.0 else None,
            "capture_fps_total_image_rate": (self._total_images_written / wall_time_s) if wall_time_s > 0.0 else None,
            "frame_timing_csv": str(self.frame_timing_path),
            "module_timing_csv": str(self.module_timing_path),
        }
        self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
