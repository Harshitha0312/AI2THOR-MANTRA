"""
Everything related to "what is currently in the scene" and grounding:
turning a word like "apple" into a real AI2-THOR objectId such as
'Apple|+00.97|+00.99|-01.05'.
"""

import difflib
import math


def get_all_objects(controller):
    """Returns the full list of object metadata dicts in the scene."""
    event = controller.step(action="Pass")
    return event.metadata["objects"]


def refresh(controller):
    """
    Pulls fresh ground-truth metadata from AI2-THOR (one Pass tick) and
    returns it. Call after anything that can change the scene without a
    normal action - loading/resetting/switching a scene most of all.
    """
    return controller.step(action="Pass").metadata


def get_agent_position(controller):
    event = controller.last_event
    return event.metadata["agent"]["position"]


def _distance(pos_a, pos_b):
    return math.sqrt(
        (pos_a["x"] - pos_b["x"]) ** 2 + (pos_a["z"] - pos_b["z"]) ** 2
    )


_MODIFIERS = ("cracked", "sliced", "broken", "chopped", "cooked", "raw", "fried", "whole", "boiled")

_PREFER_KEYS = {
    "receptacle": "receptacle",
    "pickupable": "pickupable",
    "toggleable": "toggleable",
    "openable": "openable",
}


def find_object_id_by_name(controller, name, prefer=None):
    """
    Grounding step: given a spoken/typed object name (e.g. "apple",
    "coffee table", "fridge"), find the closest matching AI2-THOR
    objectType in the current scene and return its objectId.

    prefer ("receptacle" | "pickupable" | "toggleable" | "openable"):
    when several objects match (a "stove" is both a StoveBurner you put a
    pan on and a StoveKnob you switch), keep only the ones with that
    capability if any have it. Names like "cracked egg" / "sliced apple"
    resolve to the transformed object (EggCracked / AppleSliced) because
    breaking or slicing replaces the original object.

    Returns None if nothing matches.
    """
    if not name:
        return None

    target = name.strip().lower().replace(" ", "")

    synonyms = {
        "refrigerator": "fridge",
        "icebox": "fridge",
        "trashcan": "garbagecan",
        "bin": "garbagecan",
        "trash": "garbagecan",
        "tv": "television",
        "couch": "sofa",
        "fryingpan": "pan",
        "frypan": "pan",
        "skillet": "pan",
        "cooktop": "stoveburner",
        "hob": "stoveburner",
        "burner": "stoveburner",
        "counter": "countertop",
        "worktop": "countertop",
        "eggs": "egg",
    }
    target = synonyms.get(target, target)

    objects = get_all_objects(controller)
    agent_pos = get_agent_position(controller)

    def candidates_for(t):
        exact = [o for o in objects if o["objectType"].lower() == t]
        return exact or [o for o in objects if t in o["objectType"].lower()]

    candidates = candidates_for(target)

    if not candidates:
        stripped = target
        for m in _MODIFIERS:
            stripped = stripped.replace(m, "")
        if stripped and stripped != target:
            stripped = synonyms.get(stripped, stripped)
            candidates = candidates_for(stripped)

    if not candidates:
        # Typos / mis-heard words ("ppillow", "pilllow", "tomatoe"): accept the
        # closest real object type, but only when it is a CLOSE match so that a
        # genuinely missing object ("banana") is still reported as missing.
        close = _closest_type(target, objects)
        if close:
            candidates = candidates_for(close)

    if not candidates:
        return None

    key = _PREFER_KEYS.get(prefer)
    if key:
        preferred = [o for o in candidates if o.get(key)]
        if preferred:
            candidates = preferred

    # Prefer the closest instance to the agent.
    candidates.sort(key=lambda obj: _distance(agent_pos, obj["position"]))
    return candidates[0]["objectId"]


FUZZY_CUTOFF = 0.8      # 1.0 = identical; 0.8 tolerates one or two wrong/extra letters


def _closest_type(target, objects):
    """Best fuzzy match of `target` among the scene's object types, or None."""
    types = {}
    for o in objects:
        types.setdefault(o["objectType"].lower(), o["objectType"].lower())
    hit = difflib.get_close_matches(target, list(types), n=1, cutoff=FUZZY_CUTOFF)
    return hit[0] if hit else None


def get_object_metadata(controller, object_id):
    objects = get_all_objects(controller)
    for obj in objects:
        if obj["objectId"] == object_id:
            return obj
    return None


def list_visible_object_types(controller):
    objects = get_all_objects(controller)
    return sorted({obj["objectType"] for obj in objects if obj["visible"]})
