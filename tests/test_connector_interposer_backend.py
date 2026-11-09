"""Native placement, preservation, stale previews and idempotence on KiCad 10."""
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
import pcbnew as p
from connector_interposer_workflow.backend import (
    BackendError, NativeBackend, _set_uuid, board_state, uuid_of,
)
from connector_interposer_workflow.recovery import recover
from test_connector_interposer_artifacts import artifact_fixture, rehash


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/"source"
        self.source.mkdir()
        self.backend = NativeBackend(p)
        # Isolated test fixture only: production always loads librarian files.
        library = self.root/"approved.pretty"
        library.mkdir()
        fp = p.FOOTPRINT(None)
        fp.SetValue("ApprovedGPPO")
        for number, x in (("1", 0), ("2", 2)):
            pad = p.PAD(fp)
            pad.SetNumber(number)
            pad.SetAttribute(p.PAD_ATTRIB_SMD)
            pad.SetShape(p.PAD_SHAPE_CIRCLE)
            pad.SetSize(p.VECTOR2I(600000, 600000))
            pad.SetPosition(p.VECTOR2I(x*1000000, 0))
            ls = p.LSET()
            ls.AddLayer(p.F_Cu)
            pad.SetLayerSet(ls)
            fp.Add(pad)
        fp.SetFPIDAsString("Approved:GPPO")
        p.PCB_IO_KICAD_SEXPR().FootprintSave(str(library), fp)
        part = library/"GPPO.kicad_mod"
        self.selection = {"path": str(part), "sha256": hashlib.sha256(part.read_bytes()).hexdigest(),
                          "approved": True, "library_id": "Approved:GPPO"}
        board = p.BOARD()
        board.SetCopperLayerCount(4)
        line = p.PCB_SHAPE(board)
        line.SetShape(p.SHAPE_T_SEGMENT)
        line.SetLayer(p.Dwgs_User)
        line.SetStart(p.VECTOR2I(0, 0))
        line.SetEnd(p.VECTOR2I(3000000, 0))
        line.SetWidth(50000)
        board.Add(line)
        self.foreign = uuid_of(line)
        self.board_path = self.source/"demo.kicad_pcb"
        p.SaveBoard(str(self.board_path), board, True)
        (self.source/"demo.kicad_pro").write_text(json.dumps({"meta": {"filename": "demo.kicad_pro", "version": 3},
                  "board": {"design_settings": {
                      "rules": {"min_clearance": 0.1, "min_microvia_diameter": 0.2,
                                "min_microvia_drill": 0.1},
                      "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                         {"diameter": 0.3, "drill": 0.1},
                                         {"diameter": 0.35, "drill": 0.15}]},
                  },
                  "net_settings": {"meta": {"version": 4}, "classes": [{"name": "RF", "priority": 0}],
                  "netclass_patterns": [{"netclass": "RF", "pattern": "CLOCK"}, {"netclass": "RF", "pattern": "DATA"}]}}))
        (self.source/"ordinary.kicad_sch").write_text("designer sheet")
        self.project_id = str(uuid.uuid4())
        self.desired = {"project_id": self.project_id,
                        "instances": {"J101-1": {"gang": "J101", "kind": "connector", "footprint": self.selection,
                         "pose": [20, 20, 0], "pads": {"1": "CLOCK", "2": "GND"}, "netclass": "RF", "signal_layer": "In2.Cu",
                         "rf": {"artifact": artifact_fixture(self.selection["sha256"]), "nets": {"signal": "CLOCK", "ground": "GND"}, "clock": 90}}}}

    def apply_first(self):
        preview = self.backend.preview(self.board_path, self.desired)
        return self.backend.apply(preview, self.root/"first")

    def microvia_desired(self, side='F', signal_layer='In1.Cu', diameter=.3, drill=.1):
        desired = copy.deepcopy(self.desired)
        interposer = copy.deepcopy(desired['instances']['J101-1'])
        interposer.update(
            kind='interposer', gang='E1', pose=[40, 20, 0], side=side,
            pads={'1': 'CLOCK', '2': 'GND'},
        )
        interposer.pop('rf', None)
        interposer.pop('netclass', None)
        interposer.pop('signal_layer', None)
        desired['instances']['E1'] = interposer
        desired['interposer_microvias'] = {
            'enabled': True, 'reference': 'E1',
            'diameter': diameter, 'drill': drill,
            'layers': ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'],
            'side': side, 'pose': [40, 20, 0],
            'footprint_sha256': self.selection['sha256'],
            'signals': {'1': {'net': 'CLOCK', 'layer': signal_layer}},
            'ground_net': 'GND', 'protection': 'none',
        }
        return desired

    def test_native_copy_ownership_noop_and_incremental_assignment(self):
        source_bytes = self.board_path.read_bytes()
        first = self.apply_first()
        self.assertEqual(source_bytes, self.board_path.read_bytes())
        output = Path(first["board_path"])
        self.assertEqual("designer sheet", (output.parent/"ordinary.kicad_sch").read_text())
        self.assertTrue(list((output.parent/".connector-interposer-pinned/footprints").glob("*.kicad_mod")))
        loaded = p.LoadBoard(str(output))
        state = board_state(loaded, p)
        base = first["registry"]["instances"]["J101-1"]["base"]
        self.assertIn(self.foreign, state)
        self.assertEqual({"CLOCK", "GND"}, {x["net"] for x in state[base]["pads"].values()})
        stamp, raw = output.stat().st_mtime_ns, output.read_bytes()
        noop = self.backend.preview(output, self.desired)
        self.assertEqual("NOOP", noop["status"])
        self.assertEqual("NOOP", self.backend.apply(noop, self.root/"unused")["status"])
        self.assertFalse((self.root/"unused").exists())
        self.assertEqual((stamp, raw), (output.stat().st_mtime_ns, output.read_bytes()))
        changed = copy.deepcopy(self.desired)
        changed["instances"]["J101-1"]["pads"]["1"] = "DATA"
        changed["instances"]["J101-1"]["rf"]["nets"]["signal"] = "DATA"
        preview = self.backend.preview(output, changed)
        second = self.backend.apply(preview, self.root/"second")
        self.assertEqual(base, second["registry"]["instances"]["J101-1"]["base"])
        self.assertEqual(first["registry"]["instances"]["J101-1"]["objects"], second["registry"]["instances"]["J101-1"]["objects"])

    def test_interposer_microvia_generation_noop_and_full_reconciliation(self):
        desired = self.microvia_desired()
        first = self.backend.apply(
            self.backend.preview(self.board_path, desired), self.root / 'microvia-first',
        )
        objects = first['registry']['microvia_objects']
        self.assertEqual(len(objects), 5)
        board = p.LoadBoard(first['board_path'])
        state = board_state(board, p)
        group = state[first['registry']['microvia_group']]
        self.assertTrue(group['locked'])
        vias = [state[uid] for uid in objects.values()]
        self.assertEqual({via['via_type'] for via in vias},
                         {getattr(p, 'VIATYPE_MICROVIA', 1)})
        self.assertEqual({p.ToMM(via['width']) for via in vias}, {.3})
        self.assertEqual({p.ToMM(via['drill']) for via in vias}, {.1})
        self.assertEqual({via['net'] for via in vias}, {'CLOCK', 'GND'})
        self.assertTrue(all(via['locked'] for via in vias))
        by_span = {(board.GetLayerName(via['span'][0]), board.GetLayerName(via['span'][1]), via['net'])
                   for via in vias}
        self.assertIn(('F.Cu', 'In1.Cu', 'CLOCK'), by_span)
        self.assertIn(('In2.Cu', 'B.Cu', 'GND'), by_span)
        from connector_interposer_workflow.core.sexpr import loads, child
        tree = loads(Path(first['board_path']).read_text(encoding='utf-8'))
        first_uid = next(iter(objects.values()))
        node = next(item for item in tree[1:]
                    if isinstance(item, list) and child(item, 'uuid') == ['uuid', first_uid])
        self.assertEqual(child(node, 'tenting'),
                         ['tenting', ['front', 'no'], ['back', 'no']])
        self.assertEqual(child(node, 'covering'),
                         ['covering', ['front', 'no'], ['back', 'no']])
        self.assertEqual(child(node, 'plugging'),
                         ['plugging', ['front', 'no'], ['back', 'no']])
        self.assertEqual(child(node, 'filling'), ['filling', 'no'])
        self.assertEqual(child(node, 'capping'), ['capping', 'no'])
        noop = self.backend.preview(first['board_path'], desired)
        self.assertEqual(noop['status'], 'NOOP')

        changed = self.microvia_desired(diameter=.35, drill=.15)
        second = self.backend.apply(
            self.backend.preview(first['board_path'], changed), self.root / 'microvia-size',
        )
        self.assertEqual(objects, second['registry']['microvia_objects'])
        resized = board_state(p.LoadBoard(second['board_path']), p)
        self.assertEqual({p.ToMM(resized[uid]['width']) for uid in objects.values()}, {.35})

        moved = self.microvia_desired(diameter=.35, drill=.15)
        moved['instances']['E1']['pose'] = [41, 21, 0]
        moved['interposer_microvias']['pose'] = [41, 21, 0]
        third = self.backend.apply(
            self.backend.preview(second['board_path'], moved), self.root / 'microvia-moved',
        )
        self.assertEqual(objects, third['registry']['microvia_objects'])
        moved_state = board_state(p.LoadBoard(third['board_path']), p)
        self.assertTrue(all(moved_state[uid]['xy'][0] >= 41000000 for uid in objects.values()))

        removed = copy.deepcopy(moved)
        del removed['interposer_microvias']
        fourth = self.backend.apply(
            self.backend.preview(third['board_path'], removed), self.root / 'microvia-removed',
        )
        final = board_state(p.LoadBoard(fourth['board_path']), p)
        self.assertFalse(set(objects.values()) & set(final))

    def test_edited_owned_microvia_blocks_reconciliation(self):
        desired = self.microvia_desired(side='B', signal_layer='In2.Cu')
        first = self.backend.apply(
            self.backend.preview(self.board_path, desired), self.root / 'microvia-edit',
        )
        uid = next(iter(first['registry']['microvia_objects'].values()))
        board = p.LoadBoard(first['board_path'])
        via = next(item for item in board.GetTracks() if uuid_of(item) == uid)
        via.SetDrill(p.FromMM(.12))
        p.SaveBoard(first['board_path'], board)
        preview = self.backend.preview(first['board_path'], desired)
        self.assertEqual(preview['status'], 'BLOCKED')
        self.assertTrue(any(conflict.get('uuid') == uid for conflict in preview['conflicts']))

    def test_interposer_microvia_signal_roles_must_match_connectivity(self):
        desired = self.microvia_desired()
        desired['interposer_microvias']['signals'] = {}
        with self.assertRaisesRegex(BackendError, 'signal roles differ'):
            self.backend.preview(self.board_path, desired)

    def test_dc_pin_one_anchor_uses_native_transform_and_leaves_nc_open(self):
        library = self.root / 'dc.pretty'
        library.mkdir()
        fp = p.FOOTPRINT(None)
        fp.SetValue('ApprovedDC')
        for number, x in (('1', 5), ('2', 7)):
            pad = p.PAD(fp)
            pad.SetNumber(number)
            pad.SetAttribute(p.PAD_ATTRIB_PTH)
            pad.SetShape(p.PAD_SHAPE_CIRCLE)
            pad.SetSize(p.VECTOR2I(600000, 600000))
            pad.SetDrillSize(p.VECTOR2I(300000, 300000))
            pad.SetPosition(p.VECTOR2I(x * 1000000, 7000000))
            layers = p.LSET(p.LSET.AllCuMask())
            pad.SetLayerSet(layers)
            fp.Add(pad)
        fp.SetFPIDAsString('Approved:DC')
        p.PCB_IO_KICAD_SEXPR().FootprintSave(str(library), fp)
        part = library / 'DC.kicad_mod'
        selection = {
            'path': str(part), 'sha256': hashlib.sha256(part.read_bytes()).hexdigest(),
            'approved': True, 'library_id': 'Approved:DC',
        }
        board = p.LoadBoard(str(self.board_path))
        circle_ids = {}
        for label, x in (('left', 18), ('pin1', 20), ('right', 22.005)):
            circle = p.PCB_SHAPE(board)
            circle.SetShape(p.SHAPE_T_CIRCLE)
            circle.SetStart(p.VECTOR2I(round(x * 1000000), 20000000))
            circle.SetEnd(p.VECTOR2I(round(x * 1000000 + 500000), 20000000))
            circle.SetLayer(p.Dwgs_User)
            board.Add(circle)
            circle_ids[label] = uuid_of(circle)
        p.SaveBoard(str(self.board_path), board)
        del circle, board, pad, fp
        for side in ('F', 'B'):
            revision = self.backend.inspect_graphics(
                self.board_path, 'Dwgs.User',
            )['revision']
            desired = {'project_id': self.project_id, 'instances': {'J1': {
                'kind': 'connector', 'connector_type': 'DC', 'gang': 'J1',
                'footprint': selection, 'pose': [0, 0, 0], 'side': side,
                'anchor': {'pad': '1', 'at': [20, 20],
                           'circle_uuid': circle_ids['pin1']},
                'pads': {'1': 'GND'},
            }}, 'mechanical': {
                'source_layer': 'Dwgs.User', 'rf_matches': [], 'rf_keys': [],
                'rf_anchors': {}, 'interposer_anchors': {},
                'dc_anchors': {'J1': {'circle_uuid': circle_ids['pin1'],
                                      'center': [20, 20]}},
                'feature_tolerance_mm': 0.00762,
                'drawing_revision': revision,
            }}
            too_narrow = copy.deepcopy(desired)
            too_narrow['mechanical']['feature_tolerance_mm'] = 0.004
            with self.assertRaisesRegex(
                BackendError,
                r'J1 pad 2: center deviation.*0\.005 mm.*limit.*0\.004 mm',
            ):
                self.backend.preview(self.board_path, too_narrow)
            preview = self.backend.preview(self.board_path, desired)
            result = self.backend.apply(preview, self.root / ('dc-' + side))
            placed_board = p.LoadBoard(result['board_path'])
            placed = next(iter(placed_board.GetFootprints()))
            pads = {pad.GetNumber(): pad for pad in placed.Pads()}
            self.assertEqual((pads['1'].GetPosition().x, pads['1'].GetPosition().y),
                             (20000000, 20000000), side)
            self.assertEqual(pads['1'].GetNetname(), 'GND')
            self.assertEqual(pads['2'].GetNetname(), '')
            del pads, placed, placed_board
            if side == 'F':
                changed = copy.deepcopy(desired)
                changed['instances']['J1']['pads']['1'] = 'POWER'
                changed_preview = self.backend.preview(result['board_path'], changed)
                changed_result = self.backend.apply(changed_preview, self.root / 'dc-F-changed')
                self.assertEqual(
                    result['registry']['instances']['J1']['base'],
                    changed_result['registry']['instances']['J1']['base'],
                )

    def test_interposer_pad_circles_auto_detect_warn_or_validate_with_shared_tolerance(self):
        desired = {
            'project_id': self.project_id,
            'instances': {'E1': {
                'kind': 'interposer', 'footprint': self.selection,
                'pose': [20, 20, 0], 'side': 'F',
                'pads': {'1': 'CLOCK', '2': 'GND'},
            }},
            'mechanical': {
                'source_layer': 'Dwgs.User', 'rf_matches': [], 'rf_keys': [],
                'rf_anchors': {}, 'dc_anchors': {}, 'interposer_anchors': {},
                'feature_tolerance_mm': 0.00762,
                'drawing_revision': self.backend.inspect_graphics(
                    self.board_path, 'Dwgs.User',
                )['revision'],
            },
        }
        preview = self.backend.preview(self.board_path, desired)
        self.assertEqual(
            preview['warnings'][0]['code'], 'interposer_pad_circles_missing',
        )
        self.assertIn('0 of 2', preview['warnings'][0]['message'])
        result = self.backend.apply(preview, self.root / 'interposer-no-circles')
        self.assertTrue(Path(result['board_path']).is_file())

        board = p.LoadBoard(str(self.board_path))
        circle = p.PCB_SHAPE(board)
        circle.SetShape(p.SHAPE_T_CIRCLE)
        circle.SetStart(p.VECTOR2I(20000000, 20000000))
        circle.SetEnd(p.VECTOR2I(20150000, 20000000))
        circle.SetLayer(p.Dwgs_User)
        board.Add(circle)
        p.SaveBoard(str(self.board_path), board)
        desired['mechanical']['drawing_revision'] = self.backend.inspect_graphics(
            self.board_path, 'Dwgs.User',
        )['revision']
        preview = self.backend.preview(self.board_path, desired)
        self.assertIn('1 of 2', preview['warnings'][0]['message'])

        second = p.PCB_SHAPE(board)
        second.SetShape(p.SHAPE_T_CIRCLE)
        second.SetStart(p.VECTOR2I(22005000, 20000000))
        second.SetEnd(p.VECTOR2I(22155000, 20000000))
        second.SetLayer(p.Dwgs_User)
        board.Add(second)
        p.SaveBoard(str(self.board_path), board)
        desired['mechanical']['drawing_revision'] = self.backend.inspect_graphics(
            self.board_path, 'Dwgs.User',
        )['revision']
        self.assertEqual(self.backend.preview(self.board_path, desired)['warnings'], [])

        second.SetStart(p.VECTOR2I(22010000, 20000000))
        second.SetEnd(p.VECTOR2I(22160000, 20000000))
        p.SaveBoard(str(self.board_path), board)
        desired['mechanical']['drawing_revision'] = self.backend.inspect_graphics(
            self.board_path, 'Dwgs.User',
        )['revision']
        with self.assertRaisesRegex(
            BackendError,
            r'E1 pad 2: center deviation.*0\.01 mm.*limit.*0\.00762 mm',
        ):
            self.backend.preview(self.board_path, desired)

    def test_edited_owned_and_routed_net_change_block(self):
        first = self.apply_first()
        output = Path(first["board_path"])
        board = p.LoadBoard(str(output))
        fp = next(iter(board.GetFootprints()))
        fp.Move(p.VECTOR2I(100000, 0))
        p.SaveBoard(str(output), board)
        preview = self.backend.preview(output, self.desired)
        self.assertEqual("BLOCKED", preview["status"])
        with self.assertRaises(BackendError):
            self.backend.apply(preview, self.root/"forbidden")

    def test_routed_assignment_side_and_library_change_block(self):
        first = self.apply_first()
        output = Path(first["board_path"])
        board = p.LoadBoard(str(output))
        track = p.PCB_TRACK(board)
        track.SetStart(p.VECTOR2I(20000000, 20000000))
        track.SetEnd(p.VECTOR2I(25000000, 20000000))
        track.SetLayer(p.F_Cu)
        track.SetWidth(200000)
        track.SetNet(next(n for n in board.GetNetInfo().NetsByNetcode().values() if n.GetNetname() == "CLOCK"))
        board.Add(track)
        p.SaveBoard(str(output), board)
        for mode in ("net", "side", "library"):
            desired = copy.deepcopy(self.desired)
            instance = desired["instances"]["J101-1"]
            if mode == "net":
                instance["pads"]["1"] = "DATA"
                instance["rf"]["nets"]["signal"] = "DATA"
            elif mode == "side":
                instance["side"] = "B"
                del instance["rf"]
            else:
                instance["footprint"]["library_id"] = "Approved:OtherAlias"
            preview = self.backend.preview(output, desired)
            self.assertTrue(any(c["kind"] == "routed-net-change" for c in preview["conflicts"]), mode)

    def test_exact_endpoint_isolated_route_net_swap_is_retagged(self):
        desired = copy.deepcopy(self.desired)
        second = copy.deepcopy(desired['instances']['J101-1'])
        second.update(gang='E1', kind='interposer', pose=[30, 20, 0])
        second.pop('rf', None)
        second.pop('netclass', None)
        second.pop('signal_layer', None)
        desired['instances']['E1'] = second
        first = self.backend.apply(
            self.backend.preview(self.board_path, desired), self.root / 'routed-first',
        )
        output = Path(first['board_path'])
        board = p.LoadBoard(str(output))
        track = p.PCB_TRACK(board)
        track.SetStart(p.VECTOR2I(20000000, 20000000))
        track.SetEnd(p.VECTOR2I(30000000, 20000000))
        track.SetLayer(p.F_Cu)
        track.SetWidth(200000)
        track.SetNet(next(net for net in board.GetNetInfo().NetsByNetcode().values()
                          if net.GetNetname() == 'CLOCK'))
        board.Add(track)
        route_uuid = uuid_of(track)
        p.SaveBoard(str(output), board)
        changed = copy.deepcopy(desired)
        for spec in changed['instances'].values():
            spec['pads']['1'] = 'DATA'
            if spec.get('rf'):
                spec['rf']['nets']['signal'] = 'DATA'
        preview = self.backend.preview(output, changed)
        self.assertEqual(preview['conflicts'], [])
        self.assertEqual(
            preview['desired']['route_retags'][route_uuid],
            {'before': 'CLOCK', 'after': 'DATA'},
        )
        result = self.backend.apply(preview, self.root / 'routed-retagged')
        saved = p.LoadBoard(result['board_path'])
        route = next(item for item in saved.GetTracks()
                     if uuid_of(item) == route_uuid)
        self.assertEqual(route.GetNetname(), 'DATA')

    def test_net_bearing_designer_shape_blocks_route_retag(self):
        desired = copy.deepcopy(self.desired)
        second = copy.deepcopy(desired['instances']['J101-1'])
        second.update(gang='E1', kind='interposer', pose=[30, 20, 0])
        second.pop('rf', None)
        second.pop('netclass', None)
        second.pop('signal_layer', None)
        desired['instances']['E1'] = second
        first = self.backend.apply(
            self.backend.preview(self.board_path, desired), self.root / 'shape-first',
        )
        output = Path(first['board_path'])
        board = p.LoadBoard(str(output))
        shape = p.PCB_SHAPE(board)
        shape.SetShape(p.SHAPE_T_SEGMENT)
        shape.SetStart(p.VECTOR2I(20000000, 20000000))
        shape.SetEnd(p.VECTOR2I(30000000, 20000000))
        shape.SetLayer(p.F_Cu)
        shape.SetWidth(200000)
        shape.SetNet(next(net for net in board.GetNetInfo().NetsByNetcode().values()
                          if net.GetNetname() == 'CLOCK'))
        board.Add(shape)
        p.SaveBoard(str(output), board)
        changed = copy.deepcopy(desired)
        for spec in changed['instances'].values():
            spec['pads']['1'] = 'DATA'
            if spec.get('rf'):
                spec['rf']['nets']['signal'] = 'DATA'
        preview = self.backend.preview(output, changed)
        self.assertTrue(any(
            conflict['kind'] == 'routed-net-change' and
            uuid_of(shape) in conflict['uuids']
            for conflict in preview['conflicts']
        ))

    def test_crossing_designer_track_blocks_exact_endpoint_retag(self):
        desired = copy.deepcopy(self.desired)
        second = copy.deepcopy(desired['instances']['J101-1'])
        second.update(gang='E1', kind='interposer', pose=[30, 20, 0])
        second.pop('rf', None)
        second.pop('netclass', None)
        second.pop('signal_layer', None)
        desired['instances']['E1'] = second
        first = self.backend.apply(
            self.backend.preview(self.board_path, desired), self.root / 'crossing-first',
        )
        output = Path(first['board_path'])
        board = p.LoadBoard(str(output))
        net = next(item for item in board.GetNetInfo().NetsByNetcode().values()
                   if item.GetNetname() == 'CLOCK')
        for start, end in (
            ((20000000, 20000000), (30000000, 20000000)),
            ((25000000, 19000000), (25000000, 21000000)),
        ):
            track = p.PCB_TRACK(board)
            track.SetStart(p.VECTOR2I(*start))
            track.SetEnd(p.VECTOR2I(*end))
            track.SetLayer(p.F_Cu)
            track.SetWidth(200000)
            track.SetNet(net)
            board.Add(track)
        p.SaveBoard(str(output), board)
        changed = copy.deepcopy(desired)
        for spec in changed['instances'].values():
            spec['pads']['1'] = 'DATA'
            if spec.get('rf'):
                spec['rf']['nets']['signal'] = 'DATA'
        preview = self.backend.preview(output, changed)
        self.assertEqual(preview['desired']['route_retags'], {})
        self.assertTrue(any(conflict['kind'] == 'routed-net-change'
                            for conflict in preview['conflicts']))

    def test_live_prepare_publish_and_closed_recovery(self):
        before = self.board_path.read_bytes()
        preview = self.backend.preview(self.board_path, self.desired)
        prepared = self.backend.prepare_live(preview)
        self.assertEqual(prepared['status'], 'PREPARED')
        self.assertEqual(self.board_path.read_bytes(), before)
        journal = json.loads(Path(prepared['journal']).read_text())
        self.assertEqual(journal['mode'], 'live')
        self.assertEqual(journal['status'], 'PREPARED')
        live = p.LoadBoard(str(self.board_path))
        result = self.backend.publish_live(live, preview, prepared)
        self.assertEqual(result['board_path'], str(self.board_path.resolve()))
        self.assertEqual(len(list(live.GetFootprints())), 1)
        self.assertEqual(len(list(p.LoadBoard(str(self.board_path)).GetFootprints())), 1)
        del live
        recover(result['journal'], closed_project=True)
        self.assertEqual(self.board_path.read_bytes(), before)

    def test_live_publish_accepts_foreign_saved_uuid_difference(self):
        board = p.LoadBoard(str(self.board_path))
        circle = p.PCB_SHAPE(board)
        circle.SetShape(p.SHAPE_T_CIRCLE)
        circle.SetStart(p.VECTOR2I(40000000, 40000000))
        circle.SetEnd(p.VECTOR2I(41000000, 40000000))
        circle.SetLayer(p.Dwgs_User)
        board.Add(circle)
        p.SaveBoard(str(self.board_path), board)
        preview = self.backend.preview(self.board_path, self.desired)
        prepared = self.backend.prepare_live(preview)
        live = p.LoadBoard(str(self.board_path))
        live_circle = next(item for item in live.GetDrawings()
                           if isinstance(item, p.PCB_SHAPE))
        saved_uuid = uuid_of(live_circle)
        _set_uuid(live_circle, str(uuid.uuid4()), p)
        self.assertNotEqual(uuid_of(live_circle), saved_uuid)
        result = self.backend.publish_live(live, preview, prepared)
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(uuid_of(live_circle) in board_state(live, p), True)

    def test_live_prepare_requires_closed_schematic_editor(self):
        preview = self.backend.preview(self.board_path, self.desired)
        lock = self.source / '~demo.kicad_sch.lck'
        lock.write_text('open')
        with self.assertRaisesRegex(ValueError, 'Schematic Editor'):
            self.backend.prepare_live(preview)

    def test_one_parent_per_gang_removal_reintroduction_and_foreign_member(self):
        desired = copy.deepcopy(self.desired)
        for i in range(2, 7):
            desired["instances"]["J101-"+str(i)] = copy.deepcopy(desired["instances"]["J101-1"])
            desired["instances"]["J101-"+str(i)]["pose"][0] += i*4
        first = self.backend.apply(self.backend.preview(self.board_path, desired), self.root/"six")
        board = p.LoadBoard(first["board_path"])
        self.assertEqual(7, len(list(board.Groups())))
        self.assertEqual(1, sum(g.GetName() == "Workflow:J101" for g in board.Groups()))
        former = first["registry"]["instances"]["J101-1"]["base"]
        removed = copy.deepcopy(desired)
        del removed["instances"]["J101-1"]
        second = self.backend.apply(self.backend.preview(first["board_path"], removed), self.root/"five")
        loaded = p.LoadBoard(second["board_path"])
        self.assertEqual(5, len(list(loaded.GetFootprints())))
        self.assertNotIn(former, board_state(loaded, p))
        third = self.backend.apply(self.backend.preview(second["board_path"], desired), self.root/"six-again")
        self.assertEqual(former, third["registry"]["instances"]["J101-1"]["base"])
        for key in removed["instances"]:
            self.assertEqual(first["registry"]["instances"][key]["base"], third["registry"]["instances"][key]["base"])
        # A foreign item in an owned group must survive the next own-object edit.
        board = p.LoadBoard(third["board_path"])
        line = next(x for x in board.GetDrawings() if uuid_of(x) == self.foreign)
        foreign = uuid_of(line)
        group_id = third["registry"]["instances"]["J101-1"]["group"]
        next(g for g in board.Groups() if uuid_of(g) == group_id).AddItem(line)
        p.SaveBoard(third["board_path"], board)
        changed = copy.deepcopy(desired)
        changed["instances"]["J101-1"]["rf"]["clock"] = 180
        fourth = self.backend.apply(self.backend.preview(third["board_path"], changed), self.root/"foreign-kept")
        self.assertIn(foreign, board_state(p.LoadBoard(fourth["board_path"]), p))

    def test_offline_project_relocation_noop(self):
        first = self.apply_first()
        moved = self.root/"moved"
        shutil.copytree(Path(first["board_path"]).parent, moved)
        Path(self.selection["path"]).unlink()
        board = moved/"demo.kicad_pcb"
        preview = self.backend.preview(board, self.desired)
        self.assertEqual("NOOP", preview["status"])
        self.assertEqual("NOOP", self.backend.apply(preview, self.root/"unused")["status"])

    def test_native_output_recovery_restores_exact_pre_board_and_new_file_absence(self):
        before = self.board_path.read_bytes()
        result = self.apply_first()
        recover(result["journal"], closed_project=True)
        output = Path(result["board_path"])
        self.assertEqual(before, output.read_bytes())
        self.assertFalse(Path(result["registry_path"]).exists())
        self.assertFalse(Path(result["report_path"]).exists())
        restored = p.LoadBoard(str(output))
        self.assertEqual(0, len(list(restored.GetFootprints())))
        self.assertIn(self.foreign, board_state(restored, p))

    def test_generated_file_ownership(self):
        desired = copy.deepcopy(self.desired)
        desired["project_files"] = {"ordinary.kicad_sch": "replacement"}
        desired['managed_files'] = ['ordinary.kicad_sch']
        self.assertEqual("BLOCKED", self.backend.preview(self.board_path, desired)["status"])
        desired["project_files"] = {"generated.kicad_sch": "generated sheet"}
        desired['managed_files'] = ['generated.kicad_sch']
        first = self.backend.apply(self.backend.preview(self.board_path, desired), self.root/"generated")
        (Path(first["board_path"]).parent/"generated.kicad_sch").write_text("manual edit")
        self.assertTrue(any(c["kind"] == "generated-file-edit" for c in self.backend.preview(first["board_path"], desired)["conflicts"]))

    def test_same_net_name_native_netclass_change_uses_reviewed_project_settings(self):
        first = self.apply_first()
        output = Path(first["board_path"])
        desired = copy.deepcopy(self.desired)
        spec = desired["instances"]["J101-1"]
        spec["netclass"] = "DATA"
        spec["rf"]["artifact"]["revision"] = 2
        spec["rf"]["artifact"]["compatibility"]["netclasses"] = ["RF", "DATA"]
        rehash(spec["rf"]["artifact"])
        settings = json.loads((output.parent/"demo.kicad_pro").read_text())
        settings["net_settings"]["classes"].append({"name": "DATA", "priority": 0})
        settings["net_settings"]["netclass_patterns"] = [{"netclass": "DATA", "pattern": "CLOCK"}]
        desired["project_files"] = {"demo.kicad_pro": json.dumps(settings)}
        desired["project_file_merges"] = ["demo.kicad_pro"]
        result = self.backend.apply(self.backend.preview(output, desired), self.root/"new-class")
        board = p.LoadBoard(result["board_path"])
        net = next(n for n in board.GetNetInfo().NetsByNetcode().values() if n.GetNetname() == "CLOCK")
        self.assertTrue(net.GetNetClassSlow().ContainsNetclassWithName("DATA"))
        self.assertEqual(first["registry"]["instances"]["J101-1"]["base"], result["registry"]["instances"]["J101-1"]["base"])

    def test_project_schematic_value_and_mpn_fields_roundtrip_without_library_mutation(self):
        library = Path(self.selection["path"])
        original, stamp = library.read_bytes(), library.stat().st_mtime_ns
        desired = copy.deepcopy(self.desired)
        desired["instances"]["J101-1"]["symbol_fields"] = {"Value": "LibrarianGPPO", "MPN": "B012-L13-01"}
        interposer = copy.deepcopy(desired["instances"]["J101-1"])
        interposer.update(kind="interposer", gang="Interposer", pose=[40,20,0], symbol_fields={"Value": "Interposer", "MPN": ""})
        del interposer["rf"]
        desired["instances"]["E1"] = interposer
        result = self.backend.apply(self.backend.preview(self.board_path, desired), self.root/"fields")
        board = p.LoadBoard(result["board_path"])
        footprints = {fp.GetReference(): fp for fp in board.GetFootprints()}
        self.assertEqual("LibrarianGPPO", footprints["J101-1"].GetValue())
        self.assertEqual("B012-L13-01", footprints["J101-1"].GetFieldText("MPN"))
        self.assertEqual("Interposer", footprints["E1"].GetValue())
        self.assertTrue(footprints["E1"].HasField("MPN"))
        self.assertEqual("", footprints["E1"].GetFieldText("MPN"))
        self.assertEqual((original, stamp), (library.read_bytes(), library.stat().st_mtime_ns))
        output = Path(result["board_path"])
        raw, saved = output.read_bytes(), output.stat().st_mtime_ns
        self.assertEqual("NOOP", self.backend.apply(self.backend.preview(output, desired), self.root/"unused-fields")["status"])
        self.assertEqual((raw, saved), (output.read_bytes(), output.stat().st_mtime_ns))

    def test_rf_layer_side_and_netclass_are_independent_checks(self):
        for mode in ("layer", "side", "class"):
            desired = copy.deepcopy(self.desired)
            spec = desired["instances"]["J101-1"]
            artifact = spec["rf"]["artifact"]
            artifact["payload"]["layer_roles"] = {"mount": "F.Cu", "signal": "In2.Cu"}
            spec["signal_layer"] = "In2.Cu"
            spec["netclass"] = "RF"
            if mode == "layer":
                spec["signal_layer"] = "In1.Cu"
            elif mode == "side":
                spec["side"] = "B"
            else:
                spec["netclass"] = "DATA"
            rehash(artifact)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.backend.preview(self.board_path, desired)

    def test_selected_native_outline_mounting_hole_and_noop(self):
        board = p.LoadBoard(str(self.board_path))
        ids = []
        for a, b in (((5,5),(60,5)),((60,5),(60,40)),((60,40),(5,40)),((5,40),(5,5))):
            line = p.PCB_SHAPE(board)
            line.SetShape(p.SHAPE_T_SEGMENT)
            line.SetStart(p.VECTOR2I(*[int(x*1e6) for x in a]))
            line.SetEnd(p.VECTOR2I(*[int(x*1e6) for x in b]))
            line.SetLayer(p.Dwgs_User)
            line.SetWidth(50000)
            board.Add(line)
            ids.append(uuid_of(line))
        p.SaveBoard(str(self.board_path), board)
        desired = copy.deepcopy(self.desired)
        hole = copy.deepcopy(desired["instances"]["J101-1"])
        hole.update(kind="mounting-hole", gang="Mounting", pose=[10, 10, 0], pads={})
        del hole["rf"]
        desired["instances"]["H1"] = hole
        desired["outline"] = {"enabled": True, "source_layer": "Dwgs.User", "outer_uuids": ids, "cutout_uuids": []}
        preview = self.backend.preview(self.board_path, desired)
        result = self.backend.apply(preview, self.root/"outline")
        board = p.LoadBoard(result["board_path"])
        self.assertEqual(4, sum(x.GetLayer() == p.Edge_Cuts for x in board.GetDrawings()))
        self.assertTrue(set(ids) <= set(board_state(board, p)))
        fp = next(x for x in board.GetFootprints() if x.GetReference() == "H1")
        self.assertTrue(fp.GetAttributes() & p.FP_BOARD_ONLY)
        self.assertTrue(fp.IsExcludedFromBOM())
        self.assertTrue(fp.IsExcludedFromPosFiles())
        self.assertEqual("NOOP", self.backend.preview(result["board_path"], desired)["status"])

    def test_source_edit_during_copy_rejected(self):
        preview = self.backend.preview(self.board_path, self.desired)
        original = shutil.copytree
        def edited(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.source/"demo.kicad_pro").write_text('{"later":true}')
            return result
        with mock.patch("connector_interposer_workflow.backend.shutil.copytree", side_effect=edited):
            with self.assertRaisesRegex(BackendError, "changed while copying"):
                self.backend.apply(preview, self.root/"race")

    def test_mechanical_circle_matching_ignores_labels_and_extra_circles(self):
        board = p.LoadBoard(str(self.board_path))
        circle = p.PCB_SHAPE(board)
        circle.SetShape(p.SHAPE_T_CIRCLE)
        circle.SetStart(p.VECTOR2I(20000000,20000000))
        circle.SetEnd(p.VECTOR2I(21000000,20000000))
        circle.SetWidth(50000)
        circle.SetLayer(p.Dwgs_User)
        board.Add(circle)
        label = p.PCB_TEXT(board)
        label.SetText("J101-1")
        label.SetPosition(p.VECTOR2I(17460000,17460000))
        label.SetLayer(p.Dwgs_User)
        board.Add(label)
        extra = p.PCB_SHAPE(board)
        extra.SetShape(p.SHAPE_T_CIRCLE)
        extra.SetStart(p.VECTOR2I(90000000, 90000000))
        extra.SetEnd(p.VECTOR2I(91000000, 90000000))
        extra.SetLayer(p.Dwgs_User)
        board.Add(extra)
        p.SaveBoard(str(self.board_path), board)
        accepted = self.backend.inspect_graphics(self.board_path, "Dwgs.User")
        self.assertEqual(2, len(accepted["circles"]))
        self.assertEqual(1, len(accepted["texts"]))
        desired = copy.deepcopy(self.desired)
        desired["mechanical"] = {
            "source_layer": accepted["source_layer"],
            "rf_matches": [{
                "key": "J101-1", "center": [20, 20],
                "circle_uuid": uuid_of(circle),
            }],
            "rf_keys": ["J101-1"],
            "rf_anchors": {"J101": {
                "circle_uuid": uuid_of(circle), "center": [20, 20],
            }},
            "dc_anchors": {}, "interposer_anchors": {},
            "feature_tolerance_mm": 0.00762,
            "drawing_revision": accepted["revision"],
        }
        self.assertEqual("PREVIEW", self.backend.preview(self.board_path, desired)["status"])
        shifted = copy.deepcopy(desired)
        shifted["instances"]["J101-1"]["pose"][0] += .01
        with self.assertRaisesRegex(
            BackendError,
            r"J101-1: center deviation.*0\.01 mm.*limit.*0\.00762 mm.*0\.3 mil",
        ):
            self.backend.preview(self.board_path, shifted)
        desired["instances"] = {}
        with self.assertRaisesRegex(BackendError, "populations"):
            self.backend.preview(self.board_path, desired)

    def test_stale_project_files_and_preview_tampering(self):
        preview = self.backend.preview(self.board_path, self.desired)
        (self.source/"demo.kicad_pro").write_text('{"later":true}')
        with self.assertRaises(BackendError):
            self.backend.apply(preview, self.root/"stale")
        preview = self.backend.preview(self.board_path, self.desired)
        preview["desired"]["instances"]["J101-1"]["pose"][0] = 99
        with self.assertRaises(BackendError):
            self.backend.apply(preview, self.root/"tampered")

    def test_arbitrary_adjacent_microvia_stack(self):
        board = p.LoadBoard(str(self.board_path))
        board.SetCopperLayerCount(6)
        p.SaveBoard(str(self.board_path), board)
        desired = copy.deepcopy(self.desired)
        artifact = desired["instances"]["J101-1"]["rf"]["artifact"]
        via = artifact["payload"]["objects"][0]
        artifact["payload"]["objects"] = []
        layers = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
        artifact["compatibility"]["stackup"] = layers
        for i in range(5):
            obj = dict(via, id="stack-"+str(i), via_type="microvia", layers=layers[i:i+2])
            artifact["payload"]["objects"].append(obj)
        rehash(artifact)
        result = self.backend.apply(self.backend.preview(self.board_path, desired), self.root/"stack")
        board = p.LoadBoard(result["board_path"])
        self.assertEqual(5, len(list(board.GetTracks())))
        self.assertTrue(all(v.HasValidLayerPair(6) for v in board.GetTracks()))

    def test_native_copper_arc_polygon_and_circular_rule_area(self):
        desired = copy.deepcopy(self.desired)
        artifact = desired["instances"]["J101-1"]["rf"]["artifact"]
        artifact["payload"]["objects"] += [
            {"id": "signal-arc", "type": "copper", "shape": "arc", "net": "signal", "layer": "F.Cu",
             "start": [0,0], "mid": [.70710678,.29289322], "end": [1,1], "width": .25},
            {"id": "signal-polygon", "type": "copper", "shape": "polygon", "net": "signal", "layer": "F.Cu",
             "points": [[-.3,-.3],[.3,-.3],[.3,.3],[-.3,.3]]},
            {"id": "circle-keepout", "type": "keepout", "shape": "circle", "net": "ground", "layers": ["B.Cu"],
             "at": [0,0], "diameter": 1.2, "prohibit": ["copperpour"]}]
        rehash(artifact)
        result = self.backend.apply(self.backend.preview(self.board_path, desired), self.root/"curves")
        board = p.LoadBoard(result["board_path"])
        shapes = list(board.GetDrawings())
        self.assertTrue(any(x.GetShape() == p.SHAPE_T_ARC and x.GetNetname() == "CLOCK" for x in shapes))
        self.assertTrue(any(x.GetShape() == p.SHAPE_T_POLY and x.GetNetname() == "CLOCK" for x in shapes))


if __name__ == "__main__":
    unittest.main()
