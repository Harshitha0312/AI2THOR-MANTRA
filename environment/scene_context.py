"""
Compact, always-current description of the scene for the LLM (Nlp12).

Nothing here is hard-coded per scene. Every call reads the latest
AI2-THOR metadata (controller.last_event - refreshed by every simulator
step, and re-read with a Pass at the start of each command) and turns it
into a few dense lines: what is held, what each item is sitting on/in,
what each surface/container holds, what can be switched on/off and its
state, what can be opened and whether it is open.

Instances of the same type are merged ("Cabinet x9") so a 100+ object
kitchen stays a few hundred tokens instead of raw metadata.
"""

from collections import Counter, defaultdict

import config

# Objects that are scenery - never useful to the planner.
_SCENERY = {"Floor", "Wall", "Ceiling", "Window", "Doorway", "Door", "Room"}


def _parents_label(obj, by_id):
    names = []
    for pid in obj.get("parentReceptacles") or []:
        p = by_id.get(pid)
        if p is not None and p["objectType"] not in _SCENERY:
            names.append(p["objectType"])
    return names[0] if names else "nowhere (loose)"


def _item_tags(o):
    tags = []
    if o.get("sliceable") and not o.get("isSliced"):
        tags.append("sliceable")
    if o.get("isSliced"):
        tags.append("sliced")
    if o.get("canFillWithLiquid"):
        tags.append("filled:" + str(o.get("fillLiquid") or "liquid") if o.get("isFilledWithLiquid") else "fillable")
    if o.get("breakable") and not o.get("isBroken"):
        tags.append("breakable")
    if o.get("isBroken"):
        tags.append("broken")
    if o.get("dirtyable"):
        tags.append("dirty" if o.get("isDirty") else "clean")
    if o.get("cookable"):
        tags.append("cooked" if o.get("isCooked") else "cookable")
    if o.get("receptacle") and o.get("pickupable"):
        tags.append("container")
    if o.get("toggleable"):
        tags.append("ON" if o.get("isToggled") else "OFF")
    return tags


def _contents(rec, by_id, depth=0):
    """Names of things on/in rec (looking through structural sub-receptacles like a fridge Shelf)."""
    out = []
    if depth > 4:
        return out
    for cid in rec.get("receptacleObjectIds") or []:
        c = by_id.get(cid)
        if c is None:
            continue
        inner = _contents(c, by_id, depth + 1) if c.get("receptacle") else []
        if c.get("pickupable"):
            out.append(c["objectType"] + (f"(holding {'+'.join(inner)})" if inner else ""))
        else:
            out.extend(inner)
    return out


def build_scene_context(controller, max_chars=None):
    """Returns the scene description as text ('' if no metadata is available)."""
    max_chars = max_chars or getattr(config, "SCENE_CONTEXT_MAX_CHARS", 4500)
    try:
        md = controller.last_event.metadata
        objects = md["objects"]
    except Exception:
        return ""

    by_id = {o["objectId"]: o for o in objects}
    lines = []

    held = [by_id.get(i["objectId"], i)["objectType"] if i["objectId"] in by_id else i["objectType"]
            for i in (md.get("inventoryObjects") or [])]
    lines.append("HELD IN HAND: " + (", ".join(held) if held else "nothing"))

    # --- loose / pickupable items and where each one is now -------------
    groups = Counter()
    for o in objects:
        if not o.get("pickupable") or o["objectType"] in _SCENERY:
            continue
        key = (o["objectType"], _parents_label(o, by_id), tuple(_item_tags(o)))
        groups[key] += 1
    items = []
    for (otype, where, tags), n in sorted(groups.items()):
        tag = f" [{','.join(tags)}]" if tags else ""
        cnt = f" x{n}" if n > 1 else ""
        items.append(f"{otype}{cnt}{tag} -> {where}")
    lines.append("PICKUPABLE ITEMS (item -> where it is now): " + "; ".join(items) if items
                 else "PICKUPABLE ITEMS: none")

    # --- receptacles (surfaces / containers) and what they hold ---------
    rec_groups = defaultdict(lambda: {"n": 0, "contents": [], "closed": 0, "open": 0, "openable": False})
    for o in objects:
        if not o.get("receptacle") or o["objectType"] in _SCENERY:
            continue
        g = rec_groups[o["objectType"]]
        g["n"] += 1
        g["contents"].extend(_contents(o, by_id))
        if o.get("openable"):
            g["openable"] = True
            g["open" if o.get("isOpen") else "closed"] += 1
    recs = []
    for rtype, g in sorted(rec_groups.items()):
        state = ""
        if g["openable"]:
            state = f" [openable: {g['closed']} closed, {g['open']} open]"
        cnt = f" x{g['n']}" if g["n"] > 1 else ""
        c = Counter(g["contents"])
        held_txt = ", ".join(f"{k}{' x' + str(v) if v > 1 else ''}" for k, v in sorted(c.items())) or "empty"
        recs.append(f"{rtype}{cnt}{state}: {held_txt}")
    lines.append("SURFACES / CONTAINERS (name: what is on/in it now): " + " | ".join(recs) if recs
                 else "SURFACES / CONTAINERS: none")

    # --- toggleable appliances and their state --------------------------
    tog = Counter()
    for o in objects:
        if not o.get("toggleable") or o["objectType"] in _SCENERY:
            continue
        ctrl = [by_id[c]["objectType"] for c in (o.get("controlledObjects") or []) if c in by_id]
        label = o["objectType"] + (f" (controls {'+'.join(sorted(set(ctrl)))})" if ctrl else "")
        tog[(label, "ON" if o.get("isToggled") else "OFF")] += 1
    if tog:
        lines.append("SWITCHABLE (name state): " + "; ".join(
            f"{l}{' x' + str(n) if n > 1 else ''} {s}" for (l, s), n in sorted(tog.items())))

    # --- other openable things (not containers, not items) --------------
    opn = Counter()
    for o in objects:
        if o.get("openable") and not o.get("receptacle") and not o.get("pickupable") \
                and o["objectType"] not in _SCENERY:
            opn[(o["objectType"], "open" if o.get("isOpen") else "closed")] += 1
    if opn:
        lines.append("OTHER OPENABLE: " + "; ".join(f"{t} {s}" for (t, s), _ in sorted(opn.items())))

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rsplit(" ", 1)[0] + " ...(truncated)"
    return text
