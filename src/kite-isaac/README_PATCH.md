# kite_isaac_dashboard_state_export_v24

This patch adds a **simulation-state export button** to the Streamlit dashboard.

The goal is to make it easy to share the state of a simulation run for debugging without sending the full RGB image sequence.

---

## Files to replace

Replace only:

```text
dashboard_streamlit.py
README_PATCH.md
```

No Isaac Sim scene code is changed by this patch.

---

## What this patch adds

In the dashboard `Files` tab, there is now a new section:

```text
Simulation state export
```

with a button:

```text
Download simulation state ZIP
```

The ZIP contains the relevant diagnostic/configuration files needed to inspect or share a run state.

---

## What the export ZIP includes

The generated ZIP includes:

```text
dashboard_export_summary.json
archive_inventory.json
dataset_file_inventory.csv

run/profile_used.json
run/frame_state.csv
run/capture_manifest.csv
run/camera_rig_metadata.json
run/validation_report.json
run/camera_rig_layout.json
run/camera_rig_layout_summary.csv

layout/camera_rig_layout*.svg

logs/run.log
logs/events.jsonl
logs/warnings.jsonl
logs/errors.jsonl

performance/frame_timing.csv
performance/module_timing.csv
performance/run_performance_summary.json

latest_images/camera_main_last_frame.png
latest_images/camera_main_<latest rgb>.png
latest_images/camera_secondary_last_frame.png
latest_images/camera_secondary_<latest rgb>.png

project/render_profiles.json
```

The exact content depends on which files exist for the selected output scene.

---

## What it intentionally does not include

It does **not** include every RGB frame:

```text
camera_main/rgb_*.png
camera_secondary/rgb_*.png
```

Reason: that would make the support ZIP unnecessarily huge.

Instead, it includes:

```text
last_frame.png
latest rgb_*.png
```

for each camera.

This is enough to diagnose most scene, camera, render, timing, logging, and dashboard-state problems.

---

## Generated summary file

The ZIP includes:

```text
dashboard_export_summary.json
```

This records:

```text
export timestamp
project root
dataset directory
scene name
whether full RGB sequence is included
row counts loaded by dashboard
performance summary
validation summary
recent frame_state preview
recent frame_timing preview
recent module_timing preview
```

---

## Generated inventories

The ZIP includes two inventories:

```text
archive_inventory.json
```

This lists what was attempted for inclusion, whether it existed, and whether it was included.

```text
dataset_file_inventory.csv
```

This lists all files found inside the selected dataset output folder, including relative path, size, and modification time.

This is useful when debugging missing/oversized/unexpected files.

---

## How to use

1. Run a simulation.
2. Open the dashboard.
3. Select the output scene in the sidebar.
4. Go to the `Files` tab.
5. Click `Download simulation state ZIP`.
6. Share that ZIP when debugging the run state.

---

## Notes

This patch does not change:

```text
render settings
capture logic
Isaac Sim execution
telemetry logging
performance logging
camera layout generation
```

It only adds a dashboard-side export bundle.

