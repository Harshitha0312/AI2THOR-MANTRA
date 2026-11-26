"""
Expands the structured NLP steps (pickup/place/open/close/find/go_to/
toggle_on/toggle_off/run_task) into an ordered list of low-level
actions the executor can run one at a time, e.g.:

    {"action": "place", "object": "apple", "target": "fridge"}

becomes:

    [("find", "apple"), ("go_to", "apple"), ("pickup", "apple"),
     ("find", "fridge"), ("go_to", "fridge"), ("open", "fridge"),
     ("put", "apple", "fridge"), ("close", "fridge")]

Receptacles that need opening (fridge, cabinet, drawer, microwave, ...)
are opened automatically before the object is placed and closed again
afterwards; this list is only a heuristic and can be extended.

"run_task" steps (whole-scene tasks like tidy/putaway/energy) pass
straight through as a single low-level action - they don't need a
find/go_to first because they navigate the entire house themselves
inside robot/tasks.py.
"""

OPENABLE_RECEPTACLES = {
    "fridge",
    "refrigerator",
    "cabinet",
    "drawer",
    "microwave",
    "oven",
    "safe",
    "box",
}


_HEAT_FAMILY = {"stove", "stoveburner", "stoveknob", "burner", "cooktop"}


def _same_appliance(a, b):
    """True if two names (e.g. 'StoveKnob' / 'stove burner') mean the same appliance."""
    a = (a or "").lower().replace(" ", "")
    b = (b or "").lower().replace(" ", "")
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return a in _HEAT_FAMILY and b in _HEAT_FAMILY


def fix_ordering(steps):
    """
    Physical-prerequisite safety net applied to the LLM's plan. An
    appliance must not be switched ON before everything that is meant to
    be heated on it is in place: the container goes on the appliance
    ("place pan on stove") AND whatever goes into that container ("place
    egg in pan") - transitively. If the LLM ordered it wrong, the toggle
    is moved to just after the last such place step. Pure reordering -
    nothing is added or removed.
    """
    steps = list(steps)

    def heated_names(appliance):
        names = {appliance}
        changed = True
        while changed:                         # fixpoint: stove <- pan <- egg ...
            changed = False
            for st in steps:
                if st.get("action") != "place":
                    continue
                tgt, obj = st.get("target"), st.get("object")
                if obj and tgt and obj not in names and any(_same_appliance(tgt, n) for n in names):
                    names.add(obj)
                    changed = True
        return names

    i = 0
    guard = 0
    while i < len(steps) and guard < 100:
        guard += 1
        st = steps[i]
        if st.get("action") == "toggle_on":
            names = heated_names(st.get("object"))
            later = [
                j for j in range(i + 1, len(steps))
                if steps[j].get("action") == "place"
                and any(_same_appliance(steps[j].get("target"), n) for n in names)
            ]
            if later:
                steps.insert(later[-1] + 1, steps.pop(i))
                continue          # re-check whatever slid into position i
        i += 1
    return steps


def _later_target(steps, index, obj):
    """Target of the next 'place <obj> ...' after steps[index], if there is one."""
    key = (obj or "").lower().strip()
    for st in steps[index + 1:]:
        if st.get("action") == "place" and (st.get("object") or "").lower().strip() == key:
            return st.get("target")
    return None


def build_plan(command_steps, held=None):
    """
    `held` is the (lower-case type) name of whatever is ALREADY in the
    robot's hand when the command starts, so "put it on the table" after
    "pick up the apple" doesn't walk back to the apple to pick it up again.
    """
    command_steps = fix_ordering(command_steps)
    plan = []
    held = held.lower().strip() if held else None

    def norm(name):
        return name.lower().strip() if name else None

    def amount(step):
        return int(step.get("amount") or 1)

    for step_index, step in enumerate(command_steps):
        action = step["action"]
        obj = step.get("object")
        target = step.get("target")

        if action == "list_contents":
            # "what's in the fridge" / "what's on the table": walk there,
            # look (manipulation.list_contents opens/closes it itself if
            # it's a closed receptacle), report back - no pickup involved.
            plan.append(("find", target))
            plan.append(("go_to", target))
            plan.append(("list_contents", target))

        elif action == "find":
            plan.append(("find", obj))

        elif action == "go_to":
            plan.append(("find", obj))
            plan.append(("go_to", obj))

        elif action == "pickup":
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("pickup", obj))
            held = norm(obj)

        elif action == "open":
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("open", obj))

        elif action == "close":
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("close", obj))

        elif action == "toggle_on":
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("toggle_on", obj))

        elif action == "toggle_off":
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("toggle_off", obj))

        elif action == "break":
            # Cooking pattern: the egg goes INTO the pan first and is
            # cracked there. Cracking it in the hand made the real simulator
            # drop the cracked egg on the floor. place -> then break in place.
            dest = _later_target(command_steps, step_index, obj)
            if dest:
                plan.append(("fetch", obj, dest))
                plan.append(("find", dest))
                plan.append(("go_to", dest))
                if dest.lower().strip() in OPENABLE_RECEPTACLES:
                    plan.append(("open", dest))
                    plan.append(("put", obj, dest))
                    plan.append(("close", dest))
                else:
                    plan.append(("put", obj, dest))
                held = None
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("break", obj))
            if norm(obj) == held:
                held = None

        elif action in ("clean", "dirty", "empty", "use_up"):
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append((action, obj))

        elif action == "fill":
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("fill", obj, (step.get("liquid") or "water").lower().strip()))

        elif action == "slice":
            # Slicing needs a knife in hand: fetch one first unless the
            # robot is already holding the requested tool.
            tool = step.get("tool") or "knife"
            if norm(tool) != held:
                plan.append(("find", tool))
                plan.append(("go_to", tool))
                plan.append(("pickup", tool))
                held = norm(tool)
            plan.append(("find", obj))
            plan.append(("go_to", obj))
            plan.append(("slice", obj))

        elif action == "look_at":
            plan.append(("find", obj))
            plan.append(("look_at", obj))

        elif action == "drop":
            plan.append(("drop",))
            held = None

        elif action in ("look_up", "look_down"):
            plan.append(("look", action.split("_")[1], amount(step)))

        elif action in ("turn_left", "turn_right"):
            plan.append(("turn", action.split("_")[1], amount(step)))

        elif action == "turn_around":
            plan.append(("turn", "left", 2 * amount(step)))

        elif action in ("move_forward", "move_back", "move_left", "move_right"):
            plan.append(("move", action.split("_")[1], amount(step)))

        elif action in ("crouch", "stand"):
            plan.append(("posture", action))

        elif action == "run_task":
            plan.append(("run_task", step.get("task")))

        elif action == "place":
            # Fetching is decided at RUN time (executor "fetch" step): whether
            # the thing is already in hand, already in the target, or has
            # turned into something else (broke an egg -> cracked egg) is only
            # known from the live simulator state, not from this static plan.
            plan.append(("fetch", obj, target))
            if target:
                plan.append(("find", target))
                plan.append(("go_to", target))

                if target.lower().strip() in OPENABLE_RECEPTACLES:
                    plan.append(("open", target))
                    plan.append(("put", obj, target))
                    plan.append(("close", target))
                else:
                    plan.append(("put", obj, target))

            held = None

    return plan


# =====================================================================
# Prepared-food rules (sandwich, salad ...), enforced in CODE (Nlp26)
# =====================================================================
# The LLM prompt already asks for "slice first, only sliced pieces on the
# plate, one plate on one countertop". A prompt is a request, not a
# guarantee, so the same three rules are checked here against the live
# scene before anything moves:
#
#   1. an ingredient that goes onto a Plate/Bowl is SLICED first (a slice
#      step is inserted if the plan forgot it) and the robot then picks up a
#      sliced piece - never the whole Bread / Lettuce / Tomato;
#   2. if sliced pieces of it already exist in the scene, no slice step is
#      needed (a redundant one is removed) and those pieces are used;
#   3. the Plate and everything on it share ONE countertop: one plate is
#      pinned for the whole plan, and if it is not on a countertop yet it is
#      put on one first (the countertop that is closest to the ingredients).

DISH_TYPES = {"plate", "bowl"}
_NAME_SYNONYMS = {"counter": "countertop", "worktop": "countertop", "countertops": "countertop"}


def canon_name(name):
    """'Counter Top' -> 'countertop', 'Plate' -> 'plate' (how names are compared everywhere)."""
    key = (name or "").lower().replace(" ", "").strip()
    return _NAME_SYNONYMS.get(key, key)


def _ancestor_objects(obj, by_id, depth=6):
    out, seen, frontier = [], set(), list(obj.get("parentReceptacles") or [])
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


def _dist2(a, b):
    return (a["x"] - b["x"]) ** 2 + (a["z"] - b["z"]) ** 2


def enforce_dish_rules(steps, objects, agent_pos=None):
    """
    Applies the prepared-food rules above to the structured steps.
    Returns (steps, info): info = {"items": {canonical ingredient names},
    "pins": {canonical name: objectId}} - handed to the executor so it
    resolves 'plate', 'countertop' and the sliced pieces consistently.
    A plan with no dish in it comes back unchanged with empty info.
    """
    steps = [dict(s) for s in steps]
    info = {"items": set(), "pins": {}}
    by_id = {o["objectId"]: o for o in objects}

    dish_steps = [s for s in steps if s.get("action") == "place"
                  and canon_name(s.get("target")) in DISH_TYPES and s.get("object")]
    if not dish_steps:
        return steps, info

    sliceable_types = {o["objectType"].lower() for o in objects if o.get("sliceable") and not o.get("isSliced")}
    piece_types = {o["objectType"].lower() for o in objects if o["objectType"].lower().endswith("sliced")}

    # ---- 1./2. ingredients: only sliced pieces go on the dish -----------
    for ing in dict.fromkeys(canon_name(s["object"]) for s in dish_steps):
        if ing in DISH_TYPES:
            continue
        has_pieces = (ing + "sliced") in piece_types
        if has_pieces:
            steps = [s for s in steps
                     if not (s.get("action") == "slice" and canon_name(s.get("object")) == ing)]
            info["items"].add(ing)
        elif ing in sliceable_types:
            first = next((i for i, s in enumerate(steps)
                          if s.get("action") in ("pickup", "place") and canon_name(s.get("object")) == ing), None)
            has_slice = any(s.get("action") == "slice" and canon_name(s.get("object")) == ing
                            for s in steps[: (first if first is not None else len(steps))])
            if first is not None and not has_slice:
                display = next(s["object"] for s in steps if canon_name(s.get("object")) == ing)
                steps.insert(first, {"action": "slice", "object": display, "tool": "Knife"})
            info["items"].add(ing)

    # ---- 3. one plate, on one countertop ---------------------------------
    plate_name = canon_name(dish_steps[0]["target"])
    plates = [o for o in objects if o["objectType"].lower() == plate_name]
    counters = [o for o in objects if o["objectType"] == "CounterTop" and o.get("receptacle")]
    if plates:
        on_counter = next((p for p in plates
                           if any(a["objectType"] == "CounterTop" for a in _ancestor_objects(p, by_id))), None)
        plate = on_counter or (min(plates, key=lambda o: _dist2(o["position"], agent_pos)) if agent_pos else plates[0])
        info["pins"][plate_name] = plate["objectId"]
        if on_counter is not None:
            home = next(a for a in _ancestor_objects(on_counter, by_id) if a["objectType"] == "CounterTop")
            info["pins"]["countertop"] = home["objectId"]
        elif counters:
            ingredient_pos = [o["position"] for o in objects
                              if canon_name(o["objectType"]) in info["items"]
                              or canon_name(o["objectType"]).endswith("sliced") and canon_name(o["objectType"])[:-6] in info["items"]]
            anchor = ingredient_pos or [plate["position"]]
            best = min(counters, key=lambda c: sum(_dist2(c["position"], p) for p in anchor))
            info["pins"]["countertop"] = best["objectId"]
            plate_on_counter_step = any(
                s.get("action") == "place" and canon_name(s.get("object")) == plate_name
                and canon_name(s.get("target")) == "countertop" for s in steps)
            if not plate_on_counter_step:
                display = dish_steps[0]["target"]
                steps.insert(0, {"action": "place", "object": display, "target": "CounterTop"})
        # never carry the plate to a second place: keep only the first plate->countertop step
        seen_plate_move = False
        deduped = []
        for s in steps:
            if (s.get("action") == "place" and canon_name(s.get("object")) == plate_name
                    and canon_name(s.get("target")) == "countertop"):
                if seen_plate_move:
                    continue
                seen_plate_move = True
            deduped.append(s)
        steps = deduped
    return steps, info


FLOOR_NAMES = {"floor", "ground"}


def floor_to_drop(steps):
    """
    The Floor is not an object in AI2-THOR, so "put the pillow on the floor" can never be a
    place-on-Floor step (it used to end in "Floor object does not exist"). Whatever produced the
    steps (LLM or rule parser), such a step becomes: pickup the object, then drop it.
    """
    out = []
    for st in steps:
        if st.get("action") == "place" and canon_name(st.get("target")) in FLOOR_NAMES and st.get("object"):
            out.append({"action": "pickup", "object": st["object"]})
            out.append({"action": "drop"})
        else:
            out.append(st)
    return out
