# kite_isaac_live_dashboard_exports_v14

Focused patch for live telemetry and reproducible dashboard exports.

Replace/add:

```text
scenario/tethered_glider_scene.py
dashboard_streamlit.py
```

No changes are required to `main.py`, the GUI, asset registry, or capture profile.

## What changed

### 1. Live frame_state.csv streaming

The previous code wrote `frame_state.csv` only after the Isaac capture finished. The dashboard could show live camera images, but telemetry plots stayed stale until the final CSV was written.

Now Isaac writes one `frame_state.csv` row per timestep during capture and flushes each row immediately. At the end of the run, the final `frame_state.csv` is rewritten once from the final manifest so filenames remain correct, including the post-rename case.

### 2. Live last_frame.png update

`camera_main/last_frame.png` and `camera_secondary/last_frame.png` are now updated during capture when new RGB images become available. The final last-frame copy is still written at the end of capture.

### 3. Dashboard reads partial CSVs safely

The dashboard now reads CSVs with `on_bad_lines="skip"` so one transient partial row cannot crash the dashboard while Isaac is writing.

### 4. Faster refresh slider

The dashboard slider now accepts 10 ms minimum. This is allowed, but it asks Streamlit for 100 full reruns per second and can overload the app. Prefer 100-500 ms unless you are briefly debugging.

### 5. Export bundles

Telemetry and trajectory plots now export a reproducible bundle:

```text
.png
.svg
.pdf
.html
.plotly.json
.data.csv
.data.json
```

The export uses explicit width, height, scale, white background, and Plotly layout settings so the result does not silently change aspect ratio or drop colors.

