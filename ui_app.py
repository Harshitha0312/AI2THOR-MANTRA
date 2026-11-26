"""
MANTRA desktop UI (Tkinter - ships with Python, nothing to pip install).

    python ui_app.py           real robot
    python ui_app.py --demo    no simulator; use this first to check the
                               window, buttons, exit words and voice toggle

What you can do in the window
  * Type a command and press Enter, or click a quick button.
  * Type  stop / bye / exit / quit  to end everything and close the window.
  * Stop button (or Esc) cancels the current task but keeps the window open.
  * "Voice" button turns the microphone on/off. To start with voice ON, put
    VOICE_INPUT = True in config.py.

How it is built
  * The window runs on the main thread and never blocks.
  * ONE background thread (Worker) owns the AI2-THOR controller.
  * A second background thread (VoiceListener) listens to the microphone.
  * Both talk to the window through a queue polled every 100 ms.
  * Stop -> robot.control.request_cancel(); tasks check it inside their loops.

Compatible with Python 3.8.
"""

import argparse
import importlib
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import scrolledtext

from robot import control

try:
    import config
except ImportError:          # the demo still works without a config file
    config = None

EXIT_WORDS = {"stop", "bye", "exit", "quit"}
try:   # also honour any words you already listed in config.EXIT_WORDS
    EXIT_WORDS |= {str(w).strip().lower() for w in config.EXIT_WORDS}
except Exception:
    pass


def normalize(text):
    """'  Bye!! ' -> 'bye'"""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


def is_exit_word(text):
    return normalize(text) in EXIT_WORDS


# ---------------------------------------------------------------------
#  Whole-scene tasks: found and called automatically
# ---------------------------------------------------------------------
# The window looks through your project for the file that defines
# tidy_room(), so it works whatever you named that file.
_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".idea", "site-packages"}
_tasks_module = None

# Your existing parser doesn't know "mess up the room", so those phrases go
# straight to mess_up_room(). Everything else (tidy, groceries, energy,
# pick/open/move...) goes through your normal pipeline in run_executor().
SCENE_TASKS = [
    (("mess up", "make a mess", "messy", "untidy", "mess the", "mess it"), "mess_up_room"),
]


def _find_tasks_module():
    global _tasks_module
    if _tasks_module is not None:
        return _tasks_module
    root = os.path.dirname(os.path.abspath(__file__))
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    if "def tidy_room" not in fh.read():
                        continue
            except (OSError, UnicodeDecodeError):
                continue
            rel = os.path.relpath(path, root)[:-3].replace(os.sep, ".")
            found.append(rel)
    modules = [importlib.import_module(name) for name in found]
    # Prefer the updated file (has mess_up_room) over an old copy
    modules.sort(key=lambda m: not hasattr(m, "mess_up_room"))
    _tasks_module = modules[0] if modules else None
    return _tasks_module


def pick_scene_task(text):
    """Returns a function(controller)->str for whole-scene commands, else None."""
    lowered = text.lower()
    for keywords, func_name in SCENE_TASKS:
        if any(k in lowered for k in keywords):
            module = _find_tasks_module()
            if module is None:
                raise RuntimeError("Could not find your tasks file (the one with tidy_room).")
            func = getattr(module, func_name, None)
            if func is None:
                raise RuntimeError(
                    "Your tasks file has no %s() yet - replace its contents with "
                    "tasks_fixed.py." % func_name
                )
            return func
    return None


# ---------------------------------------------------------------------
#  Your existing pipeline (copied from main.py, but returns text instead of
#  speaking it): switch scene -> parse -> validate -> plan -> execute.
# ---------------------------------------------------------------------
_SCENE_PATTERN = re.compile(r"floor\s*plan\s*0*([1-9]\d{0,3})", re.IGNORECASE)


def try_switch_scene(controller, text):
    """'switch to FloorPlan204' -> reply text; None if text isn't a scene switch."""
    match = _SCENE_PATTERN.search(text)
    if not match:
        return None
    scene_name = "FloorPlan%s" % match.group(1)
    try:
        controller.reset(scene=scene_name)
    except Exception as exc:
        return "I couldn't load %s: %s" % (scene_name, exc)
    if config is not None:
        config.SCENE = scene_name
    from environment import scene_manager
    scene_manager.refresh(controller)      # fresh ground truth for the new scene
    visible = scene_manager.list_visible_object_types(controller)
    return "Switched to %s. Objects currently visible: %s" % (scene_name, visible)


def run_executor(controller, text):
    from executor import run_text_command

    switched = try_switch_scene(controller, text)
    if switched is not None:
        return switched
    return run_text_command(controller, text)


def handle_command(controller, text):
    task = pick_scene_task(text)
    if task is not None:
        from robot import metrics, sync
        started = metrics.begin("task: " + text.strip()[:60], controller)
        try:
            reply = task(controller)
        except Exception:
            if started:
                metrics.end(0, 1)
            raise
        sync.settle(controller)          # last action on screen before the reply
        report = metrics.end(1, 1) if started else ""
        return f"{reply}\n{report}".strip()
    return run_executor(controller, text)


# ---------------------------------------------------------------------
#  Backends
# ---------------------------------------------------------------------
class RealBackend:
    def start(self):
        from robot.sim_controller import get_controller
        self.controller = get_controller()
        try:
            from environment import scene_manager
            scene = getattr(config, "SCENE", "?")
            return "Scene '%s'. Objects currently visible: %s" % (
                scene, scene_manager.list_visible_object_types(self.controller))
        except Exception:
            return None

    def run(self, text):
        reply = handle_command(self.controller, text)
        # Optional: also read replies aloud. Off by default because text-to-speech
        # can block or misbehave when called from a background thread.
        if getattr(config, "SPEAK_REPLIES", False):
            try:
                from speech.text_to_speech import speak
                speak(reply)
            except Exception:
                pass
        return reply

    def close(self):
        try:
            self.controller.stop()
        except Exception:
            pass


class DemoBackend:
    """Pretends to be the robot so the UI can be tested on its own."""

    def start(self):
        time.sleep(0.5)

    def run(self, text):
        for i in range(1, 9):
            if control.is_cancelled():
                return "Stopped."
            control.say("Demo step %d/8 for: %s" % (i, text))
            time.sleep(0.5)
        return "Demo finished: " + text

    def close(self):
        pass


# ---------------------------------------------------------------------
#  Worker thread (owns the simulator)
# ---------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self, backend, events):
        super().__init__(daemon=True)
        self.backend = backend
        self.events = events
        self.jobs = queue.Queue()

    def submit(self, text):
        self.jobs.put(text)

    def stop(self):
        self.jobs.put(None)

    def run(self):
        try:
            info = self.backend.start()
        except Exception as exc:
            self.events.put(("fatal", "Could not start the simulator: %s" % exc))
            return
        self.events.put(("ready", None))
        if info:
            self.events.put(("progress", info))

        while True:
            text = self.jobs.get()
            if text is None:
                break
            control.clear_cancel()
            self.events.put(("busy", text))
            try:
                self.events.put(("reply", self.backend.run(text)))
            except Exception as exc:   # show the problem, keep the UI alive
                if type(exc).__name__ == "TaskCancelled":
                    self.events.put(("reply", "Stopped."))
                else:
                    self.events.put(("error", "%s: %s" % (type(exc).__name__, exc)))
            self.events.put(("idle", None))

        self.backend.close()


# ---------------------------------------------------------------------
#  Voice input (microphone -> text)
# ---------------------------------------------------------------------
class VoiceError(Exception):
    pass


def _open_microphone():
    """Returns (speech_recognition module, recognizer, microphone) or raises VoiceError."""
    try:
        import speech_recognition as sr
    except ImportError:
        raise VoiceError(
            "Voice needs the SpeechRecognition package. In your venv run:\n"
            "   sudo apt install portaudio19-dev\n"
            "   pip install SpeechRecognition pyaudio"
        )
    try:
        return sr, sr.Recognizer(), sr.Microphone()
    except Exception as exc:
        raise VoiceError(
            "No usable microphone (%s). Check that a microphone is connected and "
            "selected as the system default input (on WSL, microphones often are "
            "not available). Typing still works." % exc
        )


class VoiceListener:
    """
    The Voice button is a real on/off switch: On starts listening, Off stops
    it immediately, and typing always works either way.

    Built on SpeechRecognition's listen_in_background(), which runs its own
    background thread and hands back a stop function. Calling that stop
    function is synchronous from the caller's point of view: it flips a flag
    the background thread checks BEFORE delivering each phrase, so pressing
    Off can never let one more spoken command slip through afterwards - the
    same guarantee the click gives for typing (the text box is always live,
    voice or no voice). No API key; recognize_google() is Google's free web
    speech endpoint and needs internet.
    """

    def __init__(self, events, opener=_open_microphone):
        self.events = events
        self.opener = opener
        self._stop_listening = None   # set while ON; calling it turns OFF
        self._starting = False
        self._generation = 0          # bumped on every enable(); guards races

    def is_on(self):
        return self._stop_listening is not None

    def enable(self):
        if self._stop_listening is not None or self._starting:
            return
        self._starting = True
        self._generation += 1
        threading.Thread(target=self._start, args=(self._generation,), daemon=True).start()

    def _start(self, generation):
        try:
            sr, recognizer, mic = self.opener()
        except VoiceError as exc:
            self._starting = False
            self.events.put(("voice_failed", str(exc)))
            return
        try:
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
        except Exception as exc:
            self._starting = False
            self.events.put(("voice_failed", "Could not read from the microphone: %s" % exc))
            return

        def on_phrase(_recognizer, audio):
            try:
                text = _recognizer.recognize_google(audio)
            except sr.UnknownValueError:
                return                                     # heard noise, not words
            except Exception as exc:
                self.events.put(("voice_failed", "Voice recognition error: %s" % exc))
                return
            self.events.put(("voice_text", text))

        stopper = recognizer.listen_in_background(mic, on_phrase, phrase_time_limit=8)

        self._starting = False
        if generation != self._generation:
            # disable() was pressed while we were still starting up - the
            # user already expects the mic off, so undo the start instead of
            # surprising them with "Voice: ON" a moment later.
            stopper(wait_for_stop=False)
            return
        self._stop_listening = stopper
        self.events.put(("voice_on", None))

    def disable(self):
        self._generation += 1         # cancels a start currently in flight
        if self._stop_listening is not None:
            self._stop_listening(wait_for_stop=False)
            self._stop_listening = None

    def close(self):
        self.disable()


# ---------------------------------------------------------------------
#  Window
# ---------------------------------------------------------------------
QUICK_COMMANDS = [
    ("Tidy the room", "tidy the room"),
    ("Mess up the room", "mess up the room"),
    ("Put away groceries", "put away the groceries"),
    ("Save energy", "save energy"),
]


class App:
    def __init__(self, root, worker, voice, events):
        self.root = root
        self.worker = worker
        self.voice = voice
        self.events = events
        self.history = []
        self.history_pos = 0
        self.ready = False
        self.busy = False
        self.voice_on = False
        self.closing = False
        self._after_id = None

        root.title("MANTRA - Robot Control")
        root.geometry("920x640")
        root.minsize(640, 440)

        # ---- palette -----------------------------------------------------
        BG        = "#1b1f27"   # app background
        PANEL     = "#232837"   # header / input bar
        LOG_BG    = "#12151c"   # transcript area
        ACCENT    = "#5b8cff"   # primary buttons / user bubble
        ACCENT_HI = "#75a1ff"
        GREEN     = "#33c48f"   # ready / voice-on
        RED       = "#e5566d"   # stop / errors
        TEXT      = "#e8ebf4"
        SUBTEXT   = "#8b93a7"
        ROBOT_BG  = "#2a3040"

        root.configure(bg=BG)
        self._palette = dict(BG=BG, PANEL=PANEL, LOG_BG=LOG_BG, ACCENT=ACCENT,
                             ACCENT_HI=ACCENT_HI, GREEN=GREEN, RED=RED,
                             TEXT=TEXT, SUBTEXT=SUBTEXT, ROBOT_BG=ROBOT_BG)

        def flat_button(parent, text, command, bg=ACCENT, fg="white",
                        active=None, width=None, font=("Segoe UI", 12, "bold")):
            active = active or ACCENT_HI
            b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=active, activeforeground=fg,
                         relief="flat", bd=0, padx=14, pady=7, font=font,
                         cursor="hand2", disabledforeground="#5a6072")
            if width:
                b.configure(width=width)
            b.bind("<Enter>", lambda e, w=b, c=active: w.configure(bg=c) if str(w["state"]) == "normal" else None)
            b.bind("<Leave>", lambda e, w=b, c=bg: w.configure(bg=c) if str(w["state"]) == "normal" else None)
            return b

        # ---- header --------------------------------------------------------
        header = tk.Frame(root, bg=PANEL, height=66)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="\U0001F916", bg=PANEL, fg=TEXT,
                font=("Segoe UI Emoji", 30)).pack(side="left", padx=(14, 6))
        title_box = tk.Frame(header, bg=PANEL)
        title_box.pack(side="left", pady=6)
        tk.Label(title_box, text="MANTRA", bg=PANEL, fg=TEXT,
                font=("Segoe UI", 21, "bold")).pack(anchor="w")
        status_row = tk.Frame(title_box, bg=PANEL)
        status_row.pack(anchor="w")
        self._dot = tk.Canvas(status_row, width=10, height=10, bg=PANEL,
                              highlightthickness=0)
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill="#c9a53b", outline="")
        self._dot.pack(side="left", padx=(0, 6))
        self.status = tk.StringVar(value="Starting simulator... (first run can take a minute)")
        tk.Label(status_row, textvariable=self.status, bg=PANEL, fg=SUBTEXT,
                font=("Segoe UI", 11)).pack(side="left")

        # ---- input row -----------------------------------------------------
        row = tk.Frame(root, bg=PANEL)
        row.pack(fill="x", padx=0, pady=0, side="bottom")
        inner = tk.Frame(row, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=12)

        entry_wrap = tk.Frame(inner, bg="#2d3345", highlightthickness=1,
                              highlightbackground="#3a4256", highlightcolor=ACCENT)
        entry_wrap.pack(side="left", fill="x", expand=True, ipady=2)
        self.entry = tk.Entry(entry_wrap, font=("Segoe UI", 14), bg="#2d3345", fg=TEXT,
                              insertbackground=TEXT, relief="flat", bd=0)
        self.entry.pack(fill="x", expand=True, ipady=6, padx=10)

        self.send_btn = flat_button(inner, "Send", self.on_send, bg=ACCENT, width=7)
        self.send_btn.pack(side="left", padx=(8, 0))
        self.voice_btn = flat_button(inner, "\U0001F3A4 Voice: OFF", self.toggle_voice,
                                     bg="#3a4256", active="#454e66", width=13,
                                     font=("Segoe UI", 11, "bold"))
        self.voice_btn.pack(side="left", padx=(6, 0))
        self.stop_btn = flat_button(inner, "\u25A0 Stop", self.stop_task, bg=RED,
                                    active="#f0748a", width=7)
        self.stop_btn.pack(side="left", padx=(6, 0))
        self.stop_btn.configure(state="disabled", bg="#4a3038", disabledforeground="#8a5a63")

        # ---- quick-command chips ------------------------------------------
        quick = tk.Frame(root, bg=BG)
        quick.pack(fill="x", padx=14, pady=(10, 0), side="bottom")
        self.quick_buttons = []
        chip_colors = ["#3a4256", "#3a4256", "#3a4256", "#3a4256"]
        for (label, command), c in zip(QUICK_COMMANDS, chip_colors):
            b = flat_button(quick, label, lambda cmd=command: self.submit_text(cmd),
                            bg=c, active="#454e66", font=("Segoe UI", 12, "bold"))
            b.pack(side="left", padx=(0, 8))
            self.quick_buttons.append(b)

        # ---- transcript ------------------------------------------------
        log_wrap = tk.Frame(root, bg=BG)
        log_wrap.pack(fill="both", expand=True, padx=14, pady=(12, 0))
        self.log = scrolledtext.ScrolledText(
            log_wrap, wrap="word", state="disabled", bg=LOG_BG, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0,
            font=("Segoe UI", 13), padx=6, pady=6, spacing3=8,
        )
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("user", foreground=ACCENT_HI, font=("Segoe UI", 13, "bold"),
                            spacing1=6, lmargin1=4, lmargin2=4)
        self.log.tag_config("robot", foreground=TEXT, font=("Segoe UI", 13),
                            lmargin1=4, lmargin2=4)
        self.log.tag_config("progress", foreground=SUBTEXT, font=("Segoe UI", 13, "italic"),
                            lmargin1=18, lmargin2=18)
        self.log.tag_config("error", foreground=RED, font=("Segoe UI", 13, "bold"),
                            lmargin1=4, lmargin2=4)

        self.entry.bind("<Return>", lambda e: self.on_send())
        self.entry.bind("<Up>", self.history_up)
        self.entry.bind("<Down>", self.history_down)
        root.bind("<Escape>", lambda e: self.stop_task())
        root.protocol("WM_DELETE_WINDOW", self.exit_app)

        self.refresh_controls()
        self.entry.focus_set()
        self.poll()

    # ----- helpers ---------------------------------------------------
    def write(self, text, tag="robot"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_controls(self):
        p = self._palette
        can_send = self.ready and not self.busy
        state = "normal" if can_send else "disabled"
        for b in self.quick_buttons:
            b.configure(state=state)

        if self.busy:
            self.stop_btn.configure(state="normal", bg=p["RED"], fg="white")
            self._dot.itemconfig(self._dot_id, fill="#e0a940")   # amber = working
        else:
            self.stop_btn.configure(state="disabled", bg="#4a3038", fg="#8a5a63")
            if self.ready:
                self._dot.itemconfig(self._dot_id, fill=p["GREEN"])

        if self.voice_on:
            self.voice_btn.configure(text="\U0001F3A4 Voice: ON", bg=p["GREEN"], activebackground=p["GREEN"])
        else:
            self.voice_btn.configure(text="\U0001F3A4 Voice: OFF", bg="#3a4256", activebackground="#454e66")

    # ----- commands
    # ----- commands ---------------------------------------------------
    def on_send(self):
        text = self.entry.get()
        self.entry.delete(0, "end")
        self.submit_text(text)

    def submit_text(self, text, source="typed"):
        """Single door for typed, quick-button and spoken commands."""
        text = text.strip()
        if not text or self.closing:
            return
        prefix = "You (voice): " if source == "voice" else "You: "
        self.write(prefix + text, "user")
        self.history.append(text)
        self.history_pos = len(self.history)

        if is_exit_word(text):
            self.exit_app()
            return
        if not self.ready:
            self.write("   Still starting up - one moment.", "progress")
            return
        if self.busy:
            self.write("   Busy with a task. Press Stop (or Esc) to cancel it, "
                       "or type 'stop' / 'exit' to close the program.", "progress")
            return
        self.busy = True          # set now so a double-click can't queue twice
        self.refresh_controls()
        self.worker.submit(text)

    def stop_task(self):
        if self.busy:
            control.request_cancel()
            self.status.set("Stopping...")
            self.write("(stop requested)", "progress")

    def toggle_voice(self):
        if self.voice_on or self.voice.is_on():
            self.voice.disable()
            self.voice_on = False
            self.write("Voice off - type your command below.", "progress")
            self.refresh_controls()
            self.entry.focus_set()          # ready for typing right away
        else:
            self.voice_btn.configure(text="\U0001F3A4 Starting...")
            self.voice.enable()
            self.refresh_controls()

    def exit_app(self):
        if self.closing:
            return
        self.closing = True
        self.write("Goodbye.", "robot")
        self.status.set("Shutting down...")
        self.root.update_idletasks()
        control.request_cancel()
        self.voice.close()
        self.worker.stop()
        self.worker.join(timeout=15)
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
        self.root.destroy()

    def history_up(self, _event):
        if self.history and self.history_pos > 0:
            self.history_pos -= 1
            self.entry.delete(0, "end")
            self.entry.insert(0, self.history[self.history_pos])

    def history_down(self, _event):
        self.entry.delete(0, "end")
        if self.history_pos < len(self.history) - 1:
            self.history_pos += 1
            self.entry.insert(0, self.history[self.history_pos])
        else:
            self.history_pos = len(self.history)

    # ----- messages from the worker / voice threads --------------------
    def poll(self):
        if self.closing:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self.handle(kind, payload)
        except queue.Empty:
            pass
        self._after_id = self.root.after(100, self.poll)

    def handle(self, kind, payload):
        if kind == "ready":
            self.ready = True
            self.status.set("Ready")
            self._dot.itemconfig(self._dot_id, fill=self._palette["GREEN"])
            self.write("MANTRA is ready. Type a command, use a button, or turn Voice on. "
                       "Type 'exit' to quit.", "progress")
            self.refresh_controls()
            if bool(getattr(config, "VOICE_INPUT", False)):
                self.toggle_voice()
        elif kind == "fatal":
            self.status.set("Simulator failed to start")
            self._dot.itemconfig(self._dot_id, fill=self._palette["RED"])
            self.write(payload, "error")
        elif kind == "busy":
            self.busy = True
            self.status.set("Working on: " + payload)
            self.refresh_controls()
        elif kind == "progress":
            self.write("   " + payload, "progress")
        elif kind == "reply":
            self.write("Robot: " + str(payload), "robot")
        elif kind == "error":
            self.write("Error - " + payload, "error")
        elif kind == "idle":
            self.busy = False
            self.status.set("Ready")
            self.refresh_controls()
        elif kind == "voice_on":
            self.voice_on = True
            self.write("Voice input on - listening. Say 'exit' to quit.", "progress")
            self.refresh_controls()
        elif kind == "voice_failed":
            self.voice_on = False
            self.write("Voice could not start: " + payload, "error")
            self.refresh_controls()
            self.entry.focus_set()
        elif kind == "voice_text":
            if self.voice_on:     # ignore a phrase that arrives after Voice was switched off
                self.submit_text(payload, source="voice")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true",
                        help="run without the simulator (tests the window only)")
    args = parser.parse_args()

    events = queue.Queue()
    control.set_progress_callback(lambda msg: events.put(("progress", msg)))

    worker = Worker(DemoBackend() if args.demo else RealBackend(), events)
    voice = VoiceListener(events)
    worker.start()

    root = tk.Tk()
    App(root, worker, voice, events)
    root.mainloop()


if __name__ == "__main__":
    main()