# MANTRA – Simulation-Based Robot Object Interaction

MANTRA is a robot interaction system developed using AI2-THOR and ManipulaTHOR. It allows users to give instructions through text, understands what needs to be done, and guides the robot through the required actions in a simulated environment.

The robot can identify objects, move towards them, pick them up, and place them in the required locations. It can also perform actions such as opening and closing objects.

## 1. Project Structure

```text
MANTRA/
├── main.py                    # Main file to run the project
├── executor.py                # Executes the planned actions
├── config.py                  # Project settings and configurations
├── requirements.txt           # Required Python packages
├── nlp/
│   └── command_parser.py      # Converts text instructions into structured commands
├── planner/
│   ├── action_validator.py    # Checks whether commands are valid
│   └── task_planner.py        # Converts commands into robot actions
├── robot/
│   ├── sim_controller.py      # Starts the AI2-THOR simulator
│   ├── navigation.py          # Handles robot movement
│   ├── manipulation.py        # Handles pickup, placement, opening and closing
│   └── object_detection.py    # Identifies objects in the scene
└── environment/
    └── scene_manager.py       # Provides object and scene information
```

## 2. Installation

Open the project in PyCharm and run the following command in the terminal:

```bash
pip install -r requirements.txt
```

Make sure Python and the required dependencies are installed before running the project.

## 3. Running the Project

Open `main.py` in PyCharm and run it.

On the first launch, AI2-THOR may download its Unity build. An internet connection and a supported display environment are required for the initial setup.

After successful initialization, the terminal should display something similar to:

```text
Starting MANTRA...
AI2-THOR ready in scene 'FloorPlan1'.
Objects currently visible: [...]
Robot: MANTRA is ready. Enter a command.
```

You can enter instructions such as:

```text
Pick up the apple
Pick up the apple and put it on the table
Put the apple in the fridge
Open the fridge
Find the mug
Go to the sink
```

Enter `exit`, `quit`, or `stop` to close the program.

## 4. How the System Works

MANTRA follows a sequence of steps to understand and execute a user instruction.

For example, when the user enters:

**"Put the apple in the fridge"**

The command passes through the following stages:

1. **Command Processing:** The command parser reads the instruction and identifies the required action, object, and destination.

2. **Action Validation:** The action validator checks whether the command is supported by the system.

3. **Task Planning:** The task planner breaks the instruction into smaller actions.

4. **Navigation and Object Identification:** The system identifies the apple and fridge using scene information and moves the robot towards them.

5. **Object Manipulation:** The robot picks up the apple, opens the fridge, places the apple inside, and closes the fridge.

The command is converted into a sequence similar to:

```python
[
    ("find", "apple"),
    ("go_to", "apple"),
    ("pickup", "apple"),
    ("find", "fridge"),
    ("go_to", "fridge"),
    ("open", "fridge"),
    ("put", "apple", "fridge"),
    ("close", "fridge")
]
```

Once the actions are completed, the system provides feedback about the result.

## 5. Performance Metrics

MANTRA records performance information to help evaluate how the robot performs each task.

Example:

```text
[metrics] accuracy 100% | time 17.0s | steps 22 | placed 1 | failed to place 0
```

The metrics represent:

- **Accuracy:** Percentage of the task completed successfully.
- **Time:** Total time taken to complete the task, in seconds.
- **Steps:** Total number of walking, turning, and manipulation actions.
- **Placed:** Number of objects successfully placed.
- **Failed to place:** Number of objects that could not be placed successfully.

Enter `stats` to view the session totals.

## 6. Customization and Further Development

The system is designed in separate modules, making it easier to modify and extend individual features.

- **Adding New Commands:** New command patterns can be added to `VERB_MAP` in `nlp/command_parser.py`, along with the corresponding logic in `planner/task_planner.py`.

- **Improving Language Understanding:** The system can use an LLM-based parser by enabling `USE_LLM_PARSER = True` in `config.py`. This requires installing the Anthropic package and configuring an API key. The rule-based parser is available as a fallback.

- **Handling Multiple Objects:** The task planner can be extended to identify and process multiple objects in a single instruction.

- **Adjusting Simulation Settings:** Navigation, object placement, and simulation behaviour can be configured through `config.py`.

## 7. Technical Notes

- The system uses AI2-THOR scene metadata to identify objects and their properties. This provides information such as object IDs, positions, and interaction states.

- Navigation and manipulation are handled through separate modules, keeping the implementation organised and easier to maintain.

- Simulator actions are checked using `lastActionSuccess`. If an action fails, the system reports the error instead of continuing as if it had succeeded.

- The project uses AI2-THOR 5.0.0, with actions such as `PickupObject`, `PutObject`, `OpenObject`, `CloseObject`, `GetInteractablePositions`, and `Teleport`.

- Using a different AI2-THOR version may require changes to the supported simulator actions.

---

MANTRA brings together natural language processing, task planning, navigation, and object manipulation in a simulated robotic environment. The project focuses on making robot interactions more organised and reliable while providing a foundation for further development in robotic task execution.
