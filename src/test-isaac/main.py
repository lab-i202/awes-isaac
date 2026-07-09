# main.py
#
# Run with the Python environment that can import Isaac Sim:
#
#   python main.py
#
# Flow:
#   1. Open a small GUI.
#   2. User selects rendering options.
#   3. User clicks Run.
#   4. The selected profile is saved as JSON inside ./profiles.
#   5. The GUI closes.
#   6. Isaac Sim starts with the saved profile.
#
# Important:
#   Do not import Isaac Sim before the GUI finishes.
#   SimulationApp must be created only after the final renderer settings are known.

from pathlib import Path

from gui.profile_gui import run_profile_gui


def main() -> int:
    project_root = Path(__file__).parent.resolve()

    gui_result = run_profile_gui(project_root=project_root)

    if gui_result is None:
        print("No profile selected. Exiting.")
        return 0

    if not gui_result.get("run_requested", False):
        print(f"Profile saved: {gui_result.get('profile_path')}")
        print("Run was not requested. Exiting.")
        return 0

    profile = gui_result["profile"]
    profile_path = gui_result["profile_path"]

    print(f"Profile saved: {profile_path}")
    print("Starting Isaac Sim...")

    # Import Isaac Sim code only after the GUI has closed.
    from scenario.isaac_runner import run_isaac_sim

    run_isaac_sim(
        profile=profile,
        project_root=project_root,
        profile_path=profile_path,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
