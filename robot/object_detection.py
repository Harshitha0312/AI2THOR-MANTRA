"""
Simple object-detection helpers built on top of AI2-THOR's ground-truth
metadata (this is the standard approach for ManipulaTHOR projects that
focus on manipulation/planning rather than perception research).
"""

from environment import scene_manager


class ObjectNotFoundError(Exception):
    """Raised when a requested object cannot be grounded to the scene."""


def find_object(controller, object_name, prefer=None):
    """
    Locates an object by natural-language name and returns its full
    metadata dict. Raises ObjectNotFoundError if nothing matches.
    """
    object_id = scene_manager.find_object_id_by_name(controller, object_name, prefer=prefer)
    if object_id is None:
        raise ObjectNotFoundError(
            f"I cannot find a '{object_name}' in this scene."
        )
    return scene_manager.get_object_metadata(controller, object_id)


def is_visible(controller, object_id):
    obj = scene_manager.get_object_metadata(controller, object_id)
    return bool(obj and obj.get("visible"))
