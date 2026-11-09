"""Domain tests run in ordinary Python and bundled KiCad Python."""
import dataclasses
import hashlib
import math
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins'))
from connector_interposer_workflow.core import *
from connector_interposer_workflow.core.outline import Segment, validate_outline
from connector_interposer_workflow.core.geometry import (
    DEFAULT_FEATURE_TOLERANCE_MM, drawing_revision, feature_deviation_message,
    feature_tolerance, match_circle_features, match_selected_circle,
    suggest_selected_circle,
)
from connector_interposer_workflow.core.sexpr import loads, dumps, children


def rectangle(x,y,w,h):
    points = [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
    return [Segment(points[i],points[(i+1)%4]) for i in range(4)]


class WorkbookTests(unittest.TestCase):
    def test_txt_netlist_sniffs_tab_delimited_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'signals.txt'
            path.write_text(
                '\t'.join(HEADERS) + '\n' +
                '\t'.join(('CLOCK1', 'CLOCK', 'E1', 'K3', 'J101', '1', 'L2')) + '\n',
                encoding='utf-8',
            )
            workbook = read_workbook(path)
            self.assertEqual(workbook.sheets[0].name, 'signals')
            table = parse_sheet(workbook.sheets[0])
            self.assertTrue(table.valid)
            self.assertEqual(table.records[0].key, 'J101-1')

    def test_preamble_mapping_all_rows_and_ignored(self):
        sheet = Worksheet('Pins', (('Title',),HEADERS,('SIG','RF','E1','A1','J101','1','L1'),(),('bad',)))
        table = parse_sheet(sheet,header_row=2,ignored_rows=(4,))
        self.assertTrue(table.mapping_complete)
        self.assertTrue(table.view_data_enabled)
        self.assertEqual((table.included_count,table.ignored_count),(2,1))
        self.assertEqual([r.source_row for r in table.rows],[3,4,5])
        self.assertFalse(table.valid)
        self.assertTrue(table.rows[-1].issues)
        self.assertEqual(table.records[0].key,'J101-1')
        self.assertFalse(parse_sheet(sheet,2,ignored_rows=(1,)).view_data_enabled)

    def test_manual_mapping_and_duplicate_headers(self):
        sheet = Worksheet('manual', (('Signal','Signal'),('S','RF','E1','A1','J1','1','L1')))
        self.assertFalse(parse_sheet(sheet).mapping_complete)
        self.assertTrue(parse_sheet(sheet,mapping=dict(zip(HEADERS,range(7)))).valid)
        mapping=dict(zip(HEADERS,range(7)));mapping['Layer']=0
        self.assertFalse(parse_sheet(sheet,mapping=mapping).view_data_enabled)
        mapping['Layer']=99
        self.assertFalse(parse_sheet(sheet,mapping=mapping).mapping_complete)
        self.assertFalse(parse_sheet(sheet,ignored_rows=('3',4)).view_data_enabled)

    def test_xlsx_shared_inline_sparse_and_formula_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'pins.xlsx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Pins" sheetId="1" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><r><t>Sig</t></r><r><t>nal</t></r><rPh sb="0" eb="3"><t>phonetic</t></rPh></si></sst>')
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="2"><c r="A2" t="s"><v>0</v></c><c r="C2" t="inlineStr"><is><t>NetClass</t></is></c></row><row r="4"><c r="A4"><f>1+1</f></c></row></sheetData></worksheet>')
            sheet=read_workbook(path).sheets[0]
            self.assertEqual(sheet.rows[1],('Signal','','NetClass'))
            self.assertEqual(sheet.rows[2],())
            self.assertIn('formula',sheet.rows[3][0])
            negative=Path(tmp)/'negative.xlsx'
            with zipfile.ZipFile(path) as source, zipfile.ZipFile(negative,'w') as target:
                for name in source.namelist():
                    payload=source.read(name)
                    if name.endswith('sheet1.xml'):payload=payload.replace(b'<v>0</v>',b'<v>-1</v>')
                    target.writestr(name,payload)
            with self.assertRaises(ValueError):read_workbook(negative)

    def test_xlsx_shared_string_indices_do_not_replace_column_indices(self):
        names = ('Signal Map', 'Connector-1', 'Interposer Data',
                 'Layer-Rules', 'Mechanical 2026', 'Notes-Archive')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'six-sheets.xlsx'
            sheets = ''.join(
                f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
                for index, name in enumerate(names, 1)
            )
            relationships = ''.join(
                f'<Relationship Id="rId{index}" Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(names) + 1)
            )
            strings = tuple(f'Unused {index}' for index in range(7)) + names
            shared = ''.join(f'<si><t>{value}</t></si>' for value in strings)
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(
                    'xl/workbook.xml',
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    f'<sheets>{sheets}</sheets></workbook>',
                )
                archive.writestr(
                    'xl/_rels/workbook.xml.rels',
                    f'<Relationships>{relationships}</Relationships>',
                )
                archive.writestr(
                    'xl/sharedStrings.xml',
                    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    f'{shared}</sst>',
                )
                for index, name in enumerate(names, 1):
                    archive.writestr(
                        f'xl/worksheets/sheet{index}.xml',
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        f'<sheetData><row r="1"><c r="A1" t="s"><v>{index + 6}</v></c></row>'
                        '</sheetData></worksheet>',
                    )
            workbook = read_workbook(path)
            self.assertEqual(tuple(sheet.name for sheet in workbook.sheets), names)
            self.assertEqual(tuple(sheet.rows[0][0] for sheet in workbook.sheets), names)

    def test_complete_snapshot_duplicates_and_pad_authority(self):
        row=SignalRecord('S','RF','E1','A1','J1','1','L1',4)
        issues=validate_records((row,dataclasses.replace(row,source_row=8)),{'E1':('B1',)})
        self.assertEqual({i.code for i in issues},{'duplicate_connector','duplicate_signal','duplicate_destination','pad_missing'})
        diff=diff_snapshots((row,),())
        self.assertEqual(diff.removed,('J1-1',))
        for pin in ('²', '01', '0', '-1', '١'):
            self.assertIn('connector_pin', {i.code for i in validate_records([dataclasses.replace(row,connector_pin=pin)])})
        self.assertEqual(diff_snapshots((row,),(dataclasses.replace(row,source_row=9),)).unchanged,('J1-1',))
        self.assertNotIn('ground_signal', {
            issue.code for issue in validate_records((dataclasses.replace(row, signal='GND'),))
        })

    def test_layer_rules_default_all_and_persisted_rejection(self):
        rules=LayerRules();self.assertEqual(len(rules.synchronize(['RF'],['F.Cu','B.Cu'])),2)
        rules.permissions['RF']['B.Cu']=False
        rules.synchronize(['RF','DATA'],['F.Cu','B.Cu','In1.Cu'])
        self.assertFalse(rules.allowed('RF','B.Cu'))
        record=SignalRecord('S','RF','E1','A1','J1','1','L2',7)
        self.assertEqual(validate_layers([record],{'L2':'B.Cu'},['F.Cu','B.Cu'],rules)[0].source_row,7)
        self.assertEqual(validate_layers([record],{'L2':'In1.Cu'},['F.Cu','B.Cu'],rules)[0].code,'layer_missing')


class LayerMappingTests(unittest.TestCase):
    def test_numeric_auto_assignment_default_and_custom_names(self):
        default = ('F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu')
        mapped, unresolved = suggest_layer_mappings(
            ('L1', 'Layer 2', 'metal_5', 'L6', 'NoNumber', 'L2_3'), default,
        )
        self.assertEqual(mapped, {
            'L1': 'F.Cu', 'Layer 2': 'In1.Cu', 'metal_5': 'In4.Cu', 'L6': 'B.Cu',
        })
        self.assertEqual(set(unresolved), {'NoNumber', 'L2_3'})

        custom, unresolved = suggest_layer_mappings(
            ('L1', 'Layer 2', 'metal_4'),
            ('metal_1', 'signal_2', 'plane_3', 'metal_4'),
        )
        self.assertEqual(custom, {
            'L1': 'metal_1', 'Layer 2': 'signal_2', 'metal_4': 'metal_4',
        })
        self.assertEqual(unresolved, {})

    def test_numeric_auto_assignment_rejects_ambiguous_candidates(self):
        mapped, unresolved = suggest_layer_mappings(
            ('L1', 'L2'), ('F.Cu', 'metal_1', 'In1.Cu', 'B.Cu'),
        )
        self.assertNotIn('L1', mapped)
        self.assertEqual(unresolved['L1'], ('F.Cu', 'metal_1'))
        self.assertEqual(mapped['L2'], 'In1.Cu')


class MicroviaTests(unittest.TestCase):
    def test_predefined_size_eligibility_uses_board_minima_and_interposer_pitch(self):
        presets = evaluate_presets(
            ((0, 0), (.254, .127), (.305, .152), (.254, .127)),
            min_diameter=.2, min_drill=.1, min_clearance=.2,
            pad_centres=((0, 0), (.5, 0), (0, .5)),
        )
        self.assertEqual(len(presets), 2)
        self.assertTrue(presets[0].eligible)
        self.assertFalse(presets[1].eligible)
        self.assertIn('spacing', presets[1].reason)
        self.assertEqual(minimum_pitch(((0, 0), (1, 0), (0, 2))), 1)

    def test_front_and_back_stack_topology_has_one_gap_and_no_depth_limit(self):
        layers = ('F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu',
                  'In4.Cu', 'In5.Cu', 'B.Cu')
        front = stack_plan(layers, 'F', 'In2.Cu')
        self.assertEqual(front, (
            ('signal', 'F.Cu', 'In1.Cu'),
            ('signal', 'In1.Cu', 'In2.Cu'),
            ('ground', 'In3.Cu', 'In4.Cu'),
            ('ground', 'In4.Cu', 'In5.Cu'),
            ('ground', 'In5.Cu', 'B.Cu'),
        ))
        back = stack_plan(layers, 'B', 'In3.Cu')
        self.assertEqual(back, (
            ('signal', 'In5.Cu', 'B.Cu'),
            ('signal', 'In4.Cu', 'In5.Cu'),
            ('signal', 'In3.Cu', 'In4.Cu'),
            ('ground', 'In1.Cu', 'In2.Cu'),
            ('ground', 'F.Cu', 'In1.Cu'),
        ))
        self.assertEqual(len(stack_plan(layers, 'F')), len(layers) - 1)
        with self.assertRaisesRegex(ValueError, 'outside'):
            stack_plan(layers, 'F', 'B.Cu')


class ConnectorTypeTests(unittest.TestCase):
    def test_conservative_reference_and_netclass_inference(self):
        records = (
            SignalRecord('D1', 'POWER', 'E1', 'A1', 'J1', '1', 'L1'),
            SignalRecord('D2', 'POWER', 'E1', 'A2', 'J2', '1', 'L1'),
            SignalRecord('R1', 'CLOCK', 'E1', 'A3', 'J101', '1', 'L2'),
            SignalRecord('A1', 'AMBIGUOUS', 'E1', 'A4', 'J12', '1', 'L2'),
            SignalRecord('GND', 'DC', 'E1', 'A5', 'J12', '2', 'L1'),
        )
        suggestions, conflicts, unresolved = suggest_netclass_types(records)
        self.assertEqual(suggestions, {'POWER': 'DC', 'CLOCK': 'RF', 'DC': 'DC'})
        self.assertEqual(conflicts, {})
        self.assertEqual(unresolved, ('AMBIGUOUS',))
        self.assertEqual(reference_type('J1'), 'DC')
        self.assertEqual(reference_type('j101'), 'RF')
        self.assertIsNone(reference_type('J12'))
        mapping = {'POWER': 'DC', 'CLOCK': 'RF', 'AMBIGUOUS': 'RF', 'DC': 'DC'}
        self.assertEqual(connector_type(records, mapping, 'J1'), 'DC')
        self.assertEqual(connector_type(records, mapping, 'J101'), 'RF')
        self.assertIsNone(connector_type(records, mapping, 'J12'))

    def test_inference_reports_cross_type_netclass(self):
        records = (
            SignalRecord('A', 'MIXED', 'E1', 'A1', 'J1', '1', 'L1'),
            SignalRecord('B', 'MIXED', 'E1', 'A2', 'J101', '1', 'L1'),
        )
        suggestions, conflicts, unresolved = suggest_netclass_types(records)
        self.assertNotIn('MIXED', suggestions)
        self.assertEqual(conflicts, {'MIXED': ('DC', 'RF')})
        self.assertEqual(unresolved, ())


class GeometryTests(unittest.TestCase):
    def test_drawing_feature_tolerance_has_one_validated_default_and_message(self):
        self.assertAlmostEqual(DEFAULT_FEATURE_TOLERANCE_MM, .00762)
        self.assertAlmostEqual(feature_tolerance(DEFAULT_FEATURE_TOLERANCE_MM), .00762)
        for invalid in (-.001, .500001, float('nan'), 'bad'):
            with self.assertRaises(ValueError):
                feature_tolerance(invalid)
        message = feature_deviation_message('J101-2', .01, .00762)
        self.assertIn('center deviation', message)
        self.assertIn('0.01 mm', message)
        self.assertIn('0.00762 mm (0.3 mil)', message)

    def test_independent_dimension_units_normalize_to_millimetres(self):
        self.assertEqual(DIMENSION_UNITS, ('mm', 'mil', 'inch', 'um'))
        self.assertEqual(parse_dimension('10mm'), (10, 'mm', '10'))
        self.assertEqual(parse_dimension('10 mm'), (10, 'mm', '10'))
        self.assertEqual(parse_dimension('100 mil')[0], 2.54)
        self.assertEqual(parse_dimension('1 inch')[0], 25.4)
        self.assertEqual(parse_dimension('250 um')[0], .25)
        self.assertEqual(parse_dimension('10', 'mil'), (.254, 'mil', '10'))
        self.assertEqual(dimension_text(25.4, 'inch'), '1')
        with self.assertRaises(ValueError):
            parse_dimension('10 parsecs')

    def test_circle_features_match_without_labels_and_ignore_unclaimed_geometry(self):
        circles = [
            {'uuid': 'one', 'center': [10, 20]},
            {'uuid': 'two', 'center': [12.54, 20]},
            {'uuid': 'extra', 'center': [90, 90]},
        ]
        matches = match_circle_features(
            [('J101-1', [10, 20]), ('J101-2', [12.54, 20])],
            circles, .00762, required={'J101-1': 'one'},
        )
        self.assertEqual([match['circle_uuid'] for match in matches], ['one', 'two'])
        with self.assertRaisesRegex(ValueError, '2 drawing circles match'):
            match_circle_features(
                [('J101-1', [10, 20])],
                circles + [{'uuid': 'duplicate', 'center': [10.001, 20]}], .00762,
            )
        with self.assertRaisesRegex(ValueError, 'same drawing circle'):
            match_circle_features(
                [('J101-1', [10, 20]), ('J102-1', [10, 20])], circles, .00762,
            )

    def test_selected_circle_requires_identity_and_only_suggests_geometry(self):
        accepted = {'uuid': 'live-canvas-id', 'center': [42.5625, 42.5625]}
        circles = [
            {'uuid': 'saved-board-id', 'center': [42.5625, 42.5625]},
            {'uuid': 'unrelated', 'center': [80, 80]},
        ]
        with self.assertRaisesRegex(ValueError, 'unique geometry suggestion'):
            match_selected_circle(
                'E1 interposer origin circle', accepted, circles, .00762,
            )
        matched = suggest_selected_circle(
            'E1 interposer origin circle', accepted, circles, .00762,
        )
        self.assertEqual(matched['uuid'], 'saved-board-id')
        self.assertIsNone(suggest_selected_circle(
            'E1 interposer origin circle', accepted,
            circles + [{'uuid': 'ambiguous', 'center': [42.563, 42.5625]}],
            .00762,
        ))
        self.assertIsNone(suggest_selected_circle(
            'E1 interposer origin circle', accepted,
            [{'uuid': 'moved', 'center': [42.58, 42.5625]}], .00762,
        ))
        inventory = {'source_layer': 'Dwgs.User', 'circles': circles,
                     'segments': [], 'texts': []}
        changed = {
            **inventory,
            'circles': [{**circle, 'uuid': 'new-' + circle['uuid']}
                        for circle in circles],
        }
        self.assertNotEqual(drawing_revision(inventory), drawing_revision(changed))
        self.assertEqual(
            drawing_revision(inventory, include_uuids=False),
            drawing_revision(changed, include_uuids=False),
        )

    def test_custom_numbering_location_one_anchor_and_independence(self):
        g=GangDefinition('J1',(2,2),2,3,row_offsets=(0,1),numbering=(3,4,1,2),rotation=90,anchor=(10,20))
        points=g.positions()
        self.assertEqual(points['1'],(10,20))
        self.assertAlmostEqual(points['2'][0],10)
        self.assertAlmostEqual(points['2'][1],18)
        self.assertEqual(GangDefinition('J2',(1,),100,100).positions(),{'1':(0,0)})
        self.assertEqual(set(GangDefinition('J2',(3,),2,3,numbering=(1,3,6)).positions()),{'1','3','6'})
        self.assertEqual(GangDefinition('J3', (2,), 2.54, 2.54).positions()['2'],
                         (2.54, 0))
        with self.assertRaises(ValueError):dataclasses.replace(g,numbering=(1,1,2,3)).positions()
        with self.assertRaises(ValueError):dataclasses.replace(g,pitch_x=float('nan')).positions()

    def test_serpentine_and_population(self):
        points=GangDefinition('J1',(3,2),2,2,traversal='serpentine').positions()
        self.assertEqual(points['4'],(2,2))

    def test_outline_strict_topology_intent(self):
        self.assertEqual(len(validate_outline(rectangle(0,0,20,20),[rectangle(5,5,3,3)])),2)
        self.assertTrue(validate_outline([s.reversed() for s in reversed(rectangle(0,0,20,20))]))
        for cutout in (rectangle(21,5,3,3),rectangle(0,5,3,3),rectangle(19,5,3,3)):
            with self.assertRaises(ValueError):validate_outline(rectangle(0,0,20,20),[cutout])
        with self.assertRaises(ValueError):validate_outline(rectangle(0,0,20,20)[:-1])
        with self.assertRaises(ValueError):validate_outline(rectangle(0,0,20,20)*2)
        with self.assertRaises(ValueError):validate_outline([Segment((0,0),(10,10)),Segment((10,10),(0,10)),Segment((0,10),(10,0)),Segment((10,0),(0,0))])
        with self.assertRaises(ValueError):validate_outline(rectangle(0,0,20,20),[rectangle(2,2,10,10),rectangle(3,3,2,2)])

    def test_arc_boundaries_are_not_chords(self):
        circle=[Segment((10,0),(-10,0),(0,10)),Segment((-10,0),(10,0),(0,-10))]
        self.assertTrue(validate_outline(circle,[rectangle(-1,-1,2,2)]))
        with self.assertRaises(ValueError):validate_outline(circle,[rectangle(8,5,2,2)])
        with self.assertRaises(ValueError):validate_outline(circle,[rectangle(10,-1,2,2)])
        with self.assertRaises(ValueError):validate_outline(circle,[rectangle(-11,-1,2,2)])


class LibraryTests(unittest.TestCase):
    def test_demo_symbols_constrain_footprints(self):
        path = (Path(__file__).resolve().parents[1] /
                'examples/gppo_demo/library/Example.kicad_sym')
        root = loads(path.read_text(encoding='utf-8'))
        filters = {
            symbol[1]: next(item[2] for item in children(symbol, 'property')
                            if len(item) >= 3 and item[1] == 'ki_fp_filters')
            for symbol in children(root, 'symbol')
        }
        self.assertEqual(filters, {
            'GPPO': 'GPPO_B012_L13_01_EXPERIMENTAL',
            'Interposer': 'Interposer_800',
            'DSub_9': 'DSub_9_Pins_Vertical',
        })
        connector = read_symbol(path, 'Example:GPPO')
        interposer = read_symbol(path, 'Example:Interposer', require_two_pin=False)
        self.assertEqual(connector.footprint_filters, ('GPPO_B012_L13_01_EXPERIMENTAL',))
        self.assertEqual(len(interposer.pin_numbers), 800)
        self.assertTrue(footprint_matches_filters(
            'Example:GPPO_B012_L13_01_EXPERIMENTAL', connector.footprint_filters,
        ))
        self.assertTrue(footprint_matches_filters('Example:Interposer_800', ('Example:Interposer_*',)))
        self.assertFalse(footprint_matches_filters('Other:Interposer_800', ('Example:Interposer_*',)))
        self.assertFalse(footprint_matches_filters('Example:Anything', ()))

    def test_approved_pad_universe_repeated_ground_and_npth(self):
        content='(footprint "Approved" (pad "A1" thru_hole circle (at 0 0) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) (pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu")) (pad "2" smd rect (at 3 0) (size 1 1) (layers "F.Cu")) (pad "" np_thru_hole circle (at 4 0) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask")))'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'Approved.kicad_mod';path.write_text(content)
            part=read_footprint(path,'Approved:Approved')
            self.assertEqual(set(part.pad_numbers),{'A1','2'})
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),part.sha256)
            with self.assertRaises(ValueError):read_footprint(path,'Approved:Approved','a'*64)
            path.write_text('(footprint "Hole" (pad "" np_thru_hole circle (at 0 0) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask")))')
            with self.assertRaises(ValueError):read_footprint(path,'Approved:Approved')
            self.assertEqual(read_footprint(path,'Approved:Approved',allow_mechanical=True).pad_numbers,())
        self.assertEqual(loads(dumps(loads('(a "a \\"quoted\\"" (b 1))'))),loads('(a "a \\"quoted\\"" (b 1))'))

    def test_project_schema_identity(self):
        config=ProjectConfig();self.assertEqual(ProjectConfig.from_dict(config.to_dict()).project_id,config.project_id)
        self.assertAlmostEqual(config.mechanical['feature_tolerance_mm'], .00762)
        self.assertEqual(config.mechanical['feature_tolerance_unit'], 'mil')
        self.assertEqual(stable_uuid(config.project_id,'symbol/J1-1'),stable_uuid(config.project_id,'symbol/J1-1'))
        with self.assertRaises(ValueError):ProjectConfig.from_dict(dict(config.to_dict(), schema_version=5))
        with self.assertRaises(ValueError):ProjectConfig.from_dict({'project_id':config.project_id,'schema_version':2})
        invalid = config.to_dict()
        del invalid['mechanical']['feature_tolerance_mm']
        with self.assertRaisesRegex(ValueError, 'Mechanical settings'):
            ProjectConfig.from_dict(invalid)


if __name__=='__main__':unittest.main()
