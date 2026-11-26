"""Nlp26: simple metrics, sandwich rules in code, whole-room random mess, blockage fallback,
floor / typo / already-done handling, and the rewritten prompt."""
import sys, os, random, math
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0; config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
config.METRICS_LOG = None
from mock_thor import MockController
from robot import tasks, hybrid_navigator, metrics
from environment import scene_manager
from planner import task_planner
import executor
from nlp import command_parser

fails = []
def check(n, cond, info=""):
    print((" PASS " if cond else " FAIL ") + n + (f"  [{info}]" if info else ""))
    if not cond: fails.append(n)

def mk(t, pos, **kw):
    d = dict(objectType=t, objectId=f"{t}|{pos[0]:+.2f}|{pos[1]:+.2f}|{pos[2]:+.2f}",
             position=dict(x=pos[0], y=pos[1], z=pos[2]), pickupable=False, receptacle=False,
             toggleable=False, isToggled=False, openable=False, isOpen=False,
             parentReceptacles=None, receptacleObjectIds=[], controlledObjects=None,
             canFillWithLiquid=False, isFilledWithLiquid=False, fillLiquid=None,
             sliceable=False, isSliced=False, breakable=False, isBroken=False,
             dirtyable=False, isDirty=False, isUsedUp=False)
    d.update(kw); return d
def ptypes(c, o): return [c._find(p)["objectType"] for p in (o["parentReceptacles"] or [])]
def run_steps(c, steps):
    orig = command_parser.parse_command
    command_parser.parse_command = lambda text, controller=None: {"steps": steps}
    try: return executor.run_text_command(c, "do it")
    finally: command_parser.parse_command = orig
def byt(c):
    d = {}
    for o in c.objs: d.setdefault(o["objectType"], []).append(o)
    return d
tasks._say = lambda m: None

# ============================================================ 1. metrics
print("1. metrics: accuracy | time | steps | placed | failed to place")
c = MockController()
reply = executor.run_text_command(c, "pick up the apple")
line = reply.split("\n")[-1]; print("   ", line)
check("shows accuracy, time, steps", line.startswith("[metrics] accuracy 100% | time ") and "| steps " in line)
check("no placement fields when nothing had to be placed", "placed" not in line)
steps_n = int(line.split("steps ")[1].split()[0])
real = sum(1 for a, _ in c.log if a in ("MoveAhead", "RotateLeft", "RotateRight", "PickupObject") )
check("steps = successful walking/turning/manipulation actions", steps_n >= 5 and steps_n <= real, f"{steps_n} (log has {real})")
check("controller.step is restored after the run (no leaked wrapper)", not hasattr(c, "_mantra_orig_step") and c.step.__name__ == "step")
reply = executor.run_text_command(c, "put the apple on the dining table")
line = reply.split("\n")[-1]; print("   ", line)
check("a put/place command shows placed 1 | failed to place 0", "placed 1 | failed to place 0" in line, line)
c = MockController(); c.put_fail_types = {"DiningTable"}
config.FORCE_FALLBACK = False
reply = executor.run_text_command(c, "put the apple on the dining table"); line = reply.split("\n")[-1]; print("   ", line)
check("a failed placement is counted: placed 0 | failed to place 1, accuracy < 100%", "placed 0 | failed to place 1" in line and "accuracy 100%" not in line, line)
config.FORCE_FALLBACK = True
check("old clutter is gone", not any(w in line for w in ("walked", "pickups", "pacing", "planning", "route", "success")))

# ============================================================ 2. typos, floor, already done
print("2. typos / floor / already done")
ex = [mk("Sofa", (-1.9, .5, -1.5), receptacle=True), mk("Pillow", (-1.0, .65, 1.5), pickupable=True)]
c = MockController(extra_objects=ex)
check("'ppillow' resolves to Pillow", (scene_manager.find_object_id_by_name(c, "ppillow") or "").startswith("Pillow"))
check("'pilllow' resolves to Pillow", (scene_manager.find_object_id_by_name(c, "pilllow") or "").startswith("Pillow"))
check("a really missing object is still missing (banana)", scene_manager.find_object_id_by_name(c, "banana") is None)
reply = executor.run_text_command(c, "put the ppillow on the sofa")
check("typo command works end to end", "Task complete" in reply and ptypes(c, byt(c)["Pillow"][0]) == ["Sofa"], reply.split("\n")[0][:110])
c = MockController(extra_objects=[mk("Pillow", (-1.0, .65, 1.5), pickupable=True)])
reply = run_steps(c, [{"action": "place", "object": "Pillow", "target": "Floor"}])
pil = byt(c)["Pillow"][0]
check("'place the pillow on the floor' = pickup + drop (no 'Floor does not exist')", "Dropped it" in reply and "Task complete" in reply and not pil["parentReceptacles"] and c.held is None, reply.split("\n")[0][:110])
check("   and the drop counts as placed", "placed 1 | failed to place 0" in reply, reply.split("\n")[-1])
orig = command_parser.parse_command
command_parser.parse_command = lambda t, controller=None: {"steps": [], "reason": "Already done: the Pillow is on the Sofa"}
r = executor.run_text_command(c, "pillow on the sofa")
command_parser.parse_command = lambda t, controller=None: {"steps": [], "reason": "Not possible: there is no Banana"}
r2 = executor.run_text_command(c, "peel the banana")
command_parser.parse_command = orig
check("'already true' is reported as done, not as an error", r.startswith("Already done:") and "can't do that" not in r, r)
check("'not possible' keeps the can't-do message", r2.startswith("I can't do that here: there is no Banana"), r2)
check("'make a mess' is understood without the UI", command_parser.parse_command("make a mess") == {"steps": [{"action": "run_task", "task": "mess"}]})
check("'tidy' still means tidy", command_parser.parse_command("tidy the room")["steps"][0]["task"] == "tidy")

# ============================================================ 3. sandwich rules in code
print("3. sandwich: sliced pieces only, one plate, one countertop - enforced in code")
def kitchen(two_plates=False, preslice=False):
    extras = [mk("CounterTop", (1.9, .9, -1.3), receptacle=True),
              mk("Bread", (1.9, .95, .9), pickupable=True, sliceable=True),
              mk("Lettuce", (1.9, .95, 1.3), pickupable=True, sliceable=True),
              mk("Tomato", (1.9, .95, -1.2), pickupable=True, sliceable=True),
              mk("Knife", (1.9, .95, -.9), pickupable=True),
              mk("Plate", (-1.0, .65, 1.9), pickupable=True, receptacle=True)]
    if two_plates: extras.append(mk("Plate", (0.2, .65, 1.9), pickupable=True, receptacle=True))
    if preslice:
        extras += [mk("BreadSliced", (1.9, .95, .8), pickupable=True, isSliced=True), mk("BreadSliced", (1.9, .95, .7), pickupable=True, isSliced=True)]
    c = MockController(extra_objects=extras); b = byt(c)
    c._link(b["Plate"][0], b["SideTable"][0])
    if two_plates: c._link(b["Plate"][1], b["DiningTable"][0])
    for n in ("Bread", "Lettuce", "Knife"): c._link(b[n][0], b["CounterTop"][0])
    c._link(b["Tomato"][0], b["CounterTop"][1])
    for o in b.get("BreadSliced", []): c._link(o, b["CounterTop"][0])
    return c
FORGETFUL = []
for ing in ("Bread", "Lettuce", "Tomato"):               # the LLM forgot every slice step AND the plate step
    FORGETFUL += [{"action": "pickup", "object": ing}, {"action": "place", "object": ing, "target": "Plate"}]
c = kitchen()
reply = run_steps(c, FORGETFUL); print("    reply:", reply.split("\n")[0][:130])
plate = byt(c)["Plate"][0]; on_plate = [c._find(i) for i in plate["receptacleObjectIds"]]
check("a forgotten slice step is added: only sliced pieces on the plate", sorted(o["objectType"] for o in on_plate) == ["BreadSliced", "LettuceSliced", "TomatoSliced"], str(sorted(o["objectType"] for o in on_plate)))
check("a forgotten plate step is added: plate stands on a CounterTop", ptypes(c, plate) == ["CounterTop"], str(ptypes(c, plate)))
check("no whole Bread/Lettuce/Tomato anywhere near the plate", not any(o["objectType"] in ("Bread", "Lettuce", "Tomato") for o in on_plate))
check("task completed", "Task complete" in reply)
check("window in sync", c.screen == c._world_view())

c = kitchen(preslice=True)
whole_bread = byt(c)["Bread"][0]
reply = run_steps(c, [{"action": "slice", "object": "Bread", "tool": "Knife"}, {"action": "pickup", "object": "Bread"}, {"action": "place", "object": "Bread", "target": "Plate"},
                      {"action": "pickup", "object": "Bread"}, {"action": "place", "object": "Bread", "target": "Plate"}])
plate = byt(c)["Plate"][0]; on_plate = [c._find(i)["objectType"] for i in plate["receptacleObjectIds"]]
check("sliced pieces that already exist are used (two different slices)", on_plate == ["BreadSliced", "BreadSliced"], f"{on_plate} | {reply.split(chr(10))[0][:90]}")
check("the whole Bread was never touched", whole_bread["parentReceptacles"] == [byt(c)["CounterTop"][0]["objectId"]] and not any(a == "SliceObject" for a, _ in c.log))

c = kitchen(two_plates=True)
reply = run_steps(c, FORGETFUL)
plates = byt(c)["Plate"]
used = [p for p in plates if p["receptacleObjectIds"]]
check("ONE plate only: everything is on a single plate", len(used) == 1 and len(used[0]["receptacleObjectIds"]) == 3, str([len(p["receptacleObjectIds"]) for p in plates]))
check("plate + ingredients share one countertop", ptypes(c, used[0]) == ["CounterTop"] and len({tuple(c._find(i)["parentReceptacles"]) for i in used[0]["receptacleObjectIds"]}) == 1)
check("the plate was moved to a counter only once", sum(1 for a, kw in c.log if a == "PutObject" and kw.get("objectId", "").startswith("CounterTop")) == 1)

# a slice that slides off the plate afterwards is put back
c = kitchen()
orig_repair = executor._repair_dish
def knock_off(ctx, messages):
    pl = byt(c)["Plate"][0]; sl = c._find(pl["receptacleObjectIds"][-1])
    pl["receptacleObjectIds"].remove(sl["objectId"]); c._link(sl, byt(c)["CounterTop"][1])
    return orig_repair(ctx, messages)
executor._repair_dish = knock_off
reply = run_steps(c, FORGETFUL)
executor._repair_dish = orig_repair
plate = byt(c)["Plate"][0]
check("a slice that slid off the plate is picked up and put back", len(plate["receptacleObjectIds"]) == 3 and "back on the plate" in reply, reply.split("\n")[0][-90:])

# the planner function itself, with nothing to do
steps, info = task_planner.enforce_dish_rules([{"action": "pickup", "object": "Apple"}], c.last_event.metadata["objects"])
check("a plan with no dish is left untouched", steps == [{"action": "pickup", "object": "Apple"}] and not info["items"])

# ============================================================ 4. mess up: whole room, never the next receptacle
print("4. mess_up_room: random over the WHOLE room, never the receptacle next door")
config.MESS_FLOOR_CHANCE = 0.0
def mess_room():
    ex = [mk("CounterTop", (1.9, .9, .5), receptacle=True), mk("CounterTop", (1.9, .9, -.5), receptacle=True),
          mk("Sofa", (-1.9, .5, -1.5), receptacle=True), mk("Shelf", (-1.9, 1.2, 1.5), receptacle=True),
          mk("Desk", (-1.9, .7, 0.0), receptacle=True), mk("TVStand", (0.0, .4, -1.9), receptacle=True),
          mk("Dresser", (0.5, .8, 1.9), receptacle=True), mk("SideTable", (-1.0, .6, 1.2), receptacle=True)]
    c = MockController(extra_objects=ex); return c
near_hops = same_kind = total = 0; dests = set(); far_enough = 0
for seed in range(12):
    random.seed(seed); c = mess_room()
    before = {o["objectId"]: (list(o["parentReceptacles"] or []), dict(o["position"])) for o in c.objs if o["pickupable"]}
    tasks.mess_up_room(c, count=4)
    for oid, (par, pos) in before.items():
        o = c._find(oid)
        if o["parentReceptacles"] and o["parentReceptacles"] != par:
            dest = c._find(o["parentReceptacles"][0]); total += 1; dests.add(dest["objectId"])
            origin_types = {c._find(p)["objectType"] for p in par}
            same_kind += dest["objectType"] in origin_types
            d = math.hypot(dest["position"]["x"] - pos["x"], dest["position"]["z"] - pos["z"])
            far_enough += d >= config.MESS_MIN_DISTANCE
            near_hops += d < config.MESS_MIN_DISTANCE
print(f"    {total} objects moved over 12 runs: {len(dests)} different destinations, {same_kind} onto the same kind of surface, {near_hops} hops of < {config.MESS_MIN_DISTANCE} m")
check("objects really move", total >= 36, str(total))
check("an object never just moves to the same kind of receptacle it was on (CounterTop -> CounterTop)", same_kind == 0, str(same_kind))
check("no hop to a receptacle right beside it", near_hops == 0, str(near_hops))
check("destinations cover many different receptacles", len(dests) >= 6, str(len(dests)))
random.seed(3); c = mess_room(); obj = byt(c)["Apple"][0]
spots = tasks._wrong_spots(obj, c.objs)
check("far receptacles are offered before near ones", all(math.hypot(s["position"]["x"] - obj["position"]["x"], s["position"]["z"] - obj["position"]["z"]) >= config.MESS_MIN_DISTANCE for s in spots[:4]))
check("the receptacle beside it is only a last resort", tasks._wrong_spots(obj, c.objs)[-1]["objectType"] == "CounterTop")

config.MESS_FLOOR_CHANCE = 1.0
random.seed(1); c = mess_room(); c.objs.append(mk("Mug", (1.9, .95, .9), pickupable=True, breakable=True)); c._link(c.objs[-1], byt(c)["CounterTop"][0])
reply = tasks.mess_up_room(c, count=6)
mug = byt(c)["Mug"][0]
floor_items = [o for o in c.objs if o["pickupable"] and not o["parentReceptacles"]]
check("with floor chance 1.0 non-breakable objects are left on the floor", len(floor_items) >= 2 and "Floor" in reply, reply[:140])
check("a breakable Mug is never thrown on the floor (it ends on a surface)", bool(mug["parentReceptacles"]) and ptypes(c, mug)[0] != "CounterTop", str(ptypes(c, mug)))
check("hand empty at the end", c.held is None)
config.MESS_FLOOR_CHANCE = 0.0
random.seed(2); c = mess_room()
r = executor.run_text_command(c, "make a mess"); line = r.split("\n")[-1]; print("   ", line)
moved_n = int(r.split("moved ")[1].split(" ")[0])
check("'make a mess' via the normal pipeline ends with the simple metrics line", line.startswith("[metrics] accuracy") and f"placed {moved_n} | failed to place" in line, line)

# ============================================================ 5. blockage fallback
print("5. navigation: best path, then fallback when the way is blocked")
G = 0.25
wall = [(3, z) for z in range(-6, 7)]                        # a full wall at x = 0.75 m: everything beyond is a sealed pocket
c = MockController(block_cells=wall)
p0 = dict(c.agent["position"])
hybrid_navigator.walk_to_position(c, dict(x=1.5, z=0.0))
here = c.agent["position"]
check("goal in a sealed pocket: robot walks to the NEAREST reachable cell instead of standing still", here["x"] > 0.25 and here["x"] <= 0.5 + 1e-6, str(here))
check("   and never walked through the wall", c.blocked_hits == 0)
wall_gap = [(3, z) for z in range(-6, 7) if z != 6]           # wall with one gap far away: best path goes around it
c = MockController(block_cells=wall_gap)
hybrid_navigator.walk_to_position(c, dict(x=1.25, z=0.0))
here = c.agent["position"]
check("with a gap in the wall the robot takes the detour and arrives", abs(here["x"] - 1.25) < 0.3 and abs(here["z"]) < 0.3, str(here))
c = MockController(block_cells=[(2, 0), (2, 1), (2, -1)], block_reported=True)   # simulator lists blocked cells as walkable
hybrid_navigator.walk_to_position(c, dict(x=1.0, z=0.0))
here = c.agent["position"]
check("blocked cells wrongly listed as walkable: sidestep + re-plan still gets there", abs(here["x"] - 1.0) < 0.3 and abs(here["z"]) < 0.55, str(here))

# ============================================================ 6. prompt
print("6. prompt")
pr = command_parser._build_system_prompt("SCENE")
for label, phrase in [("floor is not an object", "The Floor is NOT an object"), ("floor = pickup then drop", "pickup, then drop"),
                      ("typo repair", "'ppillow'"), ("run-together words", "'pillowon the sofa'"),
                      ("already true -> 'Already done:'", '"Already done:"'), ("impossible -> 'Not possible:'", '"Not possible:"'),
                      ("clearing a surface moves items, never drops", "CLEARING"), ("fixed movie-night recipe", "movie night = Television ON"),
                      ("same recipe for any genre", "horror movie"), ("sliced only", "ONLY SLICED pieces"), ("one work surface", "ONE WORK SURFACE"),
                      ("plate placed first, ingredients on plate", "target Plate"), ("transform / cooking kept", "OBJECTS TRANSFORM")]:
    check(label, phrase in pr)
check("prompt embeds the live scene", "CURRENT SCENE" in command_parser._build_system_prompt("DiningTable x1") and "DiningTable x1" in command_parser._build_system_prompt("DiningTable x1"))

print("\nFAILED: %s" % fails if fails else "\nALL PASSED"); sys.exit(1 if fails else 0)
