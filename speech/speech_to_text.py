"""
Speech-to-text using the free 'SpeechRecognition' library, which by
default uses Google's public web speech API (no API key needed for
light/personal use). If no working microphone is found, listen()
automatically falls back to typed input so the rest of the project
still runs without erroring out.
"""

import contextlib
import os

import config


@contextlib.contextmanager
def _suppress_alsa_errors():
    """
    PyAudio/ALSA print raw diagnostic messages ("ALSA lib ... cannot
    find card '0'", "Unknown PCM cards.pcm.front", etc.) straight to
    file descriptor 2 whenever it probes sound devices that don't
    exist - extremely common on cloud servers / containers / PyCharm
    remote interpreters with no real sound card. Those lines come from
    the ALSA C library itself, not from Python, so a Python
    try/except can never catch or hide them - the only reliable fix is
    to redirect the OS-level stderr file descriptor while the
    microphone is being opened, then restore it immediately after.
    This has no effect on any real Python exception, which is still
    raised/caught normally below.
    """
    try:
        devnull_fd = os.open(os.devnull, os.O_RDWR)
    except OSError:
        yield
        return

    saved_stderr_fd = os.dup(2)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_stderr_fd, 2)
        os.close(saved_stderr_fd)
        os.close(devnull_fd)


def listen():
    """
    Records one utterance from the microphone and returns the
    recognized text (lowercased). Falls back to typed input on any
    microphone / recognition failure.
    """
    if not config.USE_VOICE_INPUT:
        return input("Enter command: ").strip().lower()

    try:
        import speech_recognition as sr
    except ImportError:
        print("[speech_to_text] 'SpeechRecognition' not installed, using typed input.")
        return input("Enter command: ").strip().lower()

    recognizer = sr.Recognizer()

    try:
        with _suppress_alsa_errors():
            with sr.Microphone() as source:
                print("Listening... (speak now)")
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=10)
    except (OSError, sr.WaitTimeoutError) as exc:
        print(f"[speech_to_text] Microphone unavailable ({exc}); using typed input.")
        return input("Enter command: ").strip().lower()

    try:
        text = recognizer.recognize_google(audio)
        print(f"You said: {text}")
        return text.strip().lower()
    except sr.UnknownValueError:
        print("[speech_to_text] Could not understand the audio.")
        return ""
    except sr.RequestError as exc:
        print(f"[speech_to_text] Speech recognition service error: {exc}; using typed input.")
        return input("Enter command: ").strip().lower()
