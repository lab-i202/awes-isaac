# Isaac Sim JSON Profile Runner

This is a small refactor of the rendering-test script.

It separates:

```text
gui/       - Tkinter GUI for choosing renderer options
scenario/  - Isaac Sim scene construction and runner
utils/     - JSON profile save/load and scoring logic
profiles/  - saved JSON profiles
```

## Run with GUI

```bash
python main.py
```

The GUI appears first.

Pick options, then click:

- `Save JSON Only` - saves the profile into `profiles/` and exits.
- `Load JSON Profile` - loads an existing profile from `profiles/`.
- `Run` - saves the profile into `profiles/`, closes the GUI, then starts Isaac Sim.

## Run a saved profile without GUI

Edit this line in `run_saved_profile.py`:

```python
PROFILE_PATH = Path("profiles/rtx_realtime_2_balanced.json")
```

Then run:

```bash
python run_saved_profile.py
```

## Important design rule

Isaac Sim is imported only after the GUI closes.

This is intentional. `SimulationApp(...)` needs final launch settings such as renderer, resolution, and headless mode.

## Profiles

Saved profiles are JSON files in:

```text
profiles/
```

Example:

```text
profiles/rtx_realtime_2_balanced.json
```

## Smoothness and quality meters

The GUI meters are heuristic.

They are not measured FPS.

They estimate likely smoothness and quality based on:

- renderer
- resolution
- RT subframes
- DLSS mode
- path-tracing samples
- max bounces
- enabled annotations

The real FPS is still measured during the Isaac Sim run and written to:

```text
render_outputs/<profile_name>/metrics.json
```

## First tests

Start with:

```text
RTX Real-Time 2.0 - balanced
```

Then compare against:

```text
RTX Real-Time 2.0 - performance
RTX Real-Time 2.0 - quality
RTX Interactive Path Tracing - quality
```

Do not start with Path Tracing Ultra-style settings. They are slow and not useful while debugging the pipeline.
