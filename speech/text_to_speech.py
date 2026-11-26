"""
Text-to-speech feedback using pyttsx3 (fully offline, no API key,
works on Windows/macOS/Linux).

Respects config.USE_VOICE_OUTPUT:
  - If USE_VOICE_OUTPUT is False, this module never touches pyttsx3/
    espeak/aplay at all -- it just prints the robot's response. This is
    the safe, error-free path on machines/containers with no real audio
    output (no sound card, no aplay, no ALSA config).
  - If USE_VOICE_OUTPUT is True, it tries real TTS playback and falls
    back to printing if the TTS engine or audio device isn't available,
    without ever raising an exception up to the caller.
"""

import config

_engine = None
_engine_failed = False  # remembers a previous init failure so we don't retry every call


def _get_engine():
    global _engine
    if _engine is None:
        import pyttsx3

        _engine = pyttsx3.init()
        _engine.setProperty("rate", config.TTS_RATE)
    return _engine


def speak(text):
    """
    Prints the robot's response, and additionally speaks it aloud only if
    config.USE_VOICE_OUTPUT is True and a working TTS/audio backend is available.
    Never raises -- always safe to call.
    """
    print(f"Robot: {text}")

    if not config.USE_VOICE_OUTPUT:
        return

    global _engine_failed
    if _engine_failed:
        # We already know TTS playback doesn't work here; don't retry
        # every single call (that's what was spamming the ALSA errors).
        return

    try:
        engine = _get_engine()
        engine.say(text)
        engine.runAndWait()
    except Exception as exc:
        _engine_failed = True
        print(f"[text_to_speech] Voice output unavailable ({exc}); switching to text-only for the rest of this run.")
