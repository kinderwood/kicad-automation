"""RF recipes and immutable declarative geometry. No pcbnew or Workflow imports.

All coordinates are millimetres, +X right, +Y up; positive angles are CCW.
Layer names refer to the pinned stackup, not architecture worksheet aliases.
"""

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

VERSION = "0.2.0"
SCHEMA = "gppo.artifact/2.0"
PAYLOAD_SCHEMA = "gppo.rf/2.0"
RECIPE_SCHEMA = "gppo.rf-recipe/2.0"
CAPABILITIES = {"native-vias", "net-copper-shapes", "layer-rule-areas",
                "adjacent-microvia-stacks", "native-tracks", "filled-vias"}
TOLERANCE_MM = 0.001
# Every transition via is copper plated shut. This is a fixed property of the
# payload, never a recipe setting.
VIA_FILL = "copper"
MIN_ANNULAR_RING_MM = 0.127  # 5 mil; smaller rings are reported as warnings.


class RFError(ValueError):
    """A reviewable blocking authoring/compatibility error."""


def copper_layer_names(count):
    """Literal KiCad copper layer names, top to bottom, for a layer count."""
    if isinstance(count, bool) or not isinstance(count, int) or count not in range(2, 33, 2):
        raise RFError("copper layer count must be an even number from 2 to 32")
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, count - 1)] + ["B.Cu"]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RFError(f"{label}: a finite number is required")
    if positive and value <= 0:
        raise RFError(f"{label}: must be greater than zero")
    return float(value)


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise RFError(f"{label}: expected an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise RFError(f"{label}: unsupported fields {sorted(unknown)}")


def _arc(start, end, count):
    start = number(start, "arc start")
    end = number(end, "arc end")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 10000:
        raise RFError("via count: integer 1–10000 required")
    raw = end - start
    if abs(raw) > 360 or raw == 0:
        raise RFError("arc sweep must be nonzero and at most one revolution")
    full = abs(raw) == 360
    sweep = 360 if full else raw % 360
    if not full and count < 2:
        raise RFError("partial arc requires at least two vias to include both endpoints")
    return start, sweep, count if full else count - 1


def arc_angles(start, end, count):
    """Exact count, CCW partial endpoints; ±360° means full circle.

    An equal start/end is ambiguous and rejected rather than guessed full-circle.
    A decreasing endpoint crosses zero, e.g. 310→50 yields 310,335,0,25,50.
    """
    start, sweep, steps = _arc(start, end, count)
    return [round((start + sweep * index / steps) % 360, 12)
            for index in range(count)]


def arc_midpoints(start, end, count):
    """Angular midpoints of the same pattern, for a staggered microvia ring.

    A partial arc of N positions has N−1 midpoints; a full circle keeps N.
    """
    start, sweep, steps = _arc(start, end, count)
    return [round((start + sweep * (index + 0.5) / steps) % 360, 12)
            for index in range(steps)]


def _point(value, label="point"):
    if not isinstance(value, list) or len(value) != 2:
        raise RFError(f"{label}: [x,y] required")
    return [number(v, label) for v in value]


def _circle_points(at, radius, tolerance=TOLERANCE_MM):
    # Circumscribe, never undersize an antipad. Corner radial error <= tolerance.
    count = max(16, math.ceil(math.pi / math.acos(radius / (radius + tolerance))))
    if count > 10000:
        raise RFError("antipad exceeds polygon resolution limit")
    outer = radius / math.cos(math.pi / count)
    return [[round(at[0] + outer * math.cos(2 * math.pi * i / count), 12),
             round(at[1] + outer * math.sin(2 * math.pi * i / count), 12)]
            for i in range(count)]


def circle_intrudes_corridor(at, radius, corridor):
    """Circle/closed rectangle distance: touching is permitted."""
    x, y = _point(at)
    x0, x1, half_width = corridor
    distance = math.hypot(max(x0 - x, 0, x - x1), max(abs(y) - half_width, 0))
    return distance < radius - 1e-9


def _signal_intrudes_shield(signal_radius, shield, gap=0):
    """Check the signal disk plus required gap against exact/conservative shield."""
    distance = math.hypot(*shield["at"])
    radius = signal_radius + gap
    if distance >= radius + shield["radius"] - 1e-9:
        return False
    # A disk entirely inside an exact annulus opening has no ground collision.
    if "inner_radius" in shield and distance + radius <= shield["inner_radius"] + 1e-9:
        return False
    return True


def via_layers(via, stackup):
    layers = via.get("layers")
    if not isinstance(layers, list) or len(layers) != 2 or any(l not in stackup for l in layers):
        raise RFError("via layers must name two stackup layers")
    first, last = (stackup.index(layer) for layer in layers)
    if first >= last:
        raise RFError("via span must follow top-to-bottom stackup order")
    via_type = via.get("via_type")
    if via_type == "microvia":
        if last != first + 1:
            raise RFError("microvia span must connect adjacent layers")
    elif via_type == "through":
        if first != 0 or last != len(stackup) - 1:
            raise RFError("through via must span the entire board")
    else:
        raise RFError("unsupported via type: only through and one-layer microvia exist")
    if via.get("fill") != VIA_FILL:
        raise RFError("every transition via must be marked copper plated shut")
    drill = number(via.get("drill"), "via drill", True)
    diameter = number(via.get("diameter"), "via diameter", True)
    if drill >= diameter:
        raise RFError("via diameter must exceed drill")
    return stackup[first:last + 1]


def validate_ground_corridor(objects, stackup, signal_layer, corridor):
    """Check pads, holes/barrels and overlays only where present on signal layer."""
    for obj in objects:
        if obj.get("net") != "ground":
            continue
        if obj["type"] == "via":
            if signal_layer not in via_layers(obj, stackup):
                continue
            radius = max(obj["diameter"], obj["drill"]) / 2
        elif obj["type"] == "copper" and obj["layer"] == signal_layer:
            radius = obj["diameter"] / 2
        else:
            continue
        if circle_intrudes_corridor(obj["at"], radius, corridor):
            raise RFError(f"{obj['id']}: ground pad/barrel/copper intrudes on signal-layer exit corridor")


def default_recipe(part=None):
    """Editable starting point. A validated approved base part is mandatory.

    The starting values follow the part's mount: an SMD pin gets a signal
    microvia stack and antipads from the mount layer to the exit layer; a
    through-hole pin uses its own barrel and gets an antipad on every layer.
    """
    layers = copper_layer_names(4)
    pth = isinstance(part, dict) and part.get("mount") == "PTH"
    antipad_layers = layers if pth else layers[:3]
    return {
        "schema": RECIPE_SCHEMA,
        "generator": {"id": "gppo-rf-transition", "version": VERSION},
        "units": "mm", "dependencies": {"base_part": deepcopy(part)},
        "parameters": {
            "stackup": layers,
            "signal_layer": "In2.Cu", "netclasses": ["RF"],
            "signal_via": None if pth else {"diameter": 0.45, "drill": 0.2},
            "ground_via": {"via_type": "through", "diameter": 0.6, "drill": 0.3,
                           "construction": None, "stop_layer": None},
            "ground_ring": {"radius": 2.0, "start_deg": 40.0,
                            "end_deg": 320.0, "count": 9},
            "tuned_signal_copper": {"F.Cu": None, "In1.Cu": None if pth else 0.55,
                                    "In2.Cu": 0.65, "B.Cu": None},
            "tuned_ground_copper": {},
            "antipads": {layer: 1.1 for layer in antipad_layers},
            "trace_width": 0.25, "ground_pour_gap": 0.2,
            "clock_deg": 0.0,
        },
    }


def _validate_part(part):
    if not isinstance(part, dict) or part.get("approval") != "librarian-approved":
        raise RFError("select and explicitly approve a read-only central GPPO footprint")
    if not part.get("part_id") or not re.fullmatch(r"[0-9a-f]{64}", part.get("sha256", "")):
        raise RFError("base part requires identity and exact source SHA-256")
    if part.get("mount") not in ("SMD", "PTH") or part.get("side") not in ("front", "back"):
        raise RFError("base part requires SMD/PTH mount and front/back side")
    if not part.get("signal_pad") or not part.get("shield_pads") or part["signal_pad"] in part["shield_pads"]:
        raise RFError("signal and shield pad identifiers must be distinct")
    if part.get("signal_at") != [0, 0] or part.get("signal_shape") != "circle":
        raise RFError("only a circular signal land centered at footprint origin is supported")
    land = number(part.get("signal_land"), "base signal land diameter", True)
    if part["mount"] == "PTH":
        if number(part.get("signal_drill"), "base signal drill", True) >= land:
            raise RFError("through-hole signal land must exceed its plated hole")
    elif part.get("signal_drill") is not None:
        raise RFError("an SMD signal pad has no drill")
    for shield in part.get("shield_geometry", []):
        _point(shield["at"], "shield land")
        number(shield["radius"], "shield land radius", True)
        if "inner_radius" in shield and not 0 < number(shield["inner_radius"], "shield bore", True) < shield["radius"]:
            raise RFError("invalid annular shield geometry")


def _via(identity, net, at, pair, config, via_type):
    return {"id": identity, "type": "via", "net": net, "at": at,
            "layers": list(pair), "diameter": config["diameter"],
            "drill": config["drill"], "via_type": via_type, "fill": VIA_FILL}


def _annular_warning(subject, land, drill):
    ring = (land - drill) / 2
    if ring >= MIN_ANNULAR_RING_MM - 1e-9:
        return []
    return [{"code": "annular-ring", "subject": subject,
             "value_mm": round(ring, 6), "limit_mm": MIN_ANNULAR_RING_MM,
             "message": f"{subject}: annular ring {ring:.4f} mm is under "
                        f"{MIN_ANNULAR_RING_MM} mm (5 mil)"}]


def review_recipe(recipe):
    """Validate a recipe and return its payload and non-blocking warnings.

    Blocking findings raise RFError. A valid recipe returns
    ``{"payload": ..., "warnings": [...]}``; each warning is a dictionary with
    ``code``, ``subject``, ``message`` and, for measurements, ``value_mm`` and
    ``limit_mm``. Warnings never change the payload.
    """
    try:
        payload, warnings = _generate(recipe)
    except (KeyError, TypeError, IndexError) as error:
        raise RFError(f"incomplete/malformed RF recipe: {error}") from error
    return {"payload": payload, "warnings": warnings}


def generate_payload(recipe):
    """Validate all parameters then return deterministic board-native geometry."""
    return review_recipe(recipe)["payload"]


def recipe_warnings(recipe):
    """Non-blocking findings for a valid recipe; an invalid one raises RFError."""
    return review_recipe(recipe)["warnings"]


def _generate(recipe):
    _keys(recipe, ["schema", "generator", "units", "dependencies", "parameters"], "recipe")
    if recipe.get("schema") != RECIPE_SCHEMA or recipe.get("generator") != {"id": "gppo-rf-transition", "version": VERSION}:
        raise RFError("recipe requires a different generator/schema; saved payload may still be consumable")
    if recipe.get("units") != "mm":
        raise RFError("only millimetre recipes are supported")
    part = recipe["dependencies"]["base_part"]
    _validate_part(part)
    params = recipe["parameters"]
    _keys(params, default_recipe()["parameters"], "parameters")
    layers = params["stackup"]
    if not isinstance(layers, list) or len(layers) not in range(2, 33, 2):
        raise RFError("stackup requires an even 2–32 copper layers")
    if layers != copper_layer_names(len(layers)):
        raise RFError("stackup must enumerate actual KiCad copper layers in top-to-bottom order")
    signal_layer = params["signal_layer"]
    if signal_layer not in layers:
        raise RFError("signal layer is absent from stackup")
    # Depth order runs from the mount layer towards the far side of the board.
    order = layers if part["side"] == "front" else layers[::-1]
    mount_layer = order[0]
    exit_depth = order.index(signal_layer)
    pth = part["mount"] == "PTH"
    if not isinstance(params["netclasses"], list) or not params["netclasses"] or any(not isinstance(n, str) or not n for n in params["netclasses"]):
        raise RFError("at least one explicit compatible netclass is required")
    ring = params["ground_ring"]
    _keys(ring, ["radius", "start_deg", "end_deg", "count"], "ground ring")
    radius = number(ring["radius"], "ground radius", True)
    angles = arc_angles(ring["start_deg"], ring["end_deg"], ring["count"])
    width = number(params["trace_width"], "trace width", True)
    gap = number(params["ground_pour_gap"], "ground-pour gap")
    if gap < 0:
        raise RFError("ground-pour gap must be nonnegative")
    clock = number(params["clock_deg"], "clock")
    land = part["signal_land"]
    objects = []
    warnings = []

    def pair(depth):
        """The adjacent layer pair at a depth, in top-to-bottom stack order."""
        return sorted(order[depth:depth + 2], key=layers.index)

    def at(angle):
        return [round(radius * math.cos(math.radians(angle)), 12),
                round(radius * math.sin(math.radians(angle)), 12)]

    # Signal conductor. base[layer] is the signal copper that already exists
    # there; hole[layer] is a bare plated barrel with no land.
    base = {}
    hole = {}
    if pth:
        if params["signal_via"] is not None:
            raise RFError("PTH center pin already supplies the signal barrel; signal_via must be null")
        # Remove unused layers, keep start and end: KiCad draws the land on
        # both outer layers and where the exit trace connects.
        for layer in layers:
            if layer in (layers[0], layers[-1], signal_layer):
                base[layer] = land
            else:
                hole[layer] = part["signal_drill"]
        warnings += _annular_warning("footprint signal land", land, part["signal_drill"])
    else:
        if exit_depth == 0:
            raise RFError("SMD recipe requires a transition to a different routing layer")
        if exit_depth == len(order) - 1:
            raise RFError("SMD exit on the far outer layer is unsupported: there is no "
                          "layer below it for the ground disc")
        config = params["signal_via"]
        _keys(config, ["diameter", "drill"], "signal via")
        base[mount_layer] = land
        for depth in range(exit_depth):
            via = _via(f"signal-{depth}", "signal", [0, 0], pair(depth), config, "microvia")
            via_layers(via, layers)
            objects.append(via)
            for layer in via["layers"]:
                base[layer] = max(base.get(layer, 0), via["diameter"])
        warnings += _annular_warning("signal via", config["diameter"], config["drill"])
    antipad_layers = layers if pth else sorted(order[:exit_depth + 1], key=layers.index)

    tuned = params["tuned_signal_copper"]
    if set(tuned) != set(layers):
        raise RFError("explicit tuned diameter or null omission required on every copper layer")
    for layer in layers:
        diameter = tuned[layer]
        if diameter is None:
            continue  # Omission applies to generated enlargement, never base geometry.
        number(diameter, f"tuned signal copper {layer}", True)
        if layer not in antipad_layers:
            raise RFError(f"{layer}: signal copper is not allowed beyond the exit layer, where no antipad exists")
        if diameter < base.get(layer, 0):
            raise RFError(f"{layer}: tuned copper cannot shrink the approved base/via minimum")
        if layer in hole:
            if diameter <= hole[layer]:
                raise RFError(f"{layer}: tuned copper must exceed the plated signal hole")
            warnings += _annular_warning(f"signal copper {layer}", diameter, hole[layer])
        if diameter > base.get(layer, 0):
            objects.append({"id": f"signal-copper-{layer}", "type": "copper", "shape": "circle",
                            "purpose": "signal-overlay", "net": "signal", "at": [0, 0],
                            "layer": layer, "diameter": diameter})

    # Ground via ring. lands[layer] maps a position name to (angle, centre)
    # for every ground land on that layer.
    ground = params["ground_via"]
    _keys(ground, ["via_type", "diameter", "drill", "construction", "stop_layer"], "ground via")
    lands = {}
    if ground["via_type"] == "through":
        if ground["construction"] is not None or ground["stop_layer"] is not None:
            raise RFError("through ground vias span the whole board; construction and stop_layer must be null")
        for i, angle in enumerate(angles):
            via = _via(f"ground-{i}", "ground", at(angle), [layers[0], layers[-1]], ground, "through")
            via_layers(via, layers)
            objects.append(via)
            for layer in layers:
                lands.setdefault(layer, {})[str(i)] = (angle, via["at"])
    elif ground["via_type"] == "microvia":
        if ground["construction"] not in ("stacked", "staggered"):
            raise RFError("microvia ground ring construction must be stacked or staggered")
        stop = order[-1] if ground["stop_layer"] is None else ground["stop_layer"]
        if stop not in layers or stop == mount_layer:
            raise RFError("ground ring stop layer must be a copper layer below the mount layer")
        middle = (arc_midpoints(ring["start_deg"], ring["end_deg"], ring["count"])
                  if ground["construction"] == "staggered" else None)
        for depth in range(order.index(stop)):
            staggered = middle is not None and depth % 2
            for i, angle in enumerate(middle if staggered else angles):
                name = f"m{i}" if staggered else str(i)
                via = _via(f"ground-{name}-{depth}", "ground", at(angle), pair(depth), ground, "microvia")
                via_layers(via, layers)
                objects.append(via)
                for layer in via["layers"]:
                    lands.setdefault(layer, {})[name] = (angle, via["at"])
    else:
        raise RFError("ground via type must be through or microvia")
    warnings += _annular_warning("ground via", ground["diameter"], ground["drill"])
    ground_copper = params["tuned_ground_copper"]
    if any(layer not in layers for layer in ground_copper):
        raise RFError("ground tuned copper names a nonexistent layer")
    for layer in sorted(lands, key=layers.index):
        size = ground["diameter"]
        diameter = ground_copper.get(layer)
        if diameter is not None:
            if number(diameter, "ground tuned copper", True) < size:
                raise RFError("ground enlargement cannot shrink minimum via copper")
            if diameter > size:
                size = diameter
                for name, (_angle, center) in lands[layer].items():
                    objects.append({"id": f"ground-copper-{name}-{layer}", "type": "copper",
                                    "shape": "circle", "purpose": "ground-overlay", "net": "ground",
                                    "at": center, "layer": layer, "diameter": diameter})
        # Lands may be tangent, never overlapping, on every layer where they
        # coexist. A staggered ring has both sets on its shared layers. All
        # centres lie on one circle, so angular neighbours are the closest.
        ordered = sorted(lands[layer].values())
        if len(ordered) > 1 and any(
                math.dist(ordered[i][1], ordered[(i + 1) % len(ordered)][1]) < size - 1e-9
                for i in range(len(ordered))):
            raise RFError(f"{layer}: ground via lands overlap; change the recipe explicitly")
    for layer, diameter in ground_copper.items():
        if diameter is not None and layer not in lands:
            raise RFError(f"{layer}: ground overlay requires a ground via land on that layer")

    # The corridor runs from the signal centre to the trace end and stops there.
    corridor = [0, radius, width / 2 + gap]
    validate_ground_corridor(objects, layers, signal_layer, corridor)
    shields = part.get("shield_geometry", [])
    if signal_layer == mount_layer:
        for shield in shields:
            # Rectangle can lie wholly inside an annulus bore without intersecting
            # copper. Test farthest corner as well as the outer circular envelope.
            farthest = max(math.dist(shield["at"], [x, y]) for x in corridor[:2]
                           for y in [-corridor[2], corridor[2]])
            if (circle_intrudes_corridor(shield["at"], shield["radius"], corridor) and
                    farthest > shield.get("inner_radius", 0) + 1e-9):
                raise RFError("approved shield land intrudes on the requested signal-layer exit corridor")

    antipads = params["antipads"]
    if set(antipads) != set(antipad_layers):
        raise RFError("antipads are required on exactly these layers: " + ", ".join(antipad_layers) +
                      (" (every layer for a through-hole pin)" if pth else
                       " (mount layer to exit layer for an SMD pin)"))
    for layer in antipad_layers:
        diameter = antipads[layer]
        number(diameter, f"antipad {layer}", True)
        signal_size = max(base.get(layer, 0), hole.get(layer, 0), tuned[layer] or 0)
        if diameter < signal_size + 2 * gap:
            raise RFError(f"{layer}: antipad must include signal copper and configured ground-pour gap")
        if layer == mount_layer:
            for shield in shields:
                if _signal_intrudes_shield(signal_size / 2, shield, gap):
                    raise RFError(f"{layer}: signal copper or required gap intrudes on approved base shield copper")
        for obj in objects:
            if obj["net"] == "ground" and obj["type"] in ("via", "copper"):
                present = via_layers(obj, layers) if obj["type"] == "via" else [obj["layer"]]
                if layer in present and math.hypot(*obj["at"]) - obj["diameter"] / 2 < diameter / 2 - 1e-9:
                    raise RFError(f"{obj['id']}: ground copper intrudes on {layer} antipad")
        objects.append({"id": f"antipad-{layer}", "type": "keepout", "net": "ground",
                        "shape": "polygon", "layers": [layer], "purpose": "antipad",
                        "points": _circle_points([0, 0], diameter / 2),
                        "prohibit": ["copperpour"], "tolerance_mm": TOLERANCE_MM})
    if not pth:
        # Solid ground directly below the exit layer, with no antipad, so that
        # ordinary clearance checking fails anything routed under the launch.
        exact = [s for s in shields if "inner_radius" in s]
        if len(shields) != 1 or len(exact) != 1 or math.hypot(*exact[0]["at"]) > 1e-9:
            raise RFError("the ground disc needs one annular footprint ground pad centred on the signal")
        objects.append({"id": "ground-disc", "type": "copper", "shape": "circle",
                        "purpose": "ground-disc", "net": "ground", "at": [0, 0],
                        "layer": order[exit_depth + 1], "diameter": 2 * exact[0]["radius"]})
    objects.append({"id": "exit-corridor", "type": "keepout", "net": "ground",
                    "shape": "polygon", "layers": [signal_layer], "purpose": "exit-corridor",
                    "points": [[0, -corridor[2]], [corridor[1], -corridor[2]],
                               [corridor[1], corridor[2]], [0, corridor[2]]],
                    "prohibit": ["copperpour"]})
    # The exit trace is part of the transition so it stays clocked with the
    # ground ring. Its end is the handoff where designer routing starts.
    objects.append({"id": "exit-trace", "type": "track", "net": "signal",
                    "layer": signal_layer, "width": width,
                    "start": [0, 0], "end": [radius, 0]})
    # Clock is stored as authoring provenance; exact payload remains local +X exit.
    # Workflow applies per-instance clock and pose once, preserving reusable geometry.
    payload = {"units": "mm", "coordinates": "x-right-y-up-ccw",
               "anchors": {"signal": [0, 0], "exit_direction_deg": 0,
                           "handoff": [radius, 0]},
               "net_roles": ["signal", "ground"],
               "layer_roles": {"mount": mount_layer, "signal": signal_layer},
               "default_clock_deg": clock, "objects": objects}
    return payload, warnings


def make_artifact(recipe, artifact_id=None, revision=1, provenance=None):
    payload = generate_payload(recipe)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RFError("revision must be a positive integer")
    artifact_id = artifact_id or str(uuid.uuid4())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_id):
        raise RFError("artifact identity must be a safe nonempty library identifier")
    params = recipe["parameters"]
    part = recipe["dependencies"]["base_part"]
    caps = ["native-vias", "net-copper-shapes", "layer-rule-areas",
            "native-tracks", "filled-vias"]
    if any(o.get("via_type") == "microvia" for o in payload["objects"]):
        caps.append("adjacent-microvia-stacks")
    artifact = {"schema": SCHEMA, "artifact_id": artifact_id, "revision": revision,
                "kind": "rf-transition", "payload_schema": PAYLOAD_SCHEMA,
                "recipe": deepcopy(recipe), "payload": payload,
                "compatibility": {"part_id": part["part_id"], "part_sha256": part["sha256"],
                                  "mount": part["mount"], "side": part["side"],
                                  "netclasses": deepcopy(params["netclasses"]),
                                  "copper_layer_count": len(params["stackup"])},
                "required_capabilities": sorted(caps),
                "provenance": deepcopy(provenance or {"operation": "new"})}
    artifact["hashes"] = {"payload": digest(payload), "recipe": digest(recipe),
                          "artifact": digest(artifact)}
    return artifact


def _supported_schema(value, prefix):
    return isinstance(value, str) and re.fullmatch(re.escape(prefix) + r"/2\.\d+", value)


def _validate_saved_payload(artifact):
    """Consume supported data independently of understanding its recipe."""
    payload = artifact["payload"]
    if payload.get("units") != "mm" or payload.get("coordinates") != "x-right-y-up-ccw":
        raise RFError("unsupported payload units or coordinate convention")
    anchors = payload.get("anchors")
    if payload.get("net_roles") != ["signal", "ground"] or not isinstance(anchors, dict) or set(anchors) != {"signal", "exit_direction_deg", "handoff"} or anchors["signal"] != [0, 0] or anchors["exit_direction_deg"] != 0:
        raise RFError("unsupported RF anchors or net roles")
    handoff = _point(anchors["handoff"], "handoff")
    layers = copper_layer_names(artifact["compatibility"]["copper_layer_count"])
    if set(payload["layer_roles"]) != {"mount", "signal"} or any(value not in layers for value in payload["layer_roles"].values()):
        raise RFError("saved layer role is absent from stackup")
    traces = [obj for obj in payload["objects"] if obj.get("type") == "track"]
    if len(traces) != 1 or traces[0].get("id") != "exit-trace":
        raise RFError("a saved transition carries exactly one exit trace")
    seen = set()
    for obj in payload["objects"]:
        identity = obj.get("id")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise RFError("saved board objects require unique nonempty identities")
        seen.add(identity)
        if obj.get("net") not in payload["net_roles"]:
            raise RFError("saved object has an unknown net role")
        if obj.get("type") == "via":
            _point(obj["at"])
            via_layers(obj, layers)
        elif obj.get("type") == "copper":
            if obj.get("shape") != "circle" or obj.get("layer") not in layers:
                raise RFError("unsupported saved copper geometry")
            _point(obj["at"])
            number(obj["diameter"], "copper diameter", True)
        elif obj.get("type") == "keepout":
            if obj.get("shape") != "polygon" or obj.get("prohibit") != ["copperpour"]:
                raise RFError("unsupported keepout semantics")
            if not obj.get("layers") or any(layer not in layers for layer in obj["layers"]):
                raise RFError("saved keepout names nonexistent layers")
            points = obj.get("points")
            if not isinstance(points, list) or not 3 <= len(points) <= 10000:
                raise RFError("keepout requires 3–10000 polygon vertices")
            for point in points:
                _point(point)
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
            if abs(area) < 1e-12:
                raise RFError("keepout polygon has no area")
        elif obj.get("type") == "track":
            if obj["net"] != "signal" or obj.get("layer") != payload["layer_roles"]["signal"]:
                raise RFError("the exit trace must be signal copper on the exit layer")
            number(obj["width"], "exit trace width", True)
            if _point(obj["start"], "exit trace start") != [0, 0] or _point(obj["end"], "exit trace end") != handoff:
                raise RFError("the exit trace must run from the signal centre to the handoff")
        else:
            raise RFError("unsupported saved object type")


def validate_artifact(artifact, reopen=False):
    if not isinstance(artifact, dict) or not _supported_schema(artifact.get("schema"), "gppo.artifact") or artifact.get("payload_schema") != PAYLOAD_SCHEMA or artifact.get("kind") != "rf-transition":
        raise RFError("unsupported artifact envelope or RF payload schema")
    hashes = artifact.get("hashes", {})
    body = {k: v for k, v in artifact.items() if k != "hashes"}
    if hashes != {"payload": digest(artifact["payload"]), "recipe": digest(artifact["recipe"]), "artifact": digest(body)}:
        raise RFError("artifact integrity failure (payload, recipe or metadata)")
    if set(artifact.get("required_capabilities", [])) - CAPABILITIES:
        raise RFError("unsupported required native capability")
    if isinstance(artifact.get("revision"), bool) or not isinstance(artifact.get("revision"), int) or artifact["revision"] < 1:
        raise RFError("invalid saved immutable revision")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact.get("artifact_id", "")):
        raise RFError("invalid saved artifact identity")
    _validate_saved_payload(artifact)
    if reopen:
        expected = generate_payload(artifact["recipe"])
        if expected != artifact["payload"]:
            raise RFError("recipe does not reproduce the saved exact payload")
    return artifact


def clone_artifact(artifact, recipe=None, artifact_id=None):
    validate_artifact(artifact, reopen=True)
    if artifact_id == artifact["artifact_id"]:
        raise RFError("clone must receive a new identity")
    return make_artifact(recipe or artifact["recipe"], artifact_id=artifact_id,
                         provenance={"operation": "clone", "parent": pin(artifact)})


def revise_artifact(artifact, recipe):
    validate_artifact(artifact, reopen=True)
    return make_artifact(recipe, artifact["artifact_id"], artifact["revision"] + 1,
                         {"operation": "revision", "parent": pin(artifact)})


def pin(artifact):
    return {"artifact_id": artifact["artifact_id"], "revision": artifact["revision"],
            "sha256": artifact["hashes"]["artifact"]}


def load_artifact(path, reopen=False):
    try:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        return validate_artifact(artifact, reopen=reopen)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RFError(f"cannot read RF artifact: {error}") from error


def publish_bundle(directory, artifact):
    """Publish an immutable artifact and a small catalog. Existing bytes never change.

    Directory catalogs are advisory indexes; each selected file is independently
    verified. A catalog update failure leaves a valid discoverable immutable file.
    """
    validate_artifact(artifact, reopen=True)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    identity = artifact["artifact_id"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identity):
        raise RFError("unsafe artifact identity")
    target = root / f"{identity}.r{artifact['revision']}.gppo-rf.json"
    data = canonical(artifact) + b"\n"
    if target.exists():
        if target.read_bytes() != data:
            raise RFError("immutable artifact identity/revision already has different content")
        return target
    # Publish complete bytes with an exclusive hard-link, so a failed write cannot
    # expose a truncated immutable revision. Same-directory staging is required.
    staging = root / f".artifact-{uuid.uuid4()}.tmp"
    try:
        with staging.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, target)
    except FileExistsError as error:
        raise RFError("artifact revision was concurrently published; reopen library") from error
    finally:
        staging.unlink(missing_ok=True)
    catalog_path = root / "manifest.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"schema": "gppo.catalog/1.0", "artifacts": []}
        if catalog.get("schema") != "gppo.catalog/1.0" or not isinstance(catalog.get("artifacts"), list):
            raise RFError("unsupported library catalog")
        catalog["artifacts"].append({**pin(artifact), "path": target.name})
        # Replace from a same-directory unique temp file, without touching artifacts.
        temporary = root / f".manifest-{uuid.uuid4()}.tmp"
        temporary.write_bytes(canonical(catalog) + b"\n")
        temporary.replace(catalog_path)
    except (OSError, json.JSONDecodeError) as error:
        raise RFError(f"artifact saved at {target}; catalog update failed: {error}") from error
    return target


def capability_report(version):
    text = str(version)
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", text)
    supported = bool(match and (10, 0, 0) <= tuple(map(int, match.groups())) <= (10, 0, 8))
    return {"runtime": text, "supported": supported,
            "capabilities": sorted(CAPABILITIES) if supported else [],
            "limitations": ["No impedance, electromagnetic or manufacturing qualification",
                            "Every via is copper plated shut; drill and aspect-ratio feasibility is not checked",
                            "Circular centered signal lands; through-hole pads must remove unused layers and keep start and end",
                            "No back-mount mirroring, dogbone or jog launches, or base-footprint edits",
                            "Native copper connectivity and fabrication require project review"]}
