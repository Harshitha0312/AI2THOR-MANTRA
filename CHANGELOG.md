# Changelog

## Nlp26 (this version)

**Metrics are now just five numbers.** Every command / task ends with one line:

    [metrics] accuracy 100% | time 17.0s | steps 22 | placed 1 | failed to place 0

- accuracy = objects handled / attempted for tidy, put-away and mess-up, otherwise steps completed / planned.
- time = total seconds. steps = successful walking steps (0.25 m), 90-degree turns and manipulation
  actions (pick up, put, open, close, switch, slice, drop). Camera tilts and simulator ticks are not steps.
- placed / failed to place are shown ONLY when the run involves placing (put/place/drop commands, tidy,
  put away, mess up, making a dish). Planning time, pacing time, walking metres, pickup attempts, route
  comparisons etc. were removed from the display (`robot/metrics.py`).

**Put away the groceries**
- Bread (see `GROCERY_LEAVE_ON_COUNTER` in config.py) that already lies on a countertop is ignored: never
  picked up, never dropped. The summary says "Left alone on the countertop: Bread".
- An item that will not fit in the fridge goes back on a COUNTERTOP; it is only set down on the spot if no
  counter will take it (before: it was dropped).

**Sandwich / dishes - enforced in code, not only in the prompt** (`planner/task_planner.enforce_dish_rules`,
`executor.Context`)
- Every [sliceable] ingredient that goes on a Plate/Bowl is sliced first (a slice step is inserted if the
  plan forgot it); the robot then picks up a SLICED piece - never the whole Bread / Lettuce / Tomato.
  Existing sliced pieces (BreadSliced ...) are used as they are; a second bread slice is a different slice.
- ONE plate for the whole plan (however a step names it) and ONE countertop: if the plate is not on a
  counter it is put on the counter closest to the ingredients first; everything then goes ON the plate.
- At the end every item is checked to still be on the plate; one that slid off is picked up and put back.

**Mess up the room**
- Destinations are random over the whole room and never "the receptacle next door": a surface closer than
  `MESS_MIN_DISTANCE` (1.5 m) or of the same kind as what the object sits on (CounterTop -> CounterTop) is
  only a last resort. Unused receptacles first, least-used type next, random among ties.
- `MESS_FLOOR_CHANCE` (0.15): a non-breakable object may be left lying on the floor somewhere.
- "make a mess" now also works through the normal pipeline (main.py), not just the UI.

**Blocked paths** (`robot/hybrid_navigator.walk_to_position`)
- Best path first (A*); goal cut off (other side of a counter / sealed pocket) -> walk to the nearest cell
  that CAN be reached instead of standing still; blocked on the way -> sidestep + re-plan; still blocked ->
  up to `WALK_FALLBACK_GOALS` other standing spots beside the target.

**Prompt** (`nlp/command_parser._build_system_prompt`) - reorganised into labelled sections (OUTPUT, ACTIONS,
THINK, NAMES, ALREADY TRUE, IMPOSSIBLE, PLACING, CLEARING, SCENES, TRANSFORMS, COOKING, PREPARED FOOD) with:
- the Floor is not an object: "on the floor" = pickup + drop (also enforced in code: `floor_to_drop`);
  drop is never used to put things away;
- typos / run-together words are repaired to scene objects ("ppillow", "pillowon the sofa"); scene objects
  are also fuzzy-matched in `environment/scene_manager.py`; the offline parser splits "pillowon sofa";
- goal already true -> reply "Already done: ..."; impossible -> "Not possible: ...";
- "clear the table" moves each item to a different suitable receptacle (no pickup-and-drop);
- mood requests use ONE fixed recipe (movie night = Television ON + lit lamps/light switch OFF), so
  "movie night" and "horror movie night" no longer give different plans.

Tests: `tests/test_nlp26.py` (new); test_putaway / test_nlp13 / test_nlp16 updated to the new behaviour.

## Fixes made after review (before this download)

1. **planner/task_planner.py** - a "place" step (e.g. "put it on the table")
   right after a "pickup" step for the same object was issuing a *second*
   PickupObject call on an object already in the agent's hand. AI2-THOR
   would reject that second pickup and the whole command would fail.
   Fixed by tracking which object is currently considered "held" while the
   plan is built, and skipping the redundant pickup.

2. **environment/scene_manager.py** - the word "fridge" was being mapped to
   the AI2-THOR object type "Refrigerator", which does not exist in
   AI2-THOR (the real type, per the official iTHOR Object Types docs, is
   "Fridge"). Any command mentioning "fridge" would therefore always fail
   with "I cannot find a 'fridge' in this scene." Fixed the synonym map
   ("refrigerator" -> "fridge") so both words now resolve correctly.

3. **nlp/command_parser.py** - "take the apple **from** the table and put it
   in the fridge" was parsed with an object name of "apple from table"
   (the word "table" got swallowed into the object name), which could
   never be grounded to a real object. Fixed by stripping a trailing
   "from <...>" clause before extracting the object name.

All three were caught by dry-running the full pipeline (parser -> validator
-> planner -> executor) against a mock AI2-THOR controller and are verified
fixed - see the "How to verify it yourself" section of README.md.


# Nlp12 (from Nlp11)

**Render sync (the "last action only shows on the next task" bug)** -
`robot/sync.py` (new). The Unity window repaints an action's result on a
later simulator tick; Nlp11 stopped stepping right after the final
pickup/place, so the window stayed one state behind until the NEXT
command's first `Pass`. `executor.execute_atomic_step()` now settles
(several `Pass` ticks + a short dwell) after every atomic action and only
THEN reports it. Whole-scene tasks and the UI/main entry points settle too.

**Speed** - `config.MOVE_STEP_DELAY`, `TURN_STEP_DELAY`,
`TURN_INTERP_DEGREES`: one paced step helper in `hybrid_navigator`; in-place
turns are now visible (interpolated) instead of instant snaps.

**Verified actions** - `robot/manipulation.py`: pickup/put/open/close/
toggle/fill/slice/... check the resulting AI2-THOR state (inventory, parent
receptacle, isOpen, isToggled, ...) and raise if it doesn't match.
Pickup/put try the real reach rules first (no forceAction), then other
standing spots, and only force when physically close (`FORCE_FALLBACK`).

**Scene metadata for the LLM** - `environment/scene_context.py` (new):
compact live description (held item, where each item is, what each
surface/container holds, switchable state, open/closed), rebuilt from
fresh metadata on every command.

**Prompt** - goal -> final state -> sub-goals -> prerequisites -> atomic
actions -> order; physical-prerequisite rules; scene-name-only rule.
`planner.task_planner.fix_ordering()` moves a `toggle_on` after the last
"place ... on <that appliance>" if the LLM ordered it wrong.

**Bugs fixed that were already in Nlp11**
- `list_contents` was missing from `config.ALLOWED_ACTIONS`, so the
  validator rejected "what is in the fridge?" before it ran.
- After any "face the object" turn the heading was an arbitrary angle, and
  the next A* walk assumed a multiple of 90 degrees, so the agent walked the
  wrong way. Heading is now snapped (visibly) before every walk.
- Stove: a StoveBurner isn't toggleable, its StoveKnob is; `toggle_on` now
  resolves burner -> knob via `controlledObjects`.
- `tidy_room` place-recovery used `Teleport` to jump between standing spots;
  it now walks with A*. The walkable set is re-read per object (the TSP
  order is still planned once, up front).
- "put it on the table" right after "pick up the apple" no longer walks back
  to the apple (the plan knows what is already in hand).
- Contents of a fridge are flattened through its shelves; pickupable
  containers are shown with what is in them.

**Tests** - `tests/` has a mock AI2-THOR with a one-tick display lag:
`python tests/test_nlp12.py` and `python tests/test_tidy_mess.py`.


# Nlp13 (from Nlp12)

**Omelette bug ("doesn't have anything to put in the pan")** - break/slice
REPLACE an object (Egg -> EggCracked) and may empty the hand, but the old
planner assumed the egg was still held and skipped the pickup, so the later
`place egg in pan` ran with an empty hand. Fixed at the root:
- `place` now plans a run-time `fetch` step (executor): already in hand ->
  nothing; already in the target -> done; otherwise walk to it and pick it up.
- After break/slice the executor re-points the name ("egg") at the new object.
- `break` of a held object is done at the destination container first.
- Names like "cracked egg"/"sliced apple" resolve; "stove" means the
  StoveBurner for placing and the StoveKnob for switching, and the knob that
  gets switched is the one of the burner that actually holds the pan.
- `fix_ordering` also keeps the heat OFF until the food in the pan is placed.
- Prompt: transformation rule, cooking rule, generic "what does it
  physically need / use a substitute / else say why not" rule, omelette
  example, `reason` field when a task is impossible (shown to the user).
- Scene context tags containers, cookable/cooked.

**tidy_room**
- TSP now includes the DELIVERY legs (pickup A -> deliver A -> pickup B ...)
  via `hybrid_navigator.plan_pick_and_place_tour`; verified optimal against
  brute force and compared with the old pickup-only order.
- Objects already somewhere acceptable are never picked up: on/in their
  preferred receptacle, on one within `TIDY_ACCEPT_MARGIN` of the best score,
  or stored in a fridge/cabinet/drawer. Re-checked just before lifting.
- Placement budget: `TIDY_PLACE_BUDGET` (4) PutObject attempts per receptacle,
  then the next best receptacle (`TIDY_MAX_RECEPTACLES`, 5) instead of wandering.
- mess_up_room no longer teleports in its "drop on the floor" fallback.

**Metrics** - `robot/metrics.py`: success %, total/planning/execution time,
visual-pacing time, walking steps and metres, pickup/place attempts, retries,
receptacle switches, objects left in place, planned vs baseline route length.
Printed after every command/task, appended to `metrics_log.jsonl`; type
`stats` for session totals.

**Tests** - `python tests/test_nlp13.py` (plus the two Nlp12 test files).

## mess-up / obstacle / egg / fridge fix (on top of Nlp13)
- **Blocked path (all walking, incl. TSP tours):** `_walk_path` remembers the cell that blocked a step, sidesteps (left/right/forward/back), re-reads the live walkable cells and re-plans A* around it, up to `WALK_REPLAN_LIMIT` (6) times. Before, it re-planned once on the same stale graph and stood still holding the object.
- **mess_up_room rewritten:** picks several objects, a *nearby* wrong surface for each (no more crossing the house), orders them with the tidy TSP (pickup->delivery legs), time budget per object (`MESS_ITEM_SECONDS` 80) and per run (`MESS_MAX_SECONDS` 300) instead of one 60 s budget. A failed object is skipped and a greedy top-up pass continues until `MESS_ITEMS` are moved.
- **Omelette:** the egg is now put INTO the pan first and cracked there (planner, `break` with a later `place` target), so the cracked egg stays in the pan instead of landing on the floor.
- **Fridge/containers:** after taking an item out, every openable container it came from is closed with `manipulation.close_container` (verifies `isOpen`, retries from another spot, reports honestly if it can't).
- Tests: `tests/test_mess_fix.py` (+ mock obstacles / flaky close).

## place-from-far-away fix
- `approach_object` only considers standing spots A* can really walk to from the current position and verifies the robot actually arrived (before, an unreachable "nearest" spot meant no movement and the action ran from far away).
- New `manipulation.get_close()`: before every pickup/put the robot frames the target, checks live visibility/distance and moves to other spots until it is in reach; non-forced attempts are skipped when it can't get close; reposition retries are skipped for spots that weren't reached.
- `navigation.navigate_to` uses the same search.
- Real sim note: `Placing the Egg ... NullReferenceException: Target object not found within the specified visibility` means the Pan was not visible/within range from where the robot stood.

## Nlp16 (on top of Nlp15)
**Sandwich / prepared food (prompt, `nlp/command_parser.py`)**
- New rule "PREPARED FOOD": only SLICED pieces go into the dish. Every `[sliceable]` ingredient is sliced (tool Knife) first, then picked up and placed - never the whole Bread/Lettuce/Tomato. If the scene already lists sliced pieces (e.g. `BreadSliced`), the slice step is skipped and they are used.
- New rule "ONE WORK SURFACE": the Plate and all its ingredients end up together on ONE countertop. If the Plate is not already on a CounterTop it is placed on one first (one plate only); every ingredient is then placed ON THE PLATE (never directly on counters), so nothing ends up on different countertops.
- Sandwich example rewritten to show exactly that (plate on counter -> slice -> pickup -> place on plate, per ingredient).

**mess_up_room (`robot/tasks.py`, `config.py`)**
- Destinations are now random over the WHOLE room. Before, surfaces were sorted nearest-first (3 closest shuffled), so each object landed on the very next surface and the room never looked messy.
- Spread: the least-used surface TYPE (then least-used surface) comes first, random among ties, so a run uses different kinds of surfaces instead of one favourite. Surfaces shut inside a closed container are skipped.
- `MESS_SELECTION` default is now `"random"` (which objects get moved); `"nearest"` is still available. Walking is still real A* (no teleport); the visit order is still TSP-optimised, only *what* moves and *where* is random.

**Tests** - `python tests/test_nlp16.py` (mock simulator now supports `SliceObject`). Sandwich: only `*Sliced` pieces on one plate on one counter, an untouched second tomato stays put. Mess: 10 seeds, objects land on many different surfaces, first-choice destination is outside the 3 nearest in ~half the draws (old code: 0).

## Nlp16modified (on top of Nlp16) - "put away the groceries"
- **It now ends right after the last grocery is in the fridge.** Before, the task swept 25 checkpoints x 4 rotations looking for groceries and kept going after everything was away (it re-checked the room again and again). The metadata says exactly which groceries are out, so there is nothing to search: the list is built once (`_groceries_out`: grocery types, pickupable, not already in the fridge/freezer or inside another openable), ordered with the pickup -> fridge TSP, worked through one by one, and the task stops. Groceries already inside the fridge or a closed cabinet are left alone (the old sweep took them out and put them back). `put_away_groceries(controller, max_checkpoints, rotations)` keeps its signature; the two numbers are ignored.
- **Bread (and anything bulky) goes INTO the fridge.** Cause: `PutObject` onto the planned shelf and then onto the fridge each got exactly one try; when the bread was refused ("no room"), the task logged "could not place it" but kept the bread in hand and the next pickup dropped it on the nearest countertop - that was the "moves from one countertop to another". Now `_place_in_fridge` gets close to and frames the fridge, then tries the planned shelf, every other shelf and finally the fridge itself, each with 5 placement variants (`placeStationary` on/off, different `randomSeed` = different spawn point), then the full `manipulation.put_object` recovery; every success is verified from the metadata (the item's parent chain must contain the fridge). If it really cannot fit, it says so, closes the fridge and is reported as a failure instead of being carried to a counter. One retry pass for failed items, then the task stops.
- The fridge is closed with the verified `manipulation.close_container`.
- Summary: `Put away 3 of 3 grocery item(s): Tomato, Bread, Egg.` (+ `Could not put away: ...` if any).
- Tests: `tests/test_putaway.py` (mock got a "bulky item refused n times" option).
