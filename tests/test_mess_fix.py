import sys, os, random
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0; config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
config.METRICS_LOG = None
from mock_thor import MockController
from robot import control, tasks, hybrid_navigator, manipulation
import executor
from nlp import command_parser

fails = []
def check(n, cond, info=""):
    print(("  PASS " if cond else "  FAIL ") + n + (f"  [{info}]" if info else ""))
    if not cond: fails.append(n)
def O(c, t, i=0): return [o for o in c.objs if o["objectType"] == t][i]
def parents(c, o): return [c._find(p)["objectType"] for p in (o["parentReceptacles"] or [])]
def run_steps(c, steps):
    orig = command_parser.parse_command
    command_parser.parse_command = lambda text, controller=None: {"steps": steps}
    try: return executor.run_text_command(c, "do it")
    finally: command_parser.parse_command = orig
def pos(c): return (c.agent["position"]["x"], c.agent["position"]["z"])

print("A. blocked path while HOLDING an object (obstacle not in the stale graph)")
for reported in (False, True):
    # a wall of blocked cells across the straight route from (0,0) to (1.5, 0)
    wall = [(4, z) for z in range(-1, 2)]            # x = 1.0, z = -0.25..0.25
    c = MockController(block_cells=wall, block_reported=reported)
    c.held = O(c, "Apple")["objectId"]
    hybrid_navigator.walk_to_position(c, dict(x=1.5, z=0.0), reachable_positions=c.reachable())
    d = ((pos(c)[0] - 1.5) ** 2 + pos(c)[1] ** 2) ** .5
    sidesteps = sum(1 for a, _ in c.log if a in ("MoveLeft", "MoveRight"))
    check(f"[obstacle listed as reachable={reported}] arrived next to the goal", d < 0.3, f"dist {d:.2f}, hits {c.blocked_hits}")
    check(f"[obstacle listed as reachable={reported}] still holding the object", c.held is not None)
    check(f"[obstacle listed as reachable={reported}] hit the obstacle then recovered (not stuck)", c.blocked_hits >= 1 or not reported)
    if reported: check("robot sidestepped left/right when blocked", sidesteps >= 1, str(sidesteps))

print("B. walk that can never succeed ends cleanly (no infinite loop)")
box = [(x, z) for x in range(2, 5) for z in range(-6, 7)]   # full wall x=0.5..1.0
c = MockController(block_cells=box, block_reported=True)
hybrid_navigator.walk_to_position(c, dict(x=1.5, z=0.0), reachable_positions=c.reachable())
check("gave up after a bounded number of tries", sum(1 for a, _ in c.log if a == "MoveAhead") < 400)

print("C. mess up the room moves SEVERAL objects (was: one)")
random.seed(5)
c = MockController()
msgs = []; control.set_progress_callback(msgs.append)
reply = tasks.mess_up_room(c)
print("   reply:", reply)
n_moved = reply.split("moved ")[1].split(" ")[0] if "moved " in reply else "0"
check("moved more than one object", int(n_moved) >= 3, n_moved)
check("did not stop early", "Stopped early" not in reply)
check("hand empty at end", c.held is None)
check("no teleport changed position", c.teleports_moving == 0)
check("window in sync at end", c.screen == c._world_view())

print("C2. mess up with an obstacle in the way still moves several objects")
random.seed(5)
c = MockController(block_cells=[(4, z) for z in range(-2, 3)], block_reported=True)
reply = tasks.mess_up_room(c); print("   reply:", reply)
check("moved several despite obstacle", int(reply.split("moved ")[1].split(" ")[0]) >= 3 if "moved " in reply else False)

print("C3. one object that can't be picked up doesn't end the run")
random.seed(5)
c = MockController(fail_pickup_first=0)
config.FORCE_FALLBACK = False
c.pickup_calls = 0
orig_step = c.step
def flaky(action=None, **kw):
    if action == "PickupObject" and "Book" in kw.get("objectId", ""):
        c.log.append((action, kw)); ev = c.last_event; ev.metadata["lastActionSuccess"] = False; ev.metadata["errorMessage"] = "nope"; return ev
    return orig_step(action, **kw)
c.step = flaky
reply = tasks.mess_up_room(c); print("   reply:", reply)
config.FORCE_FALLBACK = True
check("skipped the stuck object but moved others", "Skipped" in reply and "moved" in reply and "Book" in reply.split("Skipped")[1])

print("D. omelette: egg goes INTO the pan and is cracked there (not on the floor)")
OMELETTE = [
    {"action": "pickup", "object": "Pan"},
    {"action": "place", "object": "Pan", "target": "StoveBurner"},
    {"action": "pickup", "object": "Egg"},
    {"action": "break", "object": "Egg"},
    {"action": "place", "object": "Egg", "target": "Pan"},
    {"action": "toggle_on", "object": "StoveKnob"},
]
for mode in ("hand", "floor"):
    c = MockController(break_mode=mode)
    reply = run_steps(c, OMELETTE)
    brk = [i for i, (a, _) in enumerate(c.log) if a == "BreakObject"]
    egg = [o for o in c.objs if o["objectType"] == "EggCracked"]
    puts = [i for i, (a, kw) in enumerate(c.log) if a == "PutObject" and "Pan" in kw["objectId"]]
    check(f"[{mode}] task complete", "Task complete" in reply, reply.split("\n")[0][:80])
    check(f"[{mode}] egg was put in the pan BEFORE it was cracked", brk and puts and puts[0] < brk[0])
    check(f"[{mode}] cracked egg is in the pan", egg and parents(c, egg[0]) == ["Pan"], str([parents(c, e) for e in egg]))
    check(f"[{mode}] hand empty / burner on", c.held is None and any(o["isToggled"] for o in c.objs if o["objectType"] == "StoveKnob"))

print("E. fridge is closed again after taking something out")
for label, pre_open, fail_first in (("closed fridge", False, 0), ("fridge already open", True, 0), ("first close attempt fails", False, 1)):
    c = MockController(close_fail_first=fail_first)
    fr = O(c, "Fridge"); fr["isOpen"] = pre_open
    run_steps(c, [{"action": "pickup", "object": "Egg"}])
    check(f"[{label}] egg in hand", c.held is not None and "Egg" in c.held)
    check(f"[{label}] fridge closed afterwards", not fr["isOpen"], f"close calls {c.close_calls}")
    check(f"[{label}] window in sync", c.screen == c._world_view())


print("F. placing: never act from far away (nearest standing spot is an unreachable pocket)")
import math
island = [(5, -2), (6, -1), (6, -3)]          # walls the cell (6,-2) off - it is still in the walk list
c = MockController(block_cells=island, block_reported=False)
c.agent["position"] = dict(x=-1.0, y=0.9, z=1.0)
reply = run_steps(c, [{"action": "place", "object": "Apple", "target": "Pan"}])
print("   reply:", reply.split("\n")[0][:110])
apple = O(c, "Apple")
check("apple ended up in the pan", parents(c, apple) == ["Pan"], str(parents(c, apple)))
pan = O(c, "Pan")
far_puts = []
for i, (a, kw) in enumerate(c.log):
    if a == "PutObject" and "Pan" in kw["objectId"]:
        # robot position at that moment = replay is overkill; check final-state distance of the successful put
        far_puts.append(kw.get("forceAction"))
check("no forced put needed", not any(far_puts), str(far_puts))
check("robot stood within reach of the pan", math.hypot(pan["position"]["x"] - c.agent["position"]["x"], pan["position"]["z"] - c.agent["position"]["z"]) <= 1.5)
n_puts = sum(1 for a, _ in c.log if a == "PutObject")
check("one clean PutObject, not a barrage from far away", n_puts <= 2, str(n_puts))

print("\nFAILED: %s" % fails if fails else "\nALL PASSED"); sys.exit(1 if fails else 0)
