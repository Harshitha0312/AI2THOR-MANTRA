"""Minimal AI2-THOR stand-in with a ONE-TICK DISPLAY LAG (the Nlp11 bug)."""
import copy, math

G = 0.25

class Ev:
    def __init__(self, md): self.metadata = md; self.frame = None

class MockController:
    def __init__(self, fail_pickup_first=0, extra_objects=None, break_mode="hand", put_fail_types=(), block_cells=(), block_reported=False, close_fail_first=0, bulky_fail_seeds=0):
        self.bulky_fail_seeds = bulky_fail_seeds   # a bulky item (Bread) is refused on a Shelf/Fridge until PutObject has been tried this many times
        self.bulky_tries = 0
        self.close_fail_first = close_fail_first; self.close_calls = 0
        self.block_cells = {(round(x*G,4), round(z*G,4)) for x, z in block_cells}   # grid indices
        self.block_reported = block_reported   # True: GetReachablePositions still lists them (stale/wrong)
        self.blocked_hits = 0
        self.break_mode = break_mode
        self.put_fail_types = set(put_fail_types)
        self.fail_pickup_first = fail_pickup_first
        self.pickup_calls = 0
        self.log = []                # (action, kwargs)
        self.teleports_moving = 0    # Teleports that changed x/z
        self.agent = dict(position=dict(x=0.0, y=0.9, z=0.0), rotation=dict(x=0, y=0.0, z=0), cameraHorizon=0)
        self.held = None
        self.screen = self._world_view()     # what the "window" currently shows
        self._pending_view = self.screen
        def O(t, pos, **kw):
            d = dict(objectType=t, objectId=f"{t}|{pos[0]:+.2f}|{pos[1]:+.2f}|{pos[2]:+.2f}",
                     position=dict(x=pos[0], y=pos[1], z=pos[2]), pickupable=False, receptacle=False,
                     toggleable=False, isToggled=False, openable=False, isOpen=False,
                     parentReceptacles=None, receptacleObjectIds=[], controlledObjects=None,
                     canFillWithLiquid=False, isFilledWithLiquid=False, fillLiquid=None,
                     sliceable=False, isSliced=False, breakable=False, isBroken=False,
                     dirtyable=False, isDirty=False, isUsedUp=False)
            d.update(kw); return d
        self.objs = [
            O("CounterTop", (1.9, .9, 0.0), receptacle=True),
            O("Apple", (1.9, .95, .2), pickupable=True, sliceable=True),
            O("Potato", (1.9, .95, -.2), pickupable=True, sliceable=True),
            O("Pot", (1.9, .95, .6), pickupable=True, receptacle=True, canFillWithLiquid=True),
            O("DiningTable", (0.0, .7, 1.9), receptacle=True),
            O("Fridge", (0.0, 0, -1.9), receptacle=True, openable=True),
            O("Egg", (0.0, .5, -1.9), pickupable=True),
            O("StoveBurner", (-1.9, .9, 0.0), receptacle=True),
            O("StoveKnob", (-1.9, .9, .4), toggleable=True),
            O("Book", (0.5, .5, 1.9), pickupable=True),
            O("Pan", (1.9, .95, -.6), pickupable=True, receptacle=True),
            O("StoveBurner", (-1.9, .9, -.5), receptacle=True),
            O("StoveKnob", (-1.9, .9, -.9), toggleable=True),
            O("SideTable", (-1.0, .6, 1.9), receptacle=True),
        ]
        by = {}
        for o in self.objs: by.setdefault(o["objectType"], o)
        self._link(by["Apple"], by["CounterTop"]); self._link(by["Potato"], by["CounterTop"])
        self._link(by["Pot"], by["CounterTop"]); self._link(by["Egg"], by["Fridge"])
        self._link(by["Book"], by["DiningTable"])
        by["StoveKnob"]["controlledObjects"] = [by["StoveBurner"]["objectId"]]
        burners = [o for o in self.objs if o["objectType"] == "StoveBurner"]
        knobs = [o for o in self.objs if o["objectType"] == "StoveKnob"]
        for k, b in zip(knobs, burners): k["controlledObjects"] = [b["objectId"]]
        self._link(by["Pan"], by["CounterTop"])
        for o in (extra_objects or []): self.objs.append(o)
        self.last_event = self._event(True)

    def _bump_bulky(self):
        self.bulky_tries += 1
        return self.bulky_tries <= self.bulky_fail_seeds
    @staticmethod
    def _link(child, parent):
        child["parentReceptacles"] = [parent["objectId"]]; parent["receptacleObjectIds"].append(child["objectId"])
    def _find(self, oid): return next((o for o in self.objs if o["objectId"] == oid), None)
    def _world_view(self):
        return dict(held=self.held, parents={o["objectId"]: tuple(o["parentReceptacles"] or []) for o in getattr(self, "objs", [])},
                    toggled={o["objectId"]: o["isToggled"] for o in getattr(self, "objs", [])},
                    open={o["objectId"]: o["isOpen"] for o in getattr(self, "objs", [])})
    def _bearing_ok(self, o):
        dx = o["position"]["x"] - self.agent["position"]["x"]; dz = o["position"]["z"] - self.agent["position"]["z"]
        yaw = math.degrees(math.atan2(dx, dz)) % 360
        d = abs(((yaw - self.agent["rotation"]["y"]) + 180) % 360 - 180)
        return d < 50
    def _dist(self, o):
        return math.hypot(o["position"]["x"] - self.agent["position"]["x"], o["position"]["z"] - self.agent["position"]["z"])
    def _event(self, ok, err=None, ret=None):
        objs = copy.deepcopy(self.objs)
        for o in objs:
            o["distance"] = self._dist(o); o["visible"] = o["distance"] <= 1.5 and self._bearing_ok(o)
        inv = [dict(objectId=self.held, objectType=self.held.split("|")[0])] if self.held else []
        return Ev(dict(lastActionSuccess=ok, errorMessage=err or "", actionReturn=ret, agent=copy.deepcopy(self.agent),
                       objects=objs, inventoryObjects=inv, cameraPosition=dict(x=self.agent["position"]["x"], y=1.5, z=self.agent["position"]["z"])))
    def reachable(self):
        cells = [dict(x=round(x*G, 4), y=0.9, z=round(z*G, 4)) for x in range(-6, 7) for z in range(-6, 7)]
        if self.block_reported:
            return cells
        return [c for c in cells if (c["x"], c["z"]) not in self.block_cells]
    def step(self, action=None, **kw):
        # DISPLAY LAG: the window now shows the world as it was at the end of the previous step
        self.screen = self._pending_view
        self.log.append((action, kw))
        ok, err, ret = True, None, None
        a = self.agent; p = a["position"]
        if action in ("MoveAhead", "MoveBack", "MoveLeft", "MoveRight"):
            off = {"MoveAhead": 0, "MoveRight": 90, "MoveBack": 180, "MoveLeft": 270}[action]
            ang = math.radians(a["rotation"]["y"] + off)
            nx, nz = round(p["x"] + G*math.sin(ang), 4), round(p["z"] + G*math.cos(ang), 4)
            if (nx, nz) in self.block_cells:
                self.blocked_hits += 1; ok, err = False, "blocked by obstacle"
            elif any(abs(c["x"]-nx) < 1e-6 and abs(c["z"]-nz) < 1e-6 for c in self.reachable()) or self.block_reported:
                p["x"], p["z"] = nx, nz
            else: ok, err = False, "blocked"
        elif action == "RotateRight": a["rotation"]["y"] = (a["rotation"]["y"] + 90) % 360
        elif action == "RotateLeft": a["rotation"]["y"] = (a["rotation"]["y"] - 90) % 360
        elif action == "LookUp": a["cameraHorizon"] = max(-30, a["cameraHorizon"] - 30)
        elif action == "LookDown": a["cameraHorizon"] = min(60, a["cameraHorizon"] + 30)
        elif action == "Teleport":
            np_ = kw.get("position", p)
            if abs(np_["x"]-p["x"]) > 1e-6 or abs(np_["z"]-p["z"]) > 1e-6: self.teleports_moving += 1
            a["position"] = dict(np_); a["rotation"]["y"] = kw.get("rotation", a["rotation"])["y"] % 360
            a["cameraHorizon"] = kw.get("horizon", a["cameraHorizon"])
        elif action == "GetReachablePositions": ret = self.reachable()
        elif action == "Pass": pass
        elif action == "PickupObject":
            o = self._find(kw["objectId"]); self.pickup_calls += 1
            if self.held: ok, err = False, "hand full"
            elif not kw.get("forceAction") and self.pickup_calls <= self.fail_pickup_first: ok, err = False, "object not visible"
            elif not kw.get("forceAction") and not (self._dist(o) <= 1.5 and self._bearing_ok(o)): ok, err = False, "object out of reach/visible"
            elif o["parentReceptacles"] and any(self._find(q)["openable"] and not self._find(q)["isOpen"] for q in o["parentReceptacles"]) and not kw.get("forceAction"): ok, err = False, "in closed receptacle"
            else:
                for q in o["parentReceptacles"] or []: self._find(q)["receptacleObjectIds"].remove(o["objectId"])
                o["parentReceptacles"] = None; self.held = o["objectId"]
        elif action == "DropHandObject":
            if not self.held: ok, err = False, "nothing held"
            else: self.held = None
        elif action == "PutObject":
            r = self._find(kw["objectId"])
            if not self.held: ok, err = False, "nothing held"
            elif r["objectType"] in self.put_fail_types: ok, err = False, "no room on receptacle"
            elif self._find(self.held)["objectType"] == "Bread" and r["objectType"] in ("Shelf", "Fridge") and self._bump_bulky(): ok, err = False, "no room for it at that spot"
            elif not kw.get("forceAction") and not (self._dist(r) <= 1.5 and self._bearing_ok(r)): ok, err = False, "receptacle out of reach"
            elif r["openable"] and not r["isOpen"]: ok, err = False, "closed"
            else:
                self._link(self._find(self.held), r); self.held = None
        elif action in ("OpenObject", "CloseObject"):
            o = self._find(kw["objectId"])
            if action == "CloseObject":
                self.close_calls += 1
                if self.close_calls <= self.close_fail_first: ok, err = False, "close blocked"
                else: o["isOpen"] = False
            else: o["isOpen"] = True
        elif action in ("ToggleObjectOn", "ToggleObjectOff"):
            o = self._find(kw["objectId"]); o["isToggled"] = action == "ToggleObjectOn"
        elif action == "BreakObject":
            o = self._find(kw["objectId"])
            if o["objectType"] != "Egg": ok, err = False, "not breakable"
            else:
                for q in o["parentReceptacles"] or []: self._find(q)["receptacleObjectIds"].remove(o["objectId"])
                self.objs.remove(o)
                was_held = self.held == o["objectId"]
                new = dict(o); new["objectType"] = "EggCracked"; new["objectId"] = "EggCracked|%+.2f|%+.2f|%+.2f" % (p["x"], .9, p["z"])
                new["position"] = dict(x=p["x"], y=.9, z=p["z"]); new["parentReceptacles"] = None; new["receptacleObjectIds"] = []
                new["isBroken"] = False
                if o["parentReceptacles"]:      # broken in place: cracked egg stays where the egg was
                    new["parentReceptacles"] = list(o["parentReceptacles"])
                    for q in new["parentReceptacles"]: self._find(q)["receptacleObjectIds"].append(new["objectId"])
                self.objs.append(new)
                if was_held:
                    self.held = new["objectId"] if self.break_mode == "hand" else None
        elif action == "SliceObject":
            o = self._find(kw["objectId"])
            held = self._find(self.held) if self.held else None
            if not o.get("sliceable"): ok, err = False, "not sliceable"
            elif not held or "Knife" not in held["objectType"]: ok, err = False, "need a knife in hand"
            else:
                for q in o["parentReceptacles"] or []: self._find(q)["receptacleObjectIds"].remove(o["objectId"])
                self.objs.remove(o)
                new = dict(o); new["objectType"] = o["objectType"] + "Sliced"; new["sliceable"] = False; new["isSliced"] = True
                new["objectId"] = new["objectType"] + "|" + o["objectId"].split("|", 1)[1]; new["receptacleObjectIds"] = []
                new["parentReceptacles"] = list(o["parentReceptacles"] or []) or None
                for q in new["parentReceptacles"] or []: self._find(q)["receptacleObjectIds"].append(new["objectId"])
                self.objs.append(new)
        elif action == "FillObjectWithLiquid":
            o = self._find(kw["objectId"])
            if not o["canFillWithLiquid"]: ok, err = False, "cannot fill"
            else: o["isFilledWithLiquid"] = True; o["fillLiquid"] = kw.get("fillLiquid")
        else: ok, err = False, "unknown action " + str(action)
        self._pending_view = self._world_view()   # visible only on the NEXT tick
        self.last_event = self._event(ok, err, ret)
        return self.last_event
    def reset(self, scene=None): pass
    def stop(self): pass
