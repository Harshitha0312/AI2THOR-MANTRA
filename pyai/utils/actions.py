import ai2thor
import numpy as np
import random
import math

def move_forward(controller):
    return controller.step(action="MoveAhead")

def move_backward(controller):
    return controller.step(action="MoveBack")

def turn_left(controller):
    return controller.step(action="RotateLeft")

def turn_right(controller):
    return controller.step(action="RotateRight")

def look_up(controller):
    return controller.step(action="LookUp")

def look_down(controller):
    return controller.step(action="LookDown")

def drop_object(controller):
    return controller.step(action="DropHandObject")


def move_smart(controller):
    action = random.choice(["MoveAhead", "MoveAhead", "MoveAhead",
                             "RotateLeft", "RotateRight"])
    event = controller.step(action=action)
    if not event.metadata["lastActionSuccess"]:
        return controller.step(action=random.choice(["RotateLeft", "RotateRight"]))
    return event


def teleport_random(controller, reachable_positions):
    pos = random.choice(reachable_positions)
    return controller.step(action="Teleport", position=pos, horizon=30)


def get_closest_position(target_pos, reachable_positions):
    return min(
        reachable_positions,
        key=lambda p: math.dist(
            [p["x"], p["z"]],
            [target_pos["x"], target_pos["z"]]
        )
    )


def teleport_to_object(controller, obj, reachable_positions):
    closest = get_closest_position(obj["position"], reachable_positions)
    return controller.step(action="Teleport", position=closest, horizon=30)
def try_pickup(controller, obj):
    return controller.step(action="PickupObject", objectId=obj["objectId"])


def try_place(controller, obj):
    return controller.step(action="PutObject", objectId=obj["objectId"])

def _can_interact(obj, max_distance=1.2):
    return (
        obj.get("openable", False)
        and obj.get("visible", False)
        and obj.get("distance", 999) <= max_distance
    )

def open_object(controller, obj):
    if not _can_interact(obj) or obj.get("isOpen", False):
        return None
    controller.step(action="LookDown")
    event = controller.step(action="OpenObject", objectId=obj["objectId"])
    return event if event.metadata["lastActionSuccess"] else None


def close_object(controller, obj):
    if not _can_interact(obj) or not obj.get("isOpen", False):
        return None
    controller.step(action="LookDown")
    event = controller.step(action="CloseObject", objectId=obj["objectId"])
    return event if event.metadata["lastActionSuccess"] else None