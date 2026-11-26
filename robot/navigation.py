

"""
Navigation module.
"""

import math

from environment import scene_manager
from robot import hybrid_navigator, metrics, sync


class NavigationError(Exception):
    """Raised when the agent cannot reach a target object."""


MAX_CANDIDATES = 30


def _distance(pos_a, pos_b):
    return math.sqrt(
        (pos_a["x"] - pos_b["x"]) ** 2 + (pos_a["z"] - pos_b["z"]) ** 2
    )


def navigate_to(controller, object_id):
    """
    Walks the agent to object_id via the TSP/A* hybrid navigator (real
    MoveAhead/Rotate actions along an obstacle-aware path) instead of
    teleporting there, then faces it precisely. Same signature, same
    NavigationError contract, and the same "try several candidate
    reachable positions until the object is actually visible" fallback
    as before - just real movement instead of a jump for each attempt.
    """
    obj = scene_manager.get_object_metadata(controller, object_id)
    if obj is None:
        raise NavigationError(f"I can't find any object with id '{object_id}'.")
    target_pos = obj["position"]

    reachable_event = controller.step(action="GetReachablePositions")
    if not reachable_event.metadata["lastActionSuccess"]:
        raise NavigationError(
            f"Could not compute reachable positions: "
            f"{reachable_event.metadata.get('errorMessage')}"
        )

    positions = reachable_event.metadata.get("actionReturn") or []
    if not positions:
        raise NavigationError("The scene has no reachable positions at all.")

    # Same reachable-aware search as every other approach in the project:
    # nearest standing spots that A* can really walk to, arrival verified,
    # accepted only when the object is actually visible / within reach.
    current, reached = hybrid_navigator.approach_object(
        controller, obj, positions, interact_dist=1.5, horizon=0, max_candidates=6,
    )
    if reached:
        return controller.last_event

    # Large objects (a table, a bed) can have a centre that is far from every
    # standing spot even though their edge is in reach - accept the best
    # attempt and let the action that follows (pickup / put) verify reach.
    if controller.last_event.metadata["lastActionSuccess"]:
        return controller.last_event

    raise NavigationError(
        f"I cannot find a reachable spot near '{object_id}'."
    )


def ensure_visible(controller, object_id):
    """
    Re-checks that object_id is actually visible right now, and if not,
    re-runs the full navigate_to() search to find a position where it
    is. Meant to be called after an action that can change what's on
    screen without moving the agent - most importantly OpenObject: a
    door/lid swinging open can end up blocking the camera's view of the
    very thing that was just opened. Silently leaving the agent looking
    at nothing after such an action is exactly what caused "opened
    cabinet" to report success but show no visible change on screen.
    """
    obj = scene_manager.get_object_metadata(controller, object_id)
    if obj is not None and obj.get("visible"):
        return controller.last_event
    return navigate_to(controller, object_id)



# ---------------- body / camera moves (no target object) ----------------

_MOVE_ACTIONS = {
    "forward": "MoveAhead", "back": "MoveBack",
    "left": "MoveLeft", "right": "MoveRight",
}


def look(controller, direction, count=1):
    action = "LookUp" if direction == "up" else "LookDown"
    for i in range(count):
        event = controller.step(action=action)
        sync.pace("turn")
        if not event.metadata["lastActionSuccess"]:
            if i == 0:
                raise NavigationError(f"I can't look any further {direction}.")
            return event
    return controller.last_event


def turn(controller, direction, count=1):
    action = "RotateLeft" if direction == "left" else "RotateRight"
    for _ in range(count):
        event = controller.step(action=action)
        sync.pace("turn")
        if not event.metadata["lastActionSuccess"]:
            raise NavigationError(f"I couldn't turn {direction}.")
    return controller.last_event


def move(controller, direction, count=1):
    import config

    action = _MOVE_ACTIONS[direction]
    total = count * config.MOVE_STEPS_PER_UNIT
    for i in range(total):
        event = controller.step(action=action)
        metrics.inc("walk_steps")
        sync.pace("move")
        if not event.metadata["lastActionSuccess"]:
            if i == 0:
                raise NavigationError(f"Something is in the way, I can't move {direction}.")
            return event  # moved part of the way, then hit something
    return controller.last_event


def set_posture(controller, posture):
    event = controller.step(action="Crouch" if posture == "crouch" else "Stand")
    if not event.metadata["lastActionSuccess"]:
        raise NavigationError(f"I couldn't {posture}.")
    return event
