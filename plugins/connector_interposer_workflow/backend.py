"""Native PCB placement and reconciliation on reviewable saved project copies.

Importing this module requires neither KiCad nor a footprint/RF authoring plugin.
``NativeBackend`` loads pcbnew lazily. Apply always writes a different project
directory, verifies its saved board and journals the board/registry/report set.
It never mutates the editor's live BOARD or uses native Undo for recovery.
"""
import copy
import hashlib
import json
import math
import shutil
import tempfile
from pathlib import Path

from .artifacts import canonical, sha256, validate_artifact
from .core.geometry import (
    MAX_FEATURE_TOLERANCE_MM, distance_text, feature_deviation_message,
    feature_tolerance,
)
from .recovery import apply_files, file_hash, require_closed


def _project_hashes(root):
    ignored = {".git", ".connector-interposer-recovery", "__pycache__"}
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts) or path.suffix == ".lck":
            continue
        if path.is_symlink():
            raise BackendError("Project-copy export does not follow symlinks")
        if path.is_file():
            result[str(relative)] = file_hash(path)
    return result


def _serialized(path, owned):
    """Full native serialized owned-item guards, including footprint children."""
    from .core.sexpr import loads, child
    tree = loads(Path(path).read_text(encoding="utf-8-sig"))
    result = {}
    for item in tree[1:]:
        if not isinstance(item, list):
            continue
        identity = child(item, "uuid")
        if identity and identity[1] in owned:
            # Foreign additions to a group never become owned or get deleted.
            value = [v for v in item if not (item[0] == "group" and isinstance(v, list) and v and v[0] == "members")]
            result[identity[1]] = sha256(value)
    return result


class BackendError(ValueError):
    pass


def uuid_of(item):
    return (item if hasattr(item, "m_Uuid") else item.AsEdaItem()).m_Uuid.AsString()


def _set_uuid(item, value, p):
    identity = p.KIID(value.AsString() if hasattr(value, "AsString") else value)
    if hasattr(item, "SetUuid"):
        item.SetUuid(identity)
    else:
        item.m_Uuid.Clone(identity)


def _spec_identity(spec):
    value = copy.deepcopy(spec)
    value.get("footprint", {}).pop("path", None)
    return value


def _xy(item):
    return [item.x, item.y]


def _polygon(poly):
    return [[_xy(poly.COutline(i).CPoint(j)) for j in range(poly.COutline(i).PointCount())]
            for i in range(poly.OutlineCount())]


def board_items(board):
    return {uuid_of(x): x for x in list(board.GetFootprints()) + list(board.GetTracks()) +
            list(board.GetDrawings()) + list(board.Zones()) + list(board.Groups())}


def board_state(board, p):
    """Expected native geometry/net/pose/group projection, keyed by exact UUID."""
    state = {}
    active_copper = {p.F_Cu, p.B_Cu} | {
        getattr(p, f"In{index}_Cu") for index in range(1, board.GetCopperLayerCount() - 1)
    }
    all_copper = set(range(0, 63, 2))
    for uid, item in board_items(board).items():
        group = item.GetParentGroup()
        value = {"type": item.GetClass(), "xy": _xy(item.GetPosition()),
                 "group": uuid_of(group) if group else None, "locked": item.IsLocked()}
        if isinstance(item, p.PCB_GROUP):
            value = {"type": item.GetClass(), "name": item.GetName(),
                     "group": uuid_of(group) if group else None, "locked": item.IsLocked()}
        elif isinstance(item, p.FOOTPRINT):
            value.update(reference=item.GetReference(), value=item.GetValue(),
                         fields=dict(item.GetFieldsText()),
                         angle=round(item.GetOrientationDegrees() % 360, 8),
                         attributes=item.GetAttributes(), path=str(item.GetPath().AsString()),
                         pads={uuid_of(q): {"number": q.GetNumber(), "net": q.GetNetname(),
                               "xy": _xy(q.GetPosition()), "size": _xy(q.GetSize()),
                               "drill": _xy(q.GetDrillSize()), "shape": q.GetShape(),
                               # A wildcard through-hole layer set may expand to
                               # all 32 copper IDs only after serialization.
                               # Inactive copper bits are not board geometry.
                               "layers": [layer for layer in q.GetLayerSet().Seq()
                                          if layer not in all_copper or layer in active_copper],
                               "angle": round(q.GetOrientationDegrees() % 360, 8)} for q in item.Pads()})
        elif isinstance(item, p.PCB_TRACK):
            value.update(net=item.GetNetname(), layer=item.GetLayer(), start=_xy(item.GetStart()),
                         end=_xy(item.GetEnd()), width=(item.GetWidth(p.F_Cu) if isinstance(item, p.PCB_VIA) else item.GetWidth()))
            if isinstance(item, p.PCB_VIA):
                value.update(drill=item.GetDrillValue(), span=[item.TopLayer(), item.BottomLayer()],
                             via_type=item.GetViaType())
        elif isinstance(item, p.PCB_SHAPE):
            value.update(layer=item.GetLayer(), shape=item.GetShape(), start=_xy(item.GetStart()),
                         end=_xy(item.GetEnd()), width=item.GetWidth(), filled=(item.IsSolidFill() if hasattr(item, "IsSolidFill") else item.IsFilled()),
                         net=item.GetNetname())
            if item.GetShape() == p.SHAPE_T_ARC:
                value["mid"] = _xy(item.GetArcMid())
            if item.GetShape() == p.SHAPE_T_POLY:
                value["polygon"] = _polygon(item.GetPolyShape())
        elif isinstance(item, p.ZONE):
            value.update(layer=item.GetLayer(), layers=list(item.GetLayerSet().Seq()),
                         net=item.GetNetname(), rule=item.GetIsRuleArea(),
                         polygon=_polygon(item.Outline()),
                         flags=[item.GetDoNotAllowTracks(), item.GetDoNotAllowVias(),
                                item.GetDoNotAllowPads(), item.GetDoNotAllowFootprints(),
                                (item.GetDoNotAllowCopperPour if hasattr(item, "GetDoNotAllowCopperPour")
                                 else item.GetDoNotAllowZoneFills)()])
        state[uid] = value
    return state


class NativeBackend:
    def __init__(self, pcbnew_module=None):
        if pcbnew_module is None:
            import pcbnew as pcbnew_module
        self.p = pcbnew_module

    def copper_layers(self, board):
        p = self.p
        count = board.GetCopperLayerCount()
        return [board.GetLayerName(p.F_Cu)] + [board.GetLayerName(getattr(p, "In%d_Cu" % i))
                for i in range(1, count - 1)] + [board.GetLayerName(p.B_Cu)]

    def inspect_graphics(self, board_path, source_layer="Dwgs.User", label_offset=None):
        """Capture already imported native geometry; never guess a DXF parser."""
        board = self._load(board_path)
        layer = self._layer(board, source_layer)
        result = {"source_layer": source_layer, "source_sha256": file_hash(board_path),
                  "circles": [], "texts": [], "segments": [], "locations": [], "errors": []}
        offset = label_offset if label_offset is not None else [0, 0]
        if len(offset) != 2 or any(not math.isfinite(v) for v in offset):
            raise BackendError("Mechanical label offset must be two finite millimetre values")
        result["label_offset"] = list(offset)
        for item in board.GetDrawings():
            if item.GetLayer() != layer:
                continue
            uid = uuid_of(item)
            if isinstance(item, self.p.PCB_TEXT):
                result["texts"].append({"uuid": uid, "text": item.GetText(),
                                        "at": [v / 1e6 for v in _xy(item.GetPosition())]})
            elif isinstance(item, self.p.PCB_SHAPE):
                if item.GetShape() == self.p.SHAPE_T_CIRCLE:
                    result["circles"].append({"uuid": uid, "center": [v / 1e6 for v in _xy(item.GetCenter())],
                                              "radius": item.GetRadius() / 1e6})
                elif item.GetShape() in (self.p.SHAPE_T_SEGMENT, self.p.SHAPE_T_ARC):
                    segment = {"source_uuid": uid, "owner": "board", "start": [v/1e6 for v in _xy(item.GetStart())],
                               "end": [v/1e6 for v in _xy(item.GetEnd())]}
                    if item.GetShape() == self.p.SHAPE_T_ARC:
                        segment["mid"] = [v/1e6 for v in _xy(item.GetArcMid())]
                    result["segments"].append(segment)
        # Only explicit complete mechanical keys inside exactly one circle are
        # accepted. Gang-only/pin-only drafts require an explicit UI association.
        import re
        seen = set()
        used_texts = set()
        for circle in result["circles"]:
            labels = [t for t in result["texts"] if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*", t["text"].strip()) and
                      math.dist([t["at"][i]-offset[i] for i in range(2)], circle["center"]) <= circle["radius"]]
            if not labels:
                # Unlabelled circles may be DC connector pad locations. Their
                # selected pin-1 anchor and full footprint geometry are checked
                # later; they are not guessed into RF identities here.
                continue
            if len(labels) != 1:
                result["errors"].append("Circle " + circle["uuid"] + " needs one explicit gang-pin label")
                continue
            key = labels[0]["text"].strip()
            if key in seen or labels[0]["uuid"] in used_texts:
                result["errors"].append("Duplicate mechanical identity " + key)
            seen.add(key)
            used_texts.add(labels[0]["uuid"])
            result["locations"].append({"key": key, "center": circle["center"],
                                        "circle_uuid": circle["uuid"], "text_uuid": labels[0]["uuid"]})
        for text in result["texts"]:
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*", text["text"].strip()) and text["uuid"] not in used_texts:
                result["errors"].append("Mechanical key has no matching circle: " + text["text"])
        return result

    def _normalize_outline(self, board, desired):
        outline = desired.get("outline")
        if not outline or not outline.get("enabled") or "outer_uuids" not in outline:
            return
        all_items = board_items(board)
        for fp in board.GetFootprints():
            all_items.update({uuid_of(x): x for x in fp.GraphicalItems()})
        source_layer = self._layer(board, outline.get("source_layer", "Dwgs.User"))
        def capture(ids):
            contour = []
            for uid in ids:
                obj = all_items.get(uid)
                if not isinstance(obj, self.p.PCB_SHAPE) or obj.GetShape() not in (self.p.SHAPE_T_SEGMENT, self.p.SHAPE_T_ARC):
                    raise BackendError("Selected outline source is missing or not a native line/arc")
                fp = obj.GetParentFootprint()
                if obj.GetLayer() != source_layer and not (fp and obj.GetLayer() == self.p.Edge_Cuts):
                    raise BackendError("Outline source is on a different imported layer")
                value = {"source_uuid": uid, "owner": "footprint" if fp else "board",
                         "start": [v/1e6 for v in _xy(obj.GetStart())], "end": [v/1e6 for v in _xy(obj.GetEnd())]}
                if obj.GetShape() == self.p.SHAPE_T_ARC:
                    value["mid"] = [v/1e6 for v in _xy(obj.GetArcMid())]
                contour.append(value)
            return contour
        outline["outer"] = capture(outline.pop("outer_uuids"))
        outline["cutouts"] = [capture(ids) for ids in outline.pop("cutout_uuids", [])]

    def _load(self, path):
        board = self.p.LoadBoard(str(path))
        if board is None:
            raise BackendError("Cannot load saved native PCB")
        return board

    def _layer(self, board, name):
        layer = board.GetLayerID(name)
        if layer < 0:
            raise BackendError("Unknown board layer: " + str(name))
        return layer

    def _approved(self, selection):
        if not selection.get("approved"):
            raise BackendError("Select a librarian-approved existing footprint")
        path = Path(selection["path"])
        if path.suffix != ".kicad_mod" or file_hash(path) != selection["sha256"]:
            raise BackendError("Selected library footprint is missing or its revision changed")
        fp = self.p.PCB_IO_KICAD_SEXPR().FootprintLoad(str(path.parent), path.stem)
        if fp is None:
            raise BackendError("Cannot read selected approved footprint")
        return fp

    def _validate_microvias(self, board, desired, instances):
        config = desired.get('interposer_microvias')
        if not config:
            return
        if config.get('enabled') is not True:
            raise BackendError('Interposer microvia configuration must be explicitly enabled')
        reference = config.get('reference', '')
        spec = instances.get(reference)
        if not spec or spec.get('kind') != 'interposer':
            raise BackendError('Interposer microvias require one selected interposer instance')
        layers = self.copper_layers(board)
        if config.get('layers') != layers:
            raise BackendError('Interposer microvia copper stack differs from the saved board')
        if config.get('side') not in ('F', 'B') or config['side'] != spec.get('side'):
            raise BackendError('Interposer microvia mounting side differs from placement')
        if config.get('pose') != spec.get('pose'):
            raise BackendError('Interposer microvia placement differs from the interposer placement')
        if config.get('footprint_sha256') != spec['footprint'].get('sha256'):
            raise BackendError('Interposer microvia footprint revision differs from the selected interposer')
        if config.get('ground_net') != 'GND' or config.get('protection') != 'none':
            raise BackendError('Interposer microvias require GND and no IPC-4761 protection')
        diameter, drill = config.get('diameter'), config.get('drill')
        settings = board.GetDesignSettings()
        presets = {(round(self.p.ToMM(item.m_Diameter), 9),
                    round(self.p.ToMM(item.m_Drill), 9))
                   for item in board.GetViasDimensionsList()}
        try:
            pair = (round(float(diameter), 9), round(float(drill), 9))
        except (TypeError, ValueError) as error:
            raise BackendError('Selected microvia size is invalid') from error
        if pair not in presets or pair == (0.0, 0.0):
            raise BackendError('Selected microvia size is absent from Board Setup predefined sizes')
        footprint = self._approved(spec['footprint'])
        centres = [(self.p.ToMM(pad.GetPosition().x), self.p.ToMM(pad.GetPosition().y))
                   for pad in footprint.Pads()
                   if pad.GetNumber() and pad.GetAttribute() != self.p.PAD_ATTRIB_NPTH]
        from .core.microvias import check_preset, stack_plan
        eligible, reason = check_preset(
            diameter, drill,
            min_diameter=self.p.ToMM(settings.m_MicroViasMinSize),
            min_drill=self.p.ToMM(settings.m_MicroViasMinDrill),
            min_clearance=self.p.ToMM(settings.m_MinClearance),
            pad_centres=centres,
        )
        if not eligible:
            raise BackendError('Selected microvia size ' + reason)
        pad_numbers = {pad.GetNumber() for pad in footprint.Pads()
                       if pad.GetNumber() and pad.GetAttribute() != self.p.PAD_ATTRIB_NPTH}
        signals = config.get('signals', {})
        if not isinstance(signals, dict) or not set(signals) <= pad_numbers:
            raise BackendError('Interposer microvia signals reference missing physical pads')
        expected_signals = {pin: net for pin, net in spec.get('pads', {}).items()
                            if net != config['ground_net']}
        if set(signals) != set(expected_signals):
            raise BackendError('Interposer microvia signal roles differ from interposer connectivity')
        for pin, signal in signals.items():
            if (not isinstance(signal, dict) or not signal.get('net') or
                    signal.get('net') == 'GND' or signal.get('net') != expected_signals[pin]):
                raise BackendError(f'Interposer microvia signal pin {pin} has an invalid net role')
            try:
                stack_plan(layers, config['side'], signal.get('layer'))
            except ValueError as error:
                raise BackendError(f'Interposer pin {pin}: {error}') from error

    def _new_unprotected_via(self, board):
        """Create a via with every native IPC-4761 property explicitly ``no``.

        KiCad 10.0.0 exposes the PCB_VIA properties but its generated Python
        module omits their typed enum constants.  On that endpoint, clone one
        native round-trip template rather than relying on Board Setup defaults.
        Later 10.0 releases use their exported constants directly.
        """
        names = (
            'TENTING_MODE_NOT_TENTED', 'TENTING_MODE_NOT_TENTED',
            'COVERING_MODE_NOT_COVERED', 'COVERING_MODE_NOT_COVERED',
            'PLUGGING_MODE_NOT_PLUGGED', 'PLUGGING_MODE_NOT_PLUGGED',
            'FILLING_MODE_NOT_FILLED', 'CAPPING_MODE_NOT_CAPPED',
        )
        if all(hasattr(self.p, name) for name in names):
            via = self.p.PCB_VIA(board)
            for method, value in zip((
                'SetFrontTentingMode', 'SetBackTentingMode',
                'SetFrontCoveringMode', 'SetBackCoveringMode',
                'SetFrontPluggingMode', 'SetBackPluggingMode',
                'SetFillingMode', 'SetCappingMode',
            ), (getattr(self.p, name) for name in names)):
                getattr(via, method)(value)
            return via
        if self.p.Version() != '10.0.0':
            raise BackendError('This native runtime cannot set explicit via protection properties')
        cached = getattr(self, '_explicit_no_via_template', None)
        if cached is None:
            with tempfile.TemporaryDirectory(
                    prefix='connector-interposer-via-modes-') as temporary:
                path = Path(temporary) / 'modes.kicad_pcb'
                template_board = self.p.BOARD()
                via = self.p.PCB_VIA(template_board)
                via.SetPosition(self.p.VECTOR2I(1000000, 1000000))
                via.SetWidth(self.p.F_Cu, self.p.FromMM(.3))
                via.SetDrill(self.p.FromMM(.1))
                template_board.Add(via)
                self.p.SaveBoard(str(path), template_board)
                text = path.read_text(encoding='utf-8')
                marker = '\n\t\t(net "")'
                protection = (
                    '\n\t\t(tenting\n\t\t\t(front no)\n\t\t\t(back no)\n\t\t)'
                    '\n\t\t(capping no)'
                    '\n\t\t(covering\n\t\t\t(front no)\n\t\t\t(back no)\n\t\t)'
                    '\n\t\t(plugging\n\t\t\t(front no)\n\t\t\t(back no)\n\t\t)'
                    '\n\t\t(filling no)'
                )
                if text.count(marker) != 1:
                    raise BackendError('KiCad 10.0.0 via compatibility template is ambiguous')
                path.write_text(text.replace(marker, protection + marker), encoding='utf-8')
                template = self.p.LoadBoard(str(path))
                source = next((item for item in template.GetTracks()
                               if isinstance(item, self.p.PCB_VIA)), None)
                if source is None:
                    raise BackendError('KiCad 10.0.0 could not load explicit via protection states')
            cached = (template, source)
            self._explicit_no_via_template = cached
        via = self.p.PCB_VIA(cached[1])
        _set_uuid(via, self.p.KIID(), self.p)
        return via

    def _matching_circle(self, feature, center, circles, claimed, tolerance):
        candidates = [
            (circle, math.dist(circle["center"], center))
            for circle in circles if circle["uuid"] not in claimed
        ]
        matches = [(circle, deviation) for circle, deviation in candidates
                   if deviation <= tolerance]
        if not matches:
            if candidates:
                closest = min(deviation for _circle, deviation in candidates)
                raise BackendError(feature_deviation_message(
                    feature, closest, tolerance,
                ))
            raise BackendError(
                f"{feature}: no drawing circles remain to match; limit is "
                f"{distance_text(tolerance)}"
            )
        if len(matches) != 1:
            deviations = ', '.join(
                distance_text(deviation) for _circle, deviation in matches
            )
            raise BackendError(
                f"{feature}: {len(matches)} drawing circles match at center "
                f"deviations {deviations}; limit is {distance_text(tolerance)}"
            )
        claimed.add(matches[0][0]["uuid"])

    def _validate(self, board, desired):
        warnings = []
        layers = self.copper_layers(board)
        instances = desired.get("instances", {})
        if desired.get("mechanical"):
            mechanical = desired["mechanical"]
            native = self.inspect_graphics(board.GetFileName(), mechanical["source_layer"], mechanical.get("label_offset"))
            if native["errors"]:
                raise BackendError("Unresolved native mechanical associations: " + "; ".join(native["errors"]))
            rf_keys = set(mechanical.get("rf_keys", []))
            physical = {x["key"] for x in native["locations"] if x["key"] in rf_keys}
            installed = {key for key, spec in instances.items()
                         if spec.get("kind", "connector") == "connector" and
                         spec.get("connector_type", "RF") == "RF"}
            if physical != installed:
                raise BackendError("Workbook and complete native mechanical populations differ")
            accepted_locations = [x for x in native["locations"] if x["key"] in rf_keys]
            if sorted(accepted_locations, key=lambda x: x["key"]) != sorted(mechanical["locations"], key=lambda x: x["key"]):
                raise BackendError("Accepted mechanical source geometry or identities changed")
            try:
                tolerance = feature_tolerance(mechanical["feature_tolerance_mm"])
            except (KeyError, TypeError, ValueError) as error:
                raise BackendError("Invalid drawing feature tolerance: " + str(error)) from error
            for location in accepted_locations:
                instance = instances.get(location["key"])
                if instance is None:
                    continue
                deviation = math.dist(instance.get("pose", ())[:2], location["center"])
                if deviation > tolerance:
                    raise BackendError(feature_deviation_message(
                        location["key"], deviation, tolerance,
                    ))
            allowed_location_keys = set(mechanical.get("allowed_location_keys", rf_keys))
            unexpected = sorted({x["key"] for x in native["locations"]} - allowed_location_keys)
            if unexpected:
                raise BackendError(f"Unexpected labelled mechanical locations: {unexpected}")
            circles = {circle["uuid"]: circle for circle in native["circles"]}
            claimed = {location["circle_uuid"] for location in accepted_locations}
            claimed.update(
                anchor.get("circle_uuid") for anchor in
                mechanical.get("interposer_anchors", {}).values()
                if anchor.get("circle_uuid")
            )
            for reference, accepted in mechanical.get("dc_anchors", {}).items():
                circle = circles.get(accepted.get("circle_uuid"))
                if circle is None or circle["center"] != accepted.get("center"):
                    raise BackendError(f"{reference}: accepted DC pin 1 circle changed or is missing")
                spec = instances.get(reference)
                if not spec or spec.get("connector_type") != "DC":
                    raise BackendError(f"{reference}: DC anchor has no matching connector instance")
                holder, fp = self._placed_approved(spec)
                for pad in fp.Pads():
                    if not pad.GetNumber():
                        continue
                    center = [value / 1e6 for value in _xy(pad.GetPosition())]
                    self._matching_circle(
                        f"{reference} pad {pad.GetNumber()}", center,
                        native["circles"], claimed, tolerance,
                    )
            for reference, spec in instances.items():
                if spec.get("kind") != "interposer":
                    continue
                holder, fp = self._placed_approved(spec)
                pads = [pad for pad in fp.Pads()
                        if pad.GetNumber() and
                        pad.GetAttribute() != self.p.PAD_ATTRIB_NPTH]
                expected = [
                    (f"{reference} pad {pad.GetNumber()}",
                     [value / 1e6 for value in _xy(pad.GetPosition())])
                    for pad in pads
                ]
                available = [circle for circle in native["circles"]
                             if circle["uuid"] not in claimed]
                detected = sum(
                    any(math.dist(circle["center"], center) <= MAX_FEATURE_TOLERANCE_MM
                        for circle in available)
                    for _feature, center in expected
                )
                if detected != len(expected):
                    warnings.append({
                        "code": "interposer_pad_circles_missing",
                        "reference": reference,
                        "message": (
                            f"{reference}: complete drawing circles for interposer pads "
                            f"were not detected ({detected} of {len(expected)}); "
                            "continuing without interposer pad-center validation"
                        ),
                    })
                    continue
                for feature, center in expected:
                    self._matching_circle(
                        feature, center, native["circles"], claimed, tolerance,
                    )
        refs = set()
        library_revisions = {}
        artifact_checks = set()
        for key, spec in instances.items():
            if not key or key in refs:
                raise BackendError("Duplicate physical connector identity")
            refs.add(key)
            for name, value in spec.get("symbol_fields", {}).items():
                if not isinstance(name, str) or not name or not isinstance(value, str):
                    raise BackendError("Schematic instance fields require string names and values")
                if name == "Reference" and value != key:
                    raise BackendError("Schematic reference differs from physical instance identity")
            if any(not isinstance(value, str) or not value or "\x00" in value for value in spec.get("pads", {}).values()):
                raise BackendError("Electrical pad assignments require nonempty native net names")
            fp = self._approved(spec["footprint"])
            selection = spec["footprint"]
            library_id = selection.get("library_id", "")
            if library_id in library_revisions and library_revisions[library_id] != selection["sha256"]:
                raise BackendError("Two selected revisions share a library identity; select distinct approved aliases")
            library_revisions[library_id] = selection["sha256"]
            pads = {x.GetNumber() for x in fp.Pads()
                    if x.GetNumber() and x.GetAttribute() != self.p.PAD_ATTRIB_NPTH}
            if set(spec.get("pads", {})) - pads:
                raise BackendError("Assignment references a missing approved footprint pad: " + key)
            if spec.get("kind", "connector") == "interposer" and set(spec.get("pads", {})) != pads:
                raise BackendError("Interposer assignments must cover the complete pad universe")
            pose = spec.get("pose", [])
            if len(pose) != 3 or any(not math.isfinite(v) for v in pose):
                raise BackendError("Invalid instance pose")
            if spec.get("mirror"):
                raise BackendError("Native footprint mirroring requires an explicit supported side; use side=B")
            if spec.get("side", "F") not in ("F", "B"):
                raise BackendError("Footprint side must be F or B")
            if spec.get("rf"):
                if not spec.get("netclass") or not spec.get("signal_layer"):
                    raise BackendError("RF instance requires explicit workbook netclass and mapped signal layer")
                rf = spec["rf"]
                if set(rf.get("nets", {})) != {"signal", "ground"}:
                    raise BackendError("RF signal and ground net bindings are required")
                artifact = copy.deepcopy(rf["artifact"])
                # Layer bindings resolve declared saved layer names only.
                mapping = rf.get("layers", {})
                check_key = (sha256(artifact), spec["footprint"]["sha256"], spec.get("netclass"))
                if check_key not in artifact_checks:
                    validate_artifact(artifact, part_sha256=spec["footprint"]["sha256"], netclass=spec.get("netclass"))
                    artifact_checks.add(check_key)
                signal_layer = artifact["payload"].get("layer_roles", {}).get("signal")
                if spec.get("signal_layer") and mapping.get(signal_layer, signal_layer) != spec["signal_layer"]:
                    raise BackendError("RF signal layer differs from the workbook's mapped routing layer")
                for item in artifact["payload"]["objects"]:
                    used = item.get("layers", [item.get("layer")])
                    if any(mapping.get(x, x) not in layers for x in used):
                        raise BackendError("RF artifact references a layer outside the copper stack")
                    if item["type"] == "via":
                        span = [mapping.get(x, x) for x in item["layers"]]
                        a, b = [layers.index(x) for x in span]
                        if a >= b or (item["via_type"] == "microvia" and b - a != 1):
                            raise BackendError("Invalid native microvia layer span")
                        if item["via_type"] == "through" and (a != 0 or b != len(layers)-1):
                            raise BackendError("Through via must span the full native stack")
                compatibility = artifact.get("compatibility", {})
                signal_pads = [q for q in fp.Pads() if spec.get("pads", {}).get(q.GetNumber()) == rf["nets"]["signal"]]
                if not signal_pads:
                    raise BackendError("RF signal binding does not match any approved footprint pad")
                if compatibility["mount"] == "PTH" and any(q.GetAttribute() != self.p.PAD_ATTRIB_PTH or min(q.GetDrillSize().x, q.GetDrillSize().y) <= 0 for q in signal_pads):
                    raise BackendError("PTH RF artifact requires an existing plated signal barrel")
                if compatibility["mount"] == "SMD" and any(q.GetAttribute() != self.p.PAD_ATTRIB_SMD for q in signal_pads):
                    raise BackendError("SMD RF artifact requires approved surface-mount signal pads")
                expected_side = "back" if spec.get("side", "F") == "B" else "front"
                if compatibility.get("side", expected_side) != expected_side:
                    raise BackendError("RF artifact was approved for a different footprint side")
                mount = artifact["payload"].get("layer_roles", {}).get("mount")
                if mount and mapping.get(mount, mount) != (layers[-1] if expected_side == "back" else layers[0]):
                    raise BackendError("RF mount layer differs from selected footprint side")
                if compatibility.get("stackup") and [mapping.get(x, x) for x in compatibility["stackup"]] != layers:
                    raise BackendError("RF artifact requires a different stackup")
                if set(rf.get("nets", {})) != {"signal", "ground"}:
                    raise BackendError("RF signal and ground net bindings are required")
        self._validate_microvias(board, desired, instances)
        outline = desired.get("outline")
        if outline and outline.get("enabled"):
            from .core.outline import Segment, validate_outline
            sources = board_items(board)
            for fp in board.GetFootprints():
                sources.update({uuid_of(x): x for x in fp.GraphicalItems()})
            selected = [s for contour in [outline["outer"], *outline.get("cutouts", [])] for s in contour]
            for segment in selected:
                uid = segment.get("source_uuid")
                source = sources.get(uid)
                if not uid or not isinstance(source, self.p.PCB_SHAPE):
                    raise BackendError("Managed outline requires an existing selected native source UUID")
                expected_owner = "footprint" if source.GetParentFootprint() else "board"
                if segment.get("owner", "board") != expected_owner:
                    raise BackendError("Outline source ownership differs from reviewed selection")
                for field, native in (("start", source.GetStart()), ("end", source.GetEnd())):
                    if [round(v*1e6) for v in segment[field]] != _xy(native):
                        raise BackendError("Outline source geometry changed")
                if source.GetShape() == self.p.SHAPE_T_ARC:
                    if segment.get("mid") is None or [round(v*1e6) for v in segment["mid"]] != _xy(source.GetArcMid()):
                        raise BackendError("Outline source arc changed")
                elif source.GetShape() != self.p.SHAPE_T_SEGMENT or segment.get("mid") is not None:
                    raise BackendError("Unsupported managed outline source shape")
            # Designer-owned board edges are never adopted or silently doubled.
            managed = set(desired.get("_owned_outline_uuids", []))
            for source in board.GetDrawings():
                if source.GetLayer() == self.p.Edge_Cuts and uuid_of(source) not in managed:
                    raise BackendError("Existing designer-owned Edge.Cuts requires explicit outline conflict review")
            def contour(items):
                return [Segment(tuple(x["start"]), tuple(x["end"]),
                                tuple(x["mid"]) if x.get("mid") is not None else None,
                                x.get("source_uuid", ""), x.get("owner", "")) for x in items]
            try:
                validate_outline(contour(outline["outer"]), [contour(c) for c in outline.get("cutouts", [])])
            except ValueError as exc:
                raise BackendError("Invalid managed outline: " + str(exc)) from exc
        for name, content in desired.get("project_files", {}).items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.suffix in (".kicad_pcb", ".lck") or any(
                    part in (".git", ".connector-interposer-recovery") for part in path.parts) or name.endswith((".connector-interposer-instances.json", ".connector-interposer-report.json")):
                raise BackendError("Generated project file path is unsafe or reserved")
            if not isinstance(content, str):
                raise BackendError("Generated project files must contain UTF-8 text")
            if path.suffix == ".kicad_pro":
                json.loads(content)
        return warnings

    def preview(self, board_path, desired, registry=None):
        path = Path(board_path).resolve()
        if registry is None:
            ledger = path.with_suffix(".connector-interposer-instances.json")
            registry = json.loads(ledger.read_text()) if ledger.is_file() else None
        registry = copy.deepcopy(registry or {"schema": "connector-interposer.instances/1.0", "instances": {}, "expected": {}})
        desired = json.loads(canonical(desired))
        for spec in desired.get("instances", {}).values():
            selection = spec["footprint"]
            pinned = path.parent/".connector-interposer-pinned"/"footprints"/(selection["sha256"] + ".kicad_mod")
            if pinned.exists() and file_hash(pinned) != selection["sha256"]:
                raise BackendError("Pinned approved footprint snapshot integrity failure")
            if file_hash(pinned) == selection["sha256"]:
                selection["path"] = str(pinned)
            elif not Path(selection["path"]).is_absolute():
                selection["path"] = str(path.parent/selection["path"])
            if spec.get("rf"):
                artifact = spec["rf"]["artifact"]
                rf_pin = path.parent/".connector-interposer-pinned"/"rf"/(artifact["hashes"]["artifact"] + ".json")
                if rf_pin.exists() and json.loads(rf_pin.read_text()) != artifact:
                    raise BackendError("Pinned RF artifact snapshot integrity failure")
        board = self._load(path)
        self._resolve_anchor_poses(desired)
        self._normalize_outline(board, desired)
        if (desired.get("outline") or {}).get("enabled"):
            desired["_owned_outline_uuids"] = registry.get("outline_objects", [])
        warnings = self._validate(board, desired)
        actual = board_state(board, self.p)
        conflicts = [{"kind": "ownership", "uuid": uid, "message": "Owned object was edited, removed or reparented"}
                     for uid, state in registry.get("expected", {}).items() if actual.get(uid) != state]
        serialized = _serialized(path, registry.get("expected", {}))
        for uid, checksum in registry.get("serialized", {}).items():
            if serialized.get(uid) != checksum and not any(c.get("uuid") == uid for c in conflicts):
                conflicts.append({"kind": "ownership", "uuid": uid, "message": "Serialized owned content changed"})
        for name, checksum in registry.get("generated_files", {}).items():
            if file_hash(path.parent/name) != checksum:
                conflicts.append({"kind": "generated-file-edit", "key": name,
                                  "message": "Tool-owned generated project file was edited or removed"})
        changes = []
        old = registry.get("instances", {})
        new = desired.get("instances", {})
        for key in sorted(set(old) | set(new)):
            if key not in old:
                kind = "add"
                if any(isinstance(x, self.p.FOOTPRINT) and x.GetReference() == key for x in board_items(board).values()):
                    conflicts.append({"kind": "unowned-reference", "key": key,
                                      "message": "Existing footprint reference has no ownership registry"})
            elif key not in new:
                kind = "remove"
            elif _spec_identity(old[key]["spec"]) != _spec_identity(new[key]):
                kind = "update"
                prior = old[key]["spec"].get("rf", {}).get("artifact")
                after = new[key].get("rf", {}).get("artifact")
                if prior and after and (prior["artifact_id"], prior["revision"]) == (after["artifact_id"], after["revision"]) and prior != after:
                    conflicts.append({"kind": "revision-collision", "key": key,
                                      "message": "Same RF artifact revision has different content"})
            else:
                continue
            previous_pads = old.get(key, {}).get("spec", {}).get("pads", {})
            next_pads = new.get(key, {}).get("pads", {})
            before_spec = old.get(key, {}).get("spec", {})
            after_spec = new.get(key, {})
            detail = {"key": key, "kind": kind, "gang": after_spec.get("gang", before_spec.get("gang", key)),
                      "assignments": [{"pad": pad, "before": previous_pads.get(pad), "after": next_pads.get(pad)}
                                      for pad in sorted(set(previous_pads) | set(next_pads)) if previous_pads.get(pad) != next_pads.get(pad)]}
            for field in ("pose", "side", "netclass", "signal_layer"):
                if before_spec.get(field) != after_spec.get(field):
                    detail[field] = {"before": before_spec.get(field), "after": after_spec.get(field)}
            if before_spec.get("rf") != after_spec.get("rf"):
                def summary(value):
                    if not value:
                        return None
                    artifact = value["artifact"]
                    return {"artifact_id": artifact["artifact_id"], "revision": artifact["revision"],
                            "sha256": artifact["hashes"]["artifact"], "clock": value.get("clock"),
                            "nets": value.get("nets"), "layers": value.get("layers")}
                detail["rf"] = {"before": summary(before_spec.get("rf")), "after": summary(after_spec.get("rf"))}
            changes.append(detail)
            affected = {v for k, v in previous_pads.items() if next_pads.get(k) != v}
            # Mechanical/RF changes also affect attached hand-routing.
            if key in old and (key not in new or old[key]["spec"].get("pose") != new[key].get("pose") or
                               old[key]["spec"].get("side", "F") != new[key].get("side", "F") or
                               _spec_identity({"footprint": old[key]["spec"].get("footprint", {})}) != _spec_identity({"footprint": new[key].get("footprint", {})}) or
                               old[key]["spec"].get("rf") != new[key].get("rf")):
                affected.update(previous_pads.values())
            owned = set(registry.get("expected", {}))
            routed = [uid for uid, x in board_items(board).items() if uid not in owned and
                      isinstance(x, (self.p.PCB_TRACK, self.p.PCB_SHAPE, self.p.ZONE)) and
                      hasattr(x, "GetNetname") and x.GetNetname() in affected]
            if routed:
                conflicts.append({"kind": "routed-net-change", "key": key, "uuids": routed,
                                  "message": "Affected designer copper requires an explicit external routing decision"})
        if registry.get("outline_spec") != desired.get("outline"):
            if registry.get("outline_spec") or desired.get("outline"):
                changes.append({"key": "board-outline", "kind": "outline"})
        before_microvias = registry.get('microvia_spec')
        after_microvias = desired.get('interposer_microvias')
        if before_microvias != after_microvias:
            changes.append({
                'key': 'interposer-microvias',
                'kind': ('add' if after_microvias and not before_microvias else
                         'remove' if before_microvias and not after_microvias else 'update'),
            })
        for name, content in sorted(desired.get("project_files", {}).items()):
            if file_hash(path.parent / name) != hashlib.sha256(content.encode("utf-8")).hexdigest():
                changes.append({"key": name, "kind": "project-file"})
                if (path.parent/name).exists() and name not in registry.get("generated_files", {}):
                    if not self._safe_project_merge(path.parent/name, content, desired):
                        conflicts.append({"kind": "unowned-file", "key": name,
                                          "message": "Generated output collides with a designer-owned project file"})
        for name in sorted(set(registry.get("generated_files", {})) - set(desired.get("project_files", {}))):
            changes.append({"key": name, "kind": "remove-project-file"})
        if changes and not conflicts and (desired.get("outline") or {}).get("enabled"):
            # Validate the actual placed approved footprint cutouts against the
            # chosen contours before presenting an applicable preview.
            self._materialize(board, desired, registry)
        payload = {"source": str(path), "source_sha256": file_hash(path), "desired": desired,
                   "source_project": _project_hashes(path.parent),
                   "registry": registry, "changes": changes, "conflicts": conflicts,
                   "warnings": warnings}
        payload["token"] = sha256(payload)
        payload["status"] = "BLOCKED" if conflicts else ("PREVIEW" if changes else "NOOP")
        return payload

    def _safe_project_merge(self, path, content, desired):
        """Only explicitly reviewed settings/library-table merges may touch unowned files."""
        if path.name not in desired.get("project_file_merges", []):
            return False
        if path.suffix == ".kicad_pro":
            before, after = json.loads(path.read_text()), json.loads(content)
            before.pop("net_settings", None)
            after.pop("net_settings", None)
            return before == after
        if path.name in ("sym-lib-table", "fp-lib-table"):
            from .core.sexpr import loads
            before, after = loads(path.read_text()), loads(content)
            return before[0] == after[0] and all(entry in after for entry in before[1:])
        return False

    def _point(self, point, pose, rf=False):
        angle = math.radians(pose[2])
        x, y = point
        a = x * math.cos(angle) - y * math.sin(angle)
        b = x * math.sin(angle) + y * math.cos(angle)
        return self.p.VECTOR2I(round((pose[0] + a) * 1e6),
                              round((pose[1] - b if rf else pose[1] + b) * 1e6))

    def _net(self, board, name):
        for net in board.GetNetInfo().NetsByNetcode().values():
            if net.GetNetname() == name:
                return net
        net = self.p.NETINFO_ITEM(board, name)
        board.Add(net)
        return net

    def _place_footprint(self, fp, spec):
        """Apply a resolved native origin pose and side to a footprint object."""
        p = self.p
        pose = spec["pose"]
        want_bottom = spec.get("side", "F") == "B"
        if fp.IsFlipped() != want_bottom:
            fp.Flip(fp.GetPosition(), False)
        fp.SetPosition(p.VECTOR2I(round(pose[0] * 1e6), round(pose[1] * 1e6)))
        fp.SetOrientationDegrees(pose[2])

    def _placed_approved(self, spec):
        """Attach a loaded footprint to a temporary board before native flips."""
        holder = self.p.BOARD()
        fp = self._approved(spec["footprint"])
        holder.Add(fp)
        self._place_footprint(fp, spec)
        return holder, fp

    def _resolve_anchor_poses(self, desired):
        """Resolve pad-centre anchors with this exact KiCad runtime."""
        p = self.p
        for key, spec in desired.get("instances", {}).items():
            anchor = spec.get("anchor")
            if not anchor:
                continue
            at = anchor.get("at", [])
            pad_number = str(anchor.get("pad", ""))
            if (len(at) != 2 or any(not math.isfinite(value) for value in at) or
                    not pad_number):
                raise BackendError(f"{key}: invalid footprint pad anchor")
            angle = float(spec.get("pose", [0, 0, 0])[2])
            spec["pose"] = [0, 0, angle]
            holder, fp = self._placed_approved(spec)
            pads = [pad for pad in fp.Pads() if pad.GetNumber() == pad_number]
            if len(pads) != 1:
                raise BackendError(f"{key}: anchor pad {pad_number} must occur exactly once")
            offset = _xy(pads[0].GetPosition())
            spec["pose"] = [at[0] - offset[0] / 1e6,
                            at[1] - offset[1] / 1e6, angle]
            self._place_footprint(fp, spec)
            if _xy(pads[0].GetPosition()) != [round(at[0] * 1e6), round(at[1] * 1e6)]:
                raise BackendError(f"{key}: KiCad could not place anchor pad {pad_number} at the selected circle")

    def _group(self, board, name, old=None, parent=None):
        group = board_items(board).get(old) if old else None
        if group is None:
            group = self.p.PCB_GROUP(board)
            group.SetName(name)
            board.Add(group)
            if parent:
                parent.AddItem(group)
        return group

    def _remove(self, board, item):
        group = item.GetParentGroup()
        if group:
            group.RemoveItem(item)
        board.Delete(item)

    def _primitive(self, board, item, pose, nets, layer_map):
        p = self.p
        layer = lambda name: self._layer(board, layer_map.get(name, name))
        pt = lambda value: self._point(value, pose, rf=True)
        kind = item["type"]
        if kind == "via":
            obj = p.PCB_VIA(board)
            obj.SetPosition(pt(item["at"]))
            obj.SetWidth(p.F_Cu, p.FromMM(item["diameter"]))
            obj.SetDrill(p.FromMM(item["drill"]))
            if not hasattr(p, "VIATYPE_THROUGH"):
                if p.Version() != "10.0.0":
                    raise BackendError("This native runtime does not expose supported via-type constants")
                # KiCad 10.0.0 omitted VIATYPE SWIG constants. Values are from
                # the exact 10.0.0 pcb_track_types.h; verify native round trips.
                span = [layer(x) for x in item["layers"]]
                via_type = {"through": 4, "microvia": 1, "blind_buried":
                            3 if p.F_Cu not in span and p.B_Cu not in span else 2}[item["via_type"]]
            elif item["via_type"] == "blind_buried":
                span = [layer(x) for x in item["layers"]]
                via_type = (getattr(p, "VIATYPE_BURIED", None) if p.F_Cu not in span and p.B_Cu not in span else
                            getattr(p, "VIATYPE_BLIND", None))
                if via_type is None:
                    via_type = p.VIATYPE_BLIND_BURIED
            else:
                via_type = p.VIATYPE_THROUGH if item["via_type"] == "through" else p.VIATYPE_MICROVIA
            obj.SetViaType(via_type)
            obj.SetLayerPair(*[layer(x) for x in item["layers"]])
            if not obj.HasValidLayerPair(board.GetCopperLayerCount()):
                raise BackendError("Native runtime rejects the requested via layer span")
            obj.SetNet(self._net(board, nets[item["net"]]))
        elif kind == "copper":
            obj = p.PCB_SHAPE(board)
            obj.SetLayer(layer(item["layer"]))
            obj.SetNet(self._net(board, nets[item["net"]]))
            shape = item.get("shape", "circle")
            if shape == "circle":
                obj.SetShape(p.SHAPE_T_CIRCLE)
                obj.SetStart(pt(item["at"]))
                obj.SetEnd(pt([item["at"][0] + item["diameter"] / 2, item["at"][1]]))
                obj.SetFilled(True)
                obj.SetWidth(0)
            elif shape == "arc":
                obj.SetShape(p.SHAPE_T_ARC)
                obj.SetArcGeometry(pt(item["start"]), pt(item["mid"]), pt(item["end"]))
                obj.SetWidth(p.FromMM(item["width"]))
            else:
                obj.SetShape(p.SHAPE_T_POLY)
                poly = p.SHAPE_POLY_SET()
                poly.NewOutline()
                for point in item["points"]:
                    poly.Append(pt(point))
                obj.SetPolyShape(poly)
                obj.SetFilled(True)
                obj.SetWidth(0)
        else:
            obj = p.ZONE(board)
            ls = p.LSET()
            for name in item["layers"]:
                ls.AddLayer(layer(name))
            obj.SetLayerSet(ls)
            obj.SetIsRuleArea(True)
            flags = item["prohibit"]
            obj.SetDoNotAllowTracks("tracks" in flags)
            obj.SetDoNotAllowVias("vias" in flags)
            obj.SetDoNotAllowPads("pads" in flags)
            obj.SetDoNotAllowFootprints("footprints" in flags)
            (obj.SetDoNotAllowCopperPour if hasattr(obj, "SetDoNotAllowCopperPour") else
             obj.SetDoNotAllowZoneFills)("copperpour" in flags)
            poly = obj.Outline()
            poly.NewOutline()
            if item.get("shape") == "circle":
                radius = item["diameter"]/2
                count = max(16, math.ceil(math.pi/math.acos(radius/(radius+.001))))
                outer = radius/math.cos(math.pi/count)
                points = [[item["at"][0]+outer*math.cos(2*math.pi*i/count),
                           item["at"][1]+outer*math.sin(2*math.pi*i/count)] for i in range(count)]
            else:
                points = item["points"]
            for point in points:
                poly.Append(pt(point))
        return obj

    def _materialize(self, board, desired, registry):
        p = self.p
        old = registry.get("instances", {})
        result = {"schema": "connector-interposer.instances/1.0", "instances": {}, "groups": {},
                  "expected": {}, "tombstones": copy.deepcopy(registry.get("tombstones", {}))}
        owned = set(registry.get("expected", {}))
        for key in sorted(set(old) - set(desired.get("instances", {}))):
            entry = old[key]
            for uid in entry.get("objects", {}).values():
                item = board_items(board).get(uid)
                if item:
                    self._remove(board, item)
            fp = board_items(board).get(entry["base"])
            if fp:
                self._remove(board, fp)
            group = board_items(board).get(entry["group"])
            if group and not list(group.GetItems()):
                self._remove(board, group)
            result["tombstones"][key] = {"previous_base_uuid": entry["base"]}
        for key, spec in sorted(desired.get("instances", {}).items()):
            entry = old.get(key, {})
            gang = spec.get("gang", key)
            gang_old = result["groups"].get(gang) or registry.get("groups", {}).get(gang)
            parent = self._group(board, "Workflow:" + gang, gang_old)
            result["groups"][gang] = uuid_of(parent)
            group = self._group(board, key, entry.get("group"), parent)
            fp = board_items(board).get(entry.get("base"))
            previous = entry.get("spec", {})
            if fp is None or _spec_identity({"footprint": previous.get("footprint", {})}) != _spec_identity({"footprint": spec["footprint"]}):
                replacement = self._approved(spec["footprint"])
                if fp:
                    _set_uuid(replacement, uuid_of(fp), p)
                    pad_ids = {}
                    for q in fp.Pads():
                        pad_ids.setdefault(q.GetNumber(), []).append(uuid_of(q))
                    for pad in replacement.Pads():
                        if pad_ids.get(pad.GetNumber()):
                            _set_uuid(pad, pad_ids[pad.GetNumber()].pop(0), p)
                    self._remove(board, fp)
                fp = replacement
                board.Add(fp)
                group.AddItem(fp)
            fp.SetReference(key)
            if spec["footprint"].get("library_id"):
                fp.SetFPIDAsString(spec["footprint"]["library_id"])
            pose = spec["pose"]
            # All approved footprint geometry is cloned, never synthesized.
            self._place_footprint(fp, spec)
            # Schematic commercial/value fields belong to this project instance;
            # the selected librarian footprint remains a read-only input.
            for name, text in spec.get("symbol_fields", {}).items():
                if name == "Reference":
                    fp.SetReference(text)
                elif name == "Value":
                    fp.SetValue(text)
                elif name == "Footprint":
                    if text != spec["footprint"].get("library_id"):
                        raise BackendError("Schematic footprint field differs from approved selection")
                else:
                    present = fp.HasField(name)
                    fp.SetField(name, text)
                    if not present:
                        field = fp.GetFieldByName(name) if hasattr(fp, "GetFieldByName") else fp.GetField(name)
                        field.SetVisible(False)
                        field.SetPosition(fp.GetPosition())
            if spec.get("symbol_path"):
                path = p.KIID_PATH()
                for value in spec["symbol_path"].strip("/").split("/"):
                    path.push_back(p.KIID(value))
                fp.SetPath(path)
            if spec.get("kind") == "mounting-hole":
                fp.SetAttributes(fp.GetAttributes() | p.FP_BOARD_ONLY | p.FP_EXCLUDE_FROM_BOM | p.FP_EXCLUDE_FROM_POS_FILES)
            for pad in fp.Pads():
                if pad.GetNumber():
                    net = spec.get("pads", {}).get(pad.GetNumber())
                    if net:
                        pad.SetNet(self._net(board, net))
                    elif spec.get("connector_type") == "DC":
                        pad.SetNetCode(0)
                    else:
                        pad.SetNet(self._net(board, "GND"))
            objects = {}
            rf = spec.get("rf")
            old_rf = previous.get("rf")
            if rf:
                rf_pose = [pose[0], pose[1], rf.get("clock", pose[2])]
                old_items = {x["id"]: x for x in old_rf["artifact"]["payload"]["objects"]} if old_rf else {}
                for item in rf["artifact"]["payload"]["objects"]:
                    local = item["id"]
                    uid = entry.get("objects", {}).get(local)
                    obj = board_items(board).get(uid)
                    unchanged = old_rf and old_items.get(local) == item and previous.get("pose") == pose and {
                        k: v for k, v in old_rf.items() if k != "artifact"} == {k: v for k, v in rf.items() if k != "artifact"}
                    if not unchanged or obj is None:
                        replacement = self._primitive(board, item, rf_pose, rf["nets"], rf.get("layers", {}))
                        if obj:
                            _set_uuid(replacement, uuid_of(obj), p)
                            self._remove(board, obj)
                        obj = replacement
                        board.Add(obj)
                        group.AddItem(obj)
                    objects[local] = uuid_of(obj)
            for local, uid in entry.get("objects", {}).items():
                if local not in objects:
                    obj = board_items(board).get(uid)
                    if obj:
                        self._remove(board, obj)
            result["instances"][key] = {"base": uuid_of(fp), "group": uuid_of(group),
                                        "objects": objects, "spec": copy.deepcopy(spec)}
        self._materialize_microvias(board, desired, registry, result)
        # Empty obsolete parents are removed, while foreign members survive.
        for gang, uid in registry.get("groups", {}).items():
            if gang not in result["groups"]:
                group = board_items(board).get(uid)
                if group and not list(group.GetItems()):
                    self._remove(board, group)
        self._outline(board, desired, registry, result)
        state = board_state(board, p)
        for entry in result["instances"].values():
            for uid in [entry["base"], entry["group"], *entry["objects"].values()]:
                result["expected"][uid] = state[uid]
        for uid in [*result.get('microvia_objects', {}).values(),
                    *([result['microvia_group']] if result.get('microvia_group') else [])]:
            result['expected'][uid] = state[uid]
        for uid in [*result["groups"].values(), *result.get("outline_objects", []), *([result["outline_group"]] if result.get("outline_group") else [])]:
            result["expected"][uid] = state[uid]
        return result

    def _materialize_microvias(self, board, desired, old, result):
        """Reconcile the dedicated locked interposer microvia object set."""
        config = desired.get('interposer_microvias')
        old_objects = old.get('microvia_objects', {})
        old_group = old.get('microvia_group')
        result['microvia_spec'] = copy.deepcopy(config)
        if not config:
            for uid in old_objects.values():
                obj = board_items(board).get(uid)
                if obj:
                    self._remove(board, obj)
            group = board_items(board).get(old_group)
            if group and not list(group.GetItems()):
                self._remove(board, group)
            return
        reference = config['reference']
        footprints = [fp for fp in board.GetFootprints() if fp.GetReference() == reference]
        if len(footprints) != 1:
            raise BackendError('Interposer microvias require exactly one placed interposer footprint')
        footprint = footprints[0]
        group = self._group(
            board, 'Workflow:Interposer Microvias:' + reference, old_group,
        )
        group.SetLocked(True)
        result['microvia_group'] = uuid_of(group)
        layers = config['layers']
        signals = config['signals']
        from .core.microvias import stack_plan
        pads = sorted(
            (pad for pad in footprint.Pads()
             if pad.GetNumber() and pad.GetAttribute() != self.p.PAD_ATTRIB_NPTH),
            key=lambda pad: (pad.GetNumber(), pad.GetPosition().x,
                             pad.GetPosition().y, uuid_of(pad)),
        )
        occurrences = {}
        wanted = {}
        for pad in pads:
            pin = pad.GetNumber()
            occurrence = occurrences.get(pin, 0)
            occurrences[pin] = occurrence + 1
            signal = signals.get(pin)
            spans = stack_plan(
                layers, config['side'], signal.get('layer') if signal else None,
            )
            for role, top_name, bottom_name in spans:
                local = f'{pin}:{occurrence}:{role}:{top_name}>{bottom_name}'
                wanted[local] = (pad.GetPosition(),
                                 signal['net'] if role == 'signal' else config['ground_net'],
                                 top_name, bottom_name)
        objects = {}
        replace_all = old.get('microvia_spec') != config
        current_items = board_items(board)
        for local, (position, net_name, top_name, bottom_name) in wanted.items():
            uid = old_objects.get(local)
            obj = current_items.get(uid)
            if replace_all or not isinstance(obj, self.p.PCB_VIA):
                replacement = self._new_unprotected_via(board)
                replacement.SetPosition(position)
                replacement.SetWidth(self.p.F_Cu, self.p.FromMM(config['diameter']))
                replacement.SetDrill(self.p.FromMM(config['drill']))
                via_type = (getattr(self.p, 'VIATYPE_MICROVIA', 1)
                            if self.p.Version() == '10.0.0'
                            else self.p.VIATYPE_MICROVIA)
                replacement.SetViaType(via_type)
                replacement.SetLayerPair(self._layer(board, top_name),
                                         self._layer(board, bottom_name))
                if not replacement.HasValidLayerPair(board.GetCopperLayerCount()):
                    raise BackendError('Native runtime rejects a generated adjacent microvia span')
                replacement.SetNet(self._net(board, net_name))
                replacement.SetLocked(True)
                if obj:
                    _set_uuid(replacement, uuid_of(obj), self.p)
                    self._remove(board, obj)
                obj = replacement
                board.Add(obj)
                group.AddItem(obj)
            objects[local] = uuid_of(obj)
        for local, uid in old_objects.items():
            if local not in objects:
                obj = board_items(board).get(uid)
                if obj:
                    self._remove(board, obj)
        result['microvia_objects'] = objects

    def _outline(self, board, desired, old, result):
        outline = desired.get("outline")
        result["outline_spec"] = copy.deepcopy(outline)
        if outline == old.get("outline_spec"):
            for name in ("outline_objects", "outline_group"):
                if name in old:
                    result[name] = old[name]
            return
        for uid in old.get("outline_objects", []):
            obj = board_items(board).get(uid)
            if obj:
                self._remove(board, obj)
        if not outline or not outline.get("enabled"):
            group = board_items(board).get(old.get("outline_group"))
            if group and not list(group.GetItems()):
                self._remove(board, group)
            return
        group = self._group(board, "Workflow:Board Outline", old.get("outline_group"))
        group.SetLocked(True)
        objects = []
        contours = [outline["outer"], *outline.get("cutouts", [])]
        footprint_edges = []
        for footprint in board.GetFootprints():
            footprint_edges.extend(x for x in footprint.GraphicalItems() if isinstance(x, self.p.PCB_SHAPE) and x.GetLayer() == self.p.Edge_Cuts)
        def matches(segment, edge):
            a = tuple(round(v*1e6) for v in segment["start"])
            b = tuple(round(v*1e6) for v in segment["end"])
            if {a, b} != {tuple(_xy(edge.GetStart())), tuple(_xy(edge.GetEnd()))}:
                return False
            if segment.get("mid") is None:
                return edge.GetShape() == self.p.SHAPE_T_SEGMENT
            return edge.GetShape() == self.p.SHAPE_T_ARC and [round(v*1e6) for v in segment["mid"]] == _xy(edge.GetArcMid())
        # Include every existing/new approved footprint cutout in contour intent
        # validation, even when the user did not select a duplicate DXF contour.
        from .core.outline import Segment, validate_outline
        remaining = list(footprint_edges)
        extra_cutouts = []
        selected_cutouts = outline.get("cutouts", [])
        while remaining:
            component = [remaining.pop()]
            changed = True
            while changed:
                changed = False
                ends = {tuple(_xy(v)) for e in component for v in (e.GetStart(), e.GetEnd())}
                for edge in list(remaining):
                    if tuple(_xy(edge.GetStart())) in ends or tuple(_xy(edge.GetEnd())) in ends:
                        component.append(edge)
                        remaining.remove(edge)
                        changed = True
            selected_matches = [any(matches(s, edge) for cutout in selected_cutouts for s in cutout) for edge in component]
            if any(selected_matches) and not all(selected_matches):
                raise BackendError("Selected cutout only partly matches an approved footprint cutout")
            if all(selected_matches):
                continue
            contour = []
            for edge in component:
                if edge.GetShape() not in (self.p.SHAPE_T_SEGMENT, self.p.SHAPE_T_ARC):
                    raise BackendError("Approved footprint cutout contains unsupported native geometry")
                contour.append(Segment(tuple(v/1e6 for v in _xy(edge.GetStart())),
                                       tuple(v/1e6 for v in _xy(edge.GetEnd())),
                                       tuple(v/1e6 for v in _xy(edge.GetArcMid())) if edge.GetShape() == self.p.SHAPE_T_ARC else None,
                                       uuid_of(edge), "footprint"))
            extra_cutouts.append(contour)
        try:
            convert = lambda contour: [Segment(tuple(s["start"]), tuple(s["end"]), tuple(s["mid"]) if s.get("mid") else None,
                                                s.get("source_uuid", ""), s.get("owner", "")) for s in contour]
            validate_outline(convert(outline["outer"]), [convert(c) for c in selected_cutouts] + extra_cutouts)
        except ValueError as exc:
            raise BackendError("Native footprint cutout conflicts with managed outline: " + str(exc)) from exc
        for contour in contours:
            for segment in contour:
                # Existing footprint-owned cutout edges stay with their footprint.
                matching = [edge for edge in footprint_edges if matches(segment, edge)]
                if len(matching) > 1:
                    raise BackendError("Duplicate interposer-owned cutout geometry")
                if segment.get("owner") == "footprint" or matching:
                    continue
                obj = self.p.PCB_SHAPE(board)
                pt = lambda v: self.p.VECTOR2I(round(v[0]*1e6), round(v[1]*1e6))
                if segment.get("mid") is not None:
                    obj.SetShape(self.p.SHAPE_T_ARC)
                    obj.SetArcGeometry(pt(segment["start"]), pt(segment["mid"]), pt(segment["end"]))
                else:
                    obj.SetShape(self.p.SHAPE_T_SEGMENT)
                    obj.SetStart(pt(segment["start"]))
                    obj.SetEnd(pt(segment["end"]))
                obj.SetLayer(self.p.Edge_Cuts)
                obj.SetWidth(50000)
                obj.SetLocked(True)
                board.Add(obj)
                group.AddItem(obj)
                objects.append(uuid_of(obj))
        result["outline_objects"] = objects
        result["outline_group"] = uuid_of(group)

    def apply(self, preview, output_dir, *, closed_project=True):
        """Materialize a full project copy; verified no-ops do not write anything."""
        original = {k: v for k, v in preview.items() if k not in ("token", "status")}
        if sha256(original) != preview.get("token"):
            raise BackendError("Preview token is invalid")
        fresh = self.preview(preview["source"], preview["desired"], preview["registry"])
        if fresh["token"] != preview["token"]:
            raise BackendError("Stale preview; compare again before applying")
        if fresh["conflicts"]:
            raise BackendError("Unresolved reconciliation conflicts")
        if fresh["status"] == "NOOP":
            return {"status": "NOOP", "board_path": preview["source"], "registry": preview["registry"]}
        source = Path(preview["source"])
        target = Path(output_dir).resolve()
        if target == source.parent or source.parent in target.parents or target in source.parent.parents:
            raise BackendError("Output must be a separate disposable project-copy directory")
        if target.exists() and any(target.iterdir()):
            raise BackendError("Output project directory must be empty")
        if not closed_project:
            raise BackendError("Output project copy must remain closed during apply")
        board = self._load(source)
        before = board_state(board, self.p)
        registry = self._materialize(board, preview["desired"], preview["registry"])
        expected = registry["expected"]
        foreign = {k: v for k, v in before.items() if k not in preview["registry"].get("expected", {})}
        with tempfile.TemporaryDirectory(prefix="connector-interposer-native-") as temporary:
            candidate = Path(temporary) / source.name
            # Native clearance/fill/class evaluation must use the reviewed
            # project settings, not a default temporary project.
            for settings in source.parent.iterdir():
                if settings.is_file() and settings.suffix in (".kicad_pro", ".kicad_dru"):
                    (Path(temporary)/settings.name).write_bytes(settings.read_bytes())
            for name, content in preview["desired"].get("project_files", {}).items():
                if Path(name).suffix in (".kicad_pro", ".kicad_dru"):
                    destination = Path(temporary)/name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(content, encoding="utf-8")
            if not self.p.SaveBoard(str(candidate), board, True):
                raise BackendError("Native board staging failed")
            # Reload so native checks use the staged project settings beside the board.
            checked = self._load(candidate)
            checked.SynchronizeNetsAndNetClasses(False)
            checked.BuildConnectivity()
            if any(not z.GetIsRuleArea() for z in checked.Zones()):
                self.p.ZONE_FILLER(checked).Fill(checked.Zones())
            if not self.p.SaveBoard(str(candidate), checked, True):
                raise BackendError("Native checked board save failed")
            checked = self._load(candidate)
            checked.SynchronizeNetsAndNetClasses(False)
            actual = board_state(checked, self.p)
            resolved_classes = {}
            for spec in preview["desired"].get("instances", {}).values():
                if spec.get("netclass") and spec.get("kind", "connector") == "connector":
                    ground = spec.get("rf", {}).get("nets", {}).get("ground", "GND")
                    for name in set(spec.get("pads", {}).values()) - {ground}:
                        net = self._net(checked, name)
                        native_class = net.GetNetClassSlow()
                        if not native_class.ContainsNetclassWithName(spec["netclass"]):
                            raise BackendError("Saved native netclass does not match workbook assignment: " + name + " (" + net.GetNetClassName() + ")")
                        resolved_classes[name] = spec["netclass"]
            mismatched = [key for key, value in expected.items()
                          if actual.get(key) != value]
            if mismatched:
                key = mismatched[0]
                before_item, after_item = expected[key], actual.get(key)
                fields = sorted(set(before_item or {}) | set(after_item or {}))
                changed = [field for field in fields
                           if (before_item or {}).get(field) != (after_item or {}).get(field)]
                raise BackendError(
                    "Saved native object state/net/group verification failed for "
                    f"{key}; changed fields: {', '.join(changed)}"
                )
            if any(actual.get(k) != v for k, v in foreign.items()):
                raise BackendError("Native reconciliation changed designer-owned content")
            registry["serialized"] = _serialized(candidate, expected)
            registry["generated_files"] = {name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                                            for name, content in preview["desired"].get("project_files", {}).items()}
            if _project_hashes(source.parent) != preview["source_project"]:
                raise BackendError("Source project changed while staging; nothing applied")
            # Read source files only; do not copy live lock files or prior recovery.
            shutil.copytree(source.parent, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.lck", "~*.lck", ".connector-interposer-recovery", ".git", "__pycache__"))
            if _project_hashes(source.parent) != preview["source_project"] or _project_hashes(target) != preview["source_project"]:
                raise BackendError("Source project changed while copying; discard the incomplete output copy")
            require_closed(target, True)
            report = {"schema": "connector-interposer.reconciliation/1.0", "status": "APPLIED", "token": preview["token"],
                      "changes": preview["changes"], "conflicts": [],
                      "validation": {"native_reload": "passed", "owned_geometry_nets_groups": "passed",
                                     "foreign_objects_preserved": "passed", "netclasses": resolved_classes,
                                     "manufacturing_qualification": "not performed"}}
            ledger = source.stem + ".connector-interposer-instances.json"
            report_name = source.stem + ".connector-interposer-report.json"
            files = {source.name: candidate.read_bytes(), ledger: canonical(registry), report_name: canonical(report)}
            files.update({name: content.encode("utf-8") for name, content in preview["desired"].get("project_files", {}).items()})
            files.update({name: None for name in set(preview["registry"].get("generated_files", {})) - set(preview["desired"].get("project_files", {}))})
            # Exact immutable selections travel with the project copy.
            for spec in preview["desired"].get("instances", {}).values():
                selection = spec["footprint"]
                selected_bytes = Path(selection["path"]).read_bytes()
                if hashlib.sha256(selected_bytes).hexdigest() != selection["sha256"]:
                    raise BackendError("Approved library revision changed during staging")
                files[".connector-interposer-pinned/footprints/" + selection["sha256"] + ".kicad_mod"] = selected_bytes
                library_id = selection.get("library_id", "")
                if ":" in library_id:
                    nickname, name = library_id.split(":", 1)
                    if any(c in nickname + name for c in ("/", "\\")) or nickname in (".", "..") or name in (".", ".."):
                        raise BackendError("Unsafe library identity for pinned project table")
                    files[".connector-interposer-pinned/" + nickname + ".pretty/" + name + ".kicad_mod"] = selected_bytes
                if spec.get("rf"):
                    artifact = spec["rf"]["artifact"]
                    files[".connector-interposer-pinned/rf/" + artifact["hashes"]["artifact"] + ".json"] = canonical(artifact)
            hashes = {name: file_hash(target / name) for name in files}
            transaction = apply_files(target, files, hashes, closed_project=True)
        return {"status": "APPLIED", "board_path": str(target / source.name),
                "registry_path": str(target / ledger), "report_path": str(target / report_name),
                "registry": registry, "journal": transaction["journal"]}
