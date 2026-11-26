# MANTRA (Nlp26) — Voice-Controlled ManipulaTHOR Robot

What changed in Nlp26 is at the top of CHANGELOG.md. Pacing/verification knobs are in config.py
("Visual pacing" section); the new knobs are `GROCERY_LEAVE_ON_COUNTER`, `MESS_MIN_DISTANCE`,
`MESS_FLOOR_CHANCE` and `WALK_FALLBACK_GOALS`.

### Reading the metrics line

    [metrics] accuracy 100% | time 17.0s | steps 22 | placed 1 | failed to place 0

`accuracy` = % of the work done, `time` = seconds, `steps` = walking steps + turns + manipulation actions,
`placed` / `failed to place` = objects put where they should go (shown only when a run places something).
Type `stats` to see the session totals.

Speak (or type) an instruction, and the robot finds the object, walks
to it, and performs the action in AI2-THOR / ManipulaTHOR.

## 1. Project layout

```
MANTRA/
├── main.py                 # run this in PyCharm
├── executor.py              # runs a low-level plan against the simulator
├── config.py                 # all settings in one place
├── requirements.txt
├── speech/
│   ├── speech_to_text.py     # microphone -> text
│   └── text_to_speech.py     # text -> voice feedback
├── nlp/
│   └── command_parser.py     # text -> structured {"steps": [...]}
├── planner/
│   ├── action_validator.py   # rejects anything not in config.ALLOWED_ACTIONS
│   └── task_planner.py       # structured steps -> low level action list
├── robot/
│   ├── sim_controller.py     # starts the AI2-THOR Controller
│   ├── navigation.py         # moves the agent next to a target object
│   ├── manipulation.py       # pickup / put / open / close
│   └── object_detection.py   # grounds object names to objectIds
└── environment/
    └── scene_manager.py      # scene metadata helpers
```

## 2. Install (PyCharm terminal)

```bash
pip install -r requirements.txt
```

`PyAudio` (needed for the microphone) sometimes fails to build from
source:

- **Windows:** `pip install pipwin` then `pipwin install pyaudio`
- **macOS:** `brew install portaudio` then `pip install pyaudio`
- **Linux:** `sudo apt install portaudio19-dev` then `pip install pyaudio`

If you don't want to deal with a microphone at all, set
`USE_VOICE = False` in `config.py` — the program will simply ask you
to type each command instead, and everything else works the same way.

## 3. Run

Open `main.py` in PyCharm and run it. On first launch AI2-THOR will
download its Unity build (this only happens once and needs internet
access + a display).

You should see:

```
Starting MANTRA...
AI2-THOR ready in scene 'FloorPlan1'.
Objects currently visible: [...]
Robot: MANTRA is ready. Tell me what to do.
Listening... (speak now)
```

Try commands such as:

```
Pick up the apple
Pick up the apple and put it on the table
Put the apple in the fridge
Open the fridge
Find the mug
Go to the sink
```

Say (or type) `exit`, `quit`, or `stop` to end the program.

## 4. How a command flows through the code

```
"Put the apple in the fridge"
        │
        ▼  speech_to_text.listen()
"put the apple in the fridge"
        │
        ▼  nlp/command_parser.parse_command()
{"steps": [{"action": "place", "object": "apple", "target": "fridge"}]}
        │
        ▼  planner/action_validator.validate_command()
        │  (raises InvalidCommandError if malformed)
        ▼  planner/task_planner.build_plan()
[("find","apple"), ("go_to","apple"), ("pickup","apple"),
 ("find","fridge"), ("go_to","fridge"), ("open","fridge"),
 ("put","apple","fridge"), ("close","fridge")]
        │
        ▼  executor.execute_plan()
        │  grounds each name to a real objectId (environment/scene_manager.py)
        │  and calls robot/navigation.py + robot/manipulation.py
        ▼
Robot: "Found apple. Moved to apple. Picked up apple. ... Task complete."
```

## 5. Extending it

- **New verbs:** add an entry to `VERB_MAP` in `nlp/command_parser.py`
  and a matching branch in `planner/task_planner.py`.
- **Smarter language understanding:** set `USE_LLM_PARSER = True` in
  `config.py`, `pip install anthropic`, and set the `ANTHROPIC_API_KEY`
  environment variable. `parse_with_llm()` in `nlp/command_parser.py`
  will then handle more complex/ambiguous sentences, with the
  rule-based parser as an automatic fallback if the API call fails.
- **Multi-object tasks** (e.g. "put all the groceries away"): extend
  `task_planner.build_plan()` to loop over every matching object
  returned by `environment/scene_manager.get_all_objects()`.

## 6. Notes

- All manipulation/navigation actions use AI2-THOR ground-truth object
  metadata for grounding (`environment/scene_manager.py`), which is the
  standard approach for manipulation/planning-focused ManipulaTHOR
  projects (as opposed to pixel-based perception research).
- Every simulator call checks `lastActionSuccess` and raises a clear
  Python exception on failure, so a missing/unreachable object results
  in a spoken message ("I cannot find a banana.") instead of a crash.
- Action names (`PickupObject`, `PutObject`, `OpenObject`,
  `CloseObject`, `GetInteractablePositions`, `Teleport`) match the
  AI2-THOR 5.0.0 API. If you use a different AI2-THOR version, check
  the changelog for any renamed actions.
