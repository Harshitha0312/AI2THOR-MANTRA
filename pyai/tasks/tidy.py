from ai2thor.controller import Controller
from pyai.utils.actions import (
    try_pickup,
    try_place,
    teleport_to_object,
    teleport_random,
    move_smart,
    get_closest_position
)
from pyai.utils.preference import get_preferred_location
import math
import time

SCENE           = "FloorPlan201"
MAX_STEPS       = 3000
PICKUP_DIST     = 1.2
PLACE_DIST      = 1.35
SLEEP_TIME      = 0.05
RETRY_LIMIT     = 5

def is_already_placed(obj, objects,id_to_type):
    parent_ids = obj.get("parentReceptacles")

    if not parent_ids:
        return False

    preferred = get_preferred_location(obj, objects)

    for parent_id in parent_ids:
        if id_to_type.get(parent_id) == preferred:
            return True

    return False

def find_pickup_target(objects):
    id_to_type={
        o["objectId"]: o["objectType"]
        for o in objects
        if o.get("receptacle",False)
    }
    candidates = []
    for o in objects:
        if not o.get("pickupable"):
            continue
        if not o.get("visible"):
            continue
        if is_already_placed(o, objects,id_to_type):
            continue
        candidates.append(o)

    if not candidates:
        return None
    for candidate in candidates:
        chosen=min(candidates, key=lambda o: o["distance"])
        current=chosen.get("parentReceptacles")
        if current:
            parent=next(
                obj for obj in objects
                if obj["objectId"]==current[0])
        return chosen

def find_destination(held_type, objects):
    preferred=get_preferred_location(
        {"objectType":held_type},
    objects)
    if preferred is None:
        return None
    candidates=[
        o for o in objects
        if o["objectType"]==preferred
        and o.get("visible")
        and o.get("receptacle",False)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o:o["distance"])
def _restore_camera(controller, steps_taken):
    reverse = {"LookDown": "LookUp", "LookUp": "LookDown"}
    for action in reversed(steps_taken):
        controller.step(action=reverse[action])

def try_place_with_recovery(controller, dest, reachable_positions):
    CAMERA_SEQUENCE = [
        "LookUp",
        "LookDown",
        "LookDown",
        "LookUp",
        "LookUp",
        "LookDown",
    ]
    POSITION_OFFSETS = [
        {"x":  0.5, "z":  0.0},   # East
        {"x": -0.5, "z":  0.0},   # West
        {"x":  0.0, "z":  0.5},   # North
        {"x":  0.0, "z": -0.5},   # South
        {"x":  0.5, "z":  0.5},   # NE
        {"x": -0.5, "z": -0.5},   # SW
    ]

    we_opened_it = False
    if dest.get("openable") and not dest.get("isOpen"):
        open_ev = controller.step(action="OpenObject", objectId=dest["objectId"])
        if open_ev.metadata["lastActionSuccess"]:
            we_opened_it = True
        else:
            print(f" Could not open {dest['objectType']} — trying anyway...")

    def attempt_with_angles(controller):
        steps_taken = []

        ev = try_place(controller, dest)
        if ev.metadata["lastActionSuccess"]:
            return ev, True
        print("place failed", ev.metadata["errorMessage"])

        for action in CAMERA_SEQUENCE:
            controller.step(action=action)
            steps_taken.append(action)
            ev = try_place(controller, dest)
            if ev.metadata["lastActionSuccess"]:
                _restore_camera(controller, steps_taken)
                return ev, True

        _restore_camera(controller, steps_taken)
        return ev, False

    def attempt_moveback_lookdown(controller):
        moved = controller.step(action="MoveBack")
        controller.step(action="LookDown")

        ev = try_place(controller, dest)
        controller.step(action="LookUp")

        if ev.metadata["lastActionSuccess"]:
            controller.step(action="MoveAhead")
            return ev, True
        controller.step(action="MoveAhead")
        return ev, False

    def close_if_opened():
        if we_opened_it:
            controller.step(action="CloseObject", objectId=dest["objectId"])
    ev, success = attempt_with_angles(controller)
    if success:
        close_if_opened()
        return ev, True
    ev, success = attempt_moveback_lookdown(controller)
    if success:
        close_if_opened()
        return ev, True

    print(f"Trying alternate positions around destination...")
    dest_pos = dest["position"]

    for offset in POSITION_OFFSETS:
        candidate = {
            "x": dest_pos["x"] + offset["x"],
            "y": dest_pos["y"],
            "z": dest_pos["z"] + offset["z"],
        }

        closest = get_closest_position(candidate, reachable_positions)
        controller.step(action="Teleport", position=closest, horizon=30)
        print(f"      📍 Trying from offset ({offset['x']:+.1f}, {offset['z']:+.1f})")

        ev, success = attempt_with_angles(controller)
        if success:
            print(f"      ✅ Placed from alternate position!")
            close_if_opened()
            return ev, True
        ev, success = attempt_moveback_lookdown(controller)
        if success:
            close_if_opened()
            return ev, True
    close_if_opened()
    print(f" All positions and angles exhausted.")
    return ev, False

def tidy_room(controller):
    print("=" * 60)
    print("  🏠  AI2-THOR CLEANUP AGENT STARTING")
    print("=" * 60)

    event = controller.step(action="Pass")
    positions_event = controller.step(action="GetReachablePositions")
    reachable_positions = positions_event.metadata["actionReturn"]
    print(f"✅ Scene loaded | Reachable positions: {len(reachable_positions)}")
    print(f"🎯tidying objects")

    holding      = False
    held_type    = None
    held_obj_id  = None
    retry_count  = 0
    tidy_count   = 0
    skip_ids     = set()
    misplaced_found = 0
    placed_count = 0
    failed_count = 0
    transfer_count = 1
    previous_receptacle=None
    expected_receptacle = None
    no_target_count=0
    max_empty_steps=15

    for step in range(MAX_STEPS):
        event   = controller.step(action="Pass")
        objects = event.metadata["objects"]
        if not holding:
            target = find_pickup_target(objects)

            if target is None:
                no_target_count+=1
                if no_target_count>=max_empty_steps:
                    print("Cleaning complete! No misplaced objects remaining")
                    break
                teleport_random(controller,reachable_positions)
                retry_count=0
                time.sleep(SLEEP_TIME)
                continue
            no_target_count=0
            if target["objectId"] in skip_ids:
                continue

            if target["distance"] > PICKUP_DIST:
                teleport_to_object(controller, target, reachable_positions)
            else:
                parent_ids=target.get("parentReceptacles") or []
                id_to_type={
                    o["objectId"]:o["objectType"]
                    for o in objects
                }
                current_receptacle="Floor"
                for parent_id in parent_ids:
                    if parent_id in id_to_type:
                        current_receptacle=id_to_type[parent_id]
                        break
                ev = try_pickup(controller, target)
                if ev.metadata["lastActionSuccess"]:
                    holding     = True
                    held_type   = target["objectType"]
                    held_obj_id = target["objectId"]
                    retry_count = 0
                    previous_receptacle=current_receptacle
                    expected_receptacle=get_preferred_location(target, objects)
                    print(f"Picked up: {held_type}")
                else:
                    retry_count += 1
                    move_smart(controller)
                    if retry_count >= RETRY_LIMIT:
                        failed_count+=1
                        misplaced_found+=1
                        skip_ids.add(target["objectId"])
                        retry_count = 0
        else:
            dest = find_destination(held_type, objects)

            if dest is None:
                teleport_random(controller, reachable_positions)
                time.sleep(SLEEP_TIME)
                continue

            if dest["distance"] > PLACE_DIST:
                teleport_to_object(controller, dest, reachable_positions)
            else:
                ev, success = try_place_with_recovery(controller, dest, reachable_positions)

                if success:
                    tidy_count += 1
                    misplaced_found+=1
                    placed_count+=1
                    print("\n"+"="*60)
                    print(f"transfer count:{transfer_count}")
                    print(f"misplaced object:{held_type}")
                    print(f"current position:{previous_receptacle}")
                    print(f"expected destination:{expected_receptacle}")
                    print("status:   Transfer successful")
                    print("="*60)
                    transfer_count+=1

                    holding     = False
                    held_type   = None
                    held_obj_id = None
                    retry_count = 0
                else:
                    retry_count += 1
                    move_smart(controller)
                    if retry_count >= RETRY_LIMIT:
                        print("\n"+"="*60)
                        print(f"tranfer  number: {transfer_count}")
                        print(f"misplaced object:{held_type}")
                        print(f"current position: {previous_receptacle}")
                        print(f"expected destination:{expected_receptacle}")
                        print("status:  transfer failed")
                        print("="*60)

                        transfer_count+=1

                        controller.step(action="DropHandObject")
                        holding     = False
                        held_type   = None
                        held_obj_id = None
                        retry_count = 0

        time.sleep(SLEEP_TIME)
    print("\n"+"="*60+"\n"+"="*60)
    print(f"misplaced objects found: {misplaced_found}")
    print(f"objects successfully placed: {placed_count}")
    print(f"objects failed to place: {failed_count}")
    if misplaced_found>0:
        success_rate=(placed_count/misplaced_found)*100
        print(f"success rate: {success_rate:.2f}%"+"\n"+"="*60)

if __name__ == "__main__":
    controller = Controller(
        scene=SCENE,
        width=800,
        height=600,
        fieldOfView=90,
        rotateStepDegrees=90,
        gridSize=0.25,
    )
    event=controller.step(action="Pass")
    objects=event.metadata["objects"]
    try:
        tidy_room(controller)
    finally:
        input("\nPress Enter to exit...")
        controller.stop()