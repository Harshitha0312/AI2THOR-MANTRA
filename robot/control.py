
import threading

cancel_event = threading.Event()
_progress = print


def set_progress_callback(fn):
    """UI installs a function here; passing None restores plain print."""
    global _progress
    _progress = fn if fn is not None else print


def say(message):
    try:
        _progress(message)
    except Exception:
        pass  # a broken UI callback must never crash a robot task


def request_cancel():
    cancel_event.set()


def clear_cancel():
    cancel_event.clear()


def is_cancelled():
    return cancel_event.is_set()