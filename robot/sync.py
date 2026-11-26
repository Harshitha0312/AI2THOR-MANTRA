"""
Render / state synchronisation for MANTRA (Nlp12).

WHY THIS EXISTS
---------------
AI2-THOR's Unity window repaints the result of an action (an object
moving into the hand, a pot settling on a burner, a door finishing its
swing) on a LATER simulator tick than the one that carried the action
out. In Nlp11 nothing stepped the simulator after the final
PickupObject/PutObject, so the picture stayed one state behind until the
NEXT command's first controller.step() ("Pass" inside find_object)
finally pushed it on screen. The task was reported complete while the
window still showed the old state.

Everything about "make the state really visible before moving on" lives
in this one module so it is not scattered through unrelated code:

  pace(kind)    tiny wait after a walk/turn step so motion is followable
  refresh(c)    ONE Pass: pulls fresh ground-truth metadata (cheap, no
                sleeping) - used to verify an action's result
  settle(c)     the full "commit to screen": several Pass ticks with a
                short delay, then a dwell. Call after every atomic action
                and BEFORE reporting it done.
"""

import time

import config
from robot import metrics


def pace(kind="move"):
    delay = config.TURN_STEP_DELAY if kind == "turn" else config.MOVE_STEP_DELAY
    if delay and delay > 0:
        time.sleep(delay)
        metrics.add_pause(delay)


def refresh(controller):
    """One simulator tick; returns the event whose metadata is now current."""
    return controller.step(action="Pass")


def settle(controller, passes=None, delay=None, dwell=None):
    passes = config.SETTLE_PASSES if passes is None else passes
    delay = config.SETTLE_DELAY if delay is None else delay
    dwell = config.ACTION_DWELL if dwell is None else dwell

    event = controller.last_event
    for _ in range(max(1, passes)):
        event = controller.step(action="Pass")
        if delay and delay > 0:
            time.sleep(delay)
            metrics.add_pause(delay)
    if dwell and dwell > 0:
        time.sleep(dwell)
        metrics.add_pause(dwell)
    return event
