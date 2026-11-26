import sys; import os; HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0; config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
from mock_thor import MockController
from robot import control, tasks, hybrid_navigator
import random

fails = []
def check(n, cond, info=""):
    print(("  PASS " if cond else "  FAIL ") + n + (f"  [{info}]" if info else ""))
    if not cond: fails.append(n)
def where(c): return {o["objectType"]: [c._find(p)["objectType"] for p in (o["parentReceptacles"] or [])] for o in c.objs if o["pickupable"]}

print("T5 tidy the room")
msgs = []
control.set_progress_callback(msgs.append)
runs = []
for r in range(2):
    c = MockController()
    # make a mess first so there is work to do
    reply = tasks.tidy_room(c)
    runs.append(([m for m in msgs if m[:1] == "(" ], where(c), c))
    msgs.clear()
print("   reply:", reply)
order1 = [m.split("Walking to the ")[1] for m in runs[0][0]]; order2 = [m.split("Walking to the ")[1] for m in runs[1][0]]
print("   visiting order run1:", order1, " run2:", order2)
c = runs[1][2]
check("route order identical for identical scene (deterministic)", order1 == order2)
check("window in sync at the end", c.screen == c._world_view())
check("no teleport changed the agent's position (real A* only)", c.teleports_moving == 0, str(c.teleports_moving))
check("hand empty at end", c.held is None)
print("   final placement:", runs[1][1])
walks = sum(1 for a, _ in c.log if a == "MoveAhead")
check("robot physically walked (MoveAhead steps)", walks > 10, str(walks))
check("fresh GetReachablePositions during execution (not one stale snapshot)", sum(1 for a, _ in c.log if a == "GetReachablePositions") > 2)

print("T6 mess up the room")
for mode in ("nearest", "random"):
    config.MESS_SELECTION = mode
    random.seed(3)
    msgs.clear()
    c = MockController()
    # tidy items first so everything starts on its home surface
    before = where(c)
    reply = tasks.mess_up_room(c, count=3)
    print(f"   [{mode}] reply:", reply)
    check(f"[{mode}] no teleport changed position", c.teleports_moving == 0)
    check(f"[{mode}] window in sync at end", c.screen == c._world_view())
    check(f"[{mode}] walked between objects", sum(1 for a, _ in c.log if a == "MoveAhead") > 5)
    check(f"[{mode}] every pickup came after MoveAhead steps (never pickup-at-spawn)", all(
        any(x[0] == "MoveAhead" for x in c.log[:i]) for i, (a, _) in enumerate(c.log) if a == "PickupObject"))
config.MESS_SELECTION = "random"
orders = set()
for seed in range(8):
    random.seed(seed); c = MockController(); tasks.mess_up_room(c, count=2)
    orders.add(tuple(a_kw[1]["objectId"] for a_kw in c.log if a_kw[0] == "PickupObject" and not a_kw[1].get("forceAction")))
check("random mode really varies selection order across seeds", len(orders) > 1, str(len(orders)))
print("\nFAILED: %s" % fails if fails else "\nALL PASSED"); sys.exit(1 if fails else 0)
