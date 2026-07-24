# kite_isaac_v25_annotations_info_hotfix

## Purpose

This is a small hotfix on top of `kite_isaac_glider_ground_truth_annotations_v25.zip`.

It fixes a real usability omission: v25 added an `Annotations` tab but did not add a matching help/info button or document the new options in the global `Info` tab.

From this point onward, every new GUI/dashboard tab or non-trivial option should include an explanatory help box or Info-tab section. This is not cosmetic. It avoids invalid datasets caused by wrong settings.

## Files to replace

Replace only:

```text
gui/scene_profile_gui.py
README_PATCH.md
```

Do not replace `scenario/tethered_glider_scene.py`, `dashboard_streamlit.py`, or `utils/profile_io.py` from this ZIP because they are unchanged from v25.

## What changed

### 1. New button in the Annotations tab

The `Annotations` tab now has:

```text
Annotations option guide
```

This opens a compact explanation dialog for:

```text
Enable annotation output
Apply semantic label to /World/Glider
Semantic segmentation
Instance segmentation
Binary glider mask requested
2D tight bounding boxes
2D loose bounding boxes
3D bounding boxes
Distance to camera
Distance to image plane
Debug overlays requested
Keep raw Replicator annotation files
```

### 2. Global Info tab updated

The `Info` tab now includes a full `Annotations tab` section explaining:

```text
Goal of annotations
Semantic labels
Semantic segmentation
Instance segmentation
Binary masks
2D tight/loose bounding boxes
3D bounding boxes
Depth/distance outputs
Debug overlays
Raw Replicator files
```

### 3. No behavior changes

This hotfix changes only GUI documentation/help.

It does not change:

```text
simulation logic
annotation capture logic
Replicator setup
output folders
camera model export
frame labels export
dashboard behavior
```

## How to apply

From the ZIP, copy:

```text
gui/scene_profile_gui.py
```

into:

```text
C:\Users\Admin\Documents\Github\awes-isaac\src\kite-isaac\gui\scene_profile_gui.py
```

Then run:

```powershell
cd C:\Users\Admin\Documents\Github\awes-isaac\src\kite-isaac
C:\Users\Admin\anaconda3\envs\env_isaaclab\python.exe main.py
```

Open the GUI and check:

```text
Annotations tab -> Annotations option guide button exists
Info tab -> Annotations tab section exists
```

## Validation performed here

Python syntax check passed:

```text
python -m py_compile gui/scene_profile_gui.py
```

Isaac Sim was not run in this environment.
