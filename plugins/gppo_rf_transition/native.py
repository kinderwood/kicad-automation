"""Read-only KiCad source inspection. No placed board or central library writes."""

import hashlib
import json
import math
from pathlib import Path
import re

import pcbnew

from .model import RFError, capability_report


def _sexpr(text):
    """Read data-only footprint syntax to recognize an exact custom annulus.

    pcbnew's shared-pointer primitive vector is not iterable in the supported
    Python bindings. Unknown primitive constructions use conservative bounds.
    """
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()]+', text)
    stack = []
    root = []
    current = root
    for token in tokens:
        if token == "(":
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ")":
            if not stack:
                raise RFError("unbalanced footprint syntax")
            current = stack.pop()
        else:
            current.append(json.loads(token) if token.startswith('"') else token)
    if stack or len(root) != 1:
        raise RFError("malformed footprint syntax")
    return root[0]


def _child(node, name):
    return next((item for item in node if isinstance(item, list) and item and item[0] == name), None)


def _annulus(pad_node):
    if len(pad_node) < 4 or pad_node[3] != "custom":
        return None
    primitives = _child(pad_node, "primitives")
    if not primitives or len(primitives) != 2 or primitives[1][0] != "gr_circle":
        return None
    circle = primitives[1]
    center, end, width = (_child(circle, key) for key in ("center", "end", "width"))
    filled = _child(circle, "fill")
    position = _child(pad_node, "at")
    size = _child(pad_node, "size")
    if not all([center, end, width, position, size]) or not filled or filled[1] != "no":
        return None
    if len(position) > 3 and float(position[3]) % 360:
        return None
    cx, cy = map(float, center[1:3])
    ex, ey = map(float, end[1:3])
    px, py = map(float, position[1:3])
    thickness = float(width[1])
    radius = math.hypot(ex - cx, ey - cy)
    inner, outer = radius - thickness / 2, radius + thickness / 2
    # The anchor primitive must be wholly contained in the annular copper.
    anchor_radius = max(map(float, size[1:3])) / 2
    anchor_distance = math.hypot(cx, cy)
    if thickness <= 0 or inner <= 0 or anchor_distance - anchor_radius < inner or anchor_distance + anchor_radius > outer:
        return None
    return {"at": [round(px + cx, 12), round(-py - cy, 12)],
            "radius": outer, "inner_radius": inner,
            "validation": "exact-single-circle-annulus"}


def read_base_part(path, signal_pad="1", shield_pads=("2",),
                   stackup=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"),
                   side="front", approved=False):
    """Pin exact source bytes and inspect the actual signal pad in KiCad.

    Shield geometry uses conservative bounding circles, so complex shield lands
    may block same-layer exits. Circular custom annuli on other layers remain
    supported; the shield is never mistaken for the center signal land.
    """
    source = Path(path)
    if source.suffix != ".kicad_mod" or not source.is_file():
        raise RFError("select an existing approved .kicad_mod library source")
    if not approved:
        raise RFError("librarian approval of the selected source is required")
    if not capability_report(pcbnew.Version())["supported"]:
        raise RFError("KiCad runtime lies outside 10.0.0–10.0.8")
    data = source.read_bytes()
    footprint = pcbnew.FootprintLoad(str(source.parent), source.stem)
    if footprint is None:
        raise RFError("KiCad could not read the selected base footprint")
    pads = list(footprint.Pads())
    signal = [pad for pad in pads if pad.GetNumber() == signal_pad]
    if len(signal) != 1:
        raise RFError("exactly one physical center signal pad is required")
    signal = signal[0]
    if signal.GetAttribute() not in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_SMD):
        raise RFError("GPPO signal pin must be plated PTH or SMD, never NPTH")
    if signal.GetPosition().x or signal.GetPosition().y:
        raise RFError("signal pad must be centered at the footprint origin")
    shields = [pad for pad in pads if pad.GetNumber() in shield_pads]
    if not shields or set(pad.GetNumber() for pad in shields) != set(shield_pads):
        raise RFError("every selected shield pad identifier must exist")
    if any(pad.GetNumber() and pad.GetNumber() not in [signal_pad, *shield_pads]
           and pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH for pad in pads):
        raise RFError("base footprint has unclassified electrical pads")
    layer_ids = {name: pcbnew.LayerNameToId(name) if hasattr(pcbnew, "LayerNameToId")
                 else pcbnew.BOARD().GetLayerID(name) for name in stackup}
    minimum = {}
    for name, layer in layer_ids.items():
        if not signal.IsOnLayer(layer):
            continue
        if signal.GetShape(layer) != pcbnew.PAD_SHAPE_CIRCLE:
            raise RFError("signal pad must have a circular land on every copper layer")
        size = signal.GetSize(layer)
        if size.x != size.y:
            raise RFError("noncircular signal land is unsupported")
        minimum[name] = pcbnew.ToMM(size.x)
    mount = "PTH" if signal.GetAttribute() == pcbnew.PAD_ATTRIB_PTH else "SMD"
    mount_layer = stackup[0] if side == "front" else stackup[-1]
    if side not in ("front", "back") or mount_layer not in minimum:
        raise RFError("selected mount side has no signal land")
    if mount == "SMD" and set(minimum) != {mount_layer}:
        raise RFError("SMD signal must have copper on the mount side only")
    if not any(pad.IsOnLayer(layer_ids[mount_layer]) for pad in shields):
        raise RFError("selected mount side has no shield copper; mirroring is an explicit instance operation")
    if mount == "PTH":
        drill = signal.GetDrillSize()
        if signal.GetDrillShape() != pcbnew.PAD_DRILL_SHAPE_CIRCLE or drill.x != drill.y or drill.x <= 0:
            raise RFError("PTH signal requires a circular plated hole")
        if any(value <= pcbnew.ToMM(drill.x) for value in minimum.values()):
            raise RFError("PTH minimum signal land must exceed plated-hole diameter")
    for item in footprint.GraphicalItems():
        if item.GetLayer() in layer_ids.values():
            raise RFError("unclassified base-footprint copper graphics require librarian review")
    shield_geometry = []
    syntax = _sexpr(data.decode("utf-8"))
    pad_nodes = [node for node in syntax if isinstance(node, list) and node and node[0] == "pad"]
    for pad in shields:
        present = [name for name, layer in layer_ids.items() if pad.IsOnLayer(layer)]
        if not present:
            raise RFError("shield pad has no copper in the selected stackup")
        bounds = pad.GetBoundingBox()
        center = bounds.GetCenter()
        matching = [node for node in pad_nodes if node[1] == pad.GetNumber()]
        exact = _annulus(matching[0]) if len(matching) == 1 else None
        shield_geometry.append({"at": [pcbnew.ToMM(center.x), -pcbnew.ToMM(center.y)],
                                "radius": (pcbnew.ToMM(bounds.GetWidth()) ** 2 +
                                           pcbnew.ToMM(bounds.GetHeight()) ** 2) ** 0.5 / 2,
                                "layers": present,
                                "validation": "conservative-bounding-circle", **(exact or {})})
    if source.read_bytes() != data:
        raise RFError("base source changed during inspection; select it again")
    return {"part_id": f"{source.parent.stem}:{source.stem}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_filename": source.name, "approval": "librarian-approved",
            "mount": mount, "side": side, "signal_pad": signal_pad,
            "inspection_stackup": list(stackup),
            "shield_pads": list(shield_pads), "signal_at": [0, 0],
            "signal_shape": "circle", "minimum_signal_copper": minimum,
            "shield_geometry": shield_geometry,
            "base_pad_policy": "Preserve approved source copper; null means no added copper, never remove base lands"}
