"""Read-only consumer contract for approved, declarative RF library artifacts.

No authoring plugin is imported and no embedded recipe is executed. Coordinates
are millimetres, X right, Y up, angles counterclockwise. The native adapter
performs the final Y-axis conversion into KiCad's board coordinate system.
"""
import copy
import hashlib
import json
import math
import re
from collections import OrderedDict
from pathlib import Path


CAPABILITIES = frozenset(("native-vias", "net-copper-shapes", "layer-rule-areas",
                          "adjacent-microvia-stacks"))


class ArtifactError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ArtifactError("Invalid positive " + name)


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ArtifactError("Invalid finite millimetre point")


def _valid_polygon(points):
    from .core.outline import Segment, validate_outline
    if len(points) < 3:
        raise ArtifactError("Polygon requires three points")
    for point in points:
        _point(point)
    try:
        validate_outline([Segment(tuple(a), tuple(b)) for a, b in zip(points, points[1:]+points[:1])])
    except ValueError as exc:
        raise ArtifactError("Malformed RF polygon: " + str(exc)) from exc
    area = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))
    if abs(area) < 1e-12:
        raise ArtifactError("RF polygon has no area")


def _circle_intrudes(points, center, radius, tolerance=1e-9):
    inside = False
    minimum = float("inf")
    x, y = center
    for a, b in zip(points, points[1:]+points[:1]):
        vx, vy = b[0]-a[0], b[1]-a[1]
        t = max(0, min(1, ((x-a[0])*vx+(y-a[1])*vy)/(vx*vx+vy*vy)))
        minimum = min(minimum, math.hypot(x-a[0]-t*vx, y-a[1]-t*vy))
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y-a[1])*vx/vy:
            inside = not inside
    return inside or minimum < radius - tolerance


def validate_artifact(artifact, *, layers=None, part_sha256=None, netclass=None):
    """Validate exact supported semantics and integrity, retaining opaque recipes."""
    try:
        canonical(artifact)
        if not artifact["schema"].startswith("gppo.artifact/1."):
            raise ArtifactError("Unsupported artifact envelope")
        if artifact["payload_schema"] != "gppo.rf/1.0" or artifact["kind"] != "rf-transition":
            raise ArtifactError("Unsupported RF payload schema")
        if not artifact["artifact_id"] or not isinstance(artifact["revision"], (str, int)):
            raise ArtifactError("Missing artifact identity/revision")
        hashes = artifact["hashes"]
        for name in ("payload", "recipe"):
            if hashes[name] != sha256(artifact[name]):
                raise ArtifactError("Artifact integrity failure: " + name)
        unsigned = {k: v for k, v in artifact.items() if k != "hashes"}
        if hashes.get("artifact") != sha256(unsigned):
            raise ArtifactError("Artifact envelope integrity failure")
        if not set(artifact.get("required_capabilities", ())) <= CAPABILITIES:
            raise ArtifactError("Unsupported required consumer capability")
        payload = artifact["payload"]
        if payload["units"] != "mm" or payload["coordinates"] != "x-right-y-up-ccw":
            raise ArtifactError("Unsupported units/coordinate convention")
        compatibility = artifact["compatibility"]
        if not compatibility["part_id"] or not re.fullmatch(r"[0-9a-f]{64}", compatibility["part_sha256"]):
            raise ArtifactError("Artifact requires an exact approved footprint identity/hash")
        if compatibility["mount"] not in ("SMD", "PTH") or compatibility["side"] not in ("front", "back"):
            raise ArtifactError("Artifact requires explicit mount and side compatibility")
        declared_layers = compatibility["stackup"]
        if not isinstance(declared_layers, list) or len(declared_layers) < 2 or len(set(declared_layers)) != len(declared_layers):
            raise ArtifactError("Artifact requires a unique ordered copper stackup")
        if not compatibility["netclasses"] or any(not isinstance(c, str) or not c for c in compatibility["netclasses"]):
            raise ArtifactError("Artifact requires explicit allowed netclasses")
        if payload["net_roles"] != ["signal", "ground"] or payload["anchors"]["signal"] != [0, 0] or payload["anchors"]["exit_direction_deg"] != 0:
            raise ArtifactError("Unsupported RF net-role or anchor convention")
        if set(payload["layer_roles"]) != {"mount", "signal"} or any(x not in declared_layers for x in payload["layer_roles"].values()):
            raise ArtifactError("Artifact requires valid mount and signal layer roles")
        if part_sha256 is not None and compatibility.get("part_sha256") not in (None, "", part_sha256):
            raise ArtifactError("Artifact is incompatible with selected footprint revision")
        if netclass is not None and compatibility.get("netclasses") and netclass not in compatibility["netclasses"]:
            raise ArtifactError("Artifact does not allow selected netclass")
        if layers is not None and compatibility.get("stackup") and list(compatibility["stackup"]) != list(layers):
            raise ArtifactError("Artifact stackup differs from board copper stack")
        layers = declared_layers if layers is None else layers
        ids = set()
        for item in payload["objects"]:
            if not item["id"] or item["id"] in ids:
                raise ArtifactError("Duplicate or empty RF local object ID")
            ids.add(item["id"])
            if item.get("net") not in ("signal", "ground"):
                raise ArtifactError("Unknown RF net role")
            kind = item["type"]
            if kind == "via":
                _point(item["at"])
                _positive(item["diameter"], "via diameter")
                _positive(item["drill"], "via drill")
                if item["drill"] >= item["diameter"]:
                    raise ArtifactError("Via drill must be smaller than copper diameter")
                span = item["layers"]
                if len(span) != 2 or span[0] == span[1]:
                    raise ArtifactError("Via requires two distinct layer endpoints")
                if item["via_type"] not in ("through", "microvia", "blind_buried"):
                    raise ArtifactError("Unknown via type")
                if item.get("treatment", "untreated") != "untreated":
                    raise ArtifactError("Requested via treatment is not supported by this renderer")
                if layers is not None:
                    if any(x not in layers for x in span):
                        raise ArtifactError("Via references nonexistent copper layer")
                    a, b = [list(layers).index(x) for x in span]
                    if a >= b:
                        raise ArtifactError("Via endpoints must follow stack order")
                    if item["via_type"] == "microvia" and b - a != 1:
                        raise ArtifactError("Microvia must span adjacent copper layers")
                    if item["via_type"] == "through" and (a != 0 or b != len(layers) - 1):
                        raise ArtifactError("Through via must span the full copper stack")
            elif kind in ("copper", "keepout"):
                item_layers = item.get("layers", [item.get("layer")])
                if not item_layers or any(not x or (layers is not None and x not in layers) for x in item_layers):
                    raise ArtifactError("Object references nonexistent copper layer")
                shape = item.get("shape", "circle")
                if shape == "circle":
                    _point(item["at"])
                    _positive(item["diameter"], "circle diameter")
                elif shape == "polygon":
                    points = item["points"]
                    _valid_polygon(points)
                elif shape == "arc" and kind == "copper":
                    for key in ("start", "mid", "end"):
                        _point(item[key])
                    _positive(item["width"], "arc width")
                else:
                    raise ArtifactError("Unsupported RF shape")
                if kind == "keepout" and (not item.get("prohibit") or not set(item["prohibit"]) <= {
                        "copperpour", "tracks", "vias", "pads", "footprints"}):
                    raise ArtifactError("Unknown rule-area prohibition")
            else:
                raise ArtifactError("Unsupported RF primitive; signal tracks are not generated")
        if compatibility["mount"] == "SMD" and not any(x["type"] == "via" and x["net"] == "signal" for x in payload["objects"]):
            raise ArtifactError("RF artifact must contain an actual signal via")
        # Independently check generated ground envelopes against saved exclusion
        # geometry. No authoring recipe execution is needed for this validation.
        for area in payload["objects"]:
            if area["type"] != "keepout" or area.get("purpose") not in ("exit-corridor", "antipad"):
                continue
            tolerance = area.get("tolerance_mm", 0)
            if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= .001:
                raise ArtifactError("Invalid declared polygon approximation tolerance")
            for item in payload["objects"]:
                if item["net"] != "ground" or item["type"] not in ("via", "copper"):
                    continue
                if item["type"] == "via":
                    a, b = [list(layers).index(x) for x in item["layers"]]
                    present = layers[a:b+1]
                else:
                    present = [item["layer"]]
                if not set(present).intersection(area["layers"]):
                    continue
                if item.get("shape", "circle") != "circle":
                    raise ArtifactError("Ground envelope validation requires circular lands")
                radius = max(item["diameter"], item.get("drill", 0))/2
                if area.get("shape") == "circle":
                    intrudes = math.dist(item["at"], area["at"]) < radius + area["diameter"]/2 - 1e-9
                else:
                    intrudes = _circle_intrudes(area["points"], item["at"], radius, tolerance+1e-9)
                if intrudes:
                    raise ArtifactError("Ground pad/barrel/copper intrudes on saved RF exclusion geometry")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ArtifactError("Malformed RF artifact: " + str(exc)) from exc
    return copy.deepcopy(artifact)


# Validation outcomes keyed by content checksum and requested checks. The file
# is still read and hashed on every request, so a changed artifact is always
# validated afresh; only the geometry checks of unchanged bytes are reused.
# Dialog refreshes ask about the same few assigned transitions many times.
_VALIDATED = OrderedDict()
_VALIDATED_LIMIT = 256


def load_artifact(path, **checks):
    data = Path(path).read_bytes()
    key = (hashlib.sha256(data).hexdigest(), json.dumps(checks, sort_keys=True))
    try:
        outcome = _VALIDATED[key]
    except KeyError:
        try:
            outcome = validate_artifact(json.loads(data.decode("utf-8")), **checks)
        except ValueError as error:
            outcome = error
        _VALIDATED[key] = outcome
        while len(_VALIDATED) > _VALIDATED_LIMIT:
            _VALIDATED.popitem(last=False)
    else:
        _VALIDATED.move_to_end(key)
    if isinstance(outcome, ValueError):
        raise outcome
    return copy.deepcopy(outcome)


def pin_artifact(path, destination, **checks):
    """Pin exact approved bytes; never rewrite or alter the librarian source."""
    source = Path(path)
    data = source.read_bytes()
    artifact = validate_artifact(json.loads(data), **checks)
    target = Path(destination)
    if target.resolve() == source.resolve():
        raise ArtifactError("Pinned snapshot must be separate from library source")
    if target.exists() and target.read_bytes() != data:
        raise ArtifactError("Pinned artifact revision collision")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return artifact
