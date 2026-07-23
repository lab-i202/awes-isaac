# kite_isaac_camera_anchor_metrics_v21

This patch extends the **camera plan-view diagnostics** so each camera wedge reports:

- the **triangle height** of the plotted horizontal-FOV wedge,
- the camera-to-anchor **Euclidean distance**,
- the camera-to-anchor **XY distance**,
- the signed **Δx** and **Δy** from camera to anchor,
- and the signed **Δz** from camera to anchor.

This directly addresses the request to make the plan-view SVG more informative for stereo-geometry interpretation.

---

## What changed

### 1) `scenario/tethered_glider_scene.py`

The camera-rig diagnostics now compute and expose additional per-camera quantities.

#### Added numeric camera-to-anchor diagnostics
For each camera, the generated JSON / CSV diagnostics now include:

- `anchor_relative.dx_anchor_minus_camera_m`
- `anchor_relative.dy_anchor_minus_camera_m`
- `anchor_relative.dz_anchor_minus_camera_m`
- `anchor_relative.distance_xy_m`
- `anchor_relative.distance_euclidean_m`

These are also appended to `camera_rig_layout_summary.csv` as rows such as:

- `camera_main_anchor_dx`
- `camera_main_anchor_dy`
- `camera_main_anchor_distance_xy`
- `camera_main_anchor_distance_euclidean`
- same for `camera_secondary`

#### Added plan-view triangle height
Inside the pure-XY plan-view builder, each camera wedge now computes:

- `base_midpoint`
- `triangle_height_m`

where `triangle_height_m` is the perpendicular distance from the camera apex to the base segment of the horizontal-FOV triangle shown in the plot.

In other words, if the plotted triangle is:

- apex = camera origin in XY,
- base endpoints = the left/right clipped ray endpoints,

then the triangle height is the length from the apex to the midpoint of the base.

#### SVG overlays added
In `camera_rig_layout.svg` / `camera_rig_layout_plan_roi.svg` / `camera_rig_layout_plan_full.svg`, each camera now shows:

- a dashed **triangle-height line** from apex to base midpoint,
- an **`h_tri=... m`** label next to that line,
- camera annotation text including:
  - `h_tri`
  - `d_E` (Euclidean distance to anchor)
  - `Δx(A-C)`
  - `Δy(A-C)`

The numeric side panel now also includes, for each camera:

- plotted triangle height,
- Euclidean distance to anchor,
- XY distance to anchor,
- signed `Δx`, `Δy`, and `Δz`.

---

## Interpretation notes

### Distance sign convention
The deltas use:

- `Δx = anchor_x - camera_x`
- `Δy = anchor_y - camera_y`
- `Δz = anchor_z - camera_z`

So these are **signed offsets from the camera to the anchor** in world coordinates.

### Triangle height meaning
The reported `h_tri` is the height of the **drawn plan-view triangle**, not a 3D frustum depth quantity.

Because the ROI plan view may clip the ray length for readability, `h_tri` corresponds to the actual triangle drawn in that figure.

That is exactly what you asked for: a value associated with each displayed triangle.

---

## Files changed

Replace:

- `scenario/tethered_glider_scene.py`

No dashboard logic change was required for this patch because the dashboard already renders the SVGs produced by the scene generator.

---

## Expected outputs after running again

After a new run, you should see updated values in:

- `camera_rig_layout.svg`
- `camera_rig_layout_plan_roi.svg`
- `camera_rig_layout_plan_full.svg`
- `camera_rig_layout.json`
- `camera_rig_layout_summary.csv`

---

## Quick verification checklist

After running a scene:

1. Open the **Camera layout** tab in the dashboard.
2. In the plan view, confirm that each camera wedge has:
   - a dashed internal height line,
   - an `h_tri=... m` label.
3. Check the right-side numeric panel and confirm each camera lists:
   - `d_E`
   - `d_XY`
   - `Δx`
   - `Δy`
   - `Δz`
4. Open `camera_rig_layout_summary.csv` and confirm the new rows were written.

---

## Important note

This patch improves diagnostics only. It does **not** change:

- capture geometry,
- camera placement,
- render settings,
- glider dynamics,
- or dashboard scene-selection behavior.

