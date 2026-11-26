"""put away the groceries: (1) ends right after the last grocery is in the fridge - no more room sweeps;
(2) the Bread goes INTO the fridge (ladder of shelves / variants), never moved from counter to counter."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0; config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
config.METRICS_LOG = None
from mock_thor import MockController
from robot import tasks
import executor

fails = []
def check(n, cond, info=""):
    print(("  PASS " if cond else "  FAIL ") + n + (f"  [{info}]" if info else ""))
    if not cond: fails.append(n)
def mk(t, pos, **kw):
    d = dict(objectType=t, objectId=f"{t}|{pos[0]:+.2f}|{pos[1]:+.2f}|{pos[2]:+.2f}", position=dict(x=pos[0], y=pos[1], z=pos[2]),
             pickupable=False, receptacle=False, toggleable=False, isToggled=False, openable=False, isOpen=False,
             parentReceptacles=None, receptacleObjectIds=[], controlledObjects=None, canFillWithLiquid=False, isFilledWithLiquid=False,
             fillLiquid=None, sliceable=False, isSliced=False, breakable=False, isBroken=False, dirtyable=False, isDirty=False, isUsedUp=False)
    d.update(kw); return d
def O(c, t, i=0): return [o for o in c.objs if o["objectType"] == t][i]
def parents(c, o): return [c._find(p)["objectType"] for p in (o["parentReceptacles"] or [])]
def in_fridge(c, o):
    seen, front = set(), list(o["parentReceptacles"] or [])
    while front:
        pid = front.pop()
        if pid in seen: continue
        seen.add(pid); par = c._find(pid)
        if par["objectType"] == "Fridge": return True
        front.extend(par["parentReceptacles"] or [])
    return False

def scene(bread_on="SideTable", **kw):
    extras = [mk("CounterTop", (1.9, .9, -1.3), receptacle=True),
              mk("Bread", (1.9, .95, 1.2), pickupable=True, sliceable=True),
              mk("Tomato", (0.0, .75, 1.9 - .0), pickupable=True, sliceable=True),
              mk("Shelf", (0.0, .4, -1.9), receptacle=True), mk("Shelf", (0.0, .9, -1.9), receptacle=True)]
    c = MockController(extra_objects=extras, **kw)
    by = {}
    for o in c.objs: by.setdefault(o["objectType"], []).append(o)
    fridge = by["Fridge"][0]
    for sh in by["Shelf"]: c._link(sh, fridge)
    c._link(by["Egg"][0], by["Shelf"][1]); fridge["receptacleObjectIds"].remove(by["Egg"][0]["objectId"])   # the egg is already on a fridge shelf
    c._link(by["Bread"][0], by[bread_on][0]); c._link(by["Tomato"][0], by["DiningTable"][0])
    for t in ("Apple", "Potato"):                     # the mock's default apple/potato are already put away on a fridge shelf
        o = by[t][0]; c._find(o["parentReceptacles"][0])["receptacleObjectIds"].remove(o["objectId"]); c._link(o, by["Shelf"][0])
    return c

print("1. stops after the last grocery - no sweeping")
c = scene()
steps_log = []
reply = executor.run_text_command(c, "put away the groceries")
print("   ", reply.split("\n")[0][:150])
bread, tomato, egg = O(c, "Bread"), O(c, "Tomato"), O(c, "Egg")
check("bread and tomato are in the fridge", in_fridge(c, bread) and in_fridge(c, tomato), f"{parents(c, bread)} {parents(c, tomato)}")
check("the egg that was already in the fridge was left alone", egg["parentReceptacles"] and c._find(egg["parentReceptacles"][0])["objectType"] == "Shelf")
check("reply says 2 of 2 and no failures", "2 of 2" in reply and "Could not" not in reply, reply.split("\n")[0][:110])
check("hand empty, fridge closed", c.held is None and not O(c, "Fridge")["isOpen"])
rot = sum(1 for a, _ in c.log if a in ("RotateRight", "RotateLeft"))
last_put = max(i for i, (a, _) in enumerate(c.log) if a == "PutObject")
after = [a for a, _ in c.log[last_put + 1:]]
check("nothing but closing the fridge happens after the last PutObject (no wandering)", all(a in ("CloseObject", "Pass", "GetReachablePositions", "RotateRight", "RotateLeft", "Teleport") for a in after) and sum(1 for a in after if a in ("MoveAhead", "MoveBack", "MoveLeft", "MoveRight")) == 0, str(after[:12]))
walks = sum(1 for a, _ in c.log if a in ("MoveAhead", "MoveBack", "MoveLeft", "MoveRight"))
print("    walk steps total:", walks)
import subprocess, json
check("walks only what the 2 groceries need (the old 25-checkpoint sweep walked several times more)", walks < 140, f"{walks} walk steps")
check("ends with the summary + metrics line", "[metrics] accuracy 100%" in reply, reply.split("\n")[-1][:60])

print("1b. nothing out -> finishes at once")
c = scene(); 
for t in ("Bread", "Tomato"):
    o = O(c, t); c._find(o["parentReceptacles"][0])["receptacleObjectIds"].remove(o["objectId"]); c._link(o, O(c, "Fridge"))
n0 = len(c.log); reply = executor.run_text_command(c, "put away the groceries")
moves = [a for a, _ in c.log[n0:] if a in ("MoveAhead", "RotateRight", "Teleport", "PickupObject")]
check("says nothing is out and does not move", "no groceries out" in reply and not moves, reply.split("\n")[0][:80] + str(moves[:4]))
c = scene(); bread = O(c, "Bread"); c._find(bread["parentReceptacles"][0])["receptacleObjectIds"].remove(bread["objectId"])
cab = mk("Cabinet", (-1.0, .9, -1.9), receptacle=True, openable=True); c.objs.append(cab); c._link(bread, cab)
reply = executor.run_text_command(c, "put away the groceries")
check("groceries already inside a closed cabinet count as put away (left alone)", parents(c, bread) == ["Cabinet"] and "2 of 2" not in reply, reply.split("\n")[0][:100])

print("2. bread that is NOT on a countertop goes INTO the fridge")
for fails_n, label in ((0, "bread fits at once"), (2, "refused twice, fits on a later variant"), (6, "refused on the planned shelf and the next shelf, fits later"), (14, "refused almost everywhere")):
    c = scene(bulky_fail_seeds=fails_n)
    reply = executor.run_text_command(c, "put away the groceries")
    bread = O(c, "Bread")
    check(f"[{label}] bread ends INSIDE the fridge", in_fridge(c, bread), f"{parents(c, bread)} | {reply.split(chr(10))[0][:90]}")
    check(f"[{label}] never dropped on a counter/floor on the way", not any(a in ("DropHandObject",) for a, _ in c.log) and c.held is None)
c = scene(bulky_fail_seeds=999)
reply = executor.run_text_command(c, "put away the groceries")
bread = O(c, "Bread")
check("if it really cannot fit: says so honestly (not 100%)", "Could not put away: Bread" in reply and "1 of 2" in reply and "accuracy 100%" not in reply, reply[:200])
check("   and the other item still went in", in_fridge(c, O(c, "Tomato")))
check("   hand empty, fridge closed, task still ends", c.held is None and not O(c, "Fridge")["isOpen"])

print("3. bread lying on a COUNTERTOP is ignored - never touched")
c = scene(bread_on="CounterTop")
bread = O(c, "Bread")
reply = executor.run_text_command(c, "put away the groceries")
picked = [kw.get("objectId", "") for a, kw in c.log if a == "PickupObject"]
check("the bread stayed on the countertop", parents(c, bread) == ["CounterTop"], str(parents(c, bread)))
check("the bread was never picked up", not any("Bread" in i for i in picked), str(picked))
check("nothing was dropped", not any(a == "DropHandObject" for a, _ in c.log))
check("the tomato still went into the fridge", in_fridge(c, O(c, "Tomato")))
check("summary: 1 of 1 and says the bread was left alone", "1 of 1" in reply and "Left alone" in reply and "Bread" in reply, reply.split(chr(10))[0][:140])
check("accuracy counts only the groceries that needed moving (100%)", "accuracy 100%" in reply and "placed 1 | failed to place 0" in reply, reply.split(chr(10))[-1])
c = scene(bread_on="CounterTop")
for t in ("Tomato",):
    o = O(c, t); c._find(o["parentReceptacles"][0])["receptacleObjectIds"].remove(o["objectId"]); c._link(o, O(c, "Fridge"))
n0 = len(c.log); reply = executor.run_text_command(c, "put away the groceries")
moves = [a for a, _ in c.log[n0:] if a in ("MoveAhead", "RotateRight", "PickupObject")]
check("only bread on the counter -> nothing to do, robot does not move", "no groceries out" in reply and not moves, reply.split(chr(10))[0][:120])

print("3b. a grocery that will not fit in the fridge goes back on a COUNTERTOP, not the floor")
c = scene(bulky_fail_seeds=999)
reply = executor.run_text_command(c, "put away the groceries")
bread = O(c, "Bread")
check("bread ended on a countertop", parents(c, bread) == ["CounterTop"], str(parents(c, bread)))
check("never dropped on the floor", not any(a == "DropHandObject" for a, _ in c.log))
check("hand empty and fridge closed", c.held is None and not O(c, "Fridge")["isOpen"])
check("failure is reported: placed 1 | failed to place 1", "placed 1 | failed to place 1" in reply, reply.split(chr(10))[-1])

print("\nFAILED: %s" % fails if fails else "\nALL PASSED"); sys.exit(1 if fails else 0)
