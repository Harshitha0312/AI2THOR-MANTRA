"""
Run metrics for MANTRA (Nlp26) - deliberately small.

Every command / whole-scene task prints ONE line:

    [metrics] accuracy 100% | time 11.2s | steps 14 | distance 2.5m | placed 2 | failed to place 0

  accuracy         % of the work that was done: objects handled / objects
                   attempted for tidy / put-away / mess-up, otherwise
                   atomic steps completed / steps planned.
  time             wall-clock seconds for the whole command (planning +
                   execution, including the short pauses that keep the
                   motion visible).
  steps            physical steps the robot really took: every successful
                   walking step (one 0.25 m move), every 90-degree turn and
                   every manipulation action (pick up, put, open, close,
                   switch, slice, drop ...). Camera tilts, simulator
                   "Pass" ticks and bookkeeping queries are not steps.
  distance         metres the robot actually walked: successful walking
                   moves (MoveAhead / MoveBack / MoveLeft / MoveRight,
                   including sidesteps) x config.GRID_SIZE (0.25 m each).
  placed           objects put where they were supposed to go
  failed to place  objects that should have been placed but could not be
                   (the two placement numbers are shown ONLY for runs that
                   involve placing something: put/place commands, tidy,
                   put away groceries, mess up, making a dish)

Other modules only call begin() / end() / inc() / count_placed() /
set_units(); nothing else formats anything. Nested begin() calls are
ignored, so a task started inside a command is counted once, in that command.
"""

import json
import time
from collections import Counter

import config

_state = None
_session = {"runs": 0, "ok": 0, "total": 0, "seconds": 0.0, "steps": 0,
            "placed": 0, "failed_place": 0}
last_report = None

# Simulator actions that count as one "step" (when they succeed).
STEP_ACTIONS = frozenset({
    "MoveAhead", "MoveBack", "MoveLeft", "MoveRight", "RotateLeft", "RotateRight",
    "PickupObject", "PutObject", "DropHandObject", "OpenObject", "CloseObject",
    "ToggleObjectOn", "ToggleObjectOff", "SliceObject", "BreakObject",
    "CleanObject", "DirtyObject", "FillObjectWithLiquid", "EmptyLiquidFromObject",
    "UseUpObject", "Crouch", "Stand",
})
WALK_ACTIONS = frozenset({"MoveAhead", "MoveBack", "MoveLeft", "MoveRight"})   # each = one grid cell


def _attach(controller):
    """Counts successful physical actions by wrapping controller.step for the length of one run."""
    if controller is None or getattr(controller, "_mantra_orig_step", None) is not None:
        return None
    orig = controller.step

    def counting_step(*args, **kwargs):
        event = orig(*args, **kwargs)
        try:
            action = kwargs.get("action")
            if action is None and args:
                action = args[0].get("action") if isinstance(args[0], dict) else args[0]
            if isinstance(action, str) and action in STEP_ACTIONS and event.metadata.get("lastActionSuccess"):
                inc("steps")
                if action in WALK_ACTIONS:
                    inc("walk_moves")
        except Exception:
            pass
        return event

    controller._mantra_orig_step = orig
    controller._mantra_had_own_step = "step" in getattr(controller, "__dict__", {})
    controller.step = counting_step
    return controller


def _detach(controller):
    """Puts controller.step back exactly as it was before begin()."""
    orig = getattr(controller, "_mantra_orig_step", None) if controller is not None else None
    if orig is None:
        return
    try:
        if getattr(controller, "_mantra_had_own_step", False):
            controller.step = orig
        else:
            controller.__dict__.pop("step", None)       # back to the class's own method
        del controller._mantra_orig_step
        del controller._mantra_had_own_step
    except Exception:
        pass


def begin(label, controller=None):
    """Starts a run. Returns True if THIS call started it (and so must call end())."""
    global _state
    if _state is not None:
        return False
    _state = {"label": label, "t0": time.monotonic(), "pause": 0.0, "plan": 0.0,
              "c": Counter(), "ok": None, "total": None, "notes": {},
              "placed": None, "failed_place": None, "controller": _attach(controller)}
    return True


def active():
    return _state is not None


def inc(key, n=1):
    if _state is not None:
        _state["c"][key] += n


def add_pause(seconds):
    if _state is not None:
        _state["pause"] += seconds


def add_plan_time(seconds):
    if _state is not None:
        _state["plan"] += seconds


def set_units(ok, total):
    """Overrides step-based accuracy with item-based accuracy (e.g. objects tidied)."""
    if _state is not None:
        _state["ok"], _state["total"] = ok, total


def count_placed(ok=0, failed=0):
    """Adds to the placement tally. Calling it at all marks the run as 'involves placing'."""
    if _state is not None:
        _state["placed"] = (_state["placed"] or 0) + ok
        _state["failed_place"] = (_state["failed_place"] or 0) + failed


def set_placed(ok, failed):
    """Sets the placement tally outright (whole-scene tasks know their final numbers)."""
    if _state is not None:
        _state["placed"], _state["failed_place"] = ok, failed


def note(key, value):
    if _state is not None:
        _state["notes"][key] = value


def end(steps_ok=0, steps_total=0):
    """Closes the run, folds it into the session totals and returns the report text."""
    global _state, last_report
    s = _state
    _state = None
    if s is None:
        return ""
    _detach(s.get("controller"))
    ok = s["ok"] if s["ok"] is not None else steps_ok
    total = s["total"] if s["total"] is not None else steps_total
    elapsed = time.monotonic() - s["t0"]
    c = s["c"]
    pct = (100.0 * ok / total) if total else 100.0
    last_report = {
        "label": s["label"], "accuracy_pct": round(pct, 1), "ok": ok, "total": total,
        "seconds": round(elapsed, 1), "steps": c["steps"],
        "distance_m": round(c["walk_moves"] * config.GRID_SIZE, 2),
        "placed": s["placed"], "failed_to_place": s["failed_place"],
        "counters": dict(c), "notes": dict(s["notes"]),
    }
    _session["runs"] += 1
    _session["ok"] += ok
    _session["total"] += total
    _session["seconds"] += elapsed
    _session["steps"] += c["steps"]
    _session["placed"] += s["placed"] or 0
    _session["failed_place"] += s["failed_place"] or 0
    _append_log(last_report)
    return format_report(last_report)


def format_report(r):
    parts = [f"accuracy {r['accuracy_pct']:.0f}%",
             f"time {r['seconds']:.1f}s",
             f"steps {r['steps']}",
             f"distance {r.get('distance_m', 0.0):.1f}m"]
    if r.get("placed") is not None or r.get("failed_to_place") is not None:
        parts.append(f"placed {r.get('placed') or 0}")
        parts.append(f"failed to place {r.get('failed_to_place') or 0}")
    return "[metrics] " + " | ".join(parts)


def session_summary():
    s = _session
    if not s["runs"]:
        return "No commands run yet."
    pct = 100.0 * s["ok"] / s["total"] if s["total"] else 100.0
    txt = (f"Session: {s['runs']} run(s) | accuracy {pct:.0f}% | time {s['seconds']:.0f}s | "
           f"steps {s['steps']}")
    if s["placed"] or s["failed_place"]:
        txt += f" | placed {s['placed']} | failed to place {s['failed_place']}"
    return txt


def _append_log(report):
    """One JSON line per run -> metrics_log.jsonl (never allowed to break a task)."""
    path = getattr(config, "METRICS_LOG", "metrics_log.jsonl")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(report) + "\n")
    except Exception:
        pass