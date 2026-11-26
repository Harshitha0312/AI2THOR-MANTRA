"""
Manipulation actions (Nlp12).

Every function here follows the same contract:

    perform the AI2-THOR action
      -> read the resulting event
      -> verify the EXPECTED STATE CHANGE in ground-truth metadata
      -> raise ManipulationError if the simulator state does not match

so "the call returned" is never mistaken for "it worked". Making the
result visible on screen is NOT done here - that is robot/sync.settle(),
called by executor.execute_atomic_step() (and by the whole-scene tasks)
after each atomic action.
"""

import math

import config
from environment import scene_manager
from robot import control, hybrid_navigator, metrics, sync

# Set to False once you no longer need the debug printout / saved pictures
DEBUG_OPEN = False
DEBUG_TOGGLE = False


class ManipulationError(Exception):
    """Raised when a manipulation action fails in the simulator."""


# ---------------------------------------------------------------------
# small state helpers (all read the most recent event's metadata)
# ---------------------------------------------------------------------

def _check(event, description):
    if not event.metadata["lastActionSuccess"]:
        raise ManipulationError(
            f"{description} failed: {event.metadata.get('errorMessage')}"
        )
    return event


def _objects(controller):
    return controller.last_event.metadata["objects"]


def _find(controller, object_id):
    return next((o for o in _objects(controller) if o["objectId"] == object_id), None)


def _type_of(controller, object_id):
    obj = _find(controller, object_id)
    return obj["objectType"] if obj else object_id.split("|")[0]


def get_held_object_id(controller):
    inventory = controller.last_event.metadata.get("inventoryObjects") or []
    if inventory:
        return inventory[0]["objectId"]
    return None


def _force_allowed(controller, object_id, max_dist):
    if not getattr(config, "FORCE_FALLBACK", True):
        return False
    obj = _find(controller, object_id)
    return obj is not None and obj.get("distance", 99) <= max_dist


def _reachable(controller):
    ev = controller.step(action="GetReachablePositions")
    return ev.metadata.get("actionReturn") or []


# ---------------------------------------------------------------------
# camera / facing (visible, interpolated turns)
# ---------------------------------------------------------------------

def drop_held_object(controller):
    if get_held_object_id(controller) is None:
        return
    event = controller.step(action="DropHandObject", forceAction=True)
    _check(event, "Dropping held object")
    sync.refresh(controller)


def face_object(controller, object_id):
    """
    Turn the agent (in place, visibly) so the camera points at the object.
    Best effort: never raises.
    """
    obj = _find(controller, object_id)
    if obj is None:
        return controller.last_event
    agent = controller.last_event.metadata["agent"]
    dx = obj["position"]["x"] - agent["position"]["x"]
    dz = obj["position"]["z"] - agent["position"]["z"]
    yaw = math.degrees(math.atan2(dx, dz)) % 360
    return hybrid_navigator.smooth_turn(
        controller, yaw, horizon=agent.get("cameraHorizon", 0)
    )


def frame_object(controller, object_id):
    """
    Turn the agent AND tilt the camera so object_id sits in the middle of
    the view. Used before PickupObject/PutObject. Best effort: never
    raises; leaves the agent where it is if the object can't be found.
    """
    event = controller.last_event
    md = event.metadata
    obj = next((o for o in md["objects"] if o["objectId"] == object_id), None)
    if obj is None:
        return event

    agent = md["agent"]
    cam = md.get("cameraPosition") or dict(
        agent["position"], y=agent["position"]["y"] + 0.675
    )
    aabb = obj.get("axisAlignedBoundingBox") or {}
    target = aabb.get("center") or obj["position"]

    dx = target["x"] - cam["x"]
    dy = target["y"] - cam["y"]
    dz = target["z"] - cam["z"]
    flat = math.hypot(dx, dz)

    yaw = math.degrees(math.atan2(dx, dz)) % 360 if flat > 1e-6 else agent["rotation"]["y"]
    pitch = math.degrees(math.atan2(-dy, flat))          # positive = looking down
    horizon = int(max(-30, min(60, 30 * round(pitch / 30.0))))   # agent tilts in 30 degree steps

    return hybrid_navigator.smooth_turn(controller, yaw, horizon=horizon)


def _save_frame(event, filename):
    """Saves the camera picture; never allowed to break an action."""
    try:
        from PIL import Image
        Image.fromarray(event.frame).save(filename)
    except Exception:
        pass


def _debug_after_open(event, object_id):
    visible = [o["objectType"] for o in event.metadata["objects"] if o["visible"]]
    control.say("Visible now: %s" % visible)
    for o in event.metadata["objects"]:
        if o["objectId"] == object_id:
            control.say("Target: %s | isOpen: %s | openness: %s | visible: %s"
                        % (o["objectId"], o.get("isOpen"), o.get("openness"), o["visible"]))
    _save_frame(event, "after_open.png")


def _debug_after_toggle(event, object_id):
    for o in event.metadata["objects"]:
        if o["objectId"] == object_id:
            control.say("Toggle result: %s | isToggled: %s | visible: %s"
                        % (o["objectId"], o.get("isToggled"), o["visible"]))
    _save_frame(event, "after_toggle.png")


# ---------------------------------------------------------------------
# pickup (verified, with recovery)
# ---------------------------------------------------------------------

def _open_closed_parent(controller, object_id):
    """If the object sits inside a closed container, open it (visibly). Returns its id or None."""
    obj = _find(controller, object_id)
    if obj is None:
        return None
    for pid in obj.get("parentReceptacles") or []:
        parent = _find(controller, pid)
        if parent and parent.get("openable") and not parent.get("isOpen"):
            face_object(controller, pid)
            ev = controller.step(action="OpenObject", objectId=pid, forceAction=True)
            if ev.metadata["lastActionSuccess"]:
                sync.settle(controller)
                return pid
    return None


def close_container(controller, container_id, tries=3):
    """
    Closes an open container and VERIFIES it. Before this, the
    close after a pickup was a single unchecked CloseObject whose failure
    was silently ignored, so the fridge could stay open with nobody
    noticing. Now: face it, close, re-read the state; if it is still open,
    walk to another standing spot and try again. Returns True if closed.
    """
    for i in range(tries):
        if control.is_cancelled():
            return False
        cont = _find(controller, container_id)
        if cont is None or not cont.get("isOpen"):
            return True
        if i == 0:
            face_object(controller, container_id)
        else:
            positions = _reachable(controller)
            if positions:
                hybrid_navigator.approach_object(
                    controller, cont, positions, interact_dist=1.0, horizon=0,
                    max_candidates=1, skip=i - 1,
                )
        controller.step(action="CloseObject", objectId=container_id, forceAction=True)
        sync.refresh(controller)
        cont = _find(controller, container_id)
        if cont is None or not cont.get("isOpen"):
            sync.settle(controller)
            return True
    return False


def get_close(controller, object_id, interact_dist=1.3, rounds=3):
    """
    Makes sure the robot really is within reach of object_id BEFORE an
    action is attempted: frames it, checks the live metadata (visible or
    within interact_dist), and if not walks to other standing spots - 3 new
    ones per round - re-framing each time. Returns True when in reach.
    Fixes "placing from a very long distance": the old code attempted the
    put anyway, then 'repositioned' to the 1st/2nd/3rd nearest cell - often
    unreachable - and kept failing from where it stood.
    """
    for i in range(rounds):
        if control.is_cancelled():
            return False
        frame_object(controller, object_id)
        obj = _find(controller, object_id)
        if obj is None:
            return False
        if obj.get("visible") or obj.get("distance", 99) <= interact_dist:
            return True
        positions = _reachable(controller)
        if not positions:
            return False
        control.say(f"Not close enough to the {obj['objectType']} - moving closer.")
        _, reached = hybrid_navigator.approach_object(
            controller, obj, positions, interact_dist=interact_dist, horizon=30,
            max_candidates=3, skip=i * 3,
        )
        if reached:
            frame_object(controller, object_id)
            return True
    return False


def _try_pickup_once(controller, object_id, force):
    frame_object(controller, object_id)
    ev = controller.step(action="PickupObject", objectId=object_id, forceAction=force)
    metrics.inc("pickup_attempts")
    if not ev.metadata["lastActionSuccess"]:
        metrics.inc("pickup_failed")
    return ev


def _pickup_sequence(controller, object_id):
    """
    1. real (non-forced) attempt where the agent stands;
    2. walk to other standing spots near the object and retry each;
    3. if physically close and config.FORCE_FALLBACK, one forced attempt.
    Returns (event, error_text). event is None on total failure.
    """
    last_error = None
    if get_close(controller, object_id, interact_dist=1.3):
        ev = _try_pickup_once(controller, object_id, force=False)
        if ev.metadata["lastActionSuccess"]:
            return ev, None
        last_error = ev.metadata.get("errorMessage")
    else:
        last_error = "I couldn't get close enough to it"

    for i in range(getattr(config, "PICKUP_REPOSITION_TRIES", 4)):
        if control.is_cancelled():
            break
        obj = _find(controller, object_id)
        positions = _reachable(controller)
        if obj is None or not positions:
            break
        control.say(f"Pickup didn't work from here - trying another standing spot ({i + 1}).")
        metrics.inc("pickup_retries")
        _, reached = hybrid_navigator.approach_object(
            controller, obj, positions, interact_dist=1.0, horizon=30,
            max_candidates=2, skip=i * 2,
        )
        if not reached:
            continue                      # never act from a spot that is out of reach
        ev = _try_pickup_once(controller, object_id, force=False)
        if ev.metadata["lastActionSuccess"]:
            return ev, None
        last_error = ev.metadata.get("errorMessage")

    if _force_allowed(controller, object_id, config.PICKUP_FORCE_MAX_DIST):
        ev = _try_pickup_once(controller, object_id, force=True)
        if ev.metadata["lastActionSuccess"]:
            return ev, None
        last_error = ev.metadata.get("errorMessage")

    return None, last_error


def pickup_object(controller, object_id):
    """
    Picks object_id up and VERIFIES it: after the action the simulator's
    inventory must contain it, and the container it sat in must no longer
    list it. Raises ManipulationError otherwise (never claims success on
    the strength of the call returning).
    """
    held = get_held_object_id(controller)
    if held == object_id:
        return controller.last_event
    if held is not None:
        drop_held_object(controller)

    before = _find(controller, object_id)
    if before is None:
        raise ManipulationError(f"I can't find {object_id} any more.")
    prev_parents = list(before.get("parentReceptacles") or [])
    name = before["objectType"]

    opened_parent = _open_closed_parent(controller, object_id)
    # Containers the item is coming out of (closed ones were just opened
    # above; ones that were already open count too) - all get closed again.
    source_containers = []
    for pid in prev_parents:
        parent = _find(controller, pid)
        if parent and parent.get("openable"):
            source_containers.append(pid)

    ev, error = _pickup_sequence(controller, object_id)
    if ev is None:
        raise ManipulationError(f"Picking up the {name} failed: {error}")

    sync.refresh(controller)
    if get_held_object_id(controller) != object_id:
        raise ManipulationError(
            f"The simulator said the {name} was picked up, but it is not in my hand."
        )
    for pid in prev_parents:
        parent = _find(controller, pid)
        if parent and object_id in (parent.get("receptacleObjectIds") or []):
            raise ManipulationError(
                f"The {name} is in my hand but the {parent['objectType']} still lists it."
            )

    if source_containers:
        sync.settle(controller)                  # let the pickup show first
        for pid in source_containers:
            if not close_container(controller, pid):
                control.say(f"Note: I couldn't close the {_type_of(controller, pid)} again.")
    return controller.last_event


# ---------------------------------------------------------------------
# place (verified, with recovery)
# ---------------------------------------------------------------------

_PUT_CAMERA_SEQUENCE = ["LookUp", "LookDown", "LookDown", "LookUp", "LookUp", "LookDown"]
_CAM_REVERSE = {"LookDown": "LookUp", "LookUp": "LookDown"}


def _put_once(controller, receptacle_id, force):
    ev = controller.step(action="PutObject", objectId=receptacle_id, forceAction=force)
    metrics.inc("place_attempts")
    if not ev.metadata["lastActionSuccess"]:
        metrics.inc("place_failed")
    return ev


def _put_sequence(controller, receptacle_id):
    in_reach = get_close(controller, receptacle_id, interact_dist=1.3)
    last_error = None
    if in_reach:
        ev = _put_once(controller, receptacle_id, force=False)
        if ev.metadata["lastActionSuccess"]:
            return ev, None
        last_error = ev.metadata.get("errorMessage")
    else:
        last_error = "I couldn't get close enough to it"

    taken = []
    for action in (_PUT_CAMERA_SEQUENCE if in_reach else []):
        controller.step(action=action)
        taken.append(action)
        ev = _put_once(controller, receptacle_id, force=False)
        if ev.metadata["lastActionSuccess"]:
            for a in reversed(taken):
                controller.step(action=_CAM_REVERSE[a])
            return ev, None
        last_error = ev.metadata.get("errorMessage")
    for a in reversed(taken):
        controller.step(action=_CAM_REVERSE[a])

    for i in range(getattr(config, "PLACE_REPOSITION_TRIES", 3)):
        if control.is_cancelled():
            break
        rec = _find(controller, receptacle_id)
        positions = _reachable(controller)
        if rec is None or not positions:
            break
        control.say(f"Placing didn't work from here - trying another standing spot ({i + 1}).")
        metrics.inc("place_retries")
        _, reached = hybrid_navigator.approach_object(
            controller, rec, positions, interact_dist=1.2, horizon=30,
            max_candidates=2, skip=i * 2,
        )
        if not reached:
            continue                      # never try a put from a spot that is out of reach
        frame_object(controller, receptacle_id)
        ev = _put_once(controller, receptacle_id, force=False)
        if ev.metadata["lastActionSuccess"]:
            return ev, None
        last_error = ev.metadata.get("errorMessage")

    if _force_allowed(controller, receptacle_id, config.PLACE_FORCE_MAX_DIST):
        frame_object(controller, receptacle_id)
        ev = _put_once(controller, receptacle_id, force=True)
        if ev.metadata["lastActionSuccess"]:
            return ev, None
        last_error = ev.metadata.get("errorMessage")

    return None, last_error


def put_object(controller, object_id, receptacle_id):
    """
    Places the held object on/in receptacle_id, then VERIFIES: the hand
    must be empty and the object must exist in the world. If the
    simulator reports a different direct parent (e.g. SinkBasin instead
    of Sink) the real parent is what gets reported.
    """
    held_id = get_held_object_id(controller)
    if held_id is None:
        raise ManipulationError("I'm not holding anything to place.")
    name = _type_of(controller, held_id)
    rec_name = _type_of(controller, receptacle_id)

    ev, error = _put_sequence(controller, receptacle_id)
    if ev is None:
        raise ManipulationError(f"Placing the {name} on/in the {rec_name} failed: {error}")

    sync.refresh(controller)                       # physics tick, then re-read
    if get_held_object_id(controller) is not None:
        raise ManipulationError(f"The {name} is still in my hand after placing it.")
    obj = _find(controller, held_id)
    if obj is None:
        raise ManipulationError(f"The {name} vanished while being placed.")
    parents = obj.get("parentReceptacles") or []
    if receptacle_id not in parents and parents:
        actual = ", ".join(_type_of(controller, p) for p in parents)
        control.say(f"Note: the {name} ended up on/in: {actual}.")
    return controller.last_event


# ---------------------------------------------------------------------
# open / close / toggle (state verified)
# ---------------------------------------------------------------------

def _expect_state(controller, object_id, key, expected, description):
    sync.refresh(controller)
    obj = _find(controller, object_id)
    if obj is None or bool(obj.get(key)) != expected:
        raise ManipulationError(
            f"{description}: the simulator state ({key}) did not change to {expected}."
        )


def open_object(controller, object_id):
    obj = _find(controller, object_id)
    if obj is not None and obj.get("isOpen"):
        return controller.last_event
    face_object(controller, object_id)
    event = controller.step(action="OpenObject", objectId=object_id, forceAction=True)
    _check(event, f"Opening {object_id}")
    _expect_state(controller, object_id, "isOpen", True, f"Opening {_type_of(controller, object_id)}")
    if DEBUG_OPEN:
        _debug_after_open(controller.last_event, object_id)
    return controller.last_event


def close_object(controller, object_id):
    obj = _find(controller, object_id)
    if obj is not None and not obj.get("isOpen"):
        return controller.last_event
    event = controller.step(action="CloseObject", objectId=object_id, forceAction=True)
    _check(event, f"Closing {object_id}")
    _expect_state(controller, object_id, "isOpen", False, f"Closing {_type_of(controller, object_id)}")
    return controller.last_event


def resolve_toggle_target(controller, object_id):
    """
    In AI2-THOR a stove burner isn't itself toggleable - its StoveKnob is,
    and the knob lists the burner in `controlledObjects`. Maps "stove" /
    burner to the knob that actually switches it; anything already
    toggleable is returned unchanged.
    """
    obj = _find(controller, object_id)
    if obj is None or obj.get("toggleable"):
        return object_id
    knobs = [
        o for o in _objects(controller)
        if o.get("toggleable") and object_id in (o.get("controlledObjects") or [])
    ]
    if knobs:
        return knobs[0]["objectId"]
    raise ManipulationError(f"The {obj['objectType']} can't be switched on or off.")


def _toggle(controller, object_id, on):
    target = resolve_toggle_target(controller, object_id)
    obj = _find(controller, target)
    if obj is not None and bool(obj.get("isToggled")) == on:
        return controller.last_event
    face_object(controller, target)
    action = "ToggleObjectOn" if on else "ToggleObjectOff"
    event = controller.step(action=action, objectId=target, forceAction=True)
    word = "Turning on" if on else "Turning off"
    _check(event, f"{word} {target}")
    _expect_state(controller, target, "isToggled", on, f"{word} {_type_of(controller, target)}")
    if DEBUG_TOGGLE:
        _debug_after_toggle(controller.last_event, target)
    return controller.last_event


def toggle_on(controller, object_id):
    return _toggle(controller, object_id, True)


def toggle_off(controller, object_id):
    return _toggle(controller, object_id, False)


# ---------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------

def _flatten_contents(controller, receptacle_id, depth=0, seen=None):
    """
    Everything physically on/in a receptacle from ground-truth metadata.
    Structural sub-receptacles (e.g. a Shelf inside a Fridge) are looked
    through rather than listed; pickupable containers (a Pot) are listed
    with whatever is in them.
    """
    seen = seen if seen is not None else set()
    rec = _find(controller, receptacle_id)
    items = []
    if rec is None or depth > 4:
        return items
    for cid in rec.get("receptacleObjectIds") or []:
        if cid in seen:
            continue
        seen.add(cid)
        child = _find(controller, cid)
        if child is None:
            continue
        inner = _flatten_contents(controller, cid, depth + 1, seen) if child.get("receptacle") else []
        if child.get("pickupable"):
            label = child["objectType"]
            if inner:
                label += " (holding " + ", ".join(inner) + ")"
            items.append(label)
        else:
            items.extend(inner)
    return items


def list_contents(controller, object_id):
    """
    Names of what is currently on/in object_id, straight from metadata.
    A closed container is opened first (visibly) and closed again after
    - only if THIS call opened it.
    """
    obj = _find(controller, object_id)
    if obj is None:
        raise ManipulationError(f"Can't find {object_id} to look inside.")
    if not obj.get("receptacle"):
        raise ManipulationError(f"{obj.get('objectType', object_id)} isn't a receptacle.")

    we_opened_it = False
    if obj.get("openable") and not obj.get("isOpen"):
        open_object(controller, object_id)
        we_opened_it = True
        sync.settle(controller)                  # show the open door before answering
    else:
        face_object(controller, object_id)

    sync.refresh(controller)
    contents = _flatten_contents(controller, object_id)

    if we_opened_it:
        sync.settle(controller)
        close_object(controller, object_id)

    return contents


# ---------------------------------------------------------------------
# extra atomic actions (state verified)
# ---------------------------------------------------------------------

_STATE_CHECKS = {
    "SliceObject": ("isSliced", True),
    "BreakObject": ("isBroken", True),
    "CleanObject": ("isDirty", False),
    "DirtyObject": ("isDirty", True),
    "UseUpObject": ("isUsedUp", True),
    "EmptyLiquidFromObject": ("isFilledWithLiquid", False),
    "FillObjectWithLiquid": ("isFilledWithLiquid", True),
}


def _act_on_object(controller, action, object_id, description, **extra):
    """Face the object, run an AI2-THOR object-state action, verify the state flag."""
    face_object(controller, object_id)
    name = _type_of(controller, object_id)
    event = controller.step(action=action, objectId=object_id, forceAction=True, **extra)
    _check(event, description)
    sync.refresh(controller)
    key, expected = _STATE_CHECKS[action]
    obj = _find(controller, object_id)
    # A sliced object is replaced by its slices, so "gone" counts as sliced.
    if obj is None and action == "SliceObject":
        return controller.last_event
    if obj is not None and key in obj and bool(obj[key]) != expected:
        raise ManipulationError(
            f"{description}: the simulator state ({key}) did not change on the {name}."
        )
    return controller.last_event


def drop_object(controller):
    held = get_held_object_id(controller)
    if held is None:
        raise ManipulationError("I'm not holding anything to drop.")
    drop_held_object(controller)
    if get_held_object_id(controller) is not None:
        raise ManipulationError("I tried to drop it but it is still in my hand.")
    return controller.last_event


def slice_object(controller, object_id):
    held = get_held_object_id(controller)
    if held is None or not held.split("|")[0].lower().endswith("knife"):
        raise ManipulationError("I need to be holding a knife to slice that.")
    return _act_on_object(controller, "SliceObject", object_id, f"Slicing {object_id}")


def break_object(controller, object_id):
    return _act_on_object(controller, "BreakObject", object_id, f"Breaking {object_id}")


def clean_object(controller, object_id):
    return _act_on_object(controller, "CleanObject", object_id, f"Cleaning {object_id}")


def dirty_object(controller, object_id):
    return _act_on_object(controller, "DirtyObject", object_id, f"Making {object_id} dirty")


def use_up_object(controller, object_id):
    return _act_on_object(controller, "UseUpObject", object_id, f"Using up {object_id}")


def empty_object(controller, object_id):
    return _act_on_object(
        controller, "EmptyLiquidFromObject", object_id, f"Emptying {object_id}"
    )


def fill_object(controller, object_id, liquid="water"):
    if liquid not in config.VALID_LIQUIDS:
        raise ManipulationError(
            f"I can only fill things with {', '.join(config.VALID_LIQUIDS)}."
        )
    return _act_on_object(
        controller, "FillObjectWithLiquid", object_id,
        f"Filling {object_id} with {liquid}", fillLiquid=liquid,
    )


def look_at_object(controller, object_id):
    """Turn and tilt the camera toward the object without walking."""
    return frame_object(controller, object_id)
