"""
Validates a structured command before it is ever allowed to reach the
planner or the simulator. This is the safety boundary described in the
project design: the LLM/NLP output is never trusted directly.
"""

import config


class InvalidCommandError(Exception):
    pass


def validate_command(command):
    """
    Raises InvalidCommandError with a human readable reason if the
    command is malformed. Returns True if the command is well formed.
    """
    if not isinstance(command, dict) or "steps" not in command:
        raise InvalidCommandError("I didn't understand that as a command.")

    steps = command["steps"]
    if not isinstance(steps, list) or len(steps) == 0:
        raise InvalidCommandError("I couldn't find any actions in that sentence.")

    for step in steps:
        action = step.get("action")
        if action not in config.ALLOWED_ACTIONS:
            raise InvalidCommandError(f"'{action}' is not an action I know how to do.")

        if action in config.OBJECT_ACTIONS and not step.get("object"):
            raise InvalidCommandError(f"I need to know what object to {action}.")

        if step.get("amount") is not None:
            try:
                amount = int(step["amount"])
            except (TypeError, ValueError):
                raise InvalidCommandError("The amount has to be a whole number.")
            if not 1 <= amount <= config.MAX_AMOUNT:
                raise InvalidCommandError(
                    f"I can only do that between 1 and {config.MAX_AMOUNT} times at once."
                )

        if action == "place" and not step.get("object"):
            raise InvalidCommandError("I need to know what object to place.")

        if action == "place" and not step.get("target"):
            raise InvalidCommandError(
                f"I need to know where to place the {step.get('object')}."
            )

        if action == "list_contents" and not step.get("target"):
            raise InvalidCommandError("I need to know what to look in or on.")

        if action == "run_task" and step.get("task") not in config.KNOWN_TASKS:
            raise InvalidCommandError(
                f"'{step.get('task')}' isn't a task I know how to run."
            )

    return True


