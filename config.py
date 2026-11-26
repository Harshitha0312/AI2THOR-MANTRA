"""
MANTRA (Nlp26) - central configuration.
Edit these values to match your setup.
"""

# ---------------- AI2-THOR / ManipulaTHOR ----------------
SCENE = "FloorPlan1"
WIDTH = 900
HEIGHT = 600
GRID_SIZE = 0.25
ROTATE_STEP_DEGREES = 90
VISIBILITY_DISTANCE = 1.5
FIELD_OF_VIEW = 90

# ---------------- Allowed high level actions ----------------
ALLOWED_ACTIONS = [
    "find",
    "pickup",
    "place",
    "open",
    "close",
    "go_to",
    "toggle_on",
    "toggle_off",
    "run_task",
    "list_contents",      # "what is in the fridge?" (needs a target, no object)
    # --- extra atomic actions ---
    "drop",
    "slice",
    "break",
    "clean",
    "dirty",
    "fill",
    "empty",
    "use_up",
    "look_at",
    "look_up",
    "look_down",
    "turn_left",
    "turn_right",
    "turn_around",
    "move_forward",
    "move_back",
    "move_left",
    "move_right",
    "crouch",
    "stand",
]

# Actions that must name an object / actions that never take one.
OBJECT_ACTIONS = [
    "pickup", "open", "close", "find", "go_to", "toggle_on", "toggle_off",
    "slice", "break", "clean", "dirty", "fill", "empty", "use_up", "look_at",
]
NO_OBJECT_ACTIONS = [
    "drop", "look_up", "look_down", "turn_left", "turn_right", "turn_around",
    "move_forward", "move_back", "move_left", "move_right", "crouch", "stand",
]

MAX_AMOUNT = 10              # biggest "amount" accepted for look/turn/move
MOVE_STEPS_PER_UNIT = 2      # one "move forward" = 2 grid steps (0.5 m)
VALID_LIQUIDS = ("water", "coffee", "wine")

KNOWN_TASKS = {"tidy", "putaway", "energy", "mess"}


# ---------------- Visual pacing / render sync (Nlp12) ----------------
# AI2-THOR's Unity window only repaints the result of an action (the apple
# in the hand, the pot on the stove) on a LATER simulator tick. If the
# program stops stepping right after the last action, the picture stays
# one state behind until the next command's first step. robot/sync.py
# fixes that by stepping a few "Pass" ticks after every atomic action.
#
# MOVE_STEP_DELAY / TURN_STEP_DELAY: seconds to wait after each 0.25 m
#   MoveAhead / each rotation step so walking is easy to follow.
#   0.0 = as fast as the simulator can go (Nlp11 behaviour).
# TURN_INTERP_DEGREES: in-place "face the object" turns are split into
#   steps of this size so you SEE the robot turn instead of snapping.
# SETTLE_PASSES / SETTLE_DELAY / ACTION_DWELL: after each atomic action
#   step the simulator this many times, SETTLE_DELAY seconds apart, then
#   hold ACTION_DWELL seconds so the final state is on screen BEFORE
#   "done" is reported.
MOVE_STEP_DELAY = 0.05
TURN_STEP_DELAY = 0.02
TURN_INTERP_DEGREES = 30
SETTLE_PASSES = 3
SETTLE_DELAY = 0.1
ACTION_DWELL = 0.40

# ---------------- Pickup / place verification (Nlp12) ----------------
# Pickup and place are first tried WITHOUT forceAction, so the simulator's
# own reach/visibility rules decide. If that fails the robot walks to
# other standing spots and retries. Only when it is physically close
# (metadata distance <= *_FORCE_MAX_DIST) is a forced attempt allowed as
# a last resort. Set FORCE_FALLBACK = False to never force.
FORCE_FALLBACK = True
PICKUP_FORCE_MAX_DIST = 1.5
PLACE_FORCE_MAX_DIST = 1.6
PICKUP_REPOSITION_TRIES = 4
PLACE_REPOSITION_TRIES = 3

# ---------------- mess_up_room (Nlp12) ----------------
# Which objects get moved: "random" (default - any movable object in the room)
# or "nearest" (closest first). Where they end up is ALWAYS random over the
# whole room and spread across different surfaces. Walking is always real A*.
MESS_SELECTION = "random"
# mess_up_room is time-limited per object AND per run (it used to be one
# 60 s budget for everything, which ended the run after a single object).
MESS_ITEMS = 6              # objects to move per "mess up the room"
MESS_MAX_SECONDS = 300
# Nlp26: where things end up. A destination closer than MESS_MIN_DISTANCE (metres) to where the
# object is now - or of the same kind as what it sits on - is only used when nothing else works,
# so an object never just hops to the receptacle beside it. MESS_FLOOR_CHANCE is the chance that a
# (non-breakable) object is left lying on the floor somewhere instead of on a surface; 0 = never.
MESS_MIN_DISTANCE = 1.5
MESS_FLOOR_CHANCE = 0.15
MESS_ITEM_SECONDS = 80

# when a step is blocked the robot sidesteps and re-plans this many
# times before giving up on a route.
WALK_REPLAN_LIMIT = 6
# if the route to a spot stays blocked, try this many OTHER standing spots next
# to the target before giving up on it (robot/hybrid_navigator.walk_to_position).
WALK_FALLBACK_GOALS = 3

# ---------------- tidy_room (Nlp13) ----------------
# An object counts as "already fine" if it sits on its preferred receptacle
# type OR on one whose preference score is within this margin of the best
# (a mug on the counter, when a cabinet is only slightly better). Those
# objects are never picked up, so nothing is lifted just to be put back.
TIDY_ACCEPT_MARGIN = 0.2
# PutObject attempts (camera tilt / another standing spot) on ONE receptacle
# before giving up on it and switching to the next best one.
TIDY_PLACE_BUDGET = 4
# How many different receptacles to try for one object, best first.
TIDY_MAX_RECEPTACLES = 5

# ---------------- put away groceries (Nlp26) ----------------
# Groceries of these types that already lie on a COUNTERTOP are ignored by "put away the
# groceries": not picked up, not carried, not put down again. Add or remove names freely.
GROCERY_LEAVE_ON_COUNTER = ("Bread",)

# ---------------- metrics (Nlp13 / Nlp26) ----------------
# One JSON line per command/task is appended here. Set to None to disable.
METRICS_LOG = "metrics_log.jsonl"

# ---------------- LLM scene context (Nlp12) ----------------
SCENE_CONTEXT_MAX_CHARS = 4500

# ---------------- Voice ----------------
# Input and output are controlled separately so you can mix and match
# (e.g. speak commands with your mic but get typed/printed responses
# only, on a machine with no speakers/aplay).
#
# USE_VOICE_INPUT:  True  -> try to listen on the microphone, falls back
#                             to typed input automatically if no mic /
#                             SpeechRecognition install is found.
#                    False -> always use typed input.
#
# USE_VOICE_OUTPUT: True  -> try to speak responses aloud via pyttsx3,
#                             falls back to printing only if TTS/audio
#                             output isn't available (e.g. no aplay/
#                             sound card, as in most cloud containers).
#                    False -> always just print responses, no TTS at all.
#
# Set both to False for a fully text-only run (no microphone, no
# speakers needed, no ALSA/aplay errors possible).
USE_VOICE_INPUT = True
USE_VOICE_OUTPUT = False
TTS_RATE = 175  # words per minute for text-to-speech, used only if USE_VOICE_OUTPUT is True

# ---------------- NLP ----------------
# Rule based parsing always works offline and needs no API key.
# For smarter parsing of complex sentences / high-level goals, set
# USE_LLM_PARSER = True and export at least one provider key (see
# LLM_PROVIDERS below) in your environment before running.
USE_LLM_PARSER = True

# Providers are tried in order; the first that has its API key set and
# answers correctly wins. If one is rate limited / exhausted / errors, the
# next is tried automatically. If all fail, the offline rule-based parser
# is used. All are OpenAI-SDK compatible. Set each key as an environment
# variable (never hardcode keys here), e.g. in the terminal:
#     export GROQ_API_KEY="..."   GEMINI_API_KEY="..."   OPENROUTER_API_KEY="..."
# Reorder, remove or add entries freely; model names change over time, so
# check each provider's current free-model list if one starts failing.
LLM_PROVIDERS = [
    {
        "name": "vertex",
        "base_url": "https://us-central1-aiplatform.googleapis.com/v1/projects/project-27f4ff4c-38db-42d2-b15/locations/us-central1/endpoints/openapi",
        "model": "google/gemini-2.5-flash",
        "key_env": "",
        "auth": "adc",
    },
    {
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_env": "GROQ_API_KEY",
    },
    {
    "name": "groq2",
    "base_url": "https://api.groq.com/openai/v1",
    "model": "openai/gpt-oss-120b",
    "key_env": "GROQ_API_KEY_2",
    },
    {
        "name": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
        "key_env": "GEMINI_API_KEY",
    },
    {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-oss-20b:free",
        "key_env": "OPENROUTER_API_KEY",
    },
]
LLM_TIMEOUT_SECONDS = 20

# ---------------- Misc ----------------
EXIT_WORDS = {"exit", "quit", "stop", "goodbye", "bye"}

# ---------------- Debug ----------------
# When True, robot/manipulation.py prints extra diagnostic info after
# OpenObject actions (visible objects, isOpen/openness state, etc.) and
# saves a snapshot of the agent's camera to after_open.png. Leave this
# False for normal use - it's noisy and writes a file to disk on every
# single "open" command.
DEBUG_OPEN = False