# v18 dashboard camera-order and frustum-diagnostic fixes

Patch name: `kite_isaac_dashboard_frustum_layout_v18.zip`

This patch builds on v17. It fixes the dashboard camera-column order issue and clarifies the camera-frustum workflow.

## Replace these files

Copy these files into the project root, preserving folders:

```text
scenario/tethered_glider_scene.py
dashboard_streamlit.py
README_PATCH.md
```

No environment assets are changed. Keep your environment at:

```text
assets/environments/flatland_trees_cloudy_01/
    metadata.json
    scene.usda
    hdri/autumn_field_8k.hdr
    source/grass_with_dirt_patches_material.glb
    textures/
```

## Fixed / changed behavior

### 1. Dashboard: swap camera columns

The dashboard now has a camera-display control:

```text
Swap camera columns
```

This only changes the dashboard display order:

```text
camera_main | camera_secondary
```

or:

```text
camera_secondary | camera_main
```

It does not rename folders, does not change `capture_manifest.csv`, does not change `frame_state.csv`, and does not change the profile.

Use this when the physical right camera is currently stored as `camera_main`, or when you simply want the dashboard display to match your visual interpretation.

### 2. Dashboard: new Camera layout tab

The dashboard now includes a tab:

```text
Camera layout
```

It displays:

```text
outputs/<scene>/camera_rig_layout.svg
outputs/<scene>/camera_rig_layout.json
```

This is the correct diagnostic for stereo geometry, camera placement, horizontal FOV, and approximate view overlap.

### 3. Camera layout SVG now uses the configured frustum distance

Before v18, the SVG drew a compact schematic wedge. That was useful but not faithful to the `frustum_distance_m` setting.

v18 changes the generated SVG so that the top-down wedges use:

```json
"camera_rig": {
  "horizontal_fov_deg": ...,
  "frustum_distance_m": ...
}
```

The diagram draws transparent wedges. Their overlap appears by transparency blending.

This makes the diagram more useful when you set, for example:

```text
frustum_distance_m = 50
frustum_distance_m = 100
frustum_distance_m = 200
```

### 4. Important: do not judge overlap from the camera RGB image

The in-scene frustum overlay under:

```text
/World/CameraFrustums
```

is renderable debug geometry.

If you look through the same camera that owns the frustum, the far-plane rectangle is projected near the image boundary. At large distances it may be clipped, partially hidden, too thin to see clearly, or outside the visible raster due to exact projection/antialiasing. It is not a good stereo-overlap diagnostic.

Use the `camera_rig_layout.svg` / dashboard Camera layout tab for geometric interpretation. Use in-scene frustums only from the Isaac perspective viewport while tuning camera pose.

### 5. Do not enable frustums for clean dataset capture

Frustum lines are visible renderable geometry. If enabled, they can appear in:

```text
camera_main/rgb_*.png
camera_secondary/rgb_*.png
```

Keep this unchecked for final datasets:

```text
Show camera frustum rectangles/rays in Isaac viewport = false
```

Enable it only for short tuning/debugging runs.

## Test procedure

1. Replace the files.

2. Run the GUI/Isaac producer normally:

```powershell
cd C:\Users\Admin\Documents\Github\awes-isaac\src\kite-isaac
C:\Users\Admin\anaconda3\envs\env_isaaclab\python.exe main.py
```

3. Use a short run first:

```text
num_frames = 60
```

4. For clean RGB capture, keep:

```text
Show camera frustum rectangles/rays = false
```

5. For geometry tuning only, enable frustums and set:

```text
Frustum distance [m] = 20, 50, 100, or 200
```

6. After the run, check:

```text
outputs/<scene>/camera_rig_layout.svg
outputs/<scene>/camera_rig_layout.json
outputs/<scene>/camera_main/last_frame.png
outputs/<scene>/camera_secondary/last_frame.png
```

7. Run the dashboard:

```powershell
cd C:\Users\Admin\Documents\Github\awes-isaac\src\kite-isaac
C:\Users\Admin\anaconda3\envs\main\python.exe -m streamlit run dashboard_streamlit.py
```

8. In the dashboard:

```text
- select the scene output folder
- use Swap camera columns if needed
- open Camera layout tab
- inspect approximate stereo overlap in camera_rig_layout.svg
```

## Expected result

```text
- dashboard camera columns can be swapped without changing data
- camera_rig_layout.svg shows full configured frustum wedges in top-down XY
- dashboard has a Camera layout tab
- RGB images remain clean when frustum overlays are disabled
```

## Files not changed

The GUI scene manager, profile normalization, asset loader, and environment loader files from v17 are unchanged by v18.

