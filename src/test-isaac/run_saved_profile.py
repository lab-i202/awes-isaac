# run_saved_profile.py
#
# Use this when you already have a saved JSON profile and do not want to open the GUI.
#
# Edit PROFILE_PATH, then run:
#
#   python run_saved_profile.py
#
# No argparse, by design.

from pathlib import Path

from utils.profile_io import load_profile


PROFILE_PATH = Path("profiles/rtx_realtime_2_balanced.json")


def main() -> int:
    project_root = Path(__file__).parent.resolve()
    profile_path = (project_root / PROFILE_PATH).resolve()

    profile = load_profile(profile_path)

    print(f"Loaded profile: {profile_path}")
    print("Starting Isaac Sim...")

    from scenario.isaac_runner import run_isaac_sim

    run_isaac_sim(
        profile=profile,
        project_root=project_root,
        profile_path=profile_path,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
