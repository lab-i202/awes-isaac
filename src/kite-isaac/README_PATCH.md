# kite_isaac_environment_loader_v15

This patch adds an external environment loader with whole-scene transform controls.

## Replace/add these files

```text
utils/asset_io.py
utils/profile_io.py
gui/scene_profile_gui.py
scenario/tethered_glider_scene.py
```

`dashboard_streamlit.py` is included unchanged from v14 for convenience.

## What changed

- Existing `plain_debug` scene remains available and unchanged.
- New `external_usd` environment mode loads local environment folders from:

```text
assets/environments/<environment_id>/
```

- The GUI now has an **Environment** tab.
- Environment dropdown is populated from local folders under `assets/environments/`.
- The whole imported environment is loaded under:

```text
/World/Environment
```

- The whole imported environment can be transformed together:

```text
translation_m
rotation_xyz_deg
uniform_scale
```

- Project-created objects still remain separate:

```text
/World/Glider
/World/Cameras
/World/CameraMarkers
/World/Tether
/World/Anchor
```

- `frame_state.csv` now logs:

```text
environment_mode
environment_asset_id
environment_scene_path
environment_translation_x_m
environment_translation_y_m
environment_translation_z_m
environment_rotation_x_deg
environment_rotation_y_deg
environment_rotation_z_deg
environment_uniform_scale
```

## Expected environment folder

Your current folder is correct:

```text
assets/environments/flatland_trees_cloudy_01/
    metadata.json
    scene.usda
    hdri/autumn_field_8k.hdr
    source/grass_with_dirt_patches_material.glb
    textures/
```

## First GUI settings

Open the GUI and set:

```text
Environment mode: external_usd
External environment ID: flatland_trees_cloudy_01
Translation: [0, 0, 0]
Rotation XYZ: [0, 0, 0]
Uniform scale: 1
Use project default lights: unchecked
Fallback to plain_debug: checked
```

Use the transform fields only if the glider/camera rig is not centered over the useful grass field.

## Notes

If the imported environment already has a Dome Light / HDRI / sun, leave project default lights off to avoid double lighting.

The NVIDIA tree URLs in the scene are still external dependencies. That is acceptable for local development but not fully self-contained.
