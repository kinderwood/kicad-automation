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
    """Inner and outer radius and centre of an exact single-circle annular pad."""
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
    return {"at": [px + cx, -py - cy], "inner": inner, "outer": outer}


def _solid_zone_connection(pad, footprint):
    """Resolve the ground pad's zone connection; RF connectors need solid."""
    names = {pcbnew.ZONE_CONNECTION_NONE: "none", pcbnew.ZONE_CONNECTION_THERMAL: "thermal relief",
             pcbnew.ZONE_CONNECTION_THT_THERMAL: "thermal relief for through-hole pads"}
    for owner, item in (("pad", pad), ("footprint", footprint)):
        setting = item.GetLocalZoneConnection()
        if setting == pcbnew.ZONE_CONNECTION_FULL:
            return
        if setting != pcbnew.ZONE_CONNECTION_INHERITED:
            raise RFError(f"ground pad zone connection must be solid, but the {owner} is set to "
                          f"{names.get(setting, 'an unknown mode')}")
    raise RFError("ground pad zone connection must be solid, but the pad and the footprint both "
                  "inherit, which leaves it to each zone's default; set the pad or the footprint to solid")


def _copper_layers(pad):
    return {layer for layer in pad.GetLayerSet().Seq() if pcbnew.IsCopperLayer(layer)}


def read_base_part(path, signal_pad="1", ground_pad="2", side="front",
                   approved=False):
    """Pin exact source bytes and inspect the actual pads in KiCad.

    The footprint has exactly two electrical pads. Pin 1 signal, pin 2 ground
    and front mounting are the defaults. The ground pad is one SMD annulus on
    the mount side, centred on the signal, with a solid zone connection.

    The result does not depend on a board stackup: a through-hole signal pad
    has one land diameter and passes through any board, and SMD pads sit on
    the mount side only.
    """
    source = Path(path)
    if source.suffix != ".kicad_mod" or not source.is_file():
        raise RFError("select an existing approved .kicad_mod library source")
    if not approved:
        raise RFError("librarian approval of the selected source is required")
    if not capability_report(pcbnew.Version())["supported"]:
        raise RFError("KiCad runtime lies outside 10.0.0–10.0.8")
    if side not in ("front", "back"):
        raise RFError("mount side must be front or back")
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
    if not isinstance(ground_pad, str) or not ground_pad or ground_pad == signal_pad:
        raise RFError("the ground pad needs its own pad identifier")
    grounds = [pad for pad in pads if pad.GetNumber() == ground_pad]
    if len(grounds) != 1:
        raise RFError("exactly one physical ground pad is required")
    ground = grounds[0]
    if any(pad.GetNumber() and pad.GetNumber() not in (signal_pad, ground_pad)
           and pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH for pad in pads):
        raise RFError("the footprint must have exactly two electrical pads, signal and ground")
    mount = "PTH" if signal.GetAttribute() == pcbnew.PAD_ATTRIB_PTH else "SMD"
    mount_layer = pcbnew.F_Cu if side == "front" else pcbnew.B_Cu
    if signal.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
        raise RFError("signal pad must use KiCad's ordinary padstack, with one land size on every layer")
    if signal.GetShape(mount_layer) != pcbnew.PAD_SHAPE_CIRCLE:
        raise RFError("signal pad must have a circular land")
    size = signal.GetSize(mount_layer)
    if size.x != size.y or size.x <= 0:
        raise RFError("noncircular signal land is unsupported")
    land = pcbnew.ToMM(size.x)
    drill = None
    if mount == "PTH":
        if not {pcbnew.F_Cu, pcbnew.B_Cu} <= _copper_layers(signal):
            raise RFError("through-hole signal pad must pass through the whole board")
        if signal.GetUnconnectedLayerMode() != pcbnew.UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END:
            raise RFError("through-hole signal pad must be set to remove unused layers and keep start and end")
        hole = signal.GetDrillSize()
        if signal.GetDrillShape() != pcbnew.PAD_DRILL_SHAPE_CIRCLE or hole.x != hole.y or hole.x <= 0:
            raise RFError("PTH signal requires a circular plated hole")
        drill = pcbnew.ToMM(hole.x)
        if land <= drill:
            raise RFError("PTH minimum signal land must exceed plated-hole diameter")
    elif _copper_layers(signal) != {mount_layer}:
        raise RFError("SMD signal must have copper on the selected mount side only")
    if ground.GetAttribute() != pcbnew.PAD_ATTRIB_SMD or _copper_layers(ground) != {mount_layer}:
        raise RFError("ground pad must be SMD copper on the selected mount side only; "
                      "a back-mounted part needs its own back-side source")
    for item in footprint.GraphicalItems():
        if pcbnew.IsCopperLayer(item.GetLayer()):
            raise RFError("unclassified base-footprint copper graphics require librarian review")
    syntax = _sexpr(data.decode("utf-8"))
    matching = [node for node in syntax if isinstance(node, list) and len(node) > 1
                and node[0] == "pad" and node[1] == ground_pad]
    annulus = _annulus(matching[0]) if len(matching) == 1 else None
    if annulus is None:
        raise RFError("ground pad must be one annular (donut) pad: a custom pad drawn as a single unfilled circle")
    if math.hypot(*annulus["at"]) > 1e-6:
        raise RFError("the annular ground pad must be centred on the signal pad")
    _solid_zone_connection(ground, footprint)
    if source.read_bytes() != data:
        raise RFError("base source changed during inspection; select it again")
    return {"part_id": f"{source.parent.stem}:{source.stem}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_filename": source.name, "approval": "librarian-approved",
            "mount": mount, "side": side, "signal_pad": signal_pad,
            "ground_pad": ground_pad, "signal_at": [0, 0],
            "signal_shape": "circle", "signal_land": land, "signal_drill": drill,
            "ground_annulus": {"inner_diameter": round(2 * annulus["inner"], 9),
                               "outer_diameter": round(2 * annulus["outer"], 9)},
            "ground_zone_connection": "solid",
            "base_pad_policy": "Preserve approved source copper; null means no added copper, never remove base lands"}
