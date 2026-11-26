"""
Thin wrapper around the AI2-THOR / ManipulaTHOR Controller so the rest
of the project never has to import ai2thor directly.
"""

from ai2thor.controller import Controller

import config


def get_controller():
    """
    Creates and returns a ready AI2-THOR controller using the settings
    in config.py. Raises a clear error if AI2-THOR cannot start
    (e.g. missing Unity build download, no display, etc.).
    """
    try:
        controller = Controller(
            scene=config.SCENE,
            width=config.WIDTH,
            height=config.HEIGHT,
            gridSize=config.GRID_SIZE,
            rotateStepDegrees=config.ROTATE_STEP_DEGREES,
            snapToGrid=True,
            visibilityDistance=config.VISIBILITY_DISTANCE,
            fieldOfView=config.FIELD_OF_VIEW,
        )
        return controller
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "Could not start AI2-THOR. Make sure the 'ai2thor' package is "
            "installed and that this machine can run its Unity build "
            f"(headless servers may need extra setup). Original error: {exc}"
        ) from exc
