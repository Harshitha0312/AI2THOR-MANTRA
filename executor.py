"""
Executes the low-level plan produced by planner/task_planner.py, one
ATOMIC step at a time (Nlp13).

Every step goes through execute_atomic_step(), the single place that
enforces the execution contract:

    perform the action
      -> AI2-THOR returns an event
      -> the action itself verifies the expected state change in ground-
         truth metadata (robot/manipulation.py) - failure raises
      -> sync.settle(): step the simulator a few ticks + dwell so the new
         state is really ON SCREEN
      -> only now is the step reported ("Picked up the apple.")
      -> only then does the next step start

Nlp13: steps that depend on the CURRENT world (fetching what a "place"
needs, toggling the burner that actually has the pan on it, following an
object that broke/sliced into something else) are decided here at run
time from live metadata, not frozen in the plan.
"""

import time
from collections import Counter

from environment import scene_manager
from planner.task_planner import DISH_TYPES, canon_name, enforce_dish_rules, floor_to_drop
from robot import control, manipulation, metrics, navigation, object_detection, sync, tasks


class ExecutionError(Exception):
    pass


# Steps that only read metadata and change nothing on screen.
_NO_RENDER = {"find"}


class Context:
    """
    Per-plan state: grounded names -> live objectIds (+ tracking of transformed objects).

    Nlp26: for a dish (sandwich, salad) the plan also carries
      pins        {'plate': id, 'countertop': id} - ONE plate and ONE countertop
                  for the whole plan, however a step happens to name them;
      dish_items  ingredient names that must be used SLICED: resolving one
                  returns a sliced piece that is not on the plate yet, never
                  the whole item;
      dish_log    (item id, plate id) for every item put on the plate, so the
                  result can be checked and repaired at the end.
    """

    def __init__(self, controller, dish=None):
        self.c = controller
        self.ids = {}
        dish = dish or {}
        self.pins = dict(dish.get("pins") or {})
        self.dish_items = set(dish.get("items") or ())
        self.dish_log = []

    def _live(self, object_id):
        return next((o for o in self.c.last_event.metadata["objects"] if o["objectId"] == object_id), None)

    def _pick_slice(self, canon):
        """A sliced piece of `canon` that is not on a plate/bowl yet (the one in hand counts). None if no pieces exist."""
        objects = self.c.last_event.metadata["objects"]
        pieces = [o for o in objects if o["objectType"].lower() == canon + "sliced"]
        if not pieces:
            return None
        held = manipulation.get_held_object_id(self.c)
        if held and any(o["objectId"] == held for o in pieces):
            return held
        by_id = {o["objectId"]: o for o in objects}
        dish_ids = [o["objectId"] for o in objects if o["objectType"].lower() in DISH_TYPES]
        free = [o for o in pieces if not any(_in_target(o, d, by_id) for d in dish_ids)]
        if not free:
            raise ExecutionError(f"There are no more sliced {canon} pieces left to use.")
        agent = self.c.last_event.metadata["agent"]["position"]
        free.sort(key=lambda o: abs(o["position"]["x"] - agent["x"]) + abs(o["position"]["z"] - agent["z"]))
        return free[0]["objectId"]

    def resolve(self, name, prefer=None):
        canon = canon_name(name)

        pin = self.pins.get(canon)
        if pin:
            obj = self._live(pin)
            flag = scene_manager._PREFER_KEYS.get(prefer)
            if obj is not None and (not flag or obj.get(flag)):
                return pin

        if canon in self.dish_items and prefer != "receptacle":
            piece = self._pick_slice(canon)
            if piece:
                return piece

        key = (name.lower().strip(), prefer)
        cached = self.ids.get(key)
        # Objects can be replaced mid-plan (a broken Egg becomes a cracked one),
        # so a cached id is only reused while it still exists.
        if cached is not None and self._live(cached) is not None:
            return cached
        try:
            obj = object_detection.find_object(self.c, name, prefer=prefer)
        except object_detection.ObjectNotFoundError as exc:
            raise ExecutionError(str(exc)) from exc
        self.ids[key] = obj["objectId"]
        return self.ids[key]

    def resolve_whole(self, name):
        """The un-sliced object itself (what a 'slice' step cuts), ignoring any sliced pieces."""
        canon = canon_name(name)
        objects = self.c.last_event.metadata["objects"]
        agent = self.c.last_event.metadata["agent"]["position"]
        whole = [o for o in objects if o["objectType"].lower() == canon and o.get("sliceable") and not o.get("isSliced")]
        if whole:
            whole.sort(key=lambda o: abs(o["position"]["x"] - agent["x"]) + abs(o["position"]["z"] - agent["z"]))
            return whole[0]["objectId"]
        return self.resolve(name)

    def forget(self, name):
        n = name.lower().strip()
        for key in [k for k in self.ids if k[0] == n]:
            del self.ids[key]


def _summarise_contents(contents):
    counts = Counter(contents)
    return ", ".join(f"{name} x{n}" if n > 1 else name for name, n in sorted(counts.items()))


def _norm(text):
    return (text or "").lower().replace(" ", "")


def _held_matches(controller, name):
    held = manipulation.get_held_object_id(controller)
    if held is None:
        return False
    held_type, want = _norm(held.split("|")[0]), _norm(name)
    for m in scene_manager._MODIFIERS:
        want = want.replace(m, "")
    return bool(want) and (held_type == want or want in held_type or held_type in want)


def _in_target(obj, target_id, by_id):
    """True if obj is on/in target_id directly or inside something that is."""
    frontier, seen = list(obj.get("parentReceptacles") or []), set()
    while frontier:
        pid = frontier.pop()
        if pid == target_id:
            return True
        if pid in seen:
            continue
        seen.add(pid)
        parent = by_id.get(pid)
        if parent:
            frontier.extend(parent.get("parentReceptacles") or [])
    return False


def _toggle_target(ctx, name, on):
    """
    "stove" / "burner" / "knob": switch the knob of the burner that
    actually has something on it (turning on) or the knob that is ON
    (turning off) - not whichever knob happens to be nearest.
    """
    c = ctx.c
    object_id = ctx.resolve(name, prefer="toggleable")
    obj = ctx._live(object_id)
    if obj is None or not any(w in obj["objectType"].lower() for w in ("stove", "burner", "knob")):
        return object_id
    objects = c.last_event.metadata["objects"]
    by_id = {o["objectId"]: o for o in objects}
    agent = c.last_event.metadata["agent"]["position"]

    def dist(o):
        return abs(o["position"]["x"] - agent["x"]) + abs(o["position"]["z"] - agent["z"])

    if on:
        loaded = [o for o in objects if o["objectType"] == "StoveBurner" and o.get("receptacleObjectIds")]
        for burner in sorted(loaded, key=dist):
            knobs = [k for k in objects if k.get("toggleable") and burner["objectId"] in (k.get("controlledObjects") or [])]
            if knobs:
                return knobs[0]["objectId"]
    else:
        lit = [k for k in objects if k.get("toggleable") and k.get("isToggled")
               and any("stove" in by_id[x]["objectType"].lower() for x in (k.get("controlledObjects") or []) if x in by_id)]
        if lit:
            return sorted(lit, key=dist)[0]["objectId"]
    return object_id


def _after_transform(ctx, name, before_ids, original_type):
    """After break/slice the object is replaced: point `name` at the new object(s)."""
    objects = ctx.c.last_event.metadata["objects"]
    fresh = [o for o in objects if o["objectId"] not in before_ids]
    base = original_type.lower()
    best = [o for o in fresh if o["objectType"].lower().startswith(base) or base in o["objectType"].lower()] or fresh
    if best:
        agent = ctx.c.last_event.metadata["agent"]["position"]
        best.sort(key=lambda o: abs(o["position"]["x"] - agent["x"]) + abs(o["position"]["z"] - agent["z"]))
        ctx.forget(name)
        ctx.ids[(name.lower().strip(), None)] = best[0]["objectId"]
        ctx.ids[(name.lower().strip(), "pickupable")] = best[0]["objectId"]


def execute_atomic_step(ctx, step):
    """
    Runs ONE low-level step and returns its human-readable message ('' for
    silent bookkeeping steps). Raises ExecutionError (speakable) on any
    failure; the simulator is always settled first so the failure state is
    visible too.
    """
    controller = ctx.c
    action = step[0]
    placing = action in ("put", "drop")
    try:
        message = _perform(ctx, step)
    except (navigation.NavigationError, manipulation.ManipulationError, tasks.TaskError) as exc:
        sync.settle(controller)
        if placing:
            metrics.count_placed(failed=1)
        raise ExecutionError(str(exc)) from exc
    except ExecutionError:
        sync.settle(controller)
        if placing:
            metrics.count_placed(failed=1)
        raise
    if placing:
        metrics.count_placed(ok=1)

    if action not in _NO_RENDER:
        sync.settle(controller)          # <- the render/commit point
    if message:
        control.say(message)             # <- only now is it reported
    return message


def _perform(ctx, step):
    controller = ctx.c
    resolve = ctx.resolve
    action = step[0]

    if action == "find":
        resolve(step[1])
        return f"Found {step[1]}."

    if action == "go_to":
        navigation.navigate_to(controller, resolve(step[1]))
        return f"Moved to {step[1]}."

    if action == "fetch":
        # Make sure the thing we are about to place is in hand - decided from live state.
        obj_name, target = step[1], step[2]
        if _held_matches(controller, obj_name):
            return ""
        other = manipulation.get_held_object_id(controller)
        if other is not None:
            manipulation.drop_held_object(controller)
        obj_id = resolve(obj_name, prefer="pickupable")
        objects = controller.last_event.metadata["objects"]
        by_id = {o["objectId"]: o for o in objects}
        obj = by_id.get(obj_id)
        if target and obj is not None:
            try:
                target_id = resolve(target, prefer="receptacle")
            except ExecutionError:
                target_id = None
            if target_id and _in_target(obj, target_id, by_id):
                return f"The {obj_name} is already in/on the {target}."
        navigation.navigate_to(controller, obj_id)
        manipulation.pickup_object(controller, obj_id)
        return f"Picked up {obj_name}."

    if action == "pickup":
        manipulation.pickup_object(controller, resolve(step[1], prefer="pickupable"))
        return f"Picked up {step[1]}."

    if action == "put":
        obj_name, target = step[1], step[2]
        target_id = resolve(target, prefer="receptacle")
        if not _held_matches(controller, obj_name):
            # Not in hand. If it is already where it should be (e.g. the egg
            # cracked straight into the pan) that is success, not an error.
            try:
                obj = ctx._live(resolve(obj_name, prefer="pickupable"))
            except ExecutionError:
                obj = None
            by_id = {o["objectId"]: o for o in controller.last_event.metadata["objects"]}
            if obj is not None and _in_target(obj, target_id, by_id):
                return f"The {obj_name} is already in/on the {target}."
            raise ExecutionError(f"I'm not holding the {obj_name} to place it.")
        held_id = manipulation.get_held_object_id(controller)
        manipulation.put_object(controller, held_id, target_id)
        if canon_name(target) in DISH_TYPES:
            ctx.dish_log.append((held_id, target_id))
        return f"Placed {obj_name} on/in {target}."

    if action == "open":
        object_id = resolve(step[1], prefer="openable")
        manipulation.open_object(controller, object_id)
        navigation.ensure_visible(controller, object_id)
        return f"Opened {step[1]}."

    if action == "list_contents":
        contents = manipulation.list_contents(controller, resolve(step[1], prefer="receptacle"))
        if contents:
            return f"The {step[1]} contains: {_summarise_contents(contents)}."
        return f"The {step[1]} is empty."

    if action == "close":
        manipulation.close_object(controller, resolve(step[1], prefer="openable"))
        return f"Closed {step[1]}."

    if action == "toggle_on":
        manipulation.toggle_on(controller, _toggle_target(ctx, step[1], True))
        return f"Turned on {step[1]}."

    if action == "toggle_off":
        manipulation.toggle_off(controller, _toggle_target(ctx, step[1], False))
        return f"Turned off {step[1]}."

    if action == "drop":
        manipulation.drop_object(controller)
        return "Dropped it."

    if action == "slice":
        object_id = ctx.resolve_whole(step[1])
        before = {o["objectId"] for o in controller.last_event.metadata["objects"]}
        original_type = object_id.split("|")[0]
        manipulation.slice_object(controller, object_id)
        sync.refresh(controller)
        _after_transform(ctx, step[1], before, original_type)
        return f"Sliced {step[1]}."

    if action == "break":
        object_id = resolve(step[1])
        before = {o["objectId"] for o in controller.last_event.metadata["objects"]}
        original_type = object_id.split("|")[0]
        manipulation.break_object(controller, object_id)
        sync.refresh(controller)
        _after_transform(ctx, step[1], before, original_type)
        return f"Broke {step[1]}."

    if action in ("clean", "dirty", "empty", "use_up"):
        getattr(manipulation, f"{action}_object")(controller, resolve(step[1]))
        past = {"clean": "Cleaned", "dirty": "Made dirty", "empty": "Emptied", "use_up": "Used up"}
        return f"{past[action]} {step[1]}."

    if action == "fill":
        manipulation.fill_object(controller, resolve(step[1]), step[2])
        return f"Filled {step[1]} with {step[2]}."

    if action == "look_at":
        manipulation.look_at_object(controller, resolve(step[1]))
        return f"Looking at {step[1]}."

    if action == "look":
        navigation.look(controller, step[1], step[2])
        return f"Looked {step[1]}."

    if action == "turn":
        navigation.turn(controller, step[1], step[2])
        return f"Turned {step[1]}."

    if action == "move":
        navigation.move(controller, step[1], step[2])
        return f"Moved {step[1]}."

    if action == "posture":
        navigation.set_posture(controller, step[1])
        return "Crouched." if step[1] == "crouch" else "Standing up."

    if action == "run_task":
        task_name = step[1]
        if task_name == "tidy":
            return tasks.tidy_room(controller)
        if task_name == "putaway":
            return tasks.put_away_groceries(controller)
        if task_name == "energy":
            return tasks.save_energy(controller)
        if task_name == "mess":
            return tasks.mess_up_room(controller)
        raise ExecutionError(f"Unknown task '{task_name}'.")

    raise ExecutionError(f"Unknown low-level action '{action}'.")


def _repair_dish(ctx, messages):
    """
    Final check for a dish: every item put on the plate must still BE on the
    plate (a slice can slide off a crowded plate). If one is not, pick it up
    and put it back once; otherwise say so honestly instead of claiming success.
    """
    c = ctx.c
    sync.refresh(c)             # physics may have moved a slice since it was placed: read the world again
    for item_id, plate_id in ctx.dish_log:
        if control.is_cancelled():
            return
        obj, plate = ctx._live(item_id), ctx._live(plate_id)
        if obj is None or plate is None or plate_id in (obj.get("parentReceptacles") or []):
            continue
        name = obj["objectType"]
        control.say(f"The {name} is not on the plate - putting it back.")
        try:
            navigation.navigate_to(c, item_id)
            manipulation.pickup_object(c, item_id)
            navigation.navigate_to(c, plate_id)
            manipulation.put_object(c, item_id, plate_id)
            sync.settle(c)
            messages.append(f"Put the {name} back on the plate.")
        except (navigation.NavigationError, manipulation.ManipulationError) as exc:
            manipulation.drop_held_object(c)
            metrics.count_placed(ok=-1, failed=1)
            messages.append(f"The {name} would not stay on the plate ({exc}).")


def execute_plan(controller, plan, progress=None, dish=None):
    """
    Runs every low-level action in order. `plan` is a list of tuples like
    ("find", "apple"), ("go_to", "apple"), ("pickup", "apple"),
    ("fetch", "egg", "pan"), ("put", "apple", "fridge"), ("run_task", "tidy")
    (see task_planner.build_plan).

    Returns the list of per-step messages; raises ExecutionError with a
    speakable message on failure. `progress`, if given, is a dict that is
    updated with {"done": n} so callers can report partial success.
    """
    ctx = Context(controller, dish)
    messages = []

    for i, step in enumerate(plan):
        if control.is_cancelled():
            sync.settle(controller)
            raise ExecutionError("Stopped.")
        msg = execute_atomic_step(ctx, step)
        if msg:
            messages.append(msg)
        if progress is not None:
            progress["done"] = i + 1

    if ctx.dish_log:
        _repair_dish(ctx, messages)
    return messages


def missing_objects(controller, command_steps):
    """
    Names in the structured steps that don't exist in the current scene.
    Checked BEFORE the robot moves, so an impossible command is refused
    up front instead of failing half way through. Things a step will
    CREATE (a cracked egg) are fine: the original name is what is checked.
    """
    missing = []
    for st in command_steps:
        names = [st.get("object"), st.get("target")]
        if st.get("action") == "slice":
            names.append(st.get("tool") or "knife")
        for name in names:
            if name and scene_manager.find_object_id_by_name(controller, name) is None:
                if name not in missing:
                    missing.append(name)
    return missing


def run_text_command(controller, text):
    """
    The whole pipeline for one typed/spoken command, shared by main.py and
    ui_app.py: parse -> validate -> check objects exist -> plan -> execute
    -> make sure the final state is on screen -> reply text + metrics.
    """
    from nlp.command_parser import parse_command
    from planner.action_validator import InvalidCommandError, validate_command
    from planner.task_planner import build_plan

    if text.strip().lower() in ("stats", "metrics", "show stats", "show metrics"):
        return metrics.session_summary() + (
            "\n" + metrics.format_report(metrics.last_report) if metrics.last_report else "")

    started = metrics.begin("command: " + text.strip()[:60], controller)
    progress = {"done": 0}
    total_steps = 0
    try:
        t0 = time.monotonic()
        command = parse_command(text, controller)
        metrics.add_plan_time(time.monotonic() - t0)

        if isinstance(command, dict) and not command.get("steps") and command.get("reason"):
            reason = str(command["reason"]).strip()
            low = reason.lower()
            if low.startswith("already"):            # goal already true: not an error
                return reason
            if low.startswith("not possible:"):
                reason = reason.split(":", 1)[1].strip()
            return f"I can't do that here: {reason}"

        try:
            validate_command(command)
        except InvalidCommandError as exc:
            return str(exc)

        command["steps"] = floor_to_drop(command["steps"])     # "on the floor" = pick up + drop

        dish = None
        try:    # sandwich-style rules checked against the live scene (slice first, one plate, one counter)
            agent_pos = controller.last_event.metadata["agent"]["position"]
            command["steps"], dish = enforce_dish_rules(
                command["steps"], controller.last_event.metadata["objects"], agent_pos)
        except Exception as exc:
            print(f"[planner] dish rules skipped: {exc}")

        absent = missing_objects(controller, command["steps"])
        if absent:
            return "I can't find " + ", ".join(f"'{n}'" for n in absent) + " in this scene."

        held_id = manipulation.get_held_object_id(controller)
        held_name = held_id.split("|")[0] if held_id else None
        plan = build_plan(command["steps"], held=held_name)
        if not plan:
            return "I understood the words, but couldn't turn them into an action."
        total_steps = sum(1 for st in plan if st[0] != "find")

        try:
            messages = execute_plan(controller, plan, progress, dish)
        except ExecutionError as exc:
            done = sum(1 for st in plan[:progress["done"]] if st[0] != "find")
            report = metrics.end(done, total_steps) if started else ""
            started = False
            return f"{exc} {report}".strip()

        sync.settle(controller)   # belt and braces: nothing is "complete" before it's visible
        reply = " ".join(messages) + " Task complete."
        report = metrics.end(total_steps, total_steps) if started else ""
        started = False
        return f"{reply}\n{report}".strip()
    finally:
        if started:
            metrics.end(progress["done"], total_steps)
