# main.py
#
# Step 4 prototype:
#   - Open GUI first.
#   - Edit/load/save tethered glider scene JSON profile.
#   - When user clicks "Run Isaac Sim", GUI closes.
#   - Then Isaac Sim starts and runs the selected profile.
#
# Run:
#
#   python main.py
#
# Important:
#   Isaac Sim is imported only after the GUI closes.

from pathlib import Path

from gui.scene_profile_gui import run_scene_profile_gui
from utils.profile_io import validate_tethered_glider_profile


PROJECT_ROOT = Path(__file__).parent.resolve()
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "profiles" / "tethered_glider_basic.json"


ISAAC_SIM_CONFIG = {
    "headless": False,
    "renderer": "RealTimePathTracing",
    "width": 1280,
    "height": 720,
    "anti_aliasing": 3,
    "sync_loads": True,
}


def main() -> int:
    gui_result = run_scene_profile_gui(
        project_root=PROJECT_ROOT,
        initial_profile_path=DEFAULT_PROFILE_PATH,
    )

    if gui_result is None:
        print("Cancelled. Isaac Sim was not started.")
        return 0

    if not gui_result.get("run_requested", False):
        print("No run requested. Isaac Sim was not started.")
        return 0

    scene_config = gui_result["profile"]
    profile_path = gui_result["profile_path"]

    validate_tethered_glider_profile(scene_config)

    print("=" * 100)
    print("Starting Isaac Sim from GUI-selected profile")
    print("=" * 100)
    print(f"Profile path: {profile_path}")
    print(f"Scene name: {scene_config['scene_name']}")
    print(f"Anchor position: {scene_config['anchor_position']}")
    print(f"Tether length: {scene_config['tether_length_m']} m")
    print(f"Glider height: {scene_config['glider_height_m']} m")
    print(f"Angular velocity: {scene_config['angular_velocity_rad_s']} rad/s")
    print(f"Frames: {scene_config['num_frames']}")
    print(f"Time step: {scene_config['time_step_s']} s")
    print("=" * 100)

    from isaacsim import SimulationApp

    simulation_app = SimulationApp(ISAAC_SIM_CONFIG)

    try:
        from scenario.tethered_glider_scene import run_tethered_glider_scene

        run_tethered_glider_scene(
            simulation_app=simulation_app,
            scene_config=scene_config,
        )
    finally:
        simulation_app.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())