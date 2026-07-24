# dashboard_streamlit.py
#
# Browser dashboard for tethered-glider synthetic-data outputs.
#
# This program does NOT import Isaac Sim.
# It reads the output folder produced by main.py and displays:
#   - latest image from camera_main and camera_secondary
#   - recent frame_state.csv rows
#   - interactive Plotly telemetry plots
#   - basic file/status diagnostics
#
# Run:
#   streamlit run dashboard_streamlit.py
#
# Optional dependencies for exporting Plotly figures to PNG/SVG/PDF:
#   pip install kaleido

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components


PROJECT_ROOT = Path(__file__).parent.resolve()
DEFAULT_DATASET_DIR = PROJECT_ROOT / "outputs" / "tethered_glider_basic"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
CAMERA_FOLDERS = ["camera_main", "camera_secondary"]
PLOT_TEMPLATE = "plotly_white"


PERFORMANCE_METRIC_DESCRIPTIONS = [
    {
        "Metric": "Wall time [s]",
        "Meaning": "Real elapsed time measured on the PC from run start to run end.",
        "Use": "Main denominator for FPS and real-time factor.",
    },
    {
        "Metric": "Sim time [s]",
        "Meaning": "Simulated duration requested by the profile: approximately (num_frames - 1) × time_step_s.",
        "Use": "This is the time represented by the synthetic trajectory, not the time the PC needed to render it.",
    },
    {
        "Metric": "Real-time factor",
        "Meaning": "simulated_time_s / wall_time_s.",
        "Use": "> 1 means faster than real time; = 1 means real time; < 1 means slower than real time.",
    },
    {
        "Metric": "FPS / camera",
        "Meaning": "frames_timed / wall_time_s for each camera stream.",
        "Use": "Use this to judge whether each camera stream is close to the target FPS.",
    },
    {
        "Metric": "Images/s total",
        "Meaning": "expected_images_written / wall_time_s across all cameras.",
        "Use": "For two cameras, this is roughly 2 × FPS/camera when both are enabled.",
    },
]

FRAME_TIMING_DESCRIPTIONS = [
    {
        "Column": "frame_index",
        "Meaning": "Integer simulation/capture frame index.",
        "Problem signal": "Gaps or duplicates indicate a logging/capture sequencing problem.",
    },
    {
        "Column": "sim_timestamp_s",
        "Meaning": "Synthetic/simulation timestamp associated with the frame.",
        "Problem signal": "Non-monotonic values indicate a time-step or logging bug.",
    },
    {
        "Column": "motion_update_ms",
        "Meaning": "Time spent computing and applying the glider/tether/scene motion update for that frame.",
        "Problem signal": "Large values mean the kinematic/dynamics update is becoming a bottleneck.",
    },
    {
        "Column": "simulation_update_ms",
        "Meaning": "Time spent advancing Isaac/Kit after the motion update.",
        "Problem signal": "Large values usually indicate stage complexity, physics/render synchronization, or viewport overhead.",
    },
    {
        "Column": "capture_step_ms",
        "Meaning": "Time spent in Replicator/orchestrator capture/render step, including waiting for render completion.",
        "Problem signal": "Usually the main rendering bottleneck. Sensitive to resolution, samples, ray tracing, lighting, and camera count.",
    },
    {
        "Column": "state_write_ms",
        "Meaning": "Time spent appending frame_state.csv telemetry for the frame.",
        "Problem signal": "Should normally be tiny. Large spikes can indicate disk I/O issues.",
    },
    {
        "Column": "last_frame_update_ms",
        "Meaning": "Time spent updating camera_main/last_frame.png and camera_secondary/last_frame.png from the latest RGB images.",
        "Problem signal": "Can spike if image files are large, disk is slow, or the copy races with the writer.",
    },
    {
        "Column": "frame_total_ms",
        "Meaning": "Total measured wall time for the frame loop body.",
        "Problem signal": "This is the overall per-frame cost. Effective FPS is approximately 1000 / mean(frame_total_ms).",
    },
    {
        "Column": "num_images_expected",
        "Meaning": "Expected number of camera images written for that frame, usually 2 for stereo.",
        "Problem signal": "0 means capture/cameras were disabled. Does not prove that files were successfully written; validate outputs separately.",
    },
]

MODULE_TIMING_DESCRIPTIONS = [
    {
        "Column": "timestamp_utc",
        "Meaning": "UTC timestamp when a timed module/event finished.",
    },
    {
        "Column": "module",
        "Meaning": "Logical subsystem being timed, for example capture, dataset_loader, detector, tracker, stereo, metrics.",
    },
    {
        "Column": "event",
        "Meaning": "Specific operation inside the module, for example load_image, preprocess, detect, triangulate.",
    },
    {
        "Column": "elapsed_ms",
        "Meaning": "Wall-clock duration of that module event in milliseconds.",
    },
    {
        "Column": "success",
        "Meaning": "Whether the timed block completed without raising an exception.",
    },
    {
        "Column": "error_message",
        "Meaning": "Exception summary when success is false.",
    },
]

TIMING_INTERPRETATION_GUIDE = """
The performance plots are diagnostic signals, not proof that the whole system is real-time. Use them to locate bottlenecks.

Rules of thumb:
- If `capture_step_ms` dominates, the bottleneck is rendering/capture. Try a faster render profile, lower RGB resolution, fewer samples, lower bounces, or headless mode.
- If `last_frame_update_ms` spikes, the bottleneck is probably file I/O for copying last_frame.png.
- If `simulation_update_ms` grows, the bottleneck may be scene complexity, viewport updates, or Isaac/Kit synchronization.
- If `motion_update_ms` grows, the motion/dynamics code is becoming expensive.
- If `frame_total_ms` trends upward during a run, look for accumulating overhead, memory pressure, or disk pressure.

For a rough effective frame rate, use:

    effective FPS ≈ 1000 / mean(frame_total_ms)

For a two-camera run, total image-write rate is expected to be approximately:

    images/s total ≈ 2 × FPS/camera
"""


def discover_dataset_dirs(outputs_root: Path) -> list[Path]:
    """Return output scene folders, newest first.

    A folder is considered useful when it contains typical dataset files, but
    empty folders are also shown so the dashboard can watch a live run that is
    just starting.
    """
    if not outputs_root.exists() or not outputs_root.is_dir():
        return []

    candidates = [path for path in outputs_root.iterdir() if path.is_dir()]

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(candidates, key=mtime, reverse=True)


def dataset_label(path: Path) -> str:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        modified = "unknown time"
    return f"{path.name} — {modified}"


def render_definition_table(rows: list[dict[str, str]], title: str | None = None) -> None:
    """Render a compact definition table in Streamlit."""
    if title:
        st.markdown(f"**{title}**")
    st.table(pd.DataFrame(rows))


# -----------------------------------------------------------------------------
# Page setup
# -----------------------------------------------------------------------------


st.set_page_config(
    page_title="Tethered Glider Dashboard",
    page_icon="🛩️",
    layout="wide",
)


# -----------------------------------------------------------------------------
# File helpers
# -----------------------------------------------------------------------------


def resolve_dataset_dir(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def list_rgb_files(camera_dir: Path) -> list[Path]:
    if not camera_dir.exists() or not camera_dir.is_dir():
        return []

    def sort_key(path: Path) -> tuple[int, str]:
        stem = path.stem.lower()
        if stem.startswith("rgb_"):
            try:
                return int(stem.split("_", 1)[1]), path.name.lower()
            except ValueError:
                pass
        return -1, path.name.lower()

    return sorted(
        [
            path
            for path in camera_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and path.name.lower().startswith("rgb_")
        ],
        key=sort_key,
    )


def latest_rgb_file(camera_dir: Path) -> Path | None:
    files = list_rgb_files(camera_dir)
    if not files:
        return None
    return files[-1]


def newest_available_camera_image(camera_dir: Path) -> Path | None:
    """Use newest rgb_*.png while a run is active; last_frame.png is fallback."""
    latest_rgb = latest_rgb_file(camera_dir)
    last_frame = camera_dir / "last_frame.png"

    if latest_rgb is None:
        return last_frame if last_frame.exists() else None

    if not last_frame.exists():
        return latest_rgb

    try:
        if latest_rgb.stat().st_mtime >= last_frame.stat().st_mtime:
            return latest_rgb
    except OSError:
        return latest_rgb

    return last_frame


def read_binary(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.is_file():
        return pd.DataFrame()

    try:
        # During live capture Isaac may be appending a row while the dashboard
        # reads the file. on_bad_lines="skip" prevents one transient partial
        # line from killing the dashboard refresh cycle.
        return pd.read_csv(path, on_bad_lines="skip")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except Exception as exc:
        st.error(f"Failed to read CSV: {path}\n\n{exc}")
        return pd.DataFrame()


def read_json_if_exists(path: Path) -> dict:
    if not path.exists() or not path.is_file():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        st.warning(f"Failed to read JSON: {path}: {exc}")
        return {}


def read_jsonl_if_exists(path: Path, max_rows: int = 200) -> pd.DataFrame:
    if not path.exists() or not path.is_file():
        return pd.DataFrame()
    rows = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return pd.DataFrame()
    if max_rows and len(rows) > max_rows:
        rows = rows[-max_rows:]
    return pd.DataFrame(rows)


def read_text_tail(path: Path, max_lines: int = 200) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def numeric_columns(df: pd.DataFrame, exclude: Iterable[str] = ()) -> list[str]:
    exclude_set = set(exclude)
    columns = []
    for column in df.columns:
        if column in exclude_set:
            continue
        if pd.api.types.is_numeric_dtype(df[column]):
            columns.append(column)
    return columns


def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert columns to numeric only when conversion is safe.

    pandas 3.x no longer accepts errors="ignore" for pd.to_numeric.
    The dashboard still needs the old practical behavior: numeric-looking
    columns should become numeric, while text columns such as paths and labels
    must remain unchanged.
    """

    text_columns = {
        "camera_main_rgb",
        "camera_secondary_rgb",
        "glider_visual_mode",
        "glider_asset_id",
        "motion_model",
        "environment_mode",
        "environment_asset_id",
        "environment_scene_path",
    }

    result = df.copy()

    for column in result.columns:
        if column in text_columns:
            continue

        series = result[column]

        if pd.api.types.is_numeric_dtype(series):
            continue

        converted = pd.to_numeric(series, errors="coerce")

        # Convert only if every non-empty original value parsed as numeric.
        # Empty cells are allowed to become NaN. Mixed text/numeric columns stay text.
        non_empty_original = series.notna() & series.astype(str).str.strip().ne("")
        if converted[non_empty_original].notna().all():
            result[column] = converted

    return result

def make_time_series_figure(df: pd.DataFrame, selected_columns: list[str], x_column: str) -> go.Figure:
    fig = go.Figure()

    if df.empty or not selected_columns:
        fig.update_layout(title="No data selected", template=PLOT_TEMPLATE)
        return fig

    for column in selected_columns:
        if column not in df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=df[x_column] if x_column in df.columns else df.index,
                y=df[column],
                mode="lines",
                name=column,
            )
        )

    fig.update_layout(
        template=PLOT_TEMPLATE,
        title="Frame telemetry",
        xaxis_title=x_column if x_column in df.columns else "row_index",
        yaxis_title="value",
        legend_title="Signals",
        hovermode="x unified",
    )
    return fig


def make_trajectory_xy_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    required = {"glider_x_m", "glider_y_m"}

    if df.empty or not required.issubset(set(df.columns)):
        fig.update_layout(title="No XY trajectory data available", template=PLOT_TEMPLATE)
        return fig

    fig.add_trace(
        go.Scatter(
            x=df["glider_x_m"],
            y=df["glider_y_m"],
            mode="lines+markers",
            name="glider XY trajectory",
            marker={"size": 4},
        )
    )

    if {"anchor_x_m", "anchor_y_m"}.issubset(set(df.columns)):
        anchor = df.tail(1).iloc[0]
        fig.add_trace(
            go.Scatter(
                x=[anchor["anchor_x_m"]],
                y=[anchor["anchor_y_m"]],
                mode="markers",
                name="anchor",
                marker={"size": 10, "symbol": "x"},
            )
        )

    fig.update_layout(
        template=PLOT_TEMPLATE,
        title="Glider XY trajectory (WebGL-free)",
        xaxis_title="x [m]",
        yaxis_title="y [m]",
        yaxis={"scaleanchor": "x", "scaleratio": 1},
        hovermode="closest",
    )
    return fig


def make_trajectory_3d_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    required = {"glider_x_m", "glider_y_m", "glider_z_m"}

    if df.empty or not required.issubset(set(df.columns)):
        fig.update_layout(title="No XYZ trajectory data available", template=PLOT_TEMPLATE)
        return fig

    # Scatter3d requires WebGL in the browser. Keep it optional because some
    # browsers or remote desktops disable WebGL.
    fig.add_trace(
        go.Scatter3d(
            x=df["glider_x_m"],
            y=df["glider_y_m"],
            z=df["glider_z_m"],
            mode="lines",
            name="glider trajectory",
        )
    )

    fig.update_layout(
        template=PLOT_TEMPLATE,
        title="Glider 3D trajectory (requires browser WebGL)",
        scene={
            "xaxis_title": "x [m]",
            "yaxis_title": "y [m]",
            "zaxis_title": "z [m]",
            "aspectmode": "data",
        },
    )
    return fig

def sanitize_filename_token(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "plot"


def prepare_figure_for_export(
    fig: go.Figure,
    width_px: int,
    height_px: int,
    title_suffix: str = "",
) -> go.Figure:
    export_fig = go.Figure(fig)
    export_fig.update_layout(
        template=PLOT_TEMPLATE,
        width=int(width_px),
        height=int(height_px),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"color": "black"},
        margin={"l": 80, "r": 40, "t": 80, "b": 70},
    )

    if title_suffix:
        current_title = export_fig.layout.title.text or "Plot"
        export_fig.update_layout(title=f"{current_title} {title_suffix}")

    return export_fig


def export_plot_bundle(
    fig: go.Figure,
    plot_data: pd.DataFrame,
    output_dir: Path,
    stem: str,
    width_px: int,
    height_px: int,
    scale: int,
) -> dict[str, Path]:
    """Export plot + data as a reproducible bundle.

    Static vector/raster image export uses Plotly/Kaleido. HTML and JSON do not
    require Kaleido and are written first so the user still gets useful outputs
    even if static image export fails.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stem = sanitize_filename_token(stem)
    base = f"{safe_stem}_{timestamp}"

    export_fig = prepare_figure_for_export(
        fig=fig,
        width_px=width_px,
        height_px=height_px,
    )

    outputs: dict[str, Path] = {}

    html_path = output_dir / f"{base}.html"
    export_fig.write_html(str(html_path), include_plotlyjs=True, full_html=True)
    outputs["html"] = html_path

    figure_json_path = output_dir / f"{base}.plotly.json"
    pio.write_json(export_fig, str(figure_json_path), pretty=True)
    outputs["plotly_json"] = figure_json_path

    data_csv_path = output_dir / f"{base}.data.csv"
    plot_data.to_csv(data_csv_path, index=False)
    outputs["data_csv"] = data_csv_path

    data_json_path = output_dir / f"{base}.data.json"
    plot_data.to_json(data_json_path, orient="records", indent=2)
    outputs["data_json"] = data_json_path

    # Static high-quality exports. PDF/SVG are vector formats; PNG uses scale.
    for file_format in ["png", "svg", "pdf"]:
        image_path = output_dir / f"{base}.{file_format}"
        export_fig.write_image(
            str(image_path),
            format=file_format,
            width=int(width_px),
            height=int(height_px),
            scale=int(scale),
        )
        outputs[file_format] = image_path

    return outputs


# -----------------------------------------------------------------------------
# Simulation-state export bundle
# -----------------------------------------------------------------------------


def dataframe_preview_records(df: pd.DataFrame, max_rows: int = 5) -> list[dict[str, Any]]:
    """Return a small JSON-safe preview of a dataframe."""
    if df.empty:
        return []
    try:
        return json.loads(df.tail(max_rows).to_json(orient="records"))
    except Exception:
        return []


def add_file_to_zip(
    zip_file: zipfile.ZipFile,
    path: Path,
    arcname: str,
    inventory: list[dict[str, Any]],
) -> None:
    """Add one file to a zip archive and update the inventory."""
    row: dict[str, Any] = {
        "archive_path": arcname,
        "source_path": str(path),
        "exists": path.exists() and path.is_file(),
        "size_bytes": None,
        "modified_local": None,
        "status": "missing",
    }

    if not row["exists"]:
        inventory.append(row)
        return

    try:
        stat = path.stat()
        row["size_bytes"] = int(stat.st_size)
        row["modified_local"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        zip_file.write(path, arcname=arcname)
        row["status"] = "included"
    except Exception as exc:
        row["status"] = f"error: {exc}"

    inventory.append(row)


def add_text_to_zip(
    zip_file: zipfile.ZipFile,
    text: str,
    arcname: str,
    inventory: list[dict[str, Any]],
) -> None:
    encoded = text.encode("utf-8")
    zip_file.writestr(arcname, encoded)
    inventory.append(
        {
            "archive_path": arcname,
            "source_path": "generated_by_dashboard",
            "exists": True,
            "size_bytes": len(encoded),
            "modified_local": datetime.now().isoformat(timespec="seconds"),
            "status": "included",
        }
    )


def build_simulation_state_export(
    dataset_dir: Path,
    project_root: Path,
    loaded_data: dict[str, Any],
) -> tuple[str, bytes, dict[str, Any]]:
    """Build a compact zip bundle describing the selected simulation state.

    This is intentionally a diagnostic/support bundle, not a full dataset export.
    It includes configuration, logs, performance tables, layout diagnostics,
    validation data, file inventory, and the latest camera images. It does not
    include all rgb_*.png frames because that would make the bundle too large to
    share during debugging.
    """

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    scene_token = sanitize_filename_token(dataset_dir.name)
    filename = f"simulation_state_{scene_token}_{timestamp}.zip"

    inventory: list[dict[str, Any]] = []
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        summary = {
            "export_created_local": datetime.now().isoformat(timespec="seconds"),
            "project_root": str(project_root),
            "dataset_dir": str(dataset_dir),
            "scene_name": dataset_dir.name,
            "purpose": "Dashboard simulation-state support bundle. Not a full image dataset export.",
            "contains_full_rgb_sequence": False,
            "camera_images_policy": "Includes last_frame.png and newest rgb_*.png per camera only.",
            "dashboard_loaded_counts": {
                "frame_state_rows": int(len(loaded_data.get("frame_state", pd.DataFrame()))),
                "capture_manifest_rows": int(len(loaded_data.get("manifest", pd.DataFrame()))),
                "frame_timing_rows": int(len(loaded_data.get("frame_timing", pd.DataFrame()))),
                "module_timing_rows": int(len(loaded_data.get("module_timing", pd.DataFrame()))),
                "events_rows_loaded": int(len(loaded_data.get("events_log", pd.DataFrame()))),
                "warnings_rows_loaded": int(len(loaded_data.get("warnings_log", pd.DataFrame()))),
                "errors_rows_loaded": int(len(loaded_data.get("errors_log", pd.DataFrame()))),
            },
            "performance_summary": loaded_data.get("performance_summary", {}),
            "validation_summary": loaded_data.get("validation", {}),
            "latest_previews": {
                "frame_state_tail": dataframe_preview_records(loaded_data.get("frame_state", pd.DataFrame())),
                "frame_timing_tail": dataframe_preview_records(loaded_data.get("frame_timing", pd.DataFrame())),
                "module_timing_tail": dataframe_preview_records(loaded_data.get("module_timing", pd.DataFrame())),
            },
        }

        add_text_to_zip(
            zip_file,
            json.dumps(summary, indent=2, ensure_ascii=False),
            "dashboard_export_summary.json",
            inventory,
        )

        # Core run/config files.
        core_files = [
            (dataset_dir / "profile_used.json", "run/profile_used.json"),
            (project_root / "render_profiles.json", "project/render_profiles.json"),
            (dataset_dir / "frame_state.csv", "run/frame_state.csv"),
            (dataset_dir / "capture_manifest.csv", "run/capture_manifest.csv"),
            (dataset_dir / "camera_rig_metadata.json", "run/camera_rig_metadata.json"),
            (dataset_dir / "validation_report.json", "run/validation_report.json"),
            (dataset_dir / "camera_rig_layout.json", "run/camera_rig_layout.json"),
            (dataset_dir / "camera_rig_layout_summary.csv", "run/camera_rig_layout_summary.csv"),
        ]

        for path, arcname in core_files:
            add_file_to_zip(zip_file, path, arcname, inventory)

        # Layout SVG diagnostics.
        for path in sorted(dataset_dir.glob("camera_rig_layout*.svg")):
            add_file_to_zip(zip_file, path, f"layout/{path.name}", inventory)

        # Logs and performance files.
        for subdir_name in ["logs", "performance"]:
            subdir = dataset_dir / subdir_name
            if subdir.exists():
                for path in sorted(subdir.iterdir()):
                    if path.is_file():
                        add_file_to_zip(zip_file, path, f"{subdir_name}/{path.name}", inventory)

        # Latest camera image diagnostics only. Do not include all rgb frames.
        for camera_folder in CAMERA_FOLDERS:
            camera_dir = dataset_dir / camera_folder
            last_frame = camera_dir / "last_frame.png"
            latest_rgb = latest_rgb_file(camera_dir)
            add_file_to_zip(
                zip_file,
                last_frame,
                f"latest_images/{camera_folder}_last_frame.png",
                inventory,
            )
            if latest_rgb is not None:
                add_file_to_zip(
                    zip_file,
                    latest_rgb,
                    f"latest_images/{camera_folder}_{latest_rgb.name}",
                    inventory,
                )

        # Dataset output inventory: useful when files are missing or unexpectedly large.
        inventory_rows = []
        if dataset_dir.exists():
            for path in sorted(dataset_dir.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(dataset_dir).as_posix()
                    stat = path.stat()
                    inventory_rows.append(
                        {
                            "relative_path": rel,
                            "size_bytes": int(stat.st_size),
                            "modified_local": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        }
                    )
                except OSError:
                    continue

        inventory_df = pd.DataFrame(inventory_rows)
        add_text_to_zip(
            zip_file,
            inventory_df.to_csv(index=False),
            "dataset_file_inventory.csv",
            inventory,
        )

        # Archive inventory itself. This is written last so it includes generated files too.
        add_text_to_zip(
            zip_file,
            json.dumps(inventory, indent=2, ensure_ascii=False),
            "archive_inventory.json",
            inventory,
        )

    data = buffer.getvalue()
    export_info = {
        "filename": filename,
        "size_bytes": len(data),
        "included_items": sum(1 for row in inventory if row.get("status") == "included"),
        "missing_items": sum(1 for row in inventory if row.get("status") == "missing"),
    }
    return filename, data, export_info


# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------


st.title("Tethered Glider Dataset Dashboard")
st.caption("Reads generated files only. It does not control Isaac Sim.")

with st.sidebar:
    st.header("Dataset")

    outputs_root = PROJECT_ROOT / "outputs"
    dataset_dirs = discover_dataset_dirs(outputs_root)
    dataset_labels = ["Manual path"] + [dataset_label(path) for path in dataset_dirs]
    dataset_label_to_path = {dataset_label(path): path for path in dataset_dirs}

    default_dataset_index = 1 if dataset_dirs else 0
    if "dataset_choice" in st.session_state and st.session_state["dataset_choice"] in dataset_labels:
        default_dataset_index = dataset_labels.index(st.session_state["dataset_choice"])

    dataset_choice = st.selectbox(
        "Existing scene output",
        options=dataset_labels,
        index=default_dataset_index,
        key="dataset_choice",
        help="Folders are read from outputs/. Newest folders appear first.",
    )

    if dataset_choice == "Manual path":
        dataset_input = st.text_input(
            "Dataset folder",
            value=str(DEFAULT_DATASET_DIR),
            help="Use an absolute path or a path relative to the project root.",
        )
        dataset_dir = resolve_dataset_dir(dataset_input)
    else:
        dataset_dir = dataset_label_to_path[dataset_choice].resolve()
        st.caption(f"Selected: `{dataset_dir}`")

    st.header("Live update")

    if "auto_refresh" not in st.session_state:
        st.session_state.auto_refresh = True

    if "paused" not in st.session_state:
        st.session_state.paused = False

    auto_refresh = st.checkbox(
        "Auto-refresh on open",
        key="auto_refresh",
        help="Enabled by default so the dashboard starts updating as soon as it opens.",
    )
    paused = st.checkbox("Pause", key="paused")

    refresh_interval_ms = st.slider(
        "Refresh interval [ms]",
        min_value=10,
        max_value=30000,
        value=250,
        step=10,
        help=(
            "10 ms asks for 100 full Streamlit reruns per second. "
            "That is usually too aggressive for CSV + image + Plotly updates. "
            "Use it only briefly; 100-500 ms is normally more stable."
        ),
    )
    refresh_interval_s = refresh_interval_ms / 1000.0

    if st.button("Refresh now"):
        st.rerun()

    st.header("Camera display")
    if "swap_camera_columns" not in st.session_state:
        st.session_state.swap_camera_columns = False

    if st.button("Swap camera columns"):
        st.session_state.swap_camera_columns = not bool(st.session_state.swap_camera_columns)
        st.rerun()

    swap_camera_columns = bool(st.session_state.swap_camera_columns)
    st.caption(
        "Display order only. This does not rename camera folders, profiles, CSV columns, "
        "or capture outputs."
    )
    st.caption(
        "Current order: "
        + ("camera_secondary | camera_main" if swap_camera_columns else "camera_main | camera_secondary")
    )

    st.header("Rows")
    tail_rows = st.slider("Recent rows to show", min_value=5, max_value=500, value=20)
    plot_window_rows = st.slider("Plot window rows", min_value=30, max_value=10000, value=900)

    st.header("Export")
    export_dir = dataset_dir / "dashboard_exports"
    export_width_px = st.number_input(
        "Export width [px]",
        min_value=640,
        max_value=8000,
        value=1920,
        step=160,
    )
    export_height_px = st.number_input(
        "Export height [px]",
        min_value=480,
        max_value=8000,
        value=1080,
        step=120,
    )
    export_scale = st.slider(
        "PNG export scale",
        min_value=1,
        max_value=6,
        value=3,
        step=1,
        help="PNG physical pixels = width × scale by height × scale. SVG/PDF are vector outputs.",
    )


# -----------------------------------------------------------------------------
# Load data
# -----------------------------------------------------------------------------


frame_state_path = dataset_dir / "frame_state.csv"
manifest_path = dataset_dir / "capture_manifest.csv"
metadata_path = dataset_dir / "camera_rig_metadata.json"
validation_path = dataset_dir / "validation_report.json"
layout_svg_path = dataset_dir / "camera_rig_layout.svg"
layout_plan_roi_svg_path = dataset_dir / "camera_rig_layout_plan_roi.svg"
layout_plan_full_svg_path = dataset_dir / "camera_rig_layout_plan_full.svg"
layout_elevation_svg_path = dataset_dir / "camera_rig_layout_elevation.svg"
layout_nearfield_svg_path = dataset_dir / "camera_rig_layout_nearfield.svg"
layout_json_path = dataset_dir / "camera_rig_layout.json"
layout_summary_path = dataset_dir / "camera_rig_layout_summary.csv"
logs_dir = dataset_dir / "logs"
run_log_path = logs_dir / "run.log"
events_jsonl_path = logs_dir / "events.jsonl"
warnings_jsonl_path = logs_dir / "warnings.jsonl"
errors_jsonl_path = logs_dir / "errors.jsonl"
performance_dir = dataset_dir / "performance"
frame_timing_path = performance_dir / "frame_timing.csv"
module_timing_path = performance_dir / "module_timing.csv"
performance_summary_path = performance_dir / "run_performance_summary.json"
profile_used_path = dataset_dir / "profile_used.json"
camera_model_path = dataset_dir / "camera_model.json"
dataset_manifest_path = dataset_dir / "dataset_manifest.json"
labels_dir = dataset_dir / "labels"
frame_labels_path = labels_dir / "frame_labels.csv"
semantic_id_map_path = labels_dir / "semantic_id_map.json"
annotation_inventory_path = labels_dir / "annotation_inventory.csv"

frame_state = coerce_numeric_columns(read_csv_if_exists(frame_state_path))
manifest = read_csv_if_exists(manifest_path)
metadata = read_json_if_exists(metadata_path)
validation = read_json_if_exists(validation_path)
performance_summary = read_json_if_exists(performance_summary_path)
camera_model = read_json_if_exists(camera_model_path)
dataset_manifest_json = read_json_if_exists(dataset_manifest_path)
frame_labels = coerce_numeric_columns(read_csv_if_exists(frame_labels_path))
annotation_inventory = coerce_numeric_columns(read_csv_if_exists(annotation_inventory_path))
semantic_id_map = read_json_if_exists(semantic_id_map_path)
frame_timing = coerce_numeric_columns(read_csv_if_exists(frame_timing_path))
module_timing = coerce_numeric_columns(read_csv_if_exists(module_timing_path))
events_log = read_jsonl_if_exists(events_jsonl_path, max_rows=tail_rows if 'tail_rows' in globals() else 200)
warnings_log = read_jsonl_if_exists(warnings_jsonl_path, max_rows=tail_rows if 'tail_rows' in globals() else 200)
errors_log = read_jsonl_if_exists(errors_jsonl_path, max_rows=tail_rows if 'tail_rows' in globals() else 200)

if not dataset_dir.exists():
    st.error(f"Dataset folder does not exist yet: {dataset_dir}")
    st.info("Auto-refresh will keep checking. Start an Isaac capture run or select an existing dataset folder.")


# -----------------------------------------------------------------------------
# Status row
# -----------------------------------------------------------------------------


status_cols = st.columns(8)
status_cols[0].metric("Dataset exists", "yes" if dataset_dir.exists() else "no")
status_cols[1].metric("Frame rows", len(frame_state))
status_cols[2].metric("Manifest rows", len(manifest))
status_cols[3].metric("Label rows", len(frame_labels))
status_cols[4].metric("Validation", str(validation.get("ok", "missing")))
status_cols[5].metric("RT factor", f"{float(performance_summary.get('real_time_factor', 0.0)):.2f}" if performance_summary.get("real_time_factor") is not None else "missing")
status_cols[6].metric("FPS/camera", f"{float(performance_summary.get('capture_fps_per_camera', 0.0)):.1f}" if performance_summary.get("capture_fps_per_camera") is not None else "missing")
status_cols[7].metric("Auto-refresh", "on" if auto_refresh and not paused else "off")

st.caption(f"Dataset: `{dataset_dir}`")

if not frame_state.empty:
    latest_row = frame_state.tail(1).iloc[0]
    latest_frame = latest_row.get("frame_index", "?")
    latest_time = latest_row.get("sim_timestamp_s", "?")
    st.caption(f"Latest telemetry row: frame `{latest_frame}`, t=`{latest_time}` s")


# -----------------------------------------------------------------------------
# Tabs
# -----------------------------------------------------------------------------


tab_live, tab_layout, tab_truth, tab_data, tab_plots, tab_perf, tab_logs, tab_files = st.tabs(
    ["Live cameras", "Camera layout", "Ground truth", "Telemetry rows", "Plots", "Performance", "Logs", "Files"]
)

with tab_live:
    st.subheader("Latest camera images")
    displayed_camera_folders = list(reversed(CAMERA_FOLDERS)) if swap_camera_columns else list(CAMERA_FOLDERS)

    control_cols = st.columns([1, 5])
    with control_cols[0]:
        if st.button("Swap columns", key="swap_camera_columns_live"):
            st.session_state.swap_camera_columns = not bool(st.session_state.swap_camera_columns)
            st.rerun()
    with control_cols[1]:
        st.caption(
            "Display order only; dataset folder names and telemetry references stay unchanged. "
            f"Current order: {displayed_camera_folders[0]} | {displayed_camera_folders[1]}"
        )

    camera_cols = st.columns(2)

    for index, camera_folder in enumerate(displayed_camera_folders):
        camera_dir = dataset_dir / camera_folder
        image_path = newest_available_camera_image(camera_dir)

        with camera_cols[index]:
            physical_side = "left display column" if index == 0 else "right display column"
            st.markdown(f"### {camera_folder}")
            st.caption(physical_side)

            if image_path is None:
                st.warning(f"No RGB image found in {camera_dir}")
                continue

            image_bytes = read_binary(image_path)
            if image_bytes is None:
                st.warning(f"Could not read image: {image_path}")
                continue

            st.image(image_bytes, caption=image_path.name, use_container_width=True)

            try:
                stat = image_path.stat()
                st.caption(
                    f"Path: `{image_path}`  \n"
                    f"Modified: {datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds')}  \n"
                    f"Size: {stat.st_size / 1024:.1f} KiB"
                )
            except OSError:
                st.caption(f"Path: `{image_path}`")

with tab_layout:
    st.subheader("Camera rig layout")
    st.caption(
        "These diagnostics are split deliberately. The plan view is pure world XY and uses only horizontal FOV. "
        "The elevation view is depth-versus-Z and uses only vertical FOV. This avoids mixing a 2D top-down plot "
        "with the projected 3D camera pyramid."
    )

    def show_svg_file(title: str, path: Path, height: int, note: str = "") -> None:
        st.markdown(f"#### {title}")
        if note:
            st.caption(note)
        if path.exists():
            try:
                svg_text = path.read_text(encoding="utf-8")
                components.html(svg_text, height=height, scrolling=True)
                st.caption(f"SVG: `{path}`")
            except Exception as exc:
                st.error(f"Could not display `{path}`: {exc}")
        else:
            st.warning(f"No SVG found at `{path}`. Run a new capture after installing the latest patch.")

    # Prefer the new v20 file names. Fall back to v19 names for old datasets.
    plan_roi_to_show = layout_plan_roi_svg_path if layout_plan_roi_svg_path.exists() else layout_nearfield_svg_path
    plan_full_to_show = layout_plan_full_svg_path if layout_plan_full_svg_path.exists() else layout_svg_path

    show_svg_file(
        "Plan view — pure XY horizontal FOV",
        plan_roi_to_show,
        980,
        "Use this view for camera XY placement, baseline interpretation, orbit coverage, and stereo horizontal overlap. "
        "If the configured frustum distance is very large, rays are clipped for readability and labelled inside the SVG.",
    )

    show_svg_file(
        "Elevation view — depth versus world Z",
        layout_elevation_svg_path,
        900,
        "Use this view for camera height, pitch, glider height, and vertical FOV checks. It intentionally does not show horizontal overlap.",
    )

    with st.expander("Full configured-distance plan view", expanded=False):
        show_svg_file(
            "Full plan view",
            plan_full_to_show,
            980,
            "This uses the configured frustum distance. It can look visually compressed when frustum_distance_m is hundreds of meters.",
        )

    if layout_summary_path.exists():
        st.subheader("Layout numeric summary")
        layout_summary = coerce_numeric_columns(read_csv_if_exists(layout_summary_path))
        if not layout_summary.empty:
            st.dataframe(layout_summary, use_container_width=True, hide_index=True)
        else:
            st.info(f"No readable rows found in `{layout_summary_path}`")

    with st.expander("camera_rig_layout.json", expanded=False):
        layout_json = read_json_if_exists(layout_json_path)
        if layout_json:
            st.json(layout_json)
        else:
            st.info(f"No layout JSON found at `{layout_json_path}`")

with tab_truth:
    st.subheader("Ground-truth package")
    st.caption(
        "This tab summarizes glider-centered dataset outputs: camera_model.json, frame_labels.csv, "
        "semantic ID metadata, and raw Replicator annotation inventory."
    )

    cols = st.columns(4)
    cols[0].metric("camera_model.json", "yes" if camera_model else "missing")
    cols[1].metric("frame_labels rows", len(frame_labels))
    cols[2].metric("annotation files", len(annotation_inventory))
    cols[3].metric("dataset_manifest", "yes" if dataset_manifest_json else "missing")

    if not frame_labels.empty:
        st.markdown("#### Recent frame_labels.csv rows")
        st.dataframe(frame_labels.tail(tail_rows), use_container_width=True, hide_index=True)
    else:
        st.warning(f"No frame_labels.csv loaded from `{frame_labels_path}`. Run a new capture with v25 annotations enabled.")

    if not annotation_inventory.empty:
        st.markdown("#### Annotation inventory")
        st.dataframe(annotation_inventory.tail(tail_rows), use_container_width=True, hide_index=True)
    else:
        st.info(f"No raw annotation files inventoried at `{annotation_inventory_path}` yet.")

    with st.expander("camera_model.json", expanded=False):
        if camera_model:
            st.json(camera_model)
        else:
            st.info(f"No camera_model.json found at `{camera_model_path}`")

    with st.expander("semantic_id_map.json", expanded=False):
        if semantic_id_map:
            st.json(semantic_id_map)
        else:
            st.info(f"No semantic_id_map.json found at `{semantic_id_map_path}`")

    with st.expander("dataset_manifest.json", expanded=False):
        if dataset_manifest_json:
            st.json(dataset_manifest_json)
        else:
            st.info(f"No dataset_manifest.json found at `{dataset_manifest_path}`")

with tab_data:
    st.subheader("Recent frame_state.csv rows")

    if frame_state.empty:
        st.warning(f"No frame_state.csv loaded from `{frame_state_path}`")
    else:
        st.dataframe(frame_state.tail(tail_rows), use_container_width=True, hide_index=True)

    st.subheader("Recent capture_manifest.csv rows")
    if manifest.empty:
        st.warning(f"No capture_manifest.csv loaded from `{manifest_path}`")
    else:
        st.dataframe(manifest.tail(tail_rows), use_container_width=True, hide_index=True)

with tab_plots:
    st.subheader("Interactive telemetry plots")

    if frame_state.empty:
        st.warning("No frame_state.csv data available.")
    else:
        plot_df = frame_state.tail(plot_window_rows).copy()
        available_numeric = numeric_columns(plot_df, exclude=["frame_index"])
        default_signals = [
            column
            for column in [
                "linear_speed_m_s",
                "glider_x_m",
                "glider_y_m",
                "glider_z_m",
                "actual_horizontal_tether_length_m",
                "tether_constraint_error_m",
            ]
            if column in available_numeric
        ]

        selected_signals = st.multiselect(
            "Signals",
            options=available_numeric,
            default=default_signals,
            help="Use the Plotly legend to show/hide individual traces.",
        )

        x_column = "sim_timestamp_s" if "sim_timestamp_s" in plot_df.columns else "frame_index"
        telemetry_fig = make_time_series_figure(plot_df, selected_signals, x_column=x_column)
        st.plotly_chart(
            telemetry_fig,
            use_container_width=True,
            config={
                "displaylogo": False,
                "toImageButtonOptions": {
                    "format": "png",
                    "filename": "tethered_glider_telemetry",
                    "height": int(export_height_px),
                    "width": int(export_width_px),
                    "scale": int(export_scale),
                },
            },
        )

        export_cols = st.columns([1, 3])
        with export_cols[0]:
            if st.button("Export telemetry bundle"):
                try:
                    telemetry_data_columns = [x_column] + [
                        column for column in selected_signals if column in plot_df.columns
                    ]
                    telemetry_data = plot_df.loc[:, list(dict.fromkeys(telemetry_data_columns))]
                    outputs = export_plot_bundle(
                        fig=telemetry_fig,
                        plot_data=telemetry_data,
                        output_dir=export_dir,
                        stem="telemetry_plot",
                        width_px=int(export_width_px),
                        height_px=int(export_height_px),
                        scale=int(export_scale),
                    )
                    st.success("Saved telemetry export bundle:")
                    st.code("\n".join(str(path) for path in outputs.values()))
                except Exception as exc:
                    st.error(
                        "Static plot export failed. HTML/JSON/CSV are written before PNG/SVG/PDF. "
                        "For PNG/SVG/PDF, install a Plotly/Kaleido-compatible setup.\n\n"
                        "Command: `pip install -U plotly kaleido`\n\n"
                        f"Error: {exc}"
                    )

        st.divider()
        trajectory_fig = make_trajectory_xy_figure(plot_df)
        st.plotly_chart(trajectory_fig, use_container_width=True, config={"displaylogo": False})

        enable_3d = st.checkbox(
            "Enable 3D trajectory plot (requires browser WebGL)",
            value=False,
            help="Plotly 3D charts need browser WebGL. Leave off when the browser shows WebGL errors.",
        )
        if enable_3d:
            trajectory_3d_fig = make_trajectory_3d_figure(plot_df)
            st.plotly_chart(trajectory_3d_fig, use_container_width=True, config={"displaylogo": False})

        if st.button("Export trajectory bundle"):
            try:
                trajectory_columns = [
                    column
                    for column in [
                        "frame_index",
                        "sim_timestamp_s",
                        "glider_x_m",
                        "glider_y_m",
                        "glider_z_m",
                    ]
                    if column in plot_df.columns
                ]
                trajectory_data = plot_df.loc[:, trajectory_columns]
                outputs = export_plot_bundle(
                    fig=trajectory_fig,
                    plot_data=trajectory_data,
                    output_dir=export_dir,
                    stem="trajectory_plot",
                    width_px=int(export_width_px),
                    height_px=int(export_height_px),
                    scale=int(export_scale),
                )
                st.success("Saved trajectory export bundle:")
                st.code("\n".join(str(path) for path in outputs.values()))
            except Exception as exc:
                st.error(
                    "Static plot export failed. HTML/JSON/CSV are written before PNG/SVG/PDF. "
                    "For PNG/SVG/PDF, install a Plotly/Kaleido-compatible setup.\n\n"
                    "Command: `pip install -U plotly kaleido`\n\n"
                    f"Error: {exc}"
                )

with tab_perf:
    st.subheader("Run performance")
    with st.expander("What do these run-level metrics mean?", expanded=False):
        render_definition_table(PERFORMANCE_METRIC_DESCRIPTIONS)
        st.markdown(TIMING_INTERPRETATION_GUIDE)

    if performance_summary:
        perf_cols = st.columns(5)
        perf_cols[0].metric(
            "Wall time [s]",
            f"{float(performance_summary.get('wall_time_s', 0.0)):.2f}",
            help="Real elapsed time on the PC from run start to run end.",
        )
        perf_cols[1].metric(
            "Sim time [s]",
            f"{float(performance_summary.get('simulated_time_s', 0.0)):.2f}",
            help="Synthetic duration represented by the trajectory: approximately (num_frames - 1) × time_step_s.",
        )
        perf_cols[2].metric(
            "Real-time factor",
            f"{float(performance_summary.get('real_time_factor', 0.0)):.3f}",
            help="simulated_time_s / wall_time_s. Greater than 1 means faster than real time.",
        )
        perf_cols[3].metric(
            "FPS / camera",
            f"{float(performance_summary.get('capture_fps_per_camera', 0.0)):.2f}",
            help="frames_timed / wall_time_s for each camera stream.",
        )
        perf_cols[4].metric(
            "Images/s total",
            f"{float(performance_summary.get('capture_fps_total_image_rate', 0.0)):.2f}",
            help="expected_images_written / wall_time_s across all cameras.",
        )
        with st.expander("run_performance_summary.json", expanded=False):
            st.json(performance_summary)
    else:
        st.warning(f"No performance summary found at `{performance_summary_path}`")

    st.subheader("Frame timing")
    with st.expander("What does each frame timing signal mean?", expanded=False):
        render_definition_table(FRAME_TIMING_DESCRIPTIONS)
        st.markdown(
            "The plotted values are wall-clock durations in milliseconds. "
            "They are not simulated time. Spikes are expected occasionally, but repeated spikes indicate a bottleneck."
        )

    if frame_timing.empty:
        st.info(f"No frame timing CSV found at `{frame_timing_path}`")
    else:
        st.dataframe(frame_timing.tail(tail_rows), use_container_width=True, hide_index=True)
        timing_numeric = numeric_columns(frame_timing, exclude=["frame_index"])
        default_timing = [c for c in ["frame_total_ms", "motion_update_ms", "simulation_update_ms", "capture_step_ms", "state_write_ms", "last_frame_update_ms"] if c in timing_numeric]
        selected_timing = st.multiselect(
            "Timing signals [ms]",
            options=timing_numeric,
            default=default_timing,
            help="Select which per-frame timing columns to plot. Definitions are in the expander above.",
        )
        if selected_timing:
            timing_df = frame_timing.tail(plot_window_rows)
            timing_fig = make_time_series_figure(timing_df, selected_timing, x_column="frame_index")
            timing_fig.update_layout(
                title="Frame timing [ms]",
                xaxis_title="Frame index",
                yaxis_title="Wall-clock duration [ms]",
                legend_title="Timing signal",
            )
            st.plotly_chart(timing_fig, use_container_width=True, config={"displaylogo": False})
            st.caption(
                "For rough throughput, use effective FPS ≈ 1000 / mean(frame_total_ms). "
                "A method intended for 30 FPS should normally stay below about 33 ms/frame, including image loading and processing."
            )

    st.subheader("Module timing")
    with st.expander("What will module timing mean for the vision algorithms?", expanded=False):
        render_definition_table(MODULE_TIMING_DESCRIPTIONS)
        st.markdown(
            "The simulator currently writes module timing only for explicitly timed blocks. "
            "The same file format should be used later by the vision package: image loading, preprocessing, detection, tracking, stereo matching, triangulation, and metrics."
        )

    if module_timing.empty:
        st.info(f"No module timing CSV found at `{module_timing_path}` yet. Vision modules can write to this same format later.")
    else:
        st.dataframe(module_timing.tail(tail_rows), use_container_width=True, hide_index=True)

with tab_logs:
    st.subheader("Run logs")
    log_text = read_text_tail(run_log_path, max_lines=tail_rows)
    if log_text:
        st.code(log_text)
    else:
        st.info(f"No run.log found at `{run_log_path}`")

    st.subheader("Structured events")
    if events_log.empty:
        st.info(f"No events found at `{events_jsonl_path}`")
    else:
        st.dataframe(events_log.tail(tail_rows), use_container_width=True, hide_index=True)

    col_w, col_e = st.columns(2)
    with col_w:
        st.markdown("#### Warnings")
        if warnings_log.empty:
            st.success("No warnings logged.")
        else:
            st.dataframe(warnings_log.tail(tail_rows), use_container_width=True, hide_index=True)
    with col_e:
        st.markdown("#### Errors")
        if errors_log.empty:
            st.success("No errors logged.")
        else:
            st.dataframe(errors_log.tail(tail_rows), use_container_width=True, hide_index=True)

with tab_files:
    st.subheader("Simulation state export")
    st.caption(
        "Creates a compact ZIP with profile_used.json, logs, performance tables, layout diagnostics, "
        "validation files, file inventory, and latest camera images. It intentionally does not include all RGB frames."
    )

    export_loaded_data = {
        "frame_state": frame_state,
        "manifest": manifest,
        "frame_timing": frame_timing,
        "module_timing": module_timing,
        "events_log": events_log,
        "warnings_log": warnings_log,
        "errors_log": errors_log,
        "performance_summary": performance_summary,
        "validation": validation,
    }

    try:
        export_filename, export_zip_bytes, export_info = build_simulation_state_export(
            dataset_dir=dataset_dir,
            project_root=PROJECT_ROOT,
            loaded_data=export_loaded_data,
        )
        export_cols = st.columns([1, 3])
        with export_cols[0]:
            st.download_button(
                "Download simulation state ZIP",
                data=export_zip_bytes,
                file_name=export_filename,
                mime="application/zip",
                help="Use this when you need to share the simulation state/debug information without sending the full RGB dataset.",
            )
        with export_cols[1]:
            st.caption(
                f"Bundle size: {export_info['size_bytes'] / 1024:.1f} KiB | "
                f"included items: {export_info['included_items']} | missing expected items: {export_info['missing_items']}"
            )
    except Exception as exc:
        st.error(f"Could not build simulation state export bundle: {exc}")

    st.divider()
    st.subheader("Dataset files")

    file_rows = []
    for path in [
        frame_state_path,
        manifest_path,
        metadata_path,
        layout_svg_path,
        layout_plan_roi_svg_path,
        layout_plan_full_svg_path,
        layout_elevation_svg_path,
        layout_nearfield_svg_path,
        layout_json_path,
        layout_summary_path,
        validation_path,
        profile_used_path,
        run_log_path,
        events_jsonl_path,
        warnings_jsonl_path,
        errors_jsonl_path,
        frame_timing_path,
        module_timing_path,
        performance_summary_path,
    ]:
        file_rows.append(
            {
                "file": path.name,
                "exists": path.exists(),
                "path": str(path),
                "size_kib": round(path.stat().st_size / 1024, 2) if path.exists() else None,
            }
        )

    for camera_folder in CAMERA_FOLDERS:
        camera_dir = dataset_dir / camera_folder
        last_frame = camera_dir / "last_frame.png"
        rgb_files = list_rgb_files(camera_dir)
        file_rows.append(
            {
                "file": f"{camera_folder}/last_frame.png",
                "exists": last_frame.exists(),
                "path": str(last_frame),
                "size_kib": round(last_frame.stat().st_size / 1024, 2) if last_frame.exists() else None,
            }
        )
        file_rows.append(
            {
                "file": f"{camera_folder}/rgb frames",
                "exists": bool(rgb_files),
                "path": str(camera_dir),
                "size_kib": None,
                "count": len(rgb_files),
            }
        )

    st.dataframe(pd.DataFrame(file_rows), use_container_width=True, hide_index=True)

    with st.expander("camera_rig_metadata.json", expanded=False):
        st.json(metadata)

    with st.expander("validation_report.json", expanded=False):
        st.json(validation)


# -----------------------------------------------------------------------------
# Refresh loop
# -----------------------------------------------------------------------------


if auto_refresh and not paused:
    time.sleep(float(refresh_interval_s))
    st.rerun()
