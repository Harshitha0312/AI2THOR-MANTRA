"""Nlp16: (1) sandwich prompt/flow - sliced pieces only, one work surface;
(2) mess_up_room - random objects, random destinations over the WHOLE room, spread out."""
import sys, os, random, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0; config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
config.METRICS_LOG = None
config.MESS_FLOOR_CHANCE = 0.0   # surface-spread checks below; floor drops are tested separately
from mock_thor import MockController
from robot import tasks
import executor
from nlp import command_parser

fails = []
def check(n, cond, info=""):
    print(("  PASS " if cond else "  FAIL ") + n + (f"  [{info}]" if info else ""))
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
def ptype(c, o): return [c._find(p)["objectType"] for p in (o["parentReceptacles"] or [])]
def run_steps(c, steps):
    orig = command_parser.parse_command
    command_parser.parse_command = lambda text, controller=None: {"steps": steps}
    try: return executor.run_text_command(c, "do it")
    finally: command_parser.parse_command = orig

# ---------------------------------------------------------------- 1. prompt
print("1. sandwich prompt")
prompt = command_parser._build_system_prompt("Bread [sliceable]")
check("rule: only SLICED pieces go in the dish", "ONLY SLICED pieces" in prompt and "never the whole" in prompt)
check("rule: every [sliceable] ingredient sliced before pickup", "slice it first" in prompt and "THEN pickup" in prompt)
check("rule: reuse sliced pieces already in the scene", "BreadSliced" in prompt)
check("rule: plate + all ingredients on ONE countertop", "ONE WORK SURFACE" in prompt and "ONE countertop" in prompt)
check("rule: ingredients target the Plate, not counters", "target Plate" in prompt and "never directly on a CounterTop" in prompt)
check("sandwich example places Plate on a counter first and slices before pickup",
      prompt.index('"action": "place", "object": "Plate", "target": "CounterTop"') < prompt.index('"action": "slice", "object": "Bread"') <
      prompt.index('"action": "pickup", "object": "Bread"'))

# ---------------------------------------------------------------- 2. sandwich flow
print("2. sandwich flow on the mock simulator (second whole tomato lies around as a trap)")
extras = [mk("CounterTop", (1.9, .9, -1.3), receptacle=True),
          mk("Bread", (1.9, .95, .9), pickupable=True, sliceable=True),
          mk("Lettuce", (1.9, .95, 1.3), pickupable=True, sliceable=True),
          mk("Tomato", (1.9, .95, -1.2), pickupable=True, sliceable=True),
          mk("Tomato", (1.9, .95, -1.5), pickupable=True, sliceable=True),
          mk("Knife", (1.9, .95, -.9), pickupable=True),
          mk("Plate", (-1.0, .65, 1.9), pickupable=True, receptacle=True)]
c = MockController(extra_objects=extras)
by = {}
for o in c.objs: by.setdefault(o["objectType"], []).append(o)
c._link(by["Plate"][0], by["SideTable"][0])
c._link(by["Bread"][0], by["CounterTop"][0]); c._link(by["Lettuce"][0], by["CounterTop"][0])
c._link(by["Tomato"][0], by["CounterTop"][1]); c._link(by["Tomato"][1], by["CounterTop"][1]); c._link(by["Knife"][0], by["CounterTop"][0])
SANDWICH = [{"action": "place", "object": "Plate", "target": "CounterTop"}]
for ing in ("Bread", "Lettuce", "Tomato"):
    SANDWICH += [{"action": "slice", "object": ing, "tool": "Knife"}, {"action": "pickup", "object": ing},
                 {"action": "place", "object": ing, "target": "Plate"}]
reply = run_steps(c, SANDWICH); print("   reply:", reply.split("\n")[0][:160])
plate = [o for o in c.objs if o["objectType"] == "Plate"][0]
on_plate = [c._find(i) for i in plate["receptacleObjectIds"]]
types = sorted(o["objectType"] for o in on_plate)
check("task completed", "Task complete" in reply, reply[-120:])
check("plate was put on a countertop", ptype(c, plate) == ["CounterTop"], str(ptype(c, plate)))
check("ONLY sliced pieces are on the plate", types == ["BreadSliced", "LettuceSliced", "TomatoSliced"], str(types))
check("no whole bread/lettuce/tomato went onto the plate", not any(not o["isSliced"] for o in on_plate))
whole_tomato = [o for o in c.objs if o["objectType"] == "Tomato"]
check("the untouched second tomato stayed put", len(whole_tomato) == 1 and ptype(c, whole_tomato[0]) == ["CounterTop"])
check("plate and everything on it share ONE countertop", len({tuple(o["parentReceptacles"] or []) for o in on_plate}) == 1 and
      all(o["parentReceptacles"] == [plate["objectId"]] for o in on_plate))
check("window in sync at the end", c.screen == c._world_view())

# ---------------------------------------------------------------- 3. mess up the room
print("3. mess_up_room - random objects, whole-room random destinations, spread out")
config.MESS_SELECTION = "random"
def rooms():
    ex = [mk("Sofa", (-1.9, .5, -1.5), receptacle=True), mk("Shelf", (1.9, 1.2, 1.5), receptacle=True),
          mk("Desk", (-1.9, .7, 1.5), receptacle=True), mk("TVStand", (0.0, .4, -1.2), receptacle=True),
          mk("Dresser", (1.0, .8, -1.9), receptacle=True),
          mk("Mug", (1.9, .95, .45), pickupable=True), mk("Bowl", (1.9, .95, -.35), pickupable=True),
          mk("Cup", (0.3, .75, 1.9), pickupable=True)]
    c = MockController(extra_objects=ex)
    t = {}
    for o in c.objs: t.setdefault(o["objectType"], o)
    c._link(t["Mug"], t["CounterTop"]); c._link(t["Bowl"], t["CounterTop"]); c._link(t["Cup"], t["DiningTable"])
    return c
tasks_msgs = []
tasks._say = lambda m: tasks_msgs.append(m)

dest_ids, runs_ok, spread_ok, teleports, synced, far_dest = set(), 0, 0, 0, True, 0
total_moved = 0
for seed in range(10):
    random.seed(seed); c = rooms()
    before = {o["objectId"]: tuple(o["parentReceptacles"] or []) for o in c.objs if o["pickupable"]}
    reply = tasks.mess_up_room(c, count=4)
    after = {o["objectId"]: tuple(o["parentReceptacles"] or []) for o in c.objs if o["pickupable"]}
    moved = [i for i in before if before[i] != after.get(i)]
    total_moved += len(moved)
    runs_ok += "Messed up the room" in reply
    new_types = [c._find(after[i][0])["objectType"] for i in moved if after[i]]
    dest_ids |= {after[i][0] for i in moved if after[i]}
    spread_ok += len(set(new_types)) >= min(3, len(moved)) and len(moved) >= 3
    teleports += c.teleports_moving
    synced &= (c.screen == c._world_view())
    # how many landed on a surface that is NOT among the 3 closest ones to where the object started (old behaviour)
    for i in moved:
        if not after[i]: continue
        o = c._find(i); start = c._find(before[i][0])["position"] if before[i] else o["position"]
        cands = sorted([x for x in c.objs if x.get("receptacle") and x["objectType"] in tasks.MESSY_SPOT_TYPES],
                       key=lambda x: (x["position"]["x"] - start["x"]) ** 2 + (x["position"]["z"] - start["z"]) ** 2)
        far_dest += c._find(after[i][0]) not in cands[:3]
print(f"   10 runs: {runs_ok} reported success, {total_moved} objects moved, {len(dest_ids)} distinct destination surfaces, {far_dest} landed beyond the 3 nearest")
check("mess-up runs complete", runs_ok >= 9, str(runs_ok))
check("objects really move (>= 3 per run on average)", total_moved >= 30, str(total_moved))
check("destinations are spread over the room (many different surfaces overall)", len(dest_ids) >= 6, str(len(dest_ids)))
check("many objects land BEYOND the nearest few surfaces", far_dest >= 8, str(far_dest))
check("within one run the mess uses 3+ different kinds of surface", spread_ok >= 7, str(spread_ok))
check("no teleport changed the position", teleports == 0)
check("window in sync after every run", synced)

# default selection is now random, destination ordering has no distance bias
check("config default MESS_SELECTION is random", open(os.path.join(os.path.dirname(HERE), "config.py")).read().count('MESS_SELECTION = "random"') == 1)
c = rooms(); obj = next(o for o in c.objs if o["objectType"] == "Mug")
def d2(x): return (x["position"]["x"] - obj["position"]["x"]) ** 2 + (x["position"]["z"] - obj["position"]["z"]) ** 2
eligible = [x for x in c.objs if x.get("receptacle") and x["objectType"] in tasks.MESSY_SPOT_TYPES and x["objectType"] != "CounterTop"]
nearest3 = {x["objectId"] for x in sorted(eligible, key=d2)[:3]}
firsts, outside = [], 0
for s in range(60):
    random.seed(s); first = tasks._wrong_spots(obj, c.objs)[0]
    firsts.append(first["objectId"]); outside += first["objectId"] not in nearest3
print(f"   first-choice destination over 60 draws: {len(set(firsts))} different surfaces, {outside} outside the 3 nearest")
check("first-choice destination is often NOT one of the 3 nearest surfaces (old code: never)", outside >= 15, str(outside))
check("first-choice destination varies across many surfaces", len(set(firsts)) >= 5, str(len(set(firsts))))

# spread: with usage counters, surfaces/types already used are pushed to the back
used = (tasks.Counter(), tasks.Counter())
random.seed(5); first = tasks._wrong_spots(obj, c.objs, used)[0]
used[0][first["objectId"]] += 1; used[1][first["objectType"]] += 1
check("an already-used surface type is not offered first again", all(tasks._wrong_spots(obj, c.objs, used)[0]["objectType"] != first["objectType"] for _ in range(30)))

print("\nFAILED: %s" % fails if fails else "\nALL PASSED"); sys.exit(1 if fails else 0)
