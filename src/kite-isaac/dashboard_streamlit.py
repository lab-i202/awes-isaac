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

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st


PROJECT_ROOT = Path(__file__).parent.resolve()
DEFAULT_DATASET_DIR = PROJECT_ROOT / "outputs" / "tethered_glider_basic"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
CAMERA_FOLDERS = ["camera_main", "camera_secondary"]
PLOT_TEMPLATE = "plotly_white"


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


def make_trajectory_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    required = {"glider_x_m", "glider_y_m", "glider_z_m"}

    if df.empty or not required.issubset(set(df.columns)):
        fig.update_layout(title="No XYZ trajectory data available", template=PLOT_TEMPLATE)
        return fig

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
        title="Glider 3D trajectory",
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
# Sidebar
# -----------------------------------------------------------------------------


st.title("Tethered Glider Dataset Dashboard")
st.caption("Reads generated files only. It does not control Isaac Sim.")

with st.sidebar:
    st.header("Dataset")
    dataset_input = st.text_input(
        "Dataset folder",
        value=str(DEFAULT_DATASET_DIR),
        help="Use an absolute path or a path relative to the project root.",
    )
    dataset_dir = resolve_dataset_dir(dataset_input)

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

frame_state = coerce_numeric_columns(read_csv_if_exists(frame_state_path))
manifest = read_csv_if_exists(manifest_path)
metadata = read_json_if_exists(metadata_path)
validation = read_json_if_exists(validation_path)

if not dataset_dir.exists():
    st.error(f"Dataset folder does not exist yet: {dataset_dir}")
    st.info("Auto-refresh will keep checking. Start an Isaac capture run or select an existing dataset folder.")


# -----------------------------------------------------------------------------
# Status row
# -----------------------------------------------------------------------------


status_cols = st.columns(5)
status_cols[0].metric("Dataset exists", "yes" if dataset_dir.exists() else "no")
status_cols[1].metric("Frame rows", len(frame_state))
status_cols[2].metric("Manifest rows", len(manifest))
status_cols[3].metric("Validation", str(validation.get("ok", "missing")))
status_cols[4].metric("Auto-refresh", "on" if auto_refresh and not paused else "off")

st.caption(f"Dataset: `{dataset_dir}`")

if not frame_state.empty:
    latest_row = frame_state.tail(1).iloc[0]
    latest_frame = latest_row.get("frame_index", "?")
    latest_time = latest_row.get("sim_timestamp_s", "?")
    st.caption(f"Latest telemetry row: frame `{latest_frame}`, t=`{latest_time}` s")


# -----------------------------------------------------------------------------
# Tabs
# -----------------------------------------------------------------------------


tab_live, tab_data, tab_plots, tab_files = st.tabs(
    ["Live cameras", "Telemetry rows", "Plots", "Files"]
)

with tab_live:
    st.subheader("Latest camera images")
    camera_cols = st.columns(2)

    for index, camera_folder in enumerate(CAMERA_FOLDERS):
        camera_dir = dataset_dir / camera_folder
        image_path = newest_available_camera_image(camera_dir)

        with camera_cols[index]:
            st.markdown(f"### {camera_folder}")

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
        trajectory_fig = make_trajectory_figure(plot_df)
        st.plotly_chart(trajectory_fig, use_container_width=True, config={"displaylogo": False})

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

with tab_files:
    st.subheader("Dataset files")

    file_rows = []
    for path in [frame_state_path, manifest_path, metadata_path, validation_path]:
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
