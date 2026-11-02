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

VERSION = "0.1.0"
SCHEMA = "gppo.artifact/1.0"
PAYLOAD_SCHEMA = "gppo.rf/1.0"
RECIPE_SCHEMA = "gppo.rf-recipe/1.0"
CAPABILITIES = {"native-vias", "net-copper-shapes", "layer-rule-areas",
                "adjacent-microvia-stacks"}
TOLERANCE_MM = 0.001


class RFError(ValueError):
    """A reviewable blocking authoring/compatibility error."""


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


def arc_angles(start, end, count):
    """Exact count, CCW partial endpoints; ±360° means full circle.

    An equal start/end is ambiguous and rejected rather than guessed full-circle.
    A decreasing endpoint crosses zero, e.g. 310→50 yields 310,335,0,25,50.
    """
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
    denominator = count if full else count - 1
    return [round((start + sweep * index / denominator) % 360, 12)
            for index in range(count)]


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
    elif via_type != "blind_buried":
        raise RFError("unsupported via type")
    if via.get("treatment", "untreated") != "untreated":
        raise RFError("filled/capped/plugged via treatment is not qualified by this renderer")
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
    """Editable starting point. A validated approved base part is mandatory."""
    return {
        "schema": RECIPE_SCHEMA,
        "generator": {"id": "gppo-rf-transition", "version": VERSION},
        "units": "mm", "dependencies": {"base_part": deepcopy(part)},
        "parameters": {
            "stackup": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
            "signal_layer": "In2.Cu", "netclasses": ["RF"],
            "signal_via": {"via_type": "microvia", "diameter": 0.45,
                           "drill": 0.2, "treatment": "untreated",
                           "spans": [["F.Cu", "In1.Cu"], ["In1.Cu", "In2.Cu"]]},
            "ground_via": {"via_type": "through", "diameter": 0.6,
                           "drill": 0.3, "treatment": "untreated",
                           "spans": [["F.Cu", "B.Cu"]]},
            "ground_ring": {"radius": 2.0, "start_deg": 40.0,
                            "end_deg": 320.0, "count": 9},
            "tuned_signal_copper": {"F.Cu": None, "In1.Cu": 0.55,
                                    "In2.Cu": 0.65, "B.Cu": None},
            "tuned_ground_copper": {},
            "antipads": {"F.Cu": 1.1, "In1.Cu": 1.1,
                         "In2.Cu": 1.1, "B.Cu": 1.1},
            "antipad_span": ["F.Cu", "B.Cu"],
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
    if not isinstance(part.get("inspection_stackup"), list) or not part["inspection_stackup"]:
        raise RFError("base part requires the copper stackup used for native source inspection")
    if not part.get("signal_pad") or not part.get("shield_pads") or part["signal_pad"] in part["shield_pads"]:
        raise RFError("signal and shield pad identifiers must be distinct")
    if part.get("signal_at") != [0, 0] or part.get("signal_shape") != "circle":
        raise RFError("only a circular signal land centered at footprint origin is supported")
    minimum = part.get("minimum_signal_copper")
    if not isinstance(minimum, dict) or not minimum:
        raise RFError("base part minimum copper by layer is required")
    for diameter in minimum.values():
        number(diameter, "base minimum signal copper", True)
    for land in part.get("shield_geometry", []):
        _point(land["at"], "shield land")
        number(land["radius"], "shield land radius", True)
        if "inner_radius" in land and not 0 < number(land["inner_radius"], "shield bore", True) < land["radius"]:
            raise RFError("invalid annular shield geometry")


def _span_objects(config, at, prefix, net, stackup):
    _keys(config, ["via_type", "diameter", "drill", "treatment", "spans"], prefix)
    spans = config.get("spans")
    if not isinstance(spans, list) or not spans:
        raise RFError(f"{prefix}: at least one explicit via span is required")
    if len({tuple(span) for span in spans}) != len(spans):
        raise RFError(f"{prefix}: duplicate via spans")
    if config.get("via_type") == "through" and len(spans) != 1:
        raise RFError("through vias cannot have stacked duplicate barrels")
    objects = []
    for i, span in enumerate(spans):
        obj = {"id": f"{prefix}-{i}", "type": "via", "net": net,
               "at": at, "layers": span, "diameter": config["diameter"],
               "drill": config["drill"], "via_type": config["via_type"],
               "treatment": config.get("treatment", "untreated")}
        via_layers(obj, stackup)
        objects.append(obj)
    return objects


def generate_payload(recipe):
    """Validate all parameters then return deterministic board-native geometry."""
    try:
        return _generate_payload(recipe)
    except (KeyError, TypeError, IndexError) as error:
        raise RFError(f"incomplete/malformed RF recipe: {error}") from error


def _generate_payload(recipe):
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
    if not isinstance(layers, list) or len(layers) < 2 or len(layers) > 32 or len(layers) % 2:
        raise RFError("stackup requires an even 2–32 copper layers")
    if layers != ["F.Cu"] + [f"In{i}.Cu" for i in range(1, len(layers) - 1)] + ["B.Cu"]:
        raise RFError("stackup must enumerate actual KiCad copper layers in top-to-bottom order")
    if part["inspection_stackup"] != layers:
        raise RFError("stackup changed since base-part inspection; select and inspect the approved source again")
    signal_layer = params["signal_layer"]
    if signal_layer not in layers:
        raise RFError("signal layer is absent from stackup")
    mount_layer = layers[0] if part["side"] == "front" else layers[-1]
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
    objects = []
    if part["mount"] == "SMD":
        objects += _span_objects(params["signal_via"], [0, 0], "signal", "signal", layers)
        intervals = sorted((layers.index(o["layers"][0]), layers.index(o["layers"][1])) for o in objects)
        start, end = sorted([layers.index(mount_layer), layers.index(signal_layer)])
        if start == end:
            raise RFError("SMD recipe requires a transition to a different routing layer")
        if intervals[0][0] != start or intervals[-1][1] != end or any(a[1] != b[0] for a, b in zip(intervals, intervals[1:])):
            raise RFError("signal via spans must form one continuous nonoverlapping mount-to-signal stack")
    elif params["signal_via"] is not None:
        raise RFError("PTH center pin already supplies the signal barrel; signal_via must be null")
    base_copper = part["minimum_signal_copper"]
    if any(layer not in layers for layer in base_copper):
        raise RFError("base part copper layer is absent from stackup")
    tuned = params["tuned_signal_copper"]
    if set(tuned) != set(layers):
        raise RFError("explicit tuned diameter or null omission required on every copper layer")
    for layer in layers:
        diameter = tuned[layer]
        if diameter is None:
            continue  # Omission applies to generated enlargement, never base geometry.
        number(diameter, f"tuned signal copper {layer}", True)
        minimum = max([base_copper.get(layer, 0)] + [obj["diameter"] for obj in objects if obj["type"] == "via" and layer in via_layers(obj, layers)])
        if diameter < minimum:
            raise RFError(f"{layer}: tuned copper cannot shrink the approved base/via minimum")
        if diameter > minimum:
            objects.append({"id": f"signal-copper-{layer}", "type": "copper", "shape": "circle",
                            "net": "signal", "at": [0, 0], "layer": layer, "diameter": diameter})
    ground_copper = params["tuned_ground_copper"]
    if any(layer not in layers for layer in ground_copper):
        raise RFError("ground tuned copper names a nonexistent layer")
    for diameter in ground_copper.values():
        if diameter is not None and number(diameter, "ground tuned copper", True) < params["ground_via"]["diameter"]:
            raise RFError("ground enlargement cannot shrink minimum via copper")
    ground_size = max([params["ground_via"]["diameter"]] + [v for v in ground_copper.values() if v is not None])
    corridor = [0, radius + ground_size / 2, width / 2 + gap]
    centers = []
    for i, angle in enumerate(angles):
        at = [round(radius * math.cos(math.radians(angle)), 12),
              round(radius * math.sin(math.radians(angle)), 12)]
        centers.append(at)
        spans = _span_objects(params["ground_via"], at, f"ground-{i}", "ground", layers)
        objects += spans
        present = {layer for obj in spans for layer in via_layers(obj, layers)}
        for layer in sorted(ground_copper, key=layers.index):
            diameter = ground_copper[layer]
            if diameter is None:
                continue
            if layer not in present:
                raise RFError(f"{layer}: ground overlay requires a ground via land on that layer")
            if diameter > params["ground_via"]["diameter"]:
                objects.append({"id": f"ground-copper-{i}-{layer}", "type": "copper", "shape": "circle",
                                "net": "ground", "at": at, "layer": layer, "diameter": diameter})
    ordered = sorted(zip(angles, centers))
    neighbors = [(ordered[i][1], ordered[(i + 1) % len(ordered)][1]) for i in range(len(ordered))] if len(ordered) > 1 else []
    if any(math.dist(a, b) < ground_size - 1e-9 for a, b in neighbors):
        raise RFError("ground via copper envelopes overlap; change the recipe explicitly")
    validate_ground_corridor(objects, layers, signal_layer, corridor)
    # Existing shield lands are also part of the complete signal-layer envelope.
    for land in part.get("shield_geometry", []):
        # Rectangle can lie wholly inside an annulus bore without intersecting
        # copper. Test farthest corner as well as the outer circular envelope.
        farthest = max(math.dist(land["at"], [x, y]) for x in corridor[:2]
                       for y in [-corridor[2], corridor[2]])
        if (signal_layer in land["layers"] and
                circle_intrudes_corridor(land["at"], land["radius"], corridor) and
                farthest > land.get("inner_radius", 0) + 1e-9):
            raise RFError("approved shield land intrudes on the requested signal-layer exit corridor")
    span = params["antipad_span"]
    if not isinstance(span, list) or len(span) != 2 or any(l not in layers for l in span) or layers.index(span[0]) > layers.index(span[1]):
        raise RFError("antipad span requires ordered existing layer endpoints")
    antipads = params["antipads"]
    required = layers[layers.index(span[0]):layers.index(span[1]) + 1]
    signal_layers = {signal_layer, *base_copper}
    signal_layers.update(layer for obj in objects if obj["type"] == "via" and obj["net"] == "signal" for layer in via_layers(obj, layers))
    if not signal_layers.issubset(required):
        raise RFError("antipad span must cover all signal lands, barrel and routing layers")
    if set(antipads) != set(required):
        raise RFError("antipad definitions must cover exactly their required layer span")
    for layer in required:
        diameter = antipads[layer]
        number(diameter, f"antipad {layer}", True)
        signal_size = max([base_copper.get(layer, 0), tuned[layer] or 0] +
                          [o["diameter"] for o in objects if o["type"] == "via" and o["net"] == "signal" and layer in via_layers(o, layers)])
        if diameter < signal_size + 2 * gap:
            raise RFError(f"{layer}: antipad must include signal copper and configured ground-pour gap")
        for shield in part.get("shield_geometry", []):
            if layer in shield["layers"] and signal_size > 0 and _signal_intrudes_shield(signal_size / 2, shield, gap):
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
    objects.append({"id": "exit-corridor", "type": "keepout", "net": "ground",
                    "shape": "polygon", "layers": [signal_layer], "purpose": "exit-corridor",
                    "points": [[0, -corridor[2]], [corridor[1], -corridor[2]],
                               [corridor[1], corridor[2]], [0, corridor[2]]],
                    "prohibit": ["copperpour"]})
    # Clock is stored as authoring provenance; exact payload remains local +X exit.
    # Workflow applies per-instance clock and pose once, preserving reusable geometry.
    return {"units": "mm", "coordinates": "x-right-y-up-ccw",
            "anchors": {"signal": [0, 0], "exit_direction_deg": 0},
            "net_roles": ["signal", "ground"],
            "layer_roles": {"mount": mount_layer, "signal": signal_layer},
            "default_clock_deg": clock, "objects": objects}


def make_artifact(recipe, artifact_id=None, revision=1, provenance=None):
    payload = generate_payload(recipe)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RFError("revision must be a positive integer")
    artifact_id = artifact_id or str(uuid.uuid4())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_id):
        raise RFError("artifact identity must be a safe nonempty library identifier")
    params = recipe["parameters"]
    part = recipe["dependencies"]["base_part"]
    caps = ["native-vias", "net-copper-shapes", "layer-rule-areas"]
    if any(o.get("via_type") == "microvia" for o in payload["objects"]):
        caps.append("adjacent-microvia-stacks")
    artifact = {"schema": SCHEMA, "artifact_id": artifact_id, "revision": revision,
                "kind": "rf-transition", "payload_schema": PAYLOAD_SCHEMA,
                "recipe": deepcopy(recipe), "payload": payload,
                "compatibility": {"part_id": part["part_id"], "part_sha256": part["sha256"],
                                  "mount": part["mount"], "side": part["side"],
                                  "netclasses": deepcopy(params["netclasses"]),
                                  "stackup": deepcopy(params["stackup"])},
                "required_capabilities": sorted(caps),
                "provenance": deepcopy(provenance or {"operation": "new"})}
    artifact["hashes"] = {"payload": digest(payload), "recipe": digest(recipe),
                          "artifact": digest(artifact)}
    return artifact


def _supported_schema(value, prefix):
    return isinstance(value, str) and re.fullmatch(re.escape(prefix) + r"/1\.\d+", value)


def _validate_saved_payload(artifact):
    """Consume supported data independently of understanding its recipe."""
    payload = artifact["payload"]
    if payload.get("units") != "mm" or payload.get("coordinates") != "x-right-y-up-ccw":
        raise RFError("unsupported payload units or coordinate convention")
    if payload.get("net_roles") != ["signal", "ground"] or payload.get("anchors") != {"signal": [0, 0], "exit_direction_deg": 0}:
        raise RFError("unsupported RF anchors or net roles")
    layers = artifact["compatibility"]["stackup"]
    if layers != ["F.Cu"] + [f"In{i}.Cu" for i in range(1, len(layers) - 1)] + ["B.Cu"] or len(layers) not in range(2, 33, 2):
        raise RFError("unsupported saved stackup")
    if any(value not in layers for value in payload["layer_roles"].values()):
        raise RFError("saved layer role is absent from stackup")
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
        else:
            raise RFError("unsupported saved object type (signal traces are never generated)")


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
    supported = bool(match and (9, 0, 0) <= tuple(map(int, match.groups())) <= (10, 0, 8))
    return {"runtime": text, "supported": supported,
            "capabilities": sorted(CAPABILITIES) if supported else [],
            "limitations": ["No impedance, electromagnetic or manufacturing qualification",
                            "Only untreated via fabrication; circular centered signal lands",
                            "No custom padstack removal or automatic base-footprint edits",
                            "Native copper connectivity and fabrication require project review"]}
