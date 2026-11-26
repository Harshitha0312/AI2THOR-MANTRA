"""
Placement preferences used by the "tidy the room" task: for each
pickupable object type, which receptacle types it "belongs" on/in,
ranked from most to least preferred.

PREFERENCES used to be hand-typed below. It's now produced by running
preference_generator.py (which scans real AI2-THOR scene metadata)
ONCE, the first time this module is imported, and caching the result to
preference_table_new.json next to this file so later runs just load it
instead of re-scanning every scene. If a cached table already exists,
it's loaded directly and the generator doesn't run at all.

The resulting PREFERENCES has the exact same shape it always has:
    PREFERENCES[object_type][receptacle_type] -> score (0-1 float)
so get_preferred_location() below, and every caller of it elsewhere in
the project, needed no changes at all.

If AI2-THOR can't run in this environment (no Unity/display, generator
import fails, a scene fails to load, etc.), PREFERENCES falls back to
the original hand-typed table further down (_HARDCODED_FALLBACK_PREFERENCES)
so the rest of MANTRA keeps working exactly as before.
"""

import json
import os

try:
    from robot.preference_generator import generate_preferences, DEFAULT_TABLE_PATH
except Exception as _import_exc:  # e.g. ai2thor not installed/usable here
    generate_preferences = None
    DEFAULT_TABLE_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "preference_table_new.json"
    )


def _load_or_generate_preferences():
    # 1. A cached table from a previous generator run -> just use it.
    if os.path.exists(DEFAULT_TABLE_PATH):
        try:
            with open(DEFAULT_TABLE_PATH, "r") as f:
                table = json.load(f)
            print(f"[preferences] loaded cached preference table "
                  f"({len(table)} object types) from {DEFAULT_TABLE_PATH}")
            return table
        except Exception as exc:
            print(f"[preferences] could not read cached table ({exc}), "
                  f"regenerating...")

    # 2. No cache yet -> run the generator ONCE to build it.
    if generate_preferences is not None:
        try:
            print("[preferences] no cached table found, running "
                  "preference_generator once (this scans AI2-THOR scenes "
                  "and may take a while the first time)...")
            table = generate_preferences(save_path=DEFAULT_TABLE_PATH)
            print(f"[preferences] generated preference table for "
                  f"{len(table)} object types")
            return table
        except Exception as exc:
            print(f"[preferences] generator failed ({exc}), falling back "
                  f"to built-in defaults")

    # 3. Last-resort fallback so the project never breaks.
    print("[preferences] using built-in fallback preference table")
    return _HARDCODED_FALLBACK_PREFERENCES


_HARDCODED_FALLBACK_PREFERENCES = {
    "Box": {
        "Floor": 1.0,
        "Shelf": 0.8,
        "Desk": 0.6,
        "CoffeeTable": 0.4,
    },
    "Book": {
        "Shelf": 0.8,
        "Desk": 0.9,
        "CoffeeTable": 0.7,
        "SideTable": 0.6,
        "DiningTable": 1.0,
    },
    "Pen": {
        "Drawer": 1.0,
        "Desk": 0.9,
        "SideTable": 0.6,
        "DiningTable": 0.4,
    },
    "Pencil": {
        "Drawer": 1.0,
        "Desk": 0.9,
        "SideTable": 0.6,
        "DiningTable": 0.4,
    },
    "Laptop": {
        "Desk": 1.0,
        "DiningTable": 0.8,
        "CoffeeTable": 0.5,
        "SideTable": 0.4,
    },
    "DebitCard": {
        "Drawer": 1.0,
        "Desk": 0.7,
        "SideTable": 0.5,
    },
    "CreditCard": {
        "Drawer": 1.0,
        "Desk": 0.7,
        "SideTable": 0.5,
    },
    "Newspaper": {
        "CoffeeTable": 1.0,
        "SideTable": 0.8,
        "DiningTable": 0.6,
        "Desk": 0.4,
    },
    "Plate": {
        "Cabinet": 1.0,
        "DiningTable": 0.8,
        "CounterTop": 0.6,
        "Sink": 0.3,
    },
    "Cup": {
        "Cabinet": 1.0,
        "DiningTable": 0.8,
        "CounterTop": 0.7,
        "CoffeeTable": 0.5,
    },
    "Mug": {
        "Cabinet": 1.0,
        "CoffeeMachine": 0.9,
        "CounterTop": 0.8,
        "DiningTable": 0.7,
        "CoffeeTable": 0.5,
    },
    "Bowl": {
        "Cabinet": 1.0,
        "CounterTop": 0.8,
        "DiningTable": 0.7,
        "CoffeeTable": 0.5,
    },
    "RemoteControl": {
        "TVStand": 1.0,
        "CoffeeTable": 0.9,
        "SideTable": 0.7,
        "Sofa": 0.3,
    },
    "TissueBox": {
        "CoffeeTable": 1.0,
        "SideTable": 0.9,
        "Desk": 0.7,
        "DiningTable": 0.5,
    },
    "Pillow": {
        "Bed": 1.0,
        "Sofa": 0.9,
        "ArmChair": 0.7,
    },
    "Statue": {
        "Shelf": 1.0,
        "SideTable": 0.8,
        "CoffeeTable": 0.6,
        "Desk": 0.5,
    },
    "Vase": {
        "DiningTable": 1.0,
        "SideTable": 0.9,
        "CoffeeTable": 0.8,
        "CounterTop": 0.5,
        "Shelf": 0.4,
    },
    "Watch": {
        "Drawer": 1.0,
        "Desk": 0.8,
        "SideTable": 0.7,
        "Dresser": 0.6,
    },
    "KeyChain": {
        "Drawer": 1.0,
        "Desk": 0.8,
        "SideTable": 0.7,
        "Dresser": 0.6,
    },
}

PREFERENCES = _load_or_generate_preferences()

DEFAULT_PREFERENCE = {
    "DiningTable": 1.0,
    "SideTable": 0.6,
    "CoffeeTable": 0.8,
    "Cabinet": 0.3,
}


def get_preferred_location(obj, objects):
    """
    Given an object (needs at least "objectType") and the full list of
    objects currently in the scene, returns the objectType of the best
    receptacle that's actually present in this scene for it to live on,
    or None if nothing in its preference list exists here.
    """
    obj_type = obj["objectType"]
    preferred_dict = PREFERENCES.get(obj_type, DEFAULT_PREFERENCE)
    available_receptacles = {
        o["objectType"] for o in objects if o.get("receptacle", False)
    }
    sorted_preference = sorted(
        preferred_dict.items(), key=lambda item: item[1], reverse=True
    )
    for preferred, _score in sorted_preference:
        if preferred in available_receptacles:
            return preferred
    return None