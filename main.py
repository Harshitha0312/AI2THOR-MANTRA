"""
MANTRA (Nlp26) - voice/NLP controlled ManipulaTHOR robot.

Run this file directly in PyCharm. Pipeline:

    Voice / typed command
        -> speech_to_text.listen()
        -> nlp.command_parser.parse_command()
        -> planner.action_validator.validate_command()
        -> planner.task_planner.build_plan()
        -> executor.execute_plan()
        -> speech.text_to_speech.speak()
"""

import re

import config
from environment import scene_manager
from executor import run_text_command
from robot.sim_controller import get_controller
from speech.speech_to_text import listen
from speech.text_to_speech import speak

# Lets you say/type things like "switch to FloorPlan204", "load floorplan 12",
# "change scene to Floorplan301" at any point, without restarting the
# program or touching config.py. AI2-THOR can only have one scene loaded
# at a time, so this replaces the current scene rather than adding to it.
_SCENE_PATTERN = re.compile(r"floor\s*plan\s*0*([1-9]\d{0,3})", re.IGNORECASE)


def try_switch_scene(controller, text):
    """
    If `text` mentions a FloorPlan name, resets the controller to that
    scene and returns True. Returns False (does nothing) otherwise, so
    the caller can fall through to normal command handling.
    """
    match = _SCENE_PATTERN.search(text)
    if not match:
        return False

    scene_name = f"FloorPlan{match.group(1)}"
    try:
        controller.reset(scene=scene_name)
    except Exception as exc:
        speak(f"I couldn't load {scene_name}: {exc}")
        return True

    config.SCENE = scene_name
    scene_manager.refresh(controller)      # fresh ground truth for the new scene
    print(f"Switched to scene '{scene_name}'.")
    print("Objects currently visible:", scene_manager.list_visible_object_types(controller))
    speak(f"Switched to {scene_name}. Ready for your next command.")
    return True


def handle_command(controller, text):
    if not text:
        speak("I didn't catch that, please try again.")
        return

    speak(run_text_command(controller, text))


def main():
    print("Starting MANTRA...")
    controller = get_controller()
    print(f"AI2-THOR ready in scene '{config.SCENE}'.")
    print("Objects currently visible:", scene_manager.list_visible_object_types(controller))

    speak("MANTRA is ready. Tell me what to do.")

    try:
        while True:
            command_text = listen()

            if not command_text:
                continue

            if command_text.strip().lower() in config.EXIT_WORDS:
                speak("Goodbye.")
                break

            if try_switch_scene(controller, command_text):
                continue

            handle_command(controller, command_text)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    finally:
        controller.stop()
        print("Controller stopped. Exiting.")


if __name__ == "__main__":
    import ui_app
    ui_app.main()

