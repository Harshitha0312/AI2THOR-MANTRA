import sys, time; import os; HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import config
config.USE_LLM_PARSER = False
config.MOVE_STEP_DELAY = config.TURN_STEP_DELAY = 0.0
config.SETTLE_DELAY = 0.0; config.ACTION_DWELL = 0.0
from mock_thor import MockController
from robot import control, sync
import executor
from executor import run_text_command, execute_plan
from planner.task_planner import build_plan
from environment import scene_context

fails = []
def check(name, cond, info=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{info}]" if info else ""))
    if not cond: fails.append(name)

def held(c): return c.held and c.held.split("|")[0]
def parent_of(c, t):
    o = next(o for o in c.objs if o["objectType"] == t)
    return [c._find(p)["objectType"] for p in (o["parentReceptacles"] or [])]

def run_with_say_check(c, text):
    """Record window-vs-world at the instant each step is REPORTED."""
    seen = []
    control.set_progress_callback(lambda m: seen.append((m, c.screen == c._world_view())))
    reply = run_text_command(c, text)
    control.set_progress_callback(None)
    return reply, seen

print("T1 pick up apple")
c = MockController()
reply, seen = run_with_say_check(c, "pick up the apple")
print("   reply:", reply)
check("apple held in simulator", held(c) == "Apple")
check("window in sync when reply returned", c.screen == c._world_view())
check("every step message reported only after window synced", all(ok for m, ok in seen if m.startswith(("Picked", "Placed", "Opened", "Turned", "Closed"))), str(seen))
check("walked (MoveAhead steps) before pickup", any(a == "MoveAhead" for a, _ in c.log[:c.log.index(next(x for x in c.log if x[0]=='PickupObject'))]))
check("no teleport changed position", c.teleports_moving == 0)
check("first pickup attempt was NOT forced", next(kw for a, kw in c.log if a == "PickupObject").get("forceAction") is False)

print("T2 put apple on table (apple already held from T1)")
reply, seen = run_with_say_check(c, "put the apple on the table")
print("   reply:", reply)
check("apple on DiningTable in simulator", parent_of(c, "Apple") == ["DiningTable"], str(parent_of(c, "Apple")))
check("window in sync when reply returned", c.screen == c._world_view())
check("placed message reported after sync", all(ok for m, ok in seen if m.startswith("Placed")))
check("no teleport changed position", c.teleports_moving == 0)

print("T3 boil potato - deliberately WRONG order from 'LLM' (stove first)")
c = MockController()
steps = [
    {"action": "toggle_on", "object": "StoveKnob"},
    {"action": "pickup", "object": "Potato"},
    {"action": "place", "object": "Potato", "target": "Pot"},
    {"action": "fill", "object": "Pot", "liquid": "water"},
    {"action": "place", "object": "Pot", "target": "StoveBurner"},
]
plan = build_plan(steps)
order = [p for p in plan if p[0] in ("pickup", "put", "fill", "toggle_on")]
print("   executed order:", [(p[0], *p[1:]) for p in order])
check("toggle_on is the LAST atomic action", order[-1][0] == "toggle_on")
toggle_idx = [i for i, p in enumerate(plan) if p[0] == "toggle_on"][0]
put_pot_idx = [i for i, p in enumerate(plan) if p[0] == "put" and p[1] == "StoveBurner" or (p[0]=="put" and p[2]=="StoveBurner")][0]
check("pot placed on burner BEFORE stove ON", put_pot_idx < toggle_idx)
# execute and snoop state at toggle time
orig = c.step
state_at_toggle = {}
def spy(action=None, **kw):
    if action == "ToggleObjectOn":
        state_at_toggle["pot_on_burner"] = "StoveBurner" in parent_of(c, "Pot")
    return orig(action=action, **kw)
c.step = spy
msgs = execute_plan(c, plan)
knob = next(o for o in c.objs if o["objectType"] == "StoveKnob")
pot = next(o for o in c.objs if o["objectType"] == "Pot")
check("pot was ON the burner at the instant the knob was toggled", state_at_toggle.get("pot_on_burner") is True)
check("final state: potato in pot, pot filled, pot on burner, knob ON",
      parent_of(c, "Potato") == ["Pot"] and pot["isFilledWithLiquid"] and parent_of(c, "Pot") == ["StoveBurner"] and knob["isToggled"])
check("window in sync", c.screen == c._world_view())
print("   toggle target resolved from 'StoveBurner' name too:")
c2 = MockController(); 
from robot import manipulation
bid = next(o["objectId"] for o in c2.objs if o["objectType"] == "StoveBurner")
manipulation.toggle_on(c2, bid)
check("toggle_on(StoveBurner) switched the StoveKnob", next(o for o in c2.objs if o["objectType"]=="StoveKnob")["isToggled"])

print("T4 what is in the fridge?")
c = MockController()
reply, seen = run_with_say_check(c, "what is in the fridge?")
print("   reply:", reply)
fr = next(o for o in c.objs if o["objectType"] == "Fridge")
check("answer lists the real content (Egg)", "Egg" in reply)
check("fridge closed again (we opened it)", fr["isOpen"] is False)
c = MockController(); fr = next(o for o in c.objs if o["objectType"] == "Fridge"); fr["isOpen"] = True
run_text_command(c, "tell me what is in the fridge")
check("fridge left OPEN if it was already open", fr["isOpen"] is True)
reply = run_text_command(c, "what's on the table?")
print("   reply:", reply)
check("table answer from metadata (Book)", "Book" in reply)

print("T4b scene context for the LLM is live metadata")
c = MockController()
before = scene_context.build_scene_context(c)
print("   " + before.replace("\n", "\n   "))
run_text_command(c, "pick up the apple")
run_text_command(c, "put the apple on the table")
after = scene_context.build_scene_context(c)
check("context before: Apple -> CounterTop", "Apple [sliceable] -> CounterTop" in before)
check("context after:  Apple -> DiningTable", "Apple [sliceable] -> DiningTable" in after, after.split("\n")[1][:200])
steps_ctx = None
from nlp import command_parser
check("prompt embeds current scene", "CURRENT SCENE" in command_parser._build_system_prompt(after) and "DiningTable" in command_parser._build_system_prompt(after))

print("T7 pickup recovery: first 2 non-forced pickups fail")
c = MockController(fail_pickup_first=2)
reply = run_text_command(c, "pick up the apple")
print("   reply:", reply)
check("recovered and picked up honestly", held(c) == "Apple" and "Picked up" in reply)
check("more than one non-forced attempt made", sum(1 for a, kw in c.log if a == "PickupObject" and not kw.get("forceAction")) >= 3)
c = MockController(fail_pickup_first=99)
reply = run_text_command(c, "pick up the apple")
print("   reply (force fallback ON):", reply)
check("forced only as last resort when physically close", held(c) == "Apple")
config.FORCE_FALLBACK = False
c = MockController(fail_pickup_first=99)
reply = run_text_command(c, "pick up the apple")
print("   reply (force fallback OFF):", reply)
check("failure reported honestly, never 'Picked up'", held(c) is None and "Picked up" not in reply)
config.FORCE_FALLBACK = True

print("T8 unknown object refused before moving")
c = MockController(); n0 = len(c.log)
reply = run_text_command(c, "pick up the banana")
print("   reply:", reply)
check("refused without walking", "can't find" in reply and not any(a.startswith("Move") for a, _ in c.log))

print("\nFAILED: %s" % fails if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
