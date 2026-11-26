"""
Real, physics-respecting navigation for MANTRA, replacing every
Teleport-across-the-room call with actual movement.

Two algorithms, two different jobs:

  - A* searches the room's reachable-position grid for the shortest
    walkable path between two points, so the agent only ever steps
    onto cells AI2-THOR itself reports as reachable - that's what
    rules out collisions: a path can't walk through a wall or a piece
    of furniture because A* never expands into a cell that isn't in
    the reachable set in the first place. Every individual MoveAhead
    is also checked for lastActionSuccess as it's executed, and if a
    step still fails (something moved into the way since the path was
    planned), the remaining route is re-planned on the spot from
    wherever the agent actually ended up.

  - TSP decides, given several target points, the order to visit them
    in that minimizes total walking distance - used only where the
    rest of the project already gathers multiple destinations into one
    list ahead of time (the checkpoint sweep in put_away_groceries).
    Everywhere else the project already decides one target at a time
    (tidy_room, save_energy, single-object voice commands), so there's
    nothing to re-order there - TSP needs more than one waypoint to
    mean anything, and A* alone is doing the "no collisions" work in
    both places.

config.GRID_SIZE / config.ROTATE_STEP_DEGREES already assume the same
4-connected, 90-degree-turn grid this module relies on, since that's
also how sim_controller.py starts the Controller (snapToGrid=True).
"""

import heapq
import itertools
import math

import config
from robot import control, metrics, sync


# ---------------------------------------------------------------------
# 1. Reachable-position graph
# ---------------------------------------------------------------------

def get_reachable_graph(controller, grid_size=None, reachable_positions=None):
    """
    Builds a 4-connected grid graph over every position AI2-THOR reports
    as reachable. reachable_positions can be passed in if the caller
    already fetched it this step (saves a GetReachablePositions round
    trip); otherwise it's fetched fresh here.
    """
    grid_size = grid_size or config.GRID_SIZE

    if reachable_positions is None:
        event = controller.step(action="GetReachablePositions")
        reachable_positions = event.metadata.get("actionReturn") or []

    def key(p):
        # round(..., 4) at the end matters: round(x/grid_size)*grid_size can
        # land on something like 0.7500000000000001 due to float noise,
        # which then fails to match the cleanly-rounded candidate keys
        # computed in the neighbor-building loop below (round(..., 4)) -
        # silently breaking edges between cells that are actually adjacent.
        return (round(round(p["x"] / grid_size) * grid_size, 4),
                round(round(p["z"] / grid_size) * grid_size, 4))

    nodes = {key(p) for p in reachable_positions}

    neighbors = {}
    deltas = [(grid_size, 0), (-grid_size, 0), (0, grid_size), (0, -grid_size)]
    for node in nodes:
        adj = []
        for dx, dz in deltas:
            cand = (round(node[0] + dx, 4), round(node[1] + dz, 4))
            if cand in nodes:
                adj.append(cand)
        neighbors[node] = adj

    return nodes, neighbors


def nearest_node(nodes, x, z):
    return min(nodes, key=lambda n: (n[0] - x) ** 2 + (n[1] - z) ** 2)


# ---------------------------------------------------------------------
# 2. A* - shortest walkable path between two points
# ---------------------------------------------------------------------

def _heuristic(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def astar(start, goal, neighbors):
    if start == goal:
        return 0.0, [start]

    open_set = [(_heuristic(start, goal), 0.0, start)]
    came_from = {}
    g_score = {start: 0.0}
    visited = set()

    while open_set:
        _, g, current = heapq.heappop(open_set)
        if current in visited:
            continue
        visited.add(current)

        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return g, path

        for nxt in neighbors.get(current, []):
            tentative_g = g + _heuristic(current, nxt)
            if tentative_g < g_score.get(nxt, float("inf")):
                g_score[nxt] = tentative_g
                came_from[nxt] = current
                heapq.heappush(open_set, (tentative_g + _heuristic(nxt, goal), tentative_g, nxt))

    return float("inf"), []


# ---------------------------------------------------------------------
# 3. TSP - visiting order over several waypoints (only used by the
#    checkpoint sweep in put_away_groceries, the one place the project
#    already builds a multi-point list ahead of time)
# ---------------------------------------------------------------------

def build_distance_matrix(waypoint_nodes, neighbors):
    n = len(waypoint_nodes)
    dist_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            cost, _ = astar(waypoint_nodes[i], waypoint_nodes[j], neighbors)
            dist_matrix[i][j] = cost
    return dist_matrix


def _tsp_held_karp(dist_matrix, start=0):
    n = len(dist_matrix)
    if n <= 2:
        return list(range(n))

    others = [i for i in range(n) if i != start]
    dp = {}
    for i in others:
        dp[(frozenset([i]), i)] = (dist_matrix[start][i], [start, i])

    for size in range(2, len(others) + 1):
        for subset in itertools.combinations(others, size):
            subset_fs = frozenset(subset)
            for last in subset:
                prev_subset = subset_fs - {last}
                best_cost, best_path = float("inf"), None
                for k in prev_subset:
                    if (prev_subset, k) not in dp:
                        continue
                    cost = dp[(prev_subset, k)][0] + dist_matrix[k][last]
                    if cost < best_cost:
                        best_cost, best_path = cost, dp[(prev_subset, k)][1] + [last]
                if best_path is not None:
                    dp[(subset_fs, last)] = (best_cost, best_path)

    full_set = frozenset(others)
    _, best_path = min(
        (dp[(full_set, last)] for last in others if (full_set, last) in dp),
        key=lambda x: x[0],
    )
    return best_path


def _tsp_nearest_neighbor_2opt(dist_matrix, start=0):
    n = len(dist_matrix)
    unvisited = set(range(n)) - {start}
    route = [start]
    current = start
    while unvisited:
        nxt = min(unvisited, key=lambda j: dist_matrix[current][j])
        route.append(nxt)
        unvisited.remove(nxt)
        current = nxt

    def cost(r):
        return sum(dist_matrix[r[i]][r[i + 1]] for i in range(len(r) - 1))

    improved = True
    while improved:
        improved = False
        for i in range(1, len(route) - 1):
            for j in range(i + 1, len(route)):
                new_route = route[:i] + route[i:j + 1][::-1] + route[j + 1:]
                if cost(new_route) < cost(route):
                    route, improved = new_route, True
    return route


def solve_tsp(dist_matrix, start=0):
    n = len(dist_matrix)
    return _tsp_held_karp(dist_matrix, start) if n <= 13 else _tsp_nearest_neighbor_2opt(dist_matrix, start)


# ---------------------------------------------------------------------
# 4. Walking a planned path: convert grid nodes into MoveAhead/Rotate
#    actions and execute them, replanning locally on any failed step
# ---------------------------------------------------------------------

def _path_to_actions(path, grid_size, start_rotation):
    actions = []
    rotation = start_rotation
    dir_map = {
        (0, grid_size): 0,
        (grid_size, 0): 90,
        (0, -grid_size): 180,
        (-grid_size, 0): 270,
    }
    for i in range(len(path) - 1):
        dx = round(path[i + 1][0] - path[i][0], 4)
        dz = round(path[i + 1][1] - path[i][1], 4)
        target_rot = dir_map.get((dx, dz))
        if target_rot is None:
            continue
        diff = (target_rot - rotation) % 360
        if diff == 90:
            actions.append("RotateRight")
        elif diff == 270:
            actions.append("RotateLeft")
        elif diff == 180:
            actions.extend(["RotateRight", "RotateRight"])
        rotation = target_rot
        actions.append("MoveAhead")
    return actions


def _snap_heading(controller):
    """
    _path_to_actions assumes the agent faces a multiple of 90 degrees
    (RotateLeft/Right are 90-degree steps). But face_position() /
    frame_object() deliberately aim the agent at an arbitrary angle (e.g.
    317 degrees) to look at an object - after that, the next A* walk used
    to start from a heading the planner didn't know about, so MoveAhead
    went off in the wrong direction and the agent never arrived. Before
    every walk, turn (in place, visibly) to the nearest 90 degrees.
    """
    agent = controller.last_event.metadata["agent"]
    yaw = agent["rotation"]["y"] % 360
    snapped = (round(yaw / 90.0) * 90) % 360
    if abs(((yaw - snapped) + 180) % 360 - 180) > 0.5:
        smooth_turn(controller, snapped, horizon=agent.get("cameraHorizon", 0))


def _step_paced(controller, action):
    """
    The single place where a walking/turning step is executed: one
    controller.step, then a short pace() wait so the motion is visible
    (config.MOVE_STEP_DELAY / TURN_STEP_DELAY) - not random sleeps spread
    around the project.
    """
    event = controller.step(action=action)
    if action.startswith("Move"):
        metrics.inc("walk_steps")
    sync.pace("turn" if action.startswith("Rotate") else "move")
    return event


def _without(neighbors, blocked):
    """Copy of the walk graph with the `blocked` cells removed."""
    return {
        n: [m for m in adj if m not in blocked]
        for n, adj in neighbors.items() if n not in blocked
    }


_SIDESTEPS = ("MoveLeft", "MoveRight", "MoveAhead", "MoveBack")


def _sidestep(controller, attempt):
    """
    Something is in the way: nudge the robot a little - left, right,
    forward or back (keeps its heading, so nothing needs re-aiming) - to
    get off whatever it is snagged on. Tries each direction in turn and
    stops at the first one that moves. Later attempts alternate which
    side goes first and take two steps instead of one, so repeated
    failures don't keep trying the same nudge. Returns steps taken.
    """
    order = list(_SIDESTEPS)
    if attempt % 2 == 0:
        order[0], order[1] = order[1], order[0]
    n = 1 if attempt < 3 else 2
    for act in order:
        taken = 0
        for _ in range(n):
            if control.is_cancelled():
                return taken
            ev = _step_paced(controller, act)
            if not ev.metadata["lastActionSuccess"]:
                break
            taken += 1
        if taken:
            metrics.inc("walk_sidesteps")
            return taken
    return 0


def _walk_path(controller, path, neighbors, grid_size, goal_node, blocked_out=None):
    """
    Executes a planned path step by step and does not give up when
    something is in the way. When a MoveAhead fails:

      1. the cell it tried to enter is remembered as BLOCKED (even if
         the simulator's reachable list still contains it - a held
         object, a chair that moved, a door);
      2. the robot nudges left / right / forward / back a little
         (_sidestep) to get clear of the obstacle;
      3. the walkable cells are re-read from the live simulator, the
         blocked ones are removed, and A* plans a fresh route from where
         the robot actually stands - around the obstacle.

    That repeats up to config.WALK_REPLAN_LIMIT times. Before mess-fix it
    re-planned once over the SAME stale graph, so A* picked the same
    blocked cell again and the robot just stood there holding its object.
    Every walk in the project (single commands, tidy, mess, the TSP
    tours) goes through here.
    """
    limit = int(getattr(config, "WALK_REPLAN_LIMIT", 6))
    blocked = blocked_out if blocked_out is not None else set()
    failures = 0
    current = path

    while current and len(current) > 1:
        _snap_heading(controller)
        rotation = controller.last_event.metadata["agent"]["rotation"]["y"]
        actions = _path_to_actions(current, grid_size, rotation)

        moves_done, failed = 0, False
        for action in actions:
            if control.is_cancelled():
                return controller.last_event
            event = _step_paced(controller, action)
            if event.metadata["lastActionSuccess"]:
                if action == "MoveAhead":
                    moves_done += 1
                continue
            failed = True
            if action == "MoveAhead":
                blocked.add(current[min(moves_done + 1, len(current) - 1)])
            break

        if not failed:
            break

        failures += 1
        metrics.inc("walk_blocked")
        if failures > limit:
            control.say("Something keeps blocking the way - giving up on this route.")
            break

        _sidestep(controller, failures)

        # Live walkable cells (obstacles that moved are reflected), minus
        # every cell that has proven blocked during this walk.
        nodes, fresh = get_reachable_graph(controller, grid_size)
        graph = _without(fresh, blocked) if blocked else fresh
        if not graph:
            break
        agent = controller.last_event.metadata["agent"]["position"]
        start = nearest_node(set(graph), agent["x"], agent["z"])
        goal = goal_node if goal_node in graph else nearest_node(set(graph), goal_node[0], goal_node[1])
        _, current = astar(start, goal, graph)
        metrics.inc("walk_replans")

    return controller.last_event


# ---------------------------------------------------------------------
# 5. Public entry points - these are what the rest of the project calls
# ---------------------------------------------------------------------

def _component(start, neighbors):
    """Every grid cell the agent can actually walk to from `start` (flood fill)."""
    seen, frontier = {start}, [start]
    while frontier:
        cur = frontier.pop()
        for nxt in neighbors.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def _arrived(controller, node, grid_size, tolerance=1.5):
    """Standing on `node` or right beside it (the goal cell itself may be the blocked one)."""
    here = controller.last_event.metadata["agent"]["position"]
    return math.hypot(here["x"] - node[0], here["z"] - node[1]) <= tolerance * grid_size


def walk_to_position(controller, target_position, grid_size=None, reachable_positions=None):
    """
    Walks the agent, via real MoveAhead/Rotate actions along an A*
    path, to the reachable grid cell nearest target_position (a dict
    with "x"/"z"). Returns the final simulator event. This is the
    single-target building block every navigation call in the project
    goes through instead of Teleport.

    When the way is blocked it does not give up (Nlp26):
      1. best path first: A* over the walkable cells;
      2. goal in a pocket the agent cannot walk to (other side of a
         counter, behind furniture) -> head for the nearest cell it CAN
         reach instead of standing still;
      3. the walk itself sidesteps and re-plans around anything in the
         way (see _walk_path);
      4. still not there -> try up to config.WALK_FALLBACK_GOALS other
         standing spots next to the target, nearest first, skipping any
         cell that proved blocked.
    """
    grid_size = grid_size or config.GRID_SIZE
    nodes, neighbors = get_reachable_graph(controller, grid_size, reachable_positions)
    if not nodes:
        return controller.last_event

    goal_node = nearest_node(nodes, target_position["x"], target_position["z"])
    agent_pos = controller.last_event.metadata["agent"]["position"]
    start_node = nearest_node(nodes, agent_pos["x"], agent_pos["z"])

    _, path = astar(start_node, goal_node, neighbors)
    reachable_set = None
    if not path:
        reachable_set = _component(start_node, neighbors)
        goal_node = nearest_node(reachable_set, target_position["x"], target_position["z"])
        control.say("That spot is cut off - going as close as I can.")
        metrics.inc("walk_fallback_goal")
        _, path = astar(start_node, goal_node, neighbors)
    if not path:
        return controller.last_event

    blocked = set()
    _walk_path(controller, path, neighbors, grid_size, goal_node, blocked_out=blocked)
    if _arrived(controller, goal_node, grid_size) or control.is_cancelled():
        return controller.last_event

    # Blocked on the way: try other standing spots beside the target.
    tries = int(getattr(config, "WALK_FALLBACK_GOALS", 3))
    here = controller.last_event.metadata["agent"]["position"]
    here_node = nearest_node(nodes, here["x"], here["z"])
    if reachable_set is None:
        reachable_set = _component(here_node, neighbors)
    tried = {goal_node} | blocked
    ranked = sorted(
        (n for n in reachable_set if n not in tried),
        key=lambda n: math.hypot(n[0] - target_position["x"], n[1] - target_position["z"]),
    )
    for alt in ranked[: tries * 3]:
        if tries <= 0 or control.is_cancelled():
            break
        here = controller.last_event.metadata["agent"]["position"]
        cur = nearest_node(nodes, here["x"], here["z"])
        graph = _without(neighbors, blocked) if blocked else neighbors
        if cur == alt or cur not in graph or alt not in graph:
            continue
        _, alt_path = astar(cur, alt, graph)
        if not alt_path:
            continue
        tries -= 1
        control.say("Route blocked - trying another spot next to it.")
        metrics.inc("walk_fallback_goal")
        _walk_path(controller, alt_path, neighbors, grid_size, alt, blocked_out=blocked)
        if _arrived(controller, alt, grid_size):
            break
    return controller.last_event


def smooth_turn(controller, target_yaw, horizon=0):
    """
    Turns the agent in place to target_yaw (degrees) and tilts the camera
    to `horizon`, in config.TURN_INTERP_DEGREES increments with a short
    pace() between them, so the turn is visible instead of an instant
    snap. Same Teleport-in-place trick as before (position unchanged -
    this is aiming, not navigation); only the yaw is interpolated, the
    final step carries the requested horizon exactly as the old
    single-Teleport version did. Returns the last event.
    """
    agent = controller.last_event.metadata["agent"]
    pos = agent["position"]
    cur_yaw = agent["rotation"]["y"] % 360
    cur_hor = agent.get("cameraHorizon", 0)
    target_yaw %= 360

    diff = ((target_yaw - cur_yaw + 180) % 360) - 180
    step_deg = max(5, int(getattr(config, "TURN_INTERP_DEGREES", 30) or 30))
    n = max(1, int(math.ceil(abs(diff) / step_deg)))

    event = controller.last_event
    for i in range(1, n):                      # intermediate yaw-only steps
        yaw = (cur_yaw + diff * i / n) % 360
        event = controller.step(
            action="Teleport", position=pos,
            rotation=dict(x=0, y=yaw, z=0), horizon=cur_hor, forceAction=True,
        )
        if not event.metadata["lastActionSuccess"]:
            break
        sync.pace("turn")

    return controller.step(                    # final step: exact yaw + horizon
        action="Teleport", position=pos,
        rotation=dict(x=0, y=target_yaw, z=0), horizon=horizon, forceAction=True,
    )


def face_position(controller, target_position, horizon=0):
    """
    Turns the agent in place (zero displacement - not navigation, just
    aiming) to face target_position, optionally tilting the camera.
    rotateStepDegrees only allows 90-degree increments, so a precise
    turn-to-angle uses Teleport with the same position and a new
    rotation (see smooth_turn, which also makes it visible).
    """
    agent = controller.last_event.metadata["agent"]
    dx = target_position["x"] - agent["position"]["x"]
    dz = target_position["z"] - agent["position"]["z"]
    yaw = math.degrees(math.atan2(dx, dz)) % 360
    return smooth_turn(controller, yaw, horizon=horizon)


def walk_to_object(controller, obj, reachable_positions=None, grid_size=None, horizon=30):
    """
    Drop-in replacement for the old _teleport_to_object() /
    _walk_to() helpers: walks to the object instead of teleporting,
    then faces it precisely (see face_position) so it ends up in
    camera view for the pickup/open/toggle action that follows -
    matching the horizon=30 downward tilt those helpers already used.
    Returns the final event, same as before.
    """
    grid_size = grid_size or config.GRID_SIZE
    walk_to_position(controller, obj["position"], grid_size, reachable_positions)
    return face_position(controller, obj["position"], horizon=horizon)


def approach_object(controller, obj, reachable_positions, interact_dist=1.2, horizon=30, max_candidates=5, skip=0):
    """
    Walks the agent close enough to obj to actually interact with it
    (pick up, open, place into), trying the next-nearest standing
    position if the first one doesn't land within interact_dist.

    This matters because a single nearest-reachable-cell walk can leave
    the agent at a spot that LOOKS close - an island counter edge, the
    far side of a table - while the object's real metadata distance
    (or visibility) still says it isn't actually reachable from there.
    Without retrying a few genuinely different standing spots, that
    looks exactly like "the object is right in front of it but pickup
    still fails": the agent walked once, landed somewhere plausible but
    wrong, and never tried anywhere else.

    Always walks (via walk_to_position) rather than first checking
    "is obj['distance'] already small enough, skip walking" - that
    straight-line distance is also what causes agents to stand still
    and attempt a pickup from clear across an obstacle: two spots can
    be close in a straight line while a wall or counter sits between
    them. Walking to an already-correct spot is cheap (A* returns an
    empty, instant path when start == goal), so there's no real cost
    to always attempting it.

    `skip` starts the search that many standing spots further out, so a
    retry after a failed pickup tries genuinely different places.

    Returns (fresh_obj_metadata, reached: bool).
    """
    object_id = obj["objectId"]
    pos = obj["position"]

    # Candidate standing spots: nearest to the object first, but only ones A*
    # can actually walk to from where the robot is now. Before, an
    # unreachable spot (other side of a counter / stove) made walk_to_position
    # return without moving and the caller then acted from far away.
    nodes, neighbors = get_reachable_graph(controller, reachable_positions=reachable_positions)
    agent = controller.last_event.metadata["agent"]["position"]
    start = nearest_node(nodes, agent["x"], agent["z"])
    by_distance = sorted(
        reachable_positions,
        key=lambda c: math.hypot(c["x"] - pos["x"], c["z"] - pos["z"]),
    )
    wanted = skip + max_candidates
    ranked = []
    for cell in by_distance[:max(40, wanted * 6)]:
        node = nearest_node(nodes, cell["x"], cell["z"])
        cost, _ = astar(start, node, neighbors)
        if cost != float("inf"):
            ranked.append(cell)
            if len(ranked) >= wanted:
                break
    cells = ranked[skip:skip + max_candidates]

    current = obj
    for cell in cells:
        walk_to_position(controller, cell, reachable_positions=reachable_positions)
        here = controller.last_event.metadata["agent"]["position"]
        arrived = math.hypot(here["x"] - cell["x"], here["z"] - cell["z"]) <= 1.5 * (grid_size_of(controller))
        face_position(controller, pos, horizon=horizon)
        fresh = next(
            (o for o in controller.last_event.metadata["objects"] if o["objectId"] == object_id),
            None,
        )
        if fresh is None:
            return current, False
        current = fresh
        if arrived and (current["distance"] <= interact_dist or current.get("visible")):
            return current, True

    return current, False


def grid_size_of(controller):
    return float(getattr(config, "GRID_SIZE", 0.25))


def walk_random(controller, reachable_positions, grid_size=None):
    """
    Drop-in replacement for _teleport_random(): walks to a random
    reachable position instead of jumping straight there.
    """
    import random
    grid_size = grid_size or config.GRID_SIZE
    target = random.choice(reachable_positions)
    return walk_to_position(controller, target, grid_size, reachable_positions)


def plan_object_tour(controller, objects, grid_size=None, reachable_positions=None):
    """
    Given a list of object metadata dicts (each with a "position"),
    plans - but does not execute - a TSP-optimized visiting order over
    them, starting from the agent's current position, using A* for the
    real pairwise walking distance between every pair. This is the
    "global path planning" step: figure out the whole route up front,
    once, rather than deciding one target at a time as the agent goes.

    Returns (ordered_objects, total_distance): objects reordered into
    the planned visiting sequence, and the total walking distance that
    route covers, so the caller can print the plan before executing it.
    """
    grid_size = grid_size or config.GRID_SIZE
    nodes, neighbors = get_reachable_graph(controller, grid_size, reachable_positions)

    waypoint_nodes = [nearest_node(nodes, o["position"]["x"], o["position"]["z"]) for o in objects]
    agent_pos = controller.last_event.metadata["agent"]["position"]
    start_node = nearest_node(nodes, agent_pos["x"], agent_pos["z"])

    all_nodes = [start_node] + waypoint_nodes
    dist_matrix = build_distance_matrix(all_nodes, neighbors)
    order = solve_tsp(dist_matrix, start=0)

    total_distance = sum(
        dist_matrix[order[k]][order[k + 1]] for k in range(len(order) - 1)
    )
    # order[0] is always the synthetic start node (index 0) - drop it and
    # shift every other index back by one to map onto `objects` directly.
    ordered_objects = [objects[idx - 1] for idx in order if idx != 0]

    return ordered_objects, total_distance


def plan_pick_and_place_tour(controller, items, grid_size=None, reachable_positions=None):
    """
    Plans the whole tidy-up including the DELIVERY legs (Nlp13).

    items: list of (obj, dest_obj_or_None). Each item is a pair of stops,
    pickup spot p_i then destination spot d_i. The walk for item i is
    always p_i -> d_i (fixed), so the only real choice is the ORDER:

        start -> p_a -> d_a -> p_b -> d_b -> ...

    total = sum(p_i -> d_i)  [same for every order]
          + start -> p_first + sum(d_prev -> p_next)

    That is a shortest-path ordering over "objects" with an asymmetric
    cost c[a][b] = A*(d_a, p_b) and c[start][b] = A*(start, p_b), which
    the existing solve_tsp (exact Held-Karp up to 12 objects, nearest-
    neighbour + 2-opt beyond) handles unchanged. Every distance is a real
    obstacle-aware A* length; results are cached so each pair is searched
    once. Deterministic: same scene -> same order.

    The old Nlp11/12 order (TSP over pickup spots only, ignoring where
    each object has to go) is evaluated under the same full-route cost so
    the saving can be reported honestly.

    Returns dict: order (indices into items), total (metres),
    baseline_total (metres, pickup-only order), legs (list of
    (label, metres) in execution order).
    """
    grid_size = grid_size or config.GRID_SIZE
    nodes, neighbors = get_reachable_graph(controller, grid_size, reachable_positions)
    agent = controller.last_event.metadata["agent"]["position"]
    start = nearest_node(nodes, agent["x"], agent["z"])

    pick = [nearest_node(nodes, o["position"]["x"], o["position"]["z"]) for o, _ in items]
    drop = [
        nearest_node(nodes, d["position"]["x"], d["position"]["z"]) if d is not None else pick[i]
        for i, (_, d) in enumerate(items)
    ]

    cache = {}
    BIG = 1e6      # unreachable pair: huge but finite so the solvers still terminate

    def dist(a, b):
        key = (a, b)
        if key not in cache:
            cost, _ = astar(a, b, neighbors)
            cache[key] = BIG if cost == float("inf") else cost
        return cache[key]

    n = len(items)
    inner = [dist(pick[i], drop[i]) for i in range(n)]

    # node 0 = start, node i+1 = item i (arrive at its pickup, leave from its drop)
    full = [[0.0] * (n + 1) for _ in range(n + 1)]
    for j in range(n):
        full[0][j + 1] = dist(start, pick[j])
    for i in range(n):
        for j in range(n):
            if i != j:
                full[i + 1][j + 1] = dist(drop[i], pick[j])

    def route_cost(order):          # order = item indices
        if not order:
            return 0.0
        total = dist(start, pick[order[0]]) + sum(inner[i] for i in order)
        for a, b in zip(order, order[1:]):
            total += dist(drop[a], pick[b])
        return total

    order = [k - 1 for k in solve_tsp(full, start=0) if k != 0]

    # Baseline: Nlp11/12 behaviour - order by pickup spots alone.
    pickup_only = [[0.0] * (n + 1) for _ in range(n + 1)]
    all_pick = [start] + pick
    for i in range(n + 1):
        for j in range(n + 1):
            if i != j:
                pickup_only[i][j] = dist(all_pick[i], all_pick[j])
    baseline_order = [k - 1 for k in solve_tsp(pickup_only, start=0) if k != 0]

    total, baseline_total = route_cost(order), route_cost(baseline_order)
    if baseline_total < total:      # never report/execute worse than the old plan
        order, total = baseline_order, baseline_total

    legs, prev = [], start
    for i in order:
        legs.append((f"to pickup {items[i][0]['objectType']}", dist(prev, pick[i])))
        legs.append((f"deliver {items[i][0]['objectType']}", inner[i]))
        prev = drop[i]

    return {"order": order, "total": total, "baseline_total": baseline_total, "legs": legs}


def walk_checkpoint_tour(controller, checkpoint_positions, grid_size=None, on_arrival=None):
    """
    The one place in the project multiple waypoints are already known
    ahead of time (put_away_groceries' scan sweep): orders them with
    TSP so the total walk is short, then physically walks the route
    with A*, calling on_arrival(controller, index) at each stop so the
    caller can run its existing per-checkpoint logic (the rotate-and-
    scan loop) exactly as before.
    """
    grid_size = grid_size or config.GRID_SIZE
    nodes, neighbors = get_reachable_graph(controller, grid_size)

    waypoint_nodes = [nearest_node(nodes, p["x"], p["z"]) for p in checkpoint_positions]

    agent_pos = controller.last_event.metadata["agent"]["position"]
    start_node = nearest_node(nodes, agent_pos["x"], agent_pos["z"])
    # Treat the agent's current spot as waypoint 0 of the tour, then drop
    # it from the walked route below - only the real checkpoints get an
    # on_arrival callback.
    all_nodes = [start_node] + waypoint_nodes
    dist_matrix = build_distance_matrix(all_nodes, neighbors)
    order = solve_tsp(dist_matrix, start=0)

    for idx in range(len(order) - 1):
        i, j = order[idx], order[idx + 1]
        _, path = astar(all_nodes[i], all_nodes[j], neighbors)
        if not path:
            continue
        _walk_path(controller, path, neighbors, grid_size, all_nodes[j])

        if j != 0 and on_arrival is not None:
            on_arrival(controller, j - 1)  # -1 to undo the "start" offset

    return controller.last_event
