# Isaac Sim Rendering Profile Test

This folder contains a small Isaac Sim rendering-profile test harness.

It is designed for a user who wants to answer this question:

> Was my Isaac Sim scene rendered in high quality, or was I just seeing some default viewport output?

The script creates a simple scene, moves a quadcopter-like object and a red target sphere, captures frames, and writes timing metrics.

## Files

```text
isaac_render_test/
├── README.md
├── render_profiles.yaml
└── render_profile_test.py
```

## What each file does

### `render_profiles.yaml`

This is the main control file.

It contains:

- The active rendering profile.
- The list of available profiles.
- Human-readable profile descriptions.
- Relative speed and quality scores.
- Renderer launch settings.
- Runtime RTX settings.
- Capture resolution.
- Number of Replicator subframes.
- Camera position.
- Scene animation length.

You normally edit this file, not the Python file.

### `render_profile_test.py`

This is the Isaac Sim script.

It:

1. Loads `render_profiles.yaml`.
2. Prints the available rendering profiles.
3. Starts Isaac Sim with the selected profile.
4. Builds a simple test scene.
5. Moves a drone-like object and target object.
6. Captures RGB frames with Replicator.
7. Writes `metrics.json`.

## Requirements

You need:

- Isaac Sim installed.
- Isaac Sim Python available through `python.sh`.
- PyYAML installed in the Isaac Sim Python environment.

Install PyYAML if needed:

```bash
/path/to/isaac-sim/python.sh -m pip install pyyaml
```

## Run

From the folder containing the files:

```bash
/path/to/isaac-sim/python.sh render_profile_test.py
```

## See available profiles without launching Isaac Sim

Edit `render_profiles.yaml`:

```yaml
run:
  list_profiles_only: true
```

Then run:

```bash
/path/to/isaac-sim/python.sh render_profile_test.py
```

The script will print a profile table and exit before Isaac Sim starts.

Set it back afterward:

```yaml
run:
  list_profiles_only: false
```

## Validate the selected profile without launching Isaac Sim

Edit `render_profiles.yaml`:

```yaml
run:
  validate_config_only: true
```

Then run:

```bash
/path/to/isaac-sim/python.sh render_profile_test.py
```

Set it back afterward:

```yaml
run:
  validate_config_only: false
```

## Select a profile

Edit:

```yaml
run:
  active_profile: "rtx_realtime_2_balanced"
```

Replace the value with one of the available profile names.

## Available profiles

| Profile | Renderer | Speed | Quality | Main use |
|---|---:|---:|---:|---|
| `rtx_minimal_textured` | `MinimalRendering` | 5 | 1 | Fast sanity check |
| `legacy_raytraced_lighting` | `RaytracedLighting` | 4 | 2 | Compatibility baseline |
| `rtx_realtime_2_performance` | `RealTimePathTracing` | 4 | 3 | Fast real-time RGB |
| `rtx_realtime_2_balanced` | `RealTimePathTracing` | 3 | 4 | Recommended default |
| `rtx_realtime_2_quality` | `RealTimePathTracing` | 2 | 4 | Higher-quality real-time |
| `pathtracing_preview` | `PathTracing` | 2 | 4 | Quick path-tracing check |
| `pathtracing_quality` | `PathTracing` | 1 | 5 | High-quality reference |
| `pathtracing_ultra` | `PathTracing` | 1 | 5 | Very slow final reference |

Speed and quality are relative scores, not absolute guarantees.

Speed 5 means fastest.

Quality 5 means highest expected visual quality.

## Recommended test order

Do not start with every profile. That wastes time.

Use this order:

### 1. `rtx_minimal_textured`

Use this first only to prove the pipeline works.

Check:

- Isaac Sim launches.
- The scene appears.
- The drone moves.
- The target moves.
- Images are written.
- `metrics.json` is created.

Do not judge realism from this profile.

### 2. `rtx_realtime_2_balanced`

This is the main practical baseline.

Use it to decide whether the scene is already good enough for robotics perception work.

### 3. `rtx_realtime_2_quality`

Use this if the balanced profile works but you want better visual quality.

This is often the practical high-quality profile for synthetic data when path tracing is too slow.

### 4. `pathtracing_quality`

Use this as the high-quality reference.

Compare it against `rtx_realtime_2_quality`.

If `pathtracing_quality` is not visibly better, your bottleneck is probably not the renderer. It is probably one of these:

- Assets.
- Materials.
- Lighting.
- Camera pose.
- Camera intrinsics.
- Resolution.
- Scene scale.
- Texture quality.

### 5. `pathtracing_ultra`

Use only for final still-frame checks.

Do not use it while learning the workflow.

Do not use it for closed-loop robotics.

Do not use it for large datasets unless you have a strong reason.

## Renderer family summary

### RTX Minimal

RTX Minimal - fastest, low fidelity.

Use it for quick debugging. Do not use it to judge photorealism.

### RTX Real-Time 2.0

RTX Real-Time 2.0 - default practical robotics/synthetic-data mode.

Use this as the first serious renderer family.

### RTX Interactive Path Tracing

RTX Interactive Path Tracing - best visual fidelity, slow.

Use it to create quality references and check whether better light transport improves your scene.

### Legacy RaytracedLighting

Legacy RaytracedLighting - useful only as a compatibility baseline.

Use it only if you are comparing against older scripts or older scenes.

## Output

The script writes output here:

```text
render_outputs/<active_profile>/
```

Example:

```text
render_outputs/rtx_realtime_2_balanced/
```

Each run writes:

```text
metrics.json
```

and the images written by Replicator's `BasicWriter`.

## How to compare profiles

Do not rely only on "it looks nice".

Compare:

- `average_capture_seconds` in `metrics.json`.
- Shadow quality.
- Noise in dark areas.
- Motion edges around the rotors.
- Ghosting around the moving target.
- Reflection behavior on the metal plate.
- Small-object visibility.
- Whether the higher-quality profile actually improves the image.

## Decision rules

### If `rtx_realtime_2_balanced` looks good enough

Use it as your default.

### If `rtx_realtime_2_quality` is clearly better and still fast enough

Use `rtx_realtime_2_quality`.

### If `pathtracing_quality` is much better than real-time

Use path tracing for reference frames or small high-quality datasets.

### If `pathtracing_quality` is not much better

Do not waste time increasing renderer quality.

Improve the scene instead:

- Better USD assets.
- Better materials.
- Better lighting.
- HDRI or more realistic environment lighting.
- Better camera intrinsics.
- Better camera placement.
- Higher texture quality.
- More realistic object scale.

## Important limitation

This test scene is not photorealistic by itself.

It is a renderer comparison harness.

Photorealism is not created by a renderer setting alone. It requires good assets, materials, lighting, camera calibration, and scene design.

## Notes about synthetic data

A visually beautiful RGB frame is not automatically good training data.

For segmentation, detection, and tracking, you also need:

- Correct semantic labels.
- Correct instance labels.
- Correct camera intrinsics.
- Correct object poses.
- Enough occlusion variation.
- Enough lighting variation.
- Enough texture and material variation.
- Evaluation against ground truth.

## Common first tests

### Test 1 - Can I run the script?

Use:

```yaml
run:
  active_profile: "rtx_minimal_textured"
```

### Test 2 - What should I use for robotics perception?

Use:

```yaml
run:
  active_profile: "rtx_realtime_2_balanced"
```

### Test 3 - Is higher real-time quality worth it?

Use:

```yaml
run:
  active_profile: "rtx_realtime_2_quality"
```

Compare it to:

```yaml
run:
  active_profile: "rtx_realtime_2_balanced"
```

### Test 4 - Is path tracing worth it?

Use:

```yaml
run:
  active_profile: "pathtracing_quality"
```

Compare it to:

```yaml
run:
  active_profile: "rtx_realtime_2_quality"
```

## Do not make this mistake

Do not conclude:

> My drone moves, therefore Isaac Sim is rendering at maximum quality.

That is false.

Object motion proves your scene update logic works. It does not prove that you selected the best renderer, samples, camera settings, lighting, or materials.
