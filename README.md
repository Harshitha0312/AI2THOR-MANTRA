# AI2THOR-MANTRA

MANTRA (Manipulation and Training Architecture)

MANTRA is an embodied Ai framework built using AI2-THOR for autonomous household task execution.

The current implementation focuses on an intelligent tidy-up agent that identifies misplaced objects, determines their most suitable destination using preference-based reasoning, and autonomously relocates them within a simulated home environment.

## Current Features

- Autonomous household tidy-up agent
- Preference-based object placement
- Scene-aware destination selection
- Automatic detection of misplaced objects
- object navigation, pickup and placement
- Navigation and interaction utilities
- Recovery strategies for failed placements
- Modular architecture for easy extension of new tasks

## Project Structure

AI2THOR-MANTRA
└── pyai
    ├── tasks
    │   └── tidy.py
    └── utils
        ├── actions.py
        └── preference.py

## Current Workflow

The agent performs the following steps:

1. Scan the environment.
2. Detect misplaced pickupable objects.
3. Determine the best destination using object preferences.
4. Navigate to the object.
5. Pick up the object.
6. Navigate to the selected destination.
7. Attempt placement with recovery strategies if necessary.
8. Continue until no misplaced objects remain.

## Technologies Used

- Python
- AI2-THOR
- Preference-based Decision-Making

## Future Improvements

This project is intended to grow into a complete embodied AI framework. Planned improvements include:

- Accept natural language commands (e.g., *"Clean the living room"*).
- Support voice-based interaction.
- Replace random exploration with intelligent path planning.
- Add more autonomous household tasks beyond tidy-up.
- Introduce reinforcement learning for adaptive decision-making.
- Improve scene understanding for smarter object placement.
- Expand to multi-room and whole-house task execution.

## Project Status

This project is currently under active development, with new features and improvements being added continuously.
