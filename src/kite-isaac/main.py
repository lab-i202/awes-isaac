# main.py
#
# Step 2 prototype:
#   - Creates a primitive fixed-wing glider proxy.
#   - Creates a central anchor.
#   - Creates a visual tether.
#   - Moves the glider center of mass in a circle around the anchor.
#
# Run with your Isaac Sim Python environment:
#
#   python main.py
#
# Keep this simple for now. GUI comes later.

from isaacsim import SimulationApp


CONFIG = {
    "headless": False,
    "renderer": "RealTimePathTracing",
    "width": 1280,
    "height": 720,
    "anti_aliasing": 3,
    "sync_loads": True,
}


simulation_app = SimulationApp(CONFIG)


def main() -> int:
    from scenario.tethered_glider_scene import run_tethered_glider_scene

    scene_config = {
        "anchor_position": [0.0, 0.0, 0.0],
        "tether_length_m": 5.0,
        "glider_height_m": 1.5,
        "angular_velocity_rad_s": 0.6,
        "num_frames": 600,
        "time_step_s": 1.0 / 60.0,
        "glider": {
            "wingspan_m": 1.4,
            "length_m": 0.95,
            "body_width_m": 0.10,
            "body_height_m": 0.10,
            "wing_chord_m": 0.18,
            "wing_thickness_m": 0.025,
        },
    }

    try:
        run_tethered_glider_scene(
            simulation_app=simulation_app,
            scene_config=scene_config,
        )
    finally:
        simulation_app.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())