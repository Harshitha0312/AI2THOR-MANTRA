"""
Generates the object -> receptacle placement preference table by reading
ground-truth scene metadata across a sample of AI2-THOR floorplans,
instead of hand-typing it.

This is the exact same generator MANTRA's preferences.py used to not
have: it scans each scene's objects, records which receptacle type each
pickupable object is actually sitting in/on, and turns the resulting
per-scene counts into a 0-1 normalized preference score per
(object type, receptacle type) pair - the same shape as the old
hardcoded PREFERENCES dict in preferences.py.

Only entry point other modules should use is generate_preferences().
build_raw_counts() / normalize_preferences() / save_preference_table()
are kept separate (rather than inlined) so this file can still be run
standalone for debugging: `python robot/preference_generator.py`.
"""

import json
import os
from collections import defaultdict

from robot.sim_controller import get_controller

ROOM_RANGES = {
    "Kitchen":    range(1, 31),     # FloorPlan1   - FloorPlan30
    "LivingRoom": range(201, 231),  # FloorPlan201 - FloorPlan230
    "Bedroom":    range(301, 331),  # FloorPlan301 - FloorPlan330
    "Bathroom":   range(401, 431),  # FloorPlan401 - FloorPlan430
}
ROOMS_PER_TYPE = 15

DEFAULT_TABLE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "preference_table_new.json"
)


def get_scene_list():
    scenes = []
    for room_type, id_range in ROOM_RANGES.items():
        ids = list(id_range)[:ROOMS_PER_TYPE]
        scenes.extend([f"FloorPlan{i}" for i in ids])
    return scenes


def read_scene_pairs(controller):
    objects = controller.last_event.metadata["objects"]
    obj_by_id = {o["objectId"]: o for o in objects}
    receptacle_type_by_id = {
        o["objectId"]: o["objectType"] for o in objects if o.get("receptacle")
    }

    seen_this_scene = set()
    for obj in objects:
        if not obj.get("pickupable"):
            continue
        parent_ids = obj.get("parentReceptacles") or []
        if not parent_ids:
            continue

        direct_parent_id = max(
            parent_ids,
            key=lambda pid: len(obj_by_id.get(pid, {}).get("parentReceptacles") or []),
        )

        receptacle_type = receptacle_type_by_id.get(direct_parent_id)
        if receptacle_type:
            seen_this_scene.add((obj["objectType"], receptacle_type))

    return list(seen_this_scene)


def build_raw_counts():
    scenes = get_scene_list()
    print(f"[preference_generator] reading metadata for {len(scenes)} scenes "
          f"(ground truth, no exploration)...")

    # counts[object_type][receptacle_type] = number of scenes it was seen in
    counts = defaultdict(lambda: defaultdict(int))
    # Uses the project's own controller factory (config.py settings) rather
    # than a bare Controller(), so this stays consistent with how the rest
    # of MANTRA starts AI2-THOR.
    controller = get_controller()

    try:
        for idx, scene in enumerate(scenes, 1):
            print(f"[preference_generator] [{idx}/{len(scenes)}] {scene}")
            try:
                controller.reset(scene=scene)
            except Exception as e:
                print(f"[preference_generator]   [warn] could not load {scene}: {e}")
                continue

            for object_type, receptacle_type in read_scene_pairs(controller):
                counts[object_type][receptacle_type] += 1
    finally:
        controller.stop()

    return counts


def normalize_preferences(counts):
    preference = {}
    for object_type, receptacle_counts in counts.items():
        max_count = max(receptacle_counts.values())
        ranked = sorted(receptacle_counts.items(), key=lambda x: -x[1])
        preference[object_type] = {
            receptacle: round(count / max_count, 2) for receptacle, count in ranked
        }
    return preference


def save_preference_table(preference, path=DEFAULT_TABLE_PATH):
    # sort_keys=False so we keep descending-score insertion order per object
    with open(path, "w") as f:
        json.dump(preference, f, indent=4, sort_keys=False)
    print(f"[preference_generator] saved preference table -> {path}")


def generate_preferences(save_path=DEFAULT_TABLE_PATH):
    """
    Runs the full generation pipeline once and returns the resulting
    {object_type: {receptacle_type: score}} dict - the exact same shape
    as the old hardcoded PREFERENCES dict in preferences.py.

    If save_path is given (default: preference_table_new.json next to
    this file), the table is also cached to disk so future runs don't
    need to re-scan every scene.
    """
    raw_counts = build_raw_counts()
    preference = normalize_preferences(raw_counts)
    if save_path:
        save_preference_table(preference, path=save_path)
    return preference


if __name__ == "__main__":
    result = generate_preferences()
    print("\nSample entries:")
    for object_type, receptacle_scores in list(result.items())[:5]:
        print(f"  {object_type}: {receptacle_scores}")
