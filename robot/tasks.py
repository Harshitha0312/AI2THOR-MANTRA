"""
High level, whole-scene tasks - "tidy the room", "put away the
groceries", "save energy" - as opposed to the single-object commands
in robot/manipulation.py and robot/navigation.py.

Adapted from three standalone scripts that each used to create and
stop their own ai2thor Controller. MANTRA keeps a single controller
alive for the whole voice/text session, so every function here just
acts on whatever controller it's handed (the same one main.py creates)
and never constructs or stops a controller itself.

Each public function returns a short, human-readable summary string
that executor.py appends to the spoken response - the same way every
other action reports what it did.
"""

import math
from collections import Counter
import random
import time

import config
from robot import control
from robot import hybrid_navigator
from robot import manipulation, metrics, sync
from robot.manipulation import drop_held_object, frame_object, get_held_object_id
from robot.preferences import DEFAULT_PREFERENCE, PREFERENCES, get_preferred_location


class TaskError(Exception):
    """
    Raised when a whole-scene task can't even get started (e.g. the
    scene has no reachable positions, or a task-critical object like
    the fridge doesn't exist here). Failures on individual items during
    a task are logged and skipped instead - the task keeps making
    progress on everything else rather than aborting entirely.
    """


class TaskCancelled(TaskError):
    """Raised when the user presses Stop (see robot/control.py)."""


def _say(message):
    """Live progress line - shows in the terminal and in the UI log."""
    control.say(message)


def _check_cancel(controller):
    """Called inside long loops. Puts down whatever is held, then aborts."""
    if control.is_cancelled():
        drop_held_object(controller)
        raise TaskCancelled("Stopped.")


# ---------------------------------------------------------------
# Small helpers shared by all three tasks
# ---------------------------------------------------------------

def _get_objects(controller):
    """
    Object list from the metadata of the most recent step. Every step already
    returns fresh metadata, so an extra "Pass" (a full simulator round trip)
    is only needed if nothing has been stepped yet. This alone removes about
    half of all simulator calls in the tidy loop and makes save_energy's
    per-target lookups free.
    """
    event = controller.last_event
    if event is None or not event.metadata.get("objects"):
        event = controller.step(action="Pass")
    return event.metadata["objects"]


def _get_reachable(controller):
    event = controller.step(action="GetReachablePositions")
    return event.metadata.get("actionReturn") or []


def _closest_position(target_pos, reachable_positions):
    return min(
        reachable_positions,
        key=lambda p: math.dist([p["x"], p["z"]], [target_pos["x"], target_pos["z"]]),
    )


def _teleport_to_object(controller, obj, reachable_positions):
    """
    Walks to the reachable tile closest to the object (via the TSP/A*
    hybrid navigator - real MoveAhead/Rotate actions along an
    obstacle-aware path, so this can't collide with or pass through
    anything on the way) AND turns to face it. Without the turn the
    agent often lands looking at a wall, the object drops out of view
    (objects only count as 'visible' when in the camera), and the task
    starts wandering randomly again. Name kept as-is (still called from
    the rest of this file) even though it no longer teleports.
    """
    return hybrid_navigator.walk_to_object(controller, obj, reachable_positions, horizon=30)


def _teleport_random(controller, reachable_positions):
    return hybrid_navigator.walk_random(controller, reachable_positions)


def _verified_pickup(controller, object_id):
    """
    Pickup through manipulation.pickup_object (real reach rules first,
    other standing spots on failure, state verified, closed parent opened)
    and then settled on screen. Returns (ok, error_text).
    """
    try:
        manipulation.pickup_object(controller, object_id)
    except manipulation.ManipulationError as exc:
        sync.settle(controller)
        return False, str(exc)
    sync.settle(controller)
    return True, None


def _move_smart(controller):
    action = random.choice(["MoveAhead", "MoveAhead", "MoveAhead", "RotateLeft", "RotateRight"])
    event = controller.step(action=action)
    if not event.metadata["lastActionSuccess"]:
        return controller.step(action=random.choice(["RotateLeft", "RotateRight"]))
    return event


# =================================================================
# Task 1: "tidy the room" / "clean up" / "organize"
# =================================================================

PICKUP_DIST = 1.2
PLACE_DIST = 1.35
RETRY_LIMIT = 5
MAX_TIDY_STEPS = 400
MAX_EMPTY_STEPS = 15
MAX_NO_DEST_STEPS = 10
MAX_APPROACH_ATTEMPTS = 3
MAX_PLACE_FAILS = 2
MAX_TIDY_SECONDS = 300
FALLBACK_RECEPTACLE_TYPES = ["CounterTop", "DiningTable", "SideTable", "Shelf", "TVStand"]


def _ancestor_types(obj, by_id, depth=3):
    """Types of everything the object sits on/in: its parents, their parents, ..."""
    types, frontier = set(), list(obj.get("parentReceptacles") or [])
    for _ in range(depth):
        nxt = []
        for pid in frontier:
            p = by_id.get(pid)
            if p is None:
                continue
            types.add(p["objectType"])
            nxt.extend(p.get("parentReceptacles") or [])
        frontier = nxt
    return types


def _is_already_placed(obj, objects, id_to_type=None):
    """
    True if the object is already somewhere acceptable and must NOT be
    picked up: on (or inside something on) its preferred receptacle type,
    or on a type whose preference score is within config.TIDY_ACCEPT_MARGIN
    of the best one present in this scene. Lifting such an object would
    only put it straight back where it was.
    """
    if not obj.get("parentReceptacles"):
        return False
    by_id = {o["objectId"]: o for o in objects}
    # Stored away inside a fridge / cabinet / drawer = already put away.
    if any((by_id.get(pid) or {}).get("openable") for pid in obj["parentReceptacles"]):
        return True
    where = _ancestor_types(obj, by_id)
    preferred = get_preferred_location(obj, objects)
    if preferred is None:
        return False
    if preferred in where:
        return True
    prefs = PREFERENCES.get(obj["objectType"], DEFAULT_PREFERENCE)
    best = prefs.get(preferred, 1.0)
    margin = getattr(config, "TIDY_ACCEPT_MARGIN", 0.2)
    return any(prefs.get(t, -1.0) >= best - margin for t in where)


def _ranked_destinations(obj, objects, exclude_ids=()):
    """
    Every receptacle this object could go on, best first: preferred types
    by score (nearest instance of each type first), then the generic flat
    surfaces. Never includes where it already is or anything in
    exclude_ids. Drives the 'switch to the next best receptacle' fallback.
    """
    by_id = {o["objectId"]: o for o in objects}
    here = set(obj.get("parentReceptacles") or [])
    skip = set(exclude_ids) | here | {obj["objectId"]}
    pos = obj["position"]

    def d(o):
        return math.hypot(o["position"]["x"] - pos["x"], o["position"]["z"] - pos["z"])

    prefs = PREFERENCES.get(obj["objectType"], DEFAULT_PREFERENCE)
    ranked, seen = [], set()

    def add(candidates):
        for o in sorted(candidates, key=d):
            if o["objectId"] not in skip and o["objectId"] not in seen and o.get("receptacle"):
                seen.add(o["objectId"])
                ranked.append(o)

    for rtype, _score in sorted(prefs.items(), key=lambda kv: kv[1], reverse=True):
        add([o for o in objects if o["objectType"] == rtype])
    for rtype in FALLBACK_RECEPTACLE_TYPES:
        add([o for o in objects if o["objectType"] == rtype])
    # a receptacle can't hold itself or something inside itself
    return [o for o in ranked if o["objectId"] != obj["objectId"]
            and obj["objectId"] not in (by_id[o["objectId"]].get("parentReceptacles") or [])]


def _find_pickup_target(objects, skip_ids):
    id_to_type = {o["objectId"]: o["objectType"] for o in objects if o.get("receptacle")}
    candidates = [
        o for o in objects
        if o.get("pickupable")
        and o.get("visible")
        and o["objectId"] not in skip_ids
        and not _is_already_placed(o, objects, id_to_type)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: o["distance"])


FALLBACK_TRY_LIMIT = 3  # alternate surfaces to try before truly giving up


def _find_fallback_candidates(objects, exclude_ids):
    """
    Common flat surfaces to try when the item's normal spot is unreachable
    or won't fit it, nearest first. Unlike _find_destination(), this does
    NOT require the surface to already be in camera view: AI2-THOR reports
    every object's position regardless of visibility, so a CounterTop in
    the next room is just as valid a target - we can walk straight to its
    known position. Requiring "visible" was the original bug behind items
    like the Book being dropped on the floor even when a perfectly good
    surface existed, just not one the camera happened to be facing.

    Returns a LIST, not just the nearest one: a countertop covered in a
    coffee maker and toaster can be too cluttered to fit anything no
    matter how many angles/offsets are tried, so the caller needs a
    second and third option to fall back to, not just one.
    """
    candidates = [
        o for o in objects
        if o["objectType"] in FALLBACK_RECEPTACLE_TYPES
        and o.get("receptacle")
        and o["objectId"] not in exclude_ids
    ]
    candidates.sort(key=lambda o: o["distance"])
    return candidates


def _find_destination(held_type, objects, exclude_ids=()):
    """
    Finds the nearest receptacle of held_type's preferred type from
    ground-truth scene metadata - no "visible" requirement. AI2-THOR
    reports every object's position and distance regardless of camera
    view (see _find_fallback_candidates above, which already relies on
    this same fact), so the agent doesn't need to already be looking at
    a receptacle to know where it is and walk straight there.
    """
    preferred = get_preferred_location({"objectType": held_type}, objects)
    if preferred is None:
        return None
    candidates = [
        o for o in objects
        if o["objectType"] == preferred and o.get("receptacle")
        and o["objectId"] not in exclude_ids
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: o["distance"])


def _restore_camera(controller, steps_taken):
    reverse = {"LookDown": "LookUp", "LookUp": "LookDown"}
    for action in reversed(steps_taken):
        controller.step(action=reverse[action])


def _placement_holds(controller, held_id, receptacle_id):
    """
    PutObject can report success the instant it's called, but a follow-up
    agent move (or, on a narrow/cluttered surface, physics settling over
    the next tick) can still knock the item onto the floor a moment later -
    which is exactly what happened to the Pot: the code reported "Placed"
    based on the immediate result, then a recovery move shoved it off the
    counter afterwards. One extra Pass lets physics settle, then we check
    the item is still actually inside/on the receptacle before believing it.
    """
    if held_id is None:
        return True   # nothing to verify against; don't block on it
    controller.step(action="Pass")
    obj = _find_object_by_id(controller, held_id)
    if obj is None:
        return False
    return receptacle_id in (obj.get("parentReceptacles") or [])


def _try_place_with_recovery(controller, dest, reachable_positions, budget=None):
    """
    Tries to put the held object on `dest`, at most `budget` PutObject
    attempts (config.TIDY_PLACE_BUDGET, default 4), then gives up so the
    caller can switch to the next best receptacle instead of wandering:

        1. as it stands (camera framed on the receptacle)
        2. camera tilted down
        3. 4. a different standing spot beside the receptacle (A* walk)

    Each attempt is verified (_placement_holds). Returns (event, success).
    """
    budget = budget or getattr(config, "TIDY_PLACE_BUDGET", 4)
    held_id = get_held_object_id(controller)
    dest_pos = dest["position"]
    offsets = [{"x": 0.5, "z": 0.0}, {"x": -0.5, "z": 0.0}, {"x": 0.0, "z": 0.5}, {"x": 0.0, "z": -0.5}]
    attempts = 0
    last = {"ev": controller.last_event}

    frame_object(controller, dest["objectId"])

    we_opened_it = False
    if dest.get("openable") and not dest.get("isOpen"):
        open_ev = controller.step(action="OpenObject", objectId=dest["objectId"], forceAction=True)
        we_opened_it = open_ev.metadata["lastActionSuccess"]
        if we_opened_it:
            sync.settle(controller)

    def put():
        nonlocal attempts
        attempts += 1
        ev = manipulation._put_once(controller, dest["objectId"], True)
        last["ev"] = ev
        return ev.metadata["lastActionSuccess"] and _placement_holds(controller, held_id, dest["objectId"])

    def finish(success):
        if we_opened_it:
            sync.settle(controller)
            controller.step(action="CloseObject", objectId=dest["objectId"], forceAction=True)
        return last["ev"], success

    if put():                                           # 1. as it stands
        return finish(True)

    if attempts < budget:                               # 2. look down at the surface
        controller.step(action="LookDown")
        ok = put()
        controller.step(action="LookUp")
        if ok:
            return finish(True)

    for offset in offsets:                              # 3./4. another standing spot
        if attempts >= budget or control.is_cancelled():
            break
        candidate = {"x": dest_pos["x"] + offset["x"], "y": dest_pos["y"], "z": dest_pos["z"] + offset["z"]}
        closest = _closest_position(candidate, reachable_positions)
        hybrid_navigator.walk_to_position(controller, closest, reachable_positions=reachable_positions)
        hybrid_navigator.face_position(controller, dest_pos, horizon=30)
        if put():
            return finish(True)

    return finish(False)


def tidy_room(controller):
    """
    1. Scan ground-truth metadata for pickupable objects that are NOT
       already somewhere acceptable (_is_already_placed). Those that are
       fine are left alone - nothing is lifted just to be put back.
    2. Give every misplaced object its best destination (nearest instance
       of its preferred receptacle type).
    3. Plan ONE route over the complete pick-and-deliver job:
       start -> pickup A -> deliver A -> pickup B -> deliver B ...
       (hybrid_navigator.plan_pick_and_place_tour: A* distances, TSP order
       that counts the delivery legs, compared against the old pickup-only
       order).
    4. Execute that exact order with real A* walking. Placement gets
       config.TIDY_PLACE_BUDGET (4) attempts per receptacle; after that
       the robot switches to the next best receptacle (up to
       config.TIDY_MAX_RECEPTACLES) rather than wandering around.
    5. Verify every pickup/placement from metadata, refresh the walkable
       set per object, and report metrics.
    """
    reachable_positions = _get_reachable(controller)
    if not reachable_positions:
        raise TaskError("I can't tidy up - this scene has no reachable positions.")

    drop_held_object(controller)

    # --- 1. scan ---------------------------------------------------------
    plan_started = time.monotonic()
    objects = _get_objects(controller)
    pickupables = [o for o in objects if o.get("pickupable")]
    misplaced = [o for o in pickupables if not _is_already_placed(o, objects)]
    skipped_in_place = len(pickupables) - len(misplaced)
    metrics.note("skipped_in_place", skipped_in_place)

    if not misplaced:
        metrics.set_units(0, 0)
        return (f"Everything was already tidy - {skipped_in_place} object(s) checked, "
                f"none needed moving.")

    # --- 2. destinations -------------------------------------------------
    items, no_home = [], []
    for o in misplaced:
        ranked = _ranked_destinations(o, objects)
        if ranked:
            items.append((o, ranked[0]))
        else:
            no_home.append(o["objectType"])
    if not items:
        metrics.set_units(0, len(misplaced))
        return "I found misplaced objects but no receptacle to put them on."

    # --- 3. one route over pickups AND deliveries ------------------------
    _say(f"Found {len(items)} misplaced object(s) ({skipped_in_place} already in place - leaving those). "
         f"Planning the shortest pick-and-deliver route...")
    tour = hybrid_navigator.plan_pick_and_place_tour(
        controller, items, reachable_positions=reachable_positions
    )
    metrics.add_plan_time(time.monotonic() - plan_started)
    metrics.note("route_planned_m", tour["total"])
    metrics.note("route_baseline_m", tour["baseline_total"])

    _say("Planned tidy-up sequence:")
    print(f"[tidy_room] planned route ~{tour['total']:.1f} m "
          f"(pickup-only order would be ~{tour['baseline_total']:.1f} m)")
    for n_, idx in enumerate(tour["order"], 1):
        o, dest = items[idx]
        line = f"  {n_}. {o['objectType']} -> {dest['objectType']}"
        print(f"[tidy_room]{line}")
        _say(line.strip())

    # --- 4. execute ------------------------------------------------------
    placed_count = failed_count = late_skipped = 0
    stop_reason = None
    started = time.monotonic()
    total = len(items)

    for n_, idx in enumerate(tour["order"], 1):
        if control.is_cancelled():
            stop_reason = "stopped by you"
            break
        if time.monotonic() - started > MAX_TIDY_SECONDS:
            stop_reason = "ran out of time"
            break

        obj, planned_dest = items[idx]
        oid, held_type = obj["objectId"], obj["objectType"]
        live = _get_objects(controller)
        current = next((o for o in live if o["objectId"] == oid), None)
        if current is None:
            failed_count += 1
            continue

        # State may have changed since planning: never lift what is already fine.
        if _is_already_placed(current, live):
            _say(f"({n_}/{total}) The {held_type} is already in a good place - leaving it.")
            late_skipped += 1
            total -= 1
            continue

        reachable_positions = _get_reachable(controller) or reachable_positions

        _say(f"({n_}/{total}) Walking to the {held_type}.")
        current, reached = hybrid_navigator.approach_object(
            controller, current, reachable_positions, interact_dist=PICKUP_DIST, horizon=30
        )
        if not reached:
            _say(f"Can't get close enough to the {held_type} - skipping it.")
            failed_count += 1
            continue

        ok, err = _verified_pickup(controller, oid)
        if not ok:
            _say(f"Could not pick up the {held_type} - skipping it. ({err})")
            failed_count += 1
            continue
        _say(f"Picked up the {held_type}.")

        # Planned destination first, then next best, up to TIDY_MAX_RECEPTACLES.
        live = _get_objects(controller)
        held_now = next((o for o in live if o["objectId"] == oid), obj)
        ranked = _ranked_destinations(held_now, live, exclude_ids=())
        ranked.sort(key=lambda o: 0 if o["objectId"] == planned_dest["objectId"] else 1)  # stable
        # (a stable sort keeps the preference order for everything else)
        candidates = ranked[: getattr(config, "TIDY_MAX_RECEPTACLES", 5)]

        placed = False
        for rank, dest in enumerate(candidates):
            if control.is_cancelled():
                break
            reachable_positions = _get_reachable(controller) or reachable_positions
            if rank:
                _say(f"Trying the next best place for the {held_type}: the {dest['objectType']}.")
            dest, reached = hybrid_navigator.approach_object(
                controller, dest, reachable_positions, interact_dist=PLACE_DIST, horizon=30
            )
            if not reached:
                _say(f"Can't reach the {dest['objectType']} - switching.")
                metrics.inc("receptacle_switches")
                continue

            ev, success = _try_place_with_recovery(controller, dest, reachable_positions)
            if success:
                sync.settle(controller)          # show the placed object before reporting it
                _say(f"Placed the {held_type} on/in the {dest['objectType']}.")
                placed_count += 1
                placed = True
                break
            _say(f"Couldn't place the {held_type} on the {dest['objectType']} "
                 f"after {getattr(config, 'TIDY_PLACE_BUDGET', 4)} tries - switching.")
            metrics.inc("receptacle_switches")

        if not placed:
            _say(f"Could not place the {held_type} anywhere suitable - setting it down here.")
            drop_held_object(controller)
            failed_count += 1

    sync.settle(controller)
    skipped_in_place += late_skipped
    metrics.note("skipped_in_place", skipped_in_place)
    metrics.set_units(placed_count, total + len(no_home))
    metrics.set_placed(placed_count, failed_count + len(no_home))
    summary = f"Tidied {placed_count} of {total} misplaced object(s)"
    if skipped_in_place:
        summary += f"; {skipped_in_place} already in place were left alone"
    if failed_count:
        summary += f"; {failed_count} could not be placed"
    if no_home:
        summary += f"; no receptacle found for {', '.join(no_home)}"
    summary += "."
    if stop_reason:
        summary += f" Stopped early: {stop_reason}."
    return summary


# =================================================================
# Task 2: "put away the groceries"
# =================================================================

GROCERY_CATEGORY = {
    "Egg": "top",
    "Milk": "top",
    "Bread": "middle",
    "Apple": "middle",
    "Tomato": "bottom",
    "Potato": "bottom",
    "Lettuce": "bottom",
    "IceCream": "freezer",
}
GROCERY_TYPES = list(GROCERY_CATEGORY.keys())
CATEGORY_LABELS = {
    "top": "egg/dairy shelf",
    "middle": "middle shelf",
    "bottom": "vegetable area",
    "freezer": "freezer",
}


def _find_object_by_type(controller, obj_type):
    return next((o for o in _get_objects(controller) if o["objectType"] == obj_type), None)


def _find_object_by_id(controller, object_id):
    return next((o for o in _get_objects(controller) if o["objectId"] == object_id), None)


def _walk_to(controller, target_obj, reachable_positions):
    event = hybrid_navigator.walk_to_object(controller, target_obj, reachable_positions, horizon=0)
    return event.metadata["lastActionSuccess"]


def _open_fridge(controller, fridge_id, reachable_positions, attempts=3):
    fridge_obj = _find_object_by_id(controller, fridge_id)
    if fridge_obj and fridge_obj.get("isOpen"):
        return True
    for _ in range(attempts):
        current = _find_object_by_id(controller, fridge_id) or fridge_obj
        _walk_to(controller, current, reachable_positions)
        event = controller.step(action="OpenObject", objectId=fridge_id, openness=1.0, forceAction=True)
        if event.metadata["lastActionSuccess"]:
            sync.settle(controller)
            return True
    return False


def _get_fridge_shelves(controller, fridge_id):
    fridge_obj = _find_object_by_id(controller, fridge_id)
    if not fridge_obj or not fridge_obj.get("receptacleObjectIds"):
        return []
    shelves = []
    for child_id in fridge_obj["receptacleObjectIds"]:
        child = _find_object_by_id(controller, child_id)
        if child and child["objectType"] == "Shelf":
            shelves.append(child)
    shelves.sort(key=lambda o: o["position"]["y"])
    return [s["objectId"] for s in shelves]


def _build_shelf_plan(shelf_ids, fridge_id):
    plan = {"bottom": fridge_id, "middle": fridge_id, "top": fridge_id}
    if len(shelf_ids) >= 3:
        plan["bottom"] = shelf_ids[0]
        plan["middle"] = shelf_ids[len(shelf_ids) // 2]
        plan["top"] = shelf_ids[-1]
    elif len(shelf_ids) == 2:
        plan["bottom"] = plan["middle"] = shelf_ids[0]
        plan["top"] = shelf_ids[1]
    elif len(shelf_ids) == 1:
        plan["bottom"] = plan["middle"] = plan["top"] = shelf_ids[0]
    return plan


def _grocery_base(object_type):
    """'BreadSliced' -> 'Bread', 'EggCracked' -> 'Egg': pieces of a grocery are groceries too."""
    for suffix in ("Sliced", "Cracked"):
        if object_type.endswith(suffix) and len(object_type) > len(suffix):
            return object_type[: -len(suffix)]
    return object_type


def _ancestors(obj, by_id, depth=5):
    """Every receptacle the object is on/in, directly or through other receptacles (a bowl on a counter ...)."""
    seen, frontier, out = set(), list(obj.get("parentReceptacles") or []), []
    while frontier and depth > 0:
        depth -= 1
        nxt = []
        for pid in frontier:
            if pid in seen or pid not in by_id:
                continue
            seen.add(pid)
            out.append(by_id[pid])
            nxt.extend(by_id[pid].get("parentReceptacles") or [])
        frontier = nxt
    return out


def _is_stored(obj, by_id):
    """Already put away: in the fridge / freezer, or inside any other openable (cabinet, drawer ...)."""
    return any(a["objectType"] in ("Fridge", "Freezer") or a.get("openable") for a in _ancestors(obj, by_id))


def _on_counter(obj, by_id):
    """True when the object sits on a CounterTop (directly, or on something that sits on one)."""
    return any(a["objectType"] == "CounterTop" for a in _ancestors(obj, by_id))


def _leave_on_counter_types():
    return {str(t) for t in getattr(config, "GROCERY_LEAVE_ON_COUNTER", ("Bread",))}


def _groceries_out(controller, left_alone=None):
    """
    Ground truth from the metadata: every grocery that is NOT put away yet (nothing to scan or guess).

    Groceries named in config.GROCERY_LEAVE_ON_COUNTER (Bread) that already sit on a countertop are
    IGNORED - they live there, so the robot never picks them up, never carries them anywhere and never
    puts them down again. They are appended to `left_alone` (if given) so the summary can mention them.
    """
    objects = _get_objects(controller)
    by_id = {o["objectId"]: o for o in objects}
    leave = _leave_on_counter_types()
    out = []
    for o in objects:
        base = _grocery_base(o["objectType"])
        if base not in GROCERY_CATEGORY or not o.get("pickupable") or _is_stored(o, by_id):
            continue
        if base in leave and _on_counter(o, by_id):
            if left_alone is not None:
                left_alone.append(o["objectType"])
            continue
        out.append(o)
    return out


def _return_to_counter(controller, obj_id, reachable_positions):
    """
    The grocery would not fit in the fridge: put it back on a COUNTERTOP (nearest first, a couple of
    tries) instead of dropping it on the floor. Returns the surface name, or None if none took it
    (the caller then falls back to setting it down).
    """
    objects = _get_objects(controller)
    counters = [o for o in objects if o["objectType"] == "CounterTop" and o.get("receptacle")]
    counters.sort(key=lambda o: o["distance"])
    for counter in counters[:3]:
        if control.is_cancelled():
            break
        spot, reached = hybrid_navigator.approach_object(
            controller, counter, reachable_positions, interact_dist=PLACE_DIST, horizon=30)
        if not reached:
            continue
        try:
            manipulation.put_object(controller, obj_id, spot["objectId"])
        except manipulation.ManipulationError:
            continue
        sync.settle(controller)
        return spot["objectType"]
    return None


def _in_fridge(controller, object_id):
    """True when the object is now in the fridge/freezer (directly or on one of its shelves)."""
    objects = _get_objects(controller)
    by_id = {o["objectId"]: o for o in objects}
    obj = by_id.get(object_id)
    return bool(obj) and any(a["objectType"] in ("Fridge", "Freezer") for a in _ancestors(obj, by_id))


# Ways to try one PutObject target: placeStationary, randomSeed (a different seed = a different
# spawn point on that shelf, which is what saves a bulky Bread that "has no room" at the first one).
_PUT_VARIANTS = ((True, None), (False, None), (True, 1), (True, 2), (False, 3))


def _place_in_fridge(controller, held_id, fridge_id, preferred, shelf_ids, reachable_positions):
    """
    Gets the held grocery INTO the fridge and verifies it from the metadata. Ladder:
    the planned shelf, every other shelf, then the fridge itself, each with several
    placement variants; finally the full verified manipulation.put_object recovery
    (camera tilt, other standing spots). Returns the target that worked, or None.
    The item is never "moved on" to a countertop: it stays in hand until it is in the fridge.
    """
    manipulation.get_close(controller, fridge_id)
    frame_object(controller, fridge_id)
    targets = [preferred] + [sid for sid in shelf_ids if sid != preferred] + [fridge_id]
    targets = list(dict.fromkeys(targets))
    for target in targets:
        for stationary, seed in _PUT_VARIANTS:
            _check_cancel(controller)
            kwargs = {"action": "PutObject", "objectId": target, "forceAction": True, "placeStationary": stationary}
            if seed is not None:
                kwargs["randomSeed"] = seed
            ev = controller.step(**kwargs)
            if not ev.metadata["lastActionSuccess"]:
                continue
            sync.refresh(controller)
            if get_held_object_id(controller) is None and _in_fridge(controller, held_id):
                return target
            if get_held_object_id(controller) is None:         # it went in, but not into the fridge: take it back
                ok, _err = _verified_pickup(controller, held_id)
                if not ok:
                    return None
    try:                                                          # last resort: the full recovery sequence
        manipulation.put_object(controller, held_id, fridge_id)
    except manipulation.ManipulationError:
        return None
    sync.refresh(controller)
    return fridge_id if _in_fridge(controller, held_id) else None


def _put_item_away(controller, obj, fridge_id, receptacle_map, shelf_ids, reachable_positions, log):
    """Picks one grocery up, opens the fridge, puts it in, closes the fridge. Returns True when it is in."""
    item_type = obj["objectType"]
    category = GROCERY_CATEGORY.get(_grocery_base(item_type), "middle")
    label = CATEGORY_LABELS[category]

    live = _find_object_by_id(controller, obj["objectId"])
    if live is None:
        log.append(f"{item_type}: it is gone.")
        return False
    if not _walk_to(controller, live, reachable_positions):
        log.append(f"{item_type}: could not reach it.")
        return False

    ok, _err = _verified_pickup(controller, obj["objectId"])
    if not ok:
        log.append(f"{item_type}: could not pick it up.")
        return False

    if not _open_fridge(controller, fridge_id, reachable_positions):
        log.append(f"{item_type}: could not open the refrigerator.")
        drop_held_object(controller)
        return False

    where = _place_in_fridge(controller, obj["objectId"], fridge_id, receptacle_map[category],
                             shelf_ids, reachable_positions)
    sync.settle(controller)
    if where is None:
        _say(f"The {item_type} would not fit in the fridge - putting it on a countertop instead.")
        manipulation.close_container(controller, fridge_id)
        parked = _return_to_counter(controller, obj["objectId"], _get_reachable(controller) or reachable_positions)
        if parked is None:                                  # no counter would take it: last resort only
            drop_held_object(controller)
        log.append(f"{item_type}: could not fit it in the fridge.")
        return False

    manipulation.close_container(controller, fridge_id)
    sync.settle(controller)
    log.append(f"{item_type}: put away in the {label if where != fridge_id else 'refrigerator'}.")
    return True


def put_away_groceries(controller, max_checkpoints=25, rotations=4):
    """
    Puts every grocery that is lying around into the fridge, then STOPS.

    The scene's metadata says exactly which groceries are out, so there is nothing to search for
    (max_checkpoints / rotations are kept only so old callers still work): the list is built once,
    ordered with the pickup -> fridge TSP, worked through item by item, and the task ends the moment
    the last one is in. Before, the robot kept sweeping 25 checkpoints x 4 rotations, looking at the
    whole house again and again after everything was already away.
    """
    reachable_positions = _get_reachable(controller)
    if not reachable_positions:
        raise TaskError("I can't put anything away - this scene has no reachable positions.")

    drop_held_object(controller)

    fridge = _find_object_by_type(controller, "Fridge")
    if fridge is None:
        raise TaskError("There's no fridge in this scene to put groceries away in.")
    fridge_id = fridge["objectId"]

    left_alone = []
    todo = _groceries_out(controller, left_alone)
    if not todo:
        metrics.set_units(0, 0)
        note = f" ({', '.join(left_alone)} on the countertop left alone)" if left_alone else ""
        return "I checked the whole house and found no groceries out" + note + "."

    shelf_ids = _get_fridge_shelves(controller, fridge_id)
    shelf_plan = _build_shelf_plan(shelf_ids, fridge_id)
    freezer_obj = _find_object_by_type(controller, "Freezer")
    receptacle_map = {
        "top": shelf_plan["top"],
        "middle": shelf_plan["middle"],
        "bottom": shelf_plan["bottom"],
        "freezer": freezer_obj["objectId"] if freezer_obj else shelf_plan["top"],
    }

    plan_started = time.monotonic()
    try:
        tour = hybrid_navigator.plan_pick_and_place_tour(
            controller, [(o, fridge) for o in todo], reachable_positions=reachable_positions)
        order = [todo[i] for i in tour["order"]]
    except Exception:                                   # planning must never stop the task
        pos = controller.last_event.metadata["agent"]["position"]
        order = sorted(todo, key=lambda o: math.hypot(o["position"]["x"] - pos["x"], o["position"]["z"] - pos["z"]))
    metrics.add_plan_time(time.monotonic() - plan_started)
    _say("Groceries to put away: " + ", ".join(o["objectType"] for o in order) + ".")

    log, done, failed = [], [], []
    for pass_no in (1, 2):                              # pass 2 = one more try for the ones that failed, then stop
        retry = []
        for obj in order:
            _check_cancel(controller)
            live = _find_object_by_id(controller, obj["objectId"])
            if live is None or _in_fridge(controller, obj["objectId"]):
                if live is not None and obj["objectId"] not in done:
                    done.append(obj["objectId"])
                continue
            reachable_positions = _get_reachable(controller) or reachable_positions
            if _put_item_away(controller, live, fridge_id, receptacle_map, shelf_ids, reachable_positions, log):
                done.append(obj["objectId"])
            else:
                retry.append(obj)
        order = retry
        if not order:
            break
    failed = [o["objectType"] for o in order]

    drop_held_object(controller)
    manipulation.close_container(controller, fridge_id)
    sync.settle(controller)
    metrics.set_units(len(done), len(todo))
    metrics.set_placed(len(done), len(todo) - len(done))

    names = [o["objectType"] for o in todo if o["objectId"] in done]
    summary = f"Put away {len(done)} of {len(todo)} grocery item(s)"
    if names:
        summary += ": " + ", ".join(names)
    summary += "."
    if failed:
        summary += f" Could not put away: {', '.join(failed)}."
    if left_alone:
        summary += f" Left alone on the countertop: {', '.join(left_alone)}."
    return summary


# =================================================================
# Task 3: "save energy" / "turn everything off"
# =================================================================

LIGHT_TYPES = {"LightSwitch", "FloorLamp", "DeskLamp"}
EXCLUDED_TOGGLE_TYPES = {"Book", "Fridge", "StoveKnob", "Kettle", "Drawer"}


def _energy_targets(controller, skip):
    targets = []
    for o in _get_objects(controller):
        if o["objectId"] in skip or o["objectType"] in EXCLUDED_TOGGLE_TYPES:
            continue
        if o.get("toggleable") and o.get("isToggled"):
            targets.append((o["objectId"], o["objectType"], "toggle"))
        if o.get("openable") and o.get("isOpen"):
            targets.append((o["objectId"], o["objectType"], "open"))
    return targets


def save_energy(controller, nav_candidates=6):
    reachable_positions = _get_reachable(controller)
    if not reachable_positions:
        raise TaskError("I can't do an energy sweep - this scene has no reachable positions.")

    drop_held_object(controller)

    def navigate_to_id(object_id):
        obj = _find_object_by_id(controller, object_id)
        if obj is None:
            return False
        candidates = sorted(
            reachable_positions,
            key=lambda p: math.hypot(p["x"] - obj["position"]["x"], p["z"] - obj["position"]["z"]),
        )[:nav_candidates]
        for cand in candidates:
            hybrid_navigator.walk_to_position(controller, cand, reachable_positions=reachable_positions)
            for _ in range(4):
                updated = _find_object_by_id(controller, object_id)
                if updated and updated.get("visible"):
                    return True
                controller.step(action="RotateRight")
        return False

    lights_off, appliances_off, closed, failed, skip = [], [], [], [], set()
    tries = {}

    while True:
        _check_cancel(controller)
        targets = _energy_targets(controller, skip)
        if not targets:
            break

        pos = controller.last_event.metadata["agent"]["position"]
        oid, otype, kind = min(
            targets,
            key=lambda t: math.hypot(
                pos["x"] - _find_object_by_id(controller, t[0])["position"]["x"],
                pos["z"] - _find_object_by_id(controller, t[0])["position"]["z"],
            ),
        )
        tries[oid] = tries.get(oid, 0) + 1
        if tries[oid] > 3:
            failed.append(otype)
            skip.add(oid)
            continue

        navigate_to_id(oid)

        action = "ToggleObjectOff" if kind == "toggle" else "CloseObject"
        event = controller.step(action=action, objectId=oid, forceAction=True)
        sync.settle(controller)                  # show the switch/door change

        if event.metadata["lastActionSuccess"]:
            if kind == "open":
                closed.append(otype)
            elif otype in LIGHT_TYPES:
                lights_off.append(otype)
            else:
                appliances_off.append(otype)
        else:
            failed.append(otype)
            skip.add(oid)

    if not (lights_off or appliances_off or closed):
        return "Everything was already off and closed - nothing to do."

    summary = (
        f"Energy sweep complete: {len(lights_off)} light(s) off, "
        f"{len(appliances_off)} appliance(s) off, {len(closed)} thing(s) closed."
    )
    if failed:
        summary += f" Could not turn off/close: {', '.join(failed)}."
    return summary


# =================================================================
# Task 4: "mess up the room" / "make a mess"
# =================================================================

MESS_ITEMS = int(getattr(config, "MESS_ITEMS", 6))
MESS_SPOT_TRIES = 3
MESSY_SPOT_TYPES = {
    "Sofa", "ArmChair", "Bed", "Chair", "CoffeeTable", "DiningTable",
    "SideTable", "Desk", "TVStand", "Dresser", "Ottoman", "Stool",
    "Bench", "Shelf", "CounterTop",
}


def _mess_limits():
    """(total seconds for the whole run, seconds allowed per object)."""
    return (float(getattr(config, "MESS_MAX_SECONDS", 300)),
            float(getattr(config, "MESS_ITEM_SECONDS", 80)))


def _is_free_to_move(obj, by_id):
    if not obj.get("pickupable") or obj.get("isBroken"):
        return False
    for pid in obj.get("parentReceptacles") or []:
        parent = by_id.get(pid)
        if parent and parent.get("openable") and not parent.get("isOpen"):
            return False
    return True


def _walk_within(controller, object_id, reachable_positions, max_dist):
    for _ in range(MAX_APPROACH_ATTEMPTS):
        obj = _find_object_by_id(controller, object_id)
        if obj is None:
            return None
        if obj["distance"] <= max_dist:
            return obj
        _teleport_to_object(controller, obj, reachable_positions)
    obj = _find_object_by_id(controller, object_id)
    return obj if obj and obj["distance"] <= max_dist else None


# How often each receptacle / receptacle TYPE has already been used as a mess
# destination in the current run - so the mess is spread over the whole room
# instead of piling everything onto one surface. Reset by mess_up_room().
_MESS_USED = (Counter(), Counter())


def _spot_usable(spot, by_id):
    """A surface we can actually put something on right now (not shut inside a closed container)."""
    if spot.get("openable") and not spot.get("isOpen"):
        return False
    for pid in spot.get("parentReceptacles") or []:
        parent = by_id.get(pid)
        if parent and parent.get("openable") and not parent.get("isOpen"):
            return False
    return True


def _wrong_spots(obj, objects, used=None):
    """
    Surfaces where `obj` does NOT belong but could plausibly be left, from the
    WHOLE room, in random order (Nlp26).

    Order of preference (earlier = tried first, ties broken at random):
      1. NOT "the very next receptacle": a surface closer than
         config.MESS_MIN_DISTANCE to where the object is now, or of the same
         kind as what it sits on (a Book on one CounterTop must not just slide
         to the CounterTop beside it), goes to the back;
      2. a receptacle nobody has been left on yet in this run;
      3. the least-used receptacle TYPE.
    `used` = (per-receptacle Counter, per-type Counter) for the current run.
    """
    preferred = get_preferred_location(obj, objects)
    parents = set(obj.get("parentReceptacles") or [])
    by_id = {o["objectId"]: o for o in objects}
    here_types = {a["objectType"] for a in _ancestors(obj, by_id)}
    pos = obj["position"]
    min_d = float(getattr(config, "MESS_MIN_DISTANCE", 1.5))
    spots = [
        o for o in objects
        if o["objectType"] in MESSY_SPOT_TYPES
        and o.get("receptacle")
        and not o.get("pickupable")
        and o["objectType"] != preferred
        and o["objectId"] not in parents
        and _spot_usable(o, by_id)
    ]
    random.shuffle(spots)
    ids, types = used if used is not None else (Counter(), Counter())

    def penalty(o):
        near = math.hypot(o["position"]["x"] - pos["x"], o["position"]["z"] - pos["z"]) < min_d
        return int(near) + int(o["objectType"] in here_types)

    spots.sort(key=lambda o: (penalty(o), ids[o["objectId"]] > 0, types[o["objectType"]]))  # stable: ties stay random
    return spots


def _drop_on_floor_somewhere(controller, held, reachable_positions):
    """
    Walks (A*) to a random free spot of the room - well away from where the
    robot is - and lets go of the object there: a real "left on the floor" mess.
    Never used for breakable things. Returns "Floor" or None.
    """
    agent = controller.last_event.metadata["agent"]["position"]
    far = [p for p in reachable_positions
           if math.hypot(p["x"] - agent["x"], p["z"] - agent["z"]) >= float(getattr(config, "MESS_MIN_DISTANCE", 1.5))]
    spot = random.choice(far or reachable_positions)
    hybrid_navigator.walk_to_position(controller, spot, reachable_positions=reachable_positions)
    if get_held_object_id(controller) is None:
        return None
    ev = controller.step(action="DropHandObject", forceAction=True)
    if not ev.metadata["lastActionSuccess"]:
        return None
    sync.settle(controller)
    return "Floor"


def _leave_somewhere_wrong(controller, held, spots, item_deadline):
    """
    Walks (A*, with the obstacle sidestep) to the first workable spot in
    `spots` and leaves the held object there. Tries up to MESS_SPOT_TRIES
    spots; if none works - or the per-object time is up - it sets the object
    down where the robot stands (never teleports). Returns where it went
    (surface type or "Floor"), or None if it could not even be put down.
    """
    tried = 0
    if (not held.get("breakable")
            and random.random() < float(getattr(config, "MESS_FLOOR_CHANCE", 0.15))):
        reach = _get_reachable(controller)
        if reach and _drop_on_floor_somewhere(controller, held, reach):
            return "Floor"
    for spot in spots:
        if tried >= MESS_SPOT_TRIES or control.is_cancelled() or time.monotonic() > item_deadline:
            break
        tried += 1
        reachable_positions = _get_reachable(controller)
        if not reachable_positions:
            break
        live = _find_object_by_id(controller, spot["objectId"]) or spot
        spot, reached = hybrid_navigator.approach_object(
            controller, live, reachable_positions, interact_dist=PLACE_DIST, horizon=30
        )
        if not reached:
            _say(f"Can't get to the {spot['objectType']} - trying another spot.")
            continue
        try:
            manipulation.put_object(controller, held["objectId"], spot["objectId"])
        except manipulation.ManipulationError:
            _say(f"Couldn't leave it on the {spot['objectType']} - trying another spot.")
            continue
        sync.settle(controller)                  # show it left on the spot
        _MESS_USED[0][spot["objectId"]] += 1
        _MESS_USED[1][spot["objectType"]] += 1
        return spot["objectType"]

    if get_held_object_id(controller) is None:
        return None
    if not held.get("breakable"):
        ev = controller.step(action="DropHandObject", forceAction=True)
        if ev.metadata["lastActionSuccess"]:
            sync.settle(controller)
            return "Floor"
    drop_held_object(controller)
    return None


MESS_REACH_CELLS = 4   # how many standing spots near an object to try before giving up on it


def _reach_for_pickup(controller, object_id, reachable_positions):
    """
    Walks toward object_id and returns its fresh metadata, trying a few
    different nearby standing spots (hybrid_navigator.approach_object).
    Always walks first - never "distance already small, skip walking" - a
    wall can sit between two spots that are close in a straight line.
    Returns None only if the object has vanished.
    """
    obj = _find_object_by_id(controller, object_id)
    if obj is None:
        return None
    current, _reached = hybrid_navigator.approach_object(
        controller, obj, reachable_positions, interact_dist=PICKUP_DIST,
        horizon=30, max_candidates=MESS_REACH_CELLS,
    )
    return current


def _nearest_movable(controller, tried):
    """
    Next object for the greedy top-up phase. config.MESS_SELECTION:
    "nearest" (default) or "random". Either way the robot WALKS there.
    """
    objects = _get_objects(controller)
    by_id = {o["objectId"]: o for o in objects}
    agent = controller.last_event.metadata["agent"]["position"]
    candidates = [
        o for o in objects
        if o["objectId"] not in tried and _is_free_to_move(o, by_id)
    ]
    if not candidates:
        return None
    if getattr(config, "MESS_SELECTION", "nearest") == "random":
        return random.choice(candidates)
    return min(
        candidates,
        key=lambda o: math.dist(
            [agent["x"], agent["z"]], [o["position"]["x"], o["position"]["z"]]
        ),
    )


def _plan_mess_tour(controller, count):
    """
    Picks `count` movable objects, gives each a nearby wrong-but-plausible
    destination and orders the whole job with the same TSP (pickup ->
    delivery legs, A* distances) tidy_room uses. Returns
    (ordered [(obj, spots)], tour_or_None).
    """
    objects = _get_objects(controller)
    by_id = {o["objectId"]: o for o in objects}
    agent = controller.last_event.metadata["agent"]["position"]
    pool = [o for o in objects if _is_free_to_move(o, by_id)]
    if getattr(config, "MESS_SELECTION", "nearest") == "random":
        random.shuffle(pool)
    else:
        pool.sort(key=lambda o: math.hypot(o["position"]["x"] - agent["x"],
                                           o["position"]["z"] - agent["z"]))
    chosen = pool[:count]
    planned = (Counter(), Counter())        # destinations handed out so far in THIS plan
    items = []
    for o in chosen:
        spots = _wrong_spots(o, objects, planned)
        if spots:
            planned[0][spots[0]["objectId"]] += 1
            planned[1][spots[0]["objectType"]] += 1
        items.append((o, spots))
    if not items:
        return [], None
    tour = hybrid_navigator.plan_pick_and_place_tour(
        controller, [(o, spots[0] if spots else None) for o, spots in items],
        reachable_positions=_get_reachable(controller),
    )
    return [items[i] for i in tour["order"]], tour


def _mess_one(controller, obj, spots, item_deadline):
    """
    Moves ONE object. Returns (where, None) on success, (None, reason) otherwise.
    A failure here never stops the run - the caller just carries on with the next object.
    """
    name, oid = obj["objectType"], obj["objectId"]
    reachable_positions = _get_reachable(controller)
    if not reachable_positions:
        return None, "no walkable space"
    target = _reach_for_pickup(controller, oid, reachable_positions)
    if target is None:
        return None, "disappeared"
    ok, _err = _verified_pickup(controller, oid)
    if not ok:
        return None, "couldn't pick it up"
    objects = _get_objects(controller)
    if not spots:
        spots = _wrong_spots(target, objects, _MESS_USED)
    fresh = [next((o for o in objects if o["objectId"] == s["objectId"]), s) for s in spots]
    where = _leave_somewhere_wrong(controller, target, fresh, item_deadline)
    if where is None:
        return None, "nowhere to leave it"
    return where, None


def mess_up_room(controller, count=None):
    """
    Moves several objects somewhere they don't belong.

    1. Choose `count` movable objects (random by default - config.MESS_SELECTION) and a
       random wrong surface for each from the WHOLE room, spread over different
       surfaces (see _wrong_spots) - not just the closest one.
    2. Order the job with the same TSP as tidy_room (pickup -> delivery legs).
    3. Walk it with A*. If the way is blocked the robot sidesteps and re-plans
       (hybrid_navigator._walk_path) instead of standing there holding the object.
    4. One object failing (can't reach / can't pick up / nowhere to put it) never
       ends the run - it moves on, and a greedy top-up pass fills in with other
       objects until `count` have really been moved or nothing is left.
    5. Time is limited per object and per run (config.MESS_ITEM_SECONDS /
       MESS_MAX_SECONDS), not one tiny budget for everything.
    """
    count = MESS_ITEMS if count is None else count
    if not _get_reachable(controller):
        raise TaskError("I can't mess anything up - this scene has no reachable positions.")

    drop_held_object(controller)
    total_limit, item_limit = _mess_limits()
    _MESS_USED[0].clear()
    _MESS_USED[1].clear()

    plan_started = time.monotonic()
    ordered, tour = _plan_mess_tour(controller, count)
    metrics.add_plan_time(time.monotonic() - plan_started)
    if tour:
        metrics.note("route_planned_m", tour["total"])
        metrics.note("route_baseline_m", tour["baseline_total"])
        _say("Planned mess-up sequence: " + " -> ".join(o["objectType"] for o, _ in ordered))

    moved, skipped, tried = [], [], set()
    stop_reason = None
    started = time.monotonic()

    def timed_out():
        return time.monotonic() - started > total_limit

    def run_item(obj, spots):
        tried.add(obj["objectId"])
        name = obj["objectType"]
        _say(f"({len(moved) + 1}/{count}) Going for the {name}.")
        where, why = _mess_one(controller, obj, spots, time.monotonic() + item_limit)
        if where:
            _say(f"Left the {name} on the {where}.")
            moved.append(f"{name} -> {where}")
        else:
            _say(f"Skipping the {name} ({why}) - moving on to the next object.")
            skipped.append(name)

    # Phase 1: the planned tour.
    for obj, spots in ordered:
        if control.is_cancelled():
            stop_reason = "stopped by you"
            break
        if timed_out():
            stop_reason = "ran out of time"
            break
        live = _find_object_by_id(controller, obj["objectId"])
        if live is None or not _is_free_to_move(live, {o["objectId"]: o for o in _get_objects(controller)}):
            skipped.append(obj["objectType"])
            tried.add(obj["objectId"])
            continue
        run_item(live, spots)

    # Phase 2: top up with other objects (greedy nearest) if some failed.
    while not stop_reason and len(moved) < count:
        if control.is_cancelled():
            stop_reason = "stopped by you"
            break
        if timed_out():
            stop_reason = "ran out of time"
            break
        item = _nearest_movable(controller, tried)
        if item is None:
            break
        run_item(item, None)

    drop_held_object(controller)
    sync.settle(controller)
    metrics.set_units(len(moved), len(moved) + len(skipped))
    metrics.set_placed(len(moved), len(skipped))

    if not moved:
        summary = "I tried to mess up the room but couldn't move anything."
    else:
        summary = f"Messed up the room: moved {len(moved)} object(s) ({'; '.join(moved)})."
    if skipped:
        summary += f" Skipped {len(skipped)} I couldn't move ({', '.join(skipped)})."
    if stop_reason:
        summary += f" Stopped early: {stop_reason}."
    return summary
