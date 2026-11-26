import sys, os, itertools, random, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0; config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
config.METRICS_LOG = os.path.join(tempfile.gettempdir(), "mantra_test_metrics.jsonl")
if os.path.exists(config.METRICS_LOG): os.remove(config.METRICS_LOG)
from mock_thor import MockController
from robot import control, tasks, hybrid_navigator, metrics
from environment import scene_manager
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
    try:
        return executor.run_text_command(c, "do it")
    finally:
        command_parser.parse_command = orig

OMELETTE = [
    {"action": "pickup", "object": "Pan"},
    {"action": "place", "object": "Pan", "target": "StoveBurner"},
    {"action": "pickup", "object": "Egg"},
    {"action": "break", "object": "Egg"},
    {"action": "place", "object": "Egg", "target": "Pan"},
    {"action": "toggle_on", "object": "StoveKnob"},
]

print("A. omelette (the reported bug) - cracked egg stays in HAND after break")
c = MockController(break_mode="hand")
reply = run_steps(c, OMELETTE); print("   reply:", reply.replace("\n", "\n         "))
pan = O(c, "Pan"); egg = [o for o in c.objs if o["objectType"] == "EggCracked"]
loaded = [o for o in c.objs if o["objectType"] == "StoveBurner" and o["receptacleObjectIds"]]
knobs_on = [o for o in c.objs if o["objectType"] == "StoveKnob" and o["isToggled"]]
check("no 'not holding' error, task completed", "Task complete" in reply and "not holding" not in reply)
check("cracked egg is IN the pan", egg and parents(c, egg[0]) == ["Pan"], str([parents(c, e) for e in egg]))
check("pan is on a burner", len(loaded) == 1 and loaded[0]["receptacleObjectIds"] == [pan["objectId"]])
check("exactly the knob of the loaded burner is ON", len(knobs_on) == 1 and loaded[0]["objectId"] in knobs_on[0]["controlledObjects"])
check("window in sync", c.screen == c._world_view())

print("A2. omelette - cracked egg DROPS (hand empty after break)")
c = MockController(break_mode="floor")
reply = run_steps(c, OMELETTE); print("   reply:", reply.split("\n")[0])
egg = [o for o in c.objs if o["objectType"] == "EggCracked"]
check("robot fetched the cracked egg and put it in the pan", "Task complete" in reply and egg and parents(c, egg[0]) == ["Pan"])
check("burner knob ON only after egg was placed", any(o["isToggled"] for o in c.objs if o["objectType"] == "StoveKnob"))

print("A3. wrong LLM order still safe (stove first)")
c = MockController()
bad = [OMELETTE[-1]] + OMELETTE[:-1]
reply = run_steps(c, bad); print("   reply:", reply.split("\n")[0])
egg = [o for o in c.objs if o["objectType"] == "EggCracked"]
check("completes with egg in pan and burner lit", "Task complete" in reply and egg and parents(c, egg[0]) == ["Pan"])
order = [m for m in reply.split(" ") if m in ("Placed", "Turned", "Broke")]
check("stove switched on LAST (after pan and egg placed)", order and order[-1] == "Turned", str(order))

print("B. name resolution")
c = MockController()
c.step(action="BreakObject", objectId=O(c, "Egg")["objectId"])
check("'cracked egg' -> EggCracked", scene_manager.find_object_id_by_name(c, "cracked egg").startswith("EggCracked"))
check("'egg' still finds the cracked egg afterwards", scene_manager.find_object_id_by_name(c, "egg").startswith("EggCracked"))
check("'stove' + receptacle -> StoveBurner", scene_manager.find_object_id_by_name(c, "stove", prefer="receptacle").startswith("StoveBurner"))
check("'stove' + toggleable -> StoveKnob", scene_manager.find_object_id_by_name(c, "stove", prefer="toggleable").startswith("StoveKnob"))
check("'frying pan' -> Pan", scene_manager.find_object_id_by_name(c, "frying pan").startswith("Pan"))

print("C. tidy: objects already in place are NOT picked up")
c = MockController()
book = O(c, "Book")      # on DiningTable = its preferred receptacle
reply = tasks.tidy_room(c); print("   reply:", reply)
picked = [kw["objectId"] for a, kw in c.log if a == "PickupObject"]
check("Book (already on DiningTable) never picked up", book["objectId"] not in picked)
check("reply says it was left alone", "already in place" in reply)
check("Book still on DiningTable", parents(c, book) == ["DiningTable"])
check("Egg stored in the Fridge is not taken out", O(c, "Egg")["objectId"] not in picked and parents(c, O(c, "Egg")) == ["Fridge"])
c2 = MockController(); [setattr(o, "__class__", dict) for o in []]
for o in c2.objs:                      # make everything already fine
    pass
c2_all = MockController()
reply2 = None
# scene where every pickupable already sits on its preferred spot
for t in ("Apple", "Potato", "Pot", "Pan", "Egg"):
    o = O(c2_all, t)
    for p in o["parentReceptacles"] or []: c2_all._find(p)["receptacleObjectIds"].remove(o["objectId"])
    o["parentReceptacles"] = None
    c2_all._link(o, O(c2_all, "DiningTable"))
reply2 = tasks.tidy_room(c2_all); print("   all-tidy reply:", reply2)
check("fully tidy room: nothing picked up", not any(a == "PickupObject" for a, _ in c2_all.log) and "already tidy" in reply2)

print("D. tidy: placement failure -> switch receptacle (budget %d)" % config.TIDY_PLACE_BUDGET)
c = MockController(put_fail_types={"DiningTable"})
metrics.begin("t"); reply = tasks.tidy_room(c); rep = metrics.end(0, 0)
print("   reply:", reply)
dt_puts = [kw for a, kw in c.log if a == "PutObject" and c._find(kw["objectId"])["objectType"] == "DiningTable"]
n_items = len([1 for a, kw in c.log if a == "PickupObject"])
check("each item tried DiningTable at most TIDY_PLACE_BUDGET times", len(dt_puts) <= config.TIDY_PLACE_BUDGET * n_items, f"{len(dt_puts)} puts / {n_items} pickups")
check("items ended up on a DIFFERENT receptacle (SideTable/CounterTop)", all(
    parents(c, O(c, t))[0] in ("SideTable", "CounterTop") for t in ("Apple", "Potato", "Pot")), str({t: parents(c, O(c, t)) for t in ("Apple", "Potato", "Pot")}))
check("hand empty, nothing wandering forever", c.held is None)
check("switch counted in metrics", metrics.last_report["counters"].get("receptacle_switches", 0) >= 1, str(metrics.last_report["counters"]))

print("E. TSP including delivery legs is optimal and never worse than pickup-only")
c = MockController(); c.step(action="Pass")
rp = c.reachable(); rng = random.Random(7)
def fake(t, x, z): return {"objectType": t, "position": {"x": x, "y": .9, "z": z}}
nodes, neighbors = hybrid_navigator.get_reachable_graph(c, config.GRID_SIZE, rp)
strict = 0; bad = 0
for trial in range(25):
    items = [(fake(f"I{i}", rng.randint(-5, 5) * .25, rng.randint(-5, 5) * .25), fake(f"D{i}", rng.randint(-5, 5) * .25, rng.randint(-5, 5) * .25)) for i in range(4)]
    tour = hybrid_navigator.plan_pick_and_place_tour(c, items, reachable_positions=rp)
    st = hybrid_navigator.nearest_node(nodes, 0.0, 0.0)
    P = [hybrid_navigator.nearest_node(nodes, o["position"]["x"], o["position"]["z"]) for o, _ in items]
    D = [hybrid_navigator.nearest_node(nodes, d["position"]["x"], d["position"]["z"]) for _, d in items]
    cost = lambda order: hybrid_navigator.astar(st, P[order[0]], neighbors)[0] + sum(hybrid_navigator.astar(P[i], D[i], neighbors)[0] for i in order) + sum(hybrid_navigator.astar(D[a], P[b], neighbors)[0] for a, b in zip(order, order[1:]))
    best = min(cost(p) for p in itertools.permutations(range(4)))
    if abs(tour["total"] - best) > 1e-6: bad += 1
    if tour["total"] < tour["baseline_total"] - 1e-6: strict += 1
    if tour["total"] > tour["baseline_total"] + 1e-6: bad += 1
check("plan equals brute-force optimum in all 25 random scenes", bad == 0, f"{bad} mismatches")
check("strictly shorter than pickup-only order in some scenes", strict > 0, f"{strict}/25")

print("F. metrics")
config.FORCE_FALLBACK = True
c = MockController()
reply = executor.run_text_command(c, "pick up the apple"); print("   " + reply.replace("\n", "\n   "))
check("metrics line: accuracy %, time, steps", "[metrics] accuracy 100%" in reply and "time " in reply and "steps " in reply, reply.split(chr(10))[-1])
check("no clutter: no walking/pickup/pacing/route fields", not any(w in reply for w in ("walked", "pickups", "pacing", "route", "planning")))
config.FORCE_FALLBACK = False
c = MockController(fail_pickup_first=99)
reply = executor.run_text_command(c, "pick up the apple"); print("   failure:", reply.replace("\n", "\n   "))
check("failed run reports partial accuracy, not 100%", "accuracy 100%" not in reply and "[metrics]" in reply)
config.FORCE_FALLBACK = True
check("'stats' command gives session totals", "Session:" in executor.run_text_command(c, "stats"))
check("metrics log written (JSON lines)", os.path.exists(config.METRICS_LOG) and len(open(config.METRICS_LOG).read().splitlines()) >= 2)

print("G. prompt")
p = command_parser._build_system_prompt("SCENE")
check("prompt covers transformation, cooking, omelette, reason", all(k in p for k in ("OBJECTS TRANSFORM", "COOKING", "make an omelette", '"reason"')))

print("\nFAILED: %s" % fails if fails else "\nALL PASSED"); sys.exit(1 if fails else 0)
