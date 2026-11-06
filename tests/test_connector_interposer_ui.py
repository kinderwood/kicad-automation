"""Workflow session invariants and optional real native-dialog smoke coverage."""
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.connector_interposer_workflow.controller import WorkflowController, row_ranges, PAGES


def write_transition(path, part_id, part_sha256, netclass='RF', layer='F.Cu',
                     stack=('F.Cu', 'B.Cu')):
    from plugins.connector_interposer_workflow.artifacts import canonical, sha256
    artifact = {
        'schema': 'gppo.artifact/1.0', 'artifact_id': 'test-transition',
        'revision': 1, 'kind': 'rf-transition', 'payload_schema': 'gppo.rf/1.0',
        'recipe': {'fixture': True},
        'compatibility': {
            'part_id': part_id, 'part_sha256': part_sha256, 'mount': 'PTH',
            'side': 'front', 'netclasses': [netclass], 'stackup': list(stack),
        },
        'required_capabilities': ['net-copper-shapes'],
        'payload': {
            'units': 'mm', 'coordinates': 'x-right-y-up-ccw',
            'net_roles': ['signal', 'ground'],
            'anchors': {'signal': [0, 0], 'exit_direction_deg': 0},
            'layer_roles': {'mount': 'F.Cu', 'signal': layer},
            'objects': [{
                'id': 'signal-land', 'type': 'copper', 'shape': 'circle',
                'net': 'signal', 'at': [0, 0], 'diameter': .8, 'layer': layer,
            }],
        },
    }
    unsigned = dict(artifact)
    artifact['hashes'] = {
        'payload': sha256(artifact['payload']), 'recipe': sha256(artifact['recipe']),
        'artifact': sha256(unsigned),
    }
    Path(path).write_bytes(canonical(artifact))
    return Path(path)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.board = self.root/'source.kicad_pcb'
        self.board.write_text('(kicad_pcb)')
        self.workbook = self.root/'signals.csv'
        self.workbook.write_text('title\nLayer,Connector Pin,Signal,Connector,Interposer Pin,NetClass,Interposer\nL1,1,CLOCK,J101,A1,RF,U1\nL1,2,DATA,J101,A2,RF,U1\nL1,1,ENABLE,J202,A3,Control,U1\n')
        self.controller = WorkflowController(self.board)
        self.controller.load_workbook(self.workbook, header_row=2)
        self.controller.set_netclass_type('RF', 'RF')
        self.controller.set_netclass_type('Control', 'RF')
    def tearDown(self): self.temporary.cleanup()

    def test_header_mapping_retains_source_rows_and_explicit_ignored_rows(self):
        self.assertEqual(self.controller.table.records[0].key, 'J101-1')
        self.assertEqual(self.controller.table.records[0].source_row, 3)
        self.controller.parse('signals', 2, '4')
        self.assertTrue(self.controller.view_data_enabled)
        self.assertEqual((self.controller.table.included_count, self.controller.table.ignored_count), (2, 1))
        self.assertEqual(tuple(row.source_row for row in self.controller.table.rows), (3, 4, 5))
        self.assertTrue(self.controller.table.rows[1].ignored)
        self.controller.save()
        restored = WorkflowController(self.board)
        self.assertEqual(restored.config['workbook']['ignored_rows'], [4])
        self.assertEqual(restored.config['project_id'], self.controller.config['project_id'])

    def test_invalid_mapping_and_range_never_leave_old_table_viewable(self):
        self.assertTrue(self.controller.view_data_enabled)
        with self.assertRaises(ValueError): self.controller.parse('signals', 2, '8-3')
        self.assertFalse(self.controller.view_data_enabled)
        self.controller.parse('signals', 2, mapping={'Signal': 0})
        self.assertFalse(self.controller.view_data_enabled)
        self.controller.parse('signals', 2, '99')
        self.assertFalse(self.controller.view_data_enabled)

    def test_reset_and_delete_settings_keep_their_scopes_separate(self):
        self.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        self.controller.set_layer_mapping('L1', 'In1.Cu')
        self.controller.save()
        project_id = self.controller.config['project_id']
        settings_path = self.controller.config_path
        snapshot_directory = self.root / '.connector-interposer-inputs'
        self.assertTrue(settings_path.is_file())
        self.assertTrue(snapshot_directory.is_dir())

        self.controller.reset_current()
        self.assertEqual(self.controller.config['project_id'], project_id)
        self.assertEqual(self.controller.copper_layers, ('F.Cu', 'In1.Cu', 'B.Cu'))
        self.assertEqual(self.controller.config['workbook'], {})
        self.assertEqual(self.controller.config['layer_mapping'], {})
        self.assertEqual(self.controller.config['outline'], {'enabled': False})
        self.assertIsNone(self.controller.workbook)
        self.assertIsNone(self.controller.table)
        self.assertTrue(settings_path.is_file(), 'Reset must not delete saved settings')

        self.assertTrue(self.controller.delete_saved_settings())
        self.assertFalse(settings_path.exists())
        self.assertTrue(snapshot_directory.is_dir(), 'Delete targets the settings sidecar only')
        self.assertFalse(self.controller.delete_saved_settings())

    def test_layer_policy_survives_stackup_and_class_growth(self):
        self.controller.set_rule('RF', 'F.Cu', False)
        self.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        permissions = self.controller.config['layer_rules']
        self.assertFalse(permissions['RF']['F.Cu'])
        self.assertTrue(permissions['RF']['In1.Cu'])
        self.controller.set_layer_mapping('L1', 'F.Cu')
        issues = self.controller.validate()
        self.assertEqual([issue.source_row for issue in issues if issue.code == 'layer_disallowed'], [3, 4])

    def test_independent_gangs_and_explicit_copy_preserve_anchors(self):
        self.controller.set_gang('J101', {
            'pitch_x': 4, 'anchor': [30, 20], 'anchor_accepted': True,
            'display_units': {'pitch_x': 'mil', 'pitch_y': 'mm', 'row_offsets': [],
                              'anchor_x': 'inch', 'anchor_y': 'inch'},
        })
        self.controller.set_gang('J202', {
            'pitch_x': 9, 'anchor': [80, 40], 'anchor_accepted': True,
            'display_units': {'pitch_x': 'mm', 'pitch_y': 'mm', 'row_offsets': [],
                              'anchor_x': 'um', 'anchor_y': 'mil'},
        })
        self.assertEqual(self.controller.pattern('J101')['2'], (34, 20))
        self.assertEqual(self.controller.gang('J202')['pitch_x'], 9)
        self.controller.copy_gang('J101', ['J202'])
        self.assertEqual(self.controller.gang('J202')['pitch_x'], 4)
        self.assertEqual(self.controller.gang('J202')['anchor'], [80, 40])
        self.assertEqual(self.controller.gang('J101')['anchor'], [30, 20])
        self.assertEqual(self.controller.gang('J202')['display_units']['pitch_x'], 'mil')
        self.assertEqual(self.controller.gang('J202')['display_units']['anchor_x'], 'um')
        self.assertEqual(self.controller.gang('J202')['display_units']['anchor_y'], 'mil')

    def test_rf_requirements_group_by_netclass_layer_and_footprint(self):
        self.controller.set_layers(('F.Cu', 'B.Cu'))
        self.controller.set_layer_mapping('L1', 'F.Cu')
        self.controller.set_netclass_type('Control', 'DC')
        from plugins.connector_interposer_workflow.core.libraries import read_footprint
        part_path = self.root / 'GPPO.kicad_mod'
        part_path.write_text(
            '(footprint "GPPO" (layer "F.Cu") '
            '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) '
            '(layers "*.Cu" "*.Mask")) '
            '(pad "2" smd circle (at 0 2) (size 1 1) (layers "F.Cu" "F.Mask")))'
        )
        part = read_footprint(part_path, 'Approved:GPPO')
        part_sha = part.sha256
        self.controller.set_gang('J101', {
            'part_id': str(part_path),
            'library_id': 'Approved:GPPO', 'part_sha256': part_sha,
        })
        requirements = self.controller.rf_transition_requirements()
        self.assertEqual(len(requirements), 1)
        requirement = requirements[0]
        self.assertEqual(
            (requirement['netclass'], requirement['layer'], requirement['footprint']),
            ('RF', 'F.Cu', 'Approved:GPPO'),
        )
        self.assertEqual(requirement['locations'], ('J101-1', 'J101-2'))
        self.assertFalse(any(issue.code == 'rf_transition'
                             for issue in self.controller.validate()))
        artifact = write_transition(
            self.root / 'transition.gppo-rf.json', 'Approved:GPPO', part_sha,
        )
        self.controller.set_rf_transition(requirement['key'], artifact)
        refreshed = self.controller.rf_transition_requirements()[0]
        self.assertEqual(self.controller.validate_rf_transition(refreshed)['artifact_id'],
                         'test-transition')

    def test_changed_external_artifact_blocks_apply(self):
        artifact = self.root/'rf.json'
        artifact.write_text('{"revision":"A"}')
        self.controller.config['artifacts']['test'] = {'path': str(artifact)}
        self.controller.preview = {'reviewed': True}
        self.controller.preview_token = self.controller.token()
        artifact.write_text('{"revision":"B"}')
        with self.assertRaisesRegex(ValueError, 'stale'): self.controller.apply()
        self.assertIsNone(self.controller.preview)

    def test_destination_selection_does_not_authorize_a_changed_preview(self):
        before = self.controller.token()
        self.controller.config['output_dir'] = str(self.root/'output')
        self.assertEqual(before, self.controller.token())
        self.controller.config['layer_mapping']['L1'] = 'B.Cu'
        self.assertNotEqual(before, self.controller.token())

    def test_changed_workbook_cannot_reuse_previously_parsed_rows(self):
        self.workbook.write_text(self.workbook.read_text().replace('CLOCK', 'NEW_CLOCK'))
        with self.assertRaisesRegex(ValueError, 'Workbook changed'):
            self.controller.review()

    def test_cancelled_analysis_discards_result(self):
        started, proceed, finished = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        def operation():
            started.set()
            proceed.wait(3)
            return 'obsolete'
        def failed(error):
            errors.append(str(error))
            finished.set()
        self.controller.run_async(operation, results.append, failed)
        self.assertTrue(started.wait(3))
        self.controller.cancel()
        proceed.set()
        self.assertTrue(finished.wait(3))
        self.assertEqual(results, [])
        self.assertIn('cancelled', errors[0])


class NativeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('DISPLAY'):
            raise unittest.SkipTest('Native wx checks need an available display')
        try:
            import wx
        except ImportError:
            raise unittest.SkipTest('KiCad wx runtime unavailable')
        cls.wx = wx
        cls.app = wx.GetApp() or wx.App(False)

    def test_semantic_palette_follows_wx_appearance_and_keeps_text_legible(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette

        def luminance(colour):
            channels = []
            for value in colour.Get()[:3]:
                value /= 255
                channels.append(value / 12.92 if value <= .04045
                                else ((value + .055) / 1.055) ** 2.4)
            return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]

        def contrast(first, second):
            high, low = sorted((luminance(first), luminance(second)), reverse=True)
            return (high + .05) / (low + .05)

        dialog = WorkflowDialog()
        try:
            palette = ui_palette()
            appearance = self.wx.SystemSettings.GetAppearance()
            if hasattr(appearance, 'IsDark'):
                self.assertEqual(palette.dark, appearance.IsDark())
            for background in (palette.info, palette.assigned, palette.warning,
                               palette.error, palette.success):
                self.assertGreaterEqual(contrast(palette.text, background), 4.5)
            self.assertGreaterEqual(contrast(palette.panel_text, palette.heading), 4.5)

            task_box, task_controls = dialog._task_box_controls[0]
            self.assertEqual(task_box.GetBackgroundColour().Get()[:3], palette.info.Get()[:3])
            self.assertEqual(task_controls[0].GetForegroundColour().Get()[:3],
                             palette.text.Get()[:3])
            self.assertEqual(dialog.columns_status.GetBackgroundColour().Get()[:3],
                             palette.warning.Get()[:3])

            task_box.SetBackgroundColour(self.wx.Colour(1, 2, 3))
            dialog.apply_theme()
            self.assertEqual(task_box.GetBackgroundColour().Get()[:3],
                             ui_palette().info.Get()[:3])
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_native_page_hierarchy_and_full_10000_row_popup(self):
        from plugins.connector_interposer_workflow.ui import (WorkflowDialog, DataDialog,
                                                               LibraryPartPickerDialog,
                                                               LibrarySelectionCtrl,
                                                               SymbolAssignmentCtrl,
                                                               FootprintAssignmentCtrl,
                                                               AssignmentRenderer, ui_palette)
        from plugins.connector_interposer_workflow.core.library_catalog import LibraryEntry, LibraryPart
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.core.model import HEADERS
        dialog = WorkflowDialog()
        self.assertEqual(tuple(dialog.book.GetPageText(i) for i in range(dialog.book.GetPageCount())), PAGES)
        self.assertEqual(PAGES[0], 'Netlist source')
        self.assertEqual(PAGES[1], 'Netclass type')
        self.assertEqual(PAGES[2], 'Layer assignment')
        self.assertEqual(PAGES[3], 'Routing permissions')
        self.assertEqual(PAGES[4], 'Connector assignments')
        self.assertEqual(PAGES[5], 'Define RF gangs')
        self.assertEqual(PAGES[6], 'RF transitions')
        self.assertEqual(PAGES[7], 'Connector placement')
        self.assertEqual(PAGES[8], 'Board outline')
        self.assertEqual(PAGES[9], 'Review changes')
        self.assertEqual(PAGES[10], 'Apply / results')
        for page_index, page_name in enumerate(PAGES):
            self.assertIsNotNone(
                self.wx.FindWindowByLabel(page_name, dialog.page_hosts[page_index]),
            )
            task_box = dialog._task_box_controls[page_index][0]
            self.assertIs(task_box.GetParent(), dialog.page_hosts[page_index])
            self.assertIs(dialog.pages[page_index].GetParent(), dialog.page_hosts[page_index])
            host_sizer = dialog.page_hosts[page_index].GetSizer()
            self.assertIs(host_sizer.GetItem(0).GetWindow(), task_box)
            self.assertIs(host_sizer.GetItem(1).GetWindow(), dialog.pages[page_index])
        self.assertIsNone(self.wx.FindWindowByLabel('Apply layer assignments', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Apply routing permissions', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Saved permissions retained.', dialog))
        self.assertIs(dialog.layer_map.GetParent().GetParent(), dialog.pages[2])
        self.assertIs(dialog.rules.GetParent(), dialog.pages[3])
        self.assertEqual(dialog.step_list.GetCount(), len(PAGES))
        longest_step = max(PAGES, key=len)
        step_extent = self.wx.ClientDC(dialog.step_list).GetTextExtent(longest_step)[0]
        self.assertGreater(dialog.step_list.GetSize().width, step_extent)
        self.assertLess(dialog.step_list.GetSize().height, dialog.GetClientSize().height // 2)
        self.assertEqual(dialog.step_list.GetBackgroundColour().Get()[:3],
                         ui_palette().panel.Get()[:3])
        self.assertEqual(dialog.step_list.item_font(0).GetWeight(), self.wx.FONTWEIGHT_BOLD)
        self.assertEqual(dialog.step_list.item_font(1).GetWeight(), self.wx.FONTWEIGHT_NORMAL)
        self.assertTrue(dialog.step_list.GetWindowStyleFlag() & self.wx.BORDER_SIMPLE)
        self.assertIsNotNone(self.wx.FindWindowByLabel('Save workflow settings', dialog))
        self.assertIsNotNone(self.wx.FindWindowByLabel('Delete workflow settings', dialog))
        self.assertIsNotNone(self.wx.FindWindowByLabel('Reset workflow settings', dialog))
        self.assertIn('without applying board changes', dialog.save_button.GetToolTipText())
        self.assertEqual(dialog.layer_map.GetRowLabelSize(), 0)
        self.assertEqual(dialog.layer_map.GetColLabelValue(0), 'Netlist layer')
        self.assertEqual(dialog.layer_map.GetColLabelValue(1), 'KiCad layer')
        task_heading = self.wx.FindWindowByLabel('Layer assignment', dialog.page_hosts[2])
        self.assertGreater(task_heading.GetFont().GetPointSize(), dialog.GetFont().GetPointSize())
        dialog.Show()
        self.app.Yield()
        display_index = self.wx.Display.GetFromWindow(dialog)
        display_area = self.wx.Display(display_index).GetClientArea()
        expected_minimum_width = min(980, max(760, int(display_area.width * .65)))
        expected_minimum_height = min(640, max(560, int(display_area.height * .62)))
        self.assertLessEqual(
            dialog.GetSize().width,
            max(expected_minimum_width, min(1340, int(display_area.width * .86))),
        )
        self.assertLessEqual(
            dialog.GetSize().height,
            max(expected_minimum_height, min(800, int(display_area.height * .84))),
        )
        self.assertLessEqual(dialog.pages[0].GetVirtualSize().width,
                             dialog.pages[0].GetClientSize().width)
        self.assertFalse(dialog.view_button.IsEnabled())
        self.assertFalse(dialog.apply_button.IsEnabled())
        self.assertFalse(dialog.workbook_path.IsShown())
        self.assertEqual(dialog.mapper.GetNumberRows(), 6)
        self.assertEqual(dialog.mapper.GetNumberCols(), 9)
        self.assertIsInstance(dialog.mapper.GetCellRenderer(0, 2), AssignmentRenderer)
        self.assertIsNotNone(self.wx.FindWindowByLabel('Open full table…', dialog))
        self.assertIsInstance(dialog.interposer_symbol_assignment, SymbolAssignmentCtrl)
        self.assertIsInstance(dialog.interposer_footprint_assignment, FootprintAssignmentCtrl)
        self.assertIsInstance(dialog.hole_path, LibrarySelectionCtrl)
        self.assertIsInstance(dialog.gang_rows, self.wx.grid.Grid)
        self.assertFalse(dialog.open_transition_designer.IsEnabled())
        self.assertIsNone(self.wx.FindWindowByLabel('Per-location RF assignments…', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Accept library selections', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Project BOM MPN override (if absent)', dialog))
        footprint = Path(__file__).resolve().parents[1] / 'examples/gppo_demo/library/Example.pretty/GPPO_B012_L13_01_EXPERIMENTAL.kicad_mod'
        class Catalog:
            kind = 'footprint'
            def libraries(self):
                return (LibraryEntry('Example', 'fp_lib_table', footprint.parent, 'Project'),)
            def parts(self, nickname):
                return (LibraryPart('Example:GPPO_B012_L13_01_EXPERIMENTAL', footprint, 'Project'),)
        picker = LibraryPartPickerDialog(dialog, Catalog(), 'Choose connector',
                                         'Example:GPPO_B012_L13_01_EXPERIMENTAL')
        self.assertEqual(picker.selection['library_id'],
                         'Example:GPPO_B012_L13_01_EXPERIMENTAL')
        self.assertTrue(picker.ok.IsEnabled())
        picker.Destroy()
        rows = (HEADERS,) + tuple((f'SIGNAL_NAME_{i:05}', 'RF', 'U1', f'A{i}', 'J101', str(i), 'L1') for i in range(1, 10001))
        table = parse_sheet(Worksheet('Signals', rows), ignored_rows=(2, 5001))
        dialog.controller.table = table
        dialog.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        dialog.refresh_layers()
        palette = ui_palette()
        self.assertEqual(dialog.netclass_type_grid.GetNumberRows(), 1)
        self.assertEqual(dialog.netclass_type_grid.GetCellBackgroundColour(0, 3).Get()[:3],
                         palette.warning.Get()[:3])
        self.assertTrue(dialog.netclass_type_grid.IsReadOnly(0, 3))
        self.assertFalse(dialog.netclass_type_grid.IsCellEditControlShown())
        menu_labels = []
        def dismiss_menu(_window, menu, _position):
            menu_labels.append(tuple(item.GetItemLabelText() for item in menu.GetMenuItems()))
            return False
        with mock.patch.object(dialog, '_popup_assignment_menu', side_effect=dismiss_menu):
            dialog._show_netclass_type_menu(0)
            dialog._show_layer_assignment_menu(0)
        self.assertEqual(menu_labels[0], ('Clear assignment', 'RF', 'DC'))
        self.assertEqual(menu_labels[1], ('Clear assignment', 'F.Cu', 'In1.Cu', 'B.Cu'))
        self.assertFalse(dialog.netclass_type_grid.IsCellEditControlShown())
        self.assertFalse(dialog.layer_map.IsCellEditControlShown())
        dialog.auto_assign_netclass_types(None)
        self.assertEqual(dialog.controller.config['netclass_types']['RF'], 'RF')
        self.assertEqual(dialog.netclass_type_status.GetLabel(), '1 of 1 assigned')
        self.assertEqual(dialog.netclass_type_grid.GetCellBackgroundColour(0, 3).Get()[:3],
                         palette.assigned.Get()[:3])
        self.assertTrue(dialog.new_rules.IsShown())
        self.assertIn('new combinations require review', dialog.new_rules.GetLabel())
        self.assertEqual(dialog.layer_map.GetNumberRows(), 1)
        self.assertIsInstance(dialog.layer_map.GetCellRenderer(0, 1), AssignmentRenderer)
        self.assertEqual(dialog.layer_map.GetCellBackgroundColour(0, 1).Get()[:3],
                         palette.warning.Get()[:3])
        self.assertIsNotNone(self.wx.FindWindowByLabel('Auto assign', dialog.pages[2]))
        self.assertLess(dialog.layer_map.GetSize().height, 100)
        self.assertEqual(
            dialog.layer_map.GetSize().height,
            dialog.layer_map.GetColLabelSize() + dialog.layer_map.GetRowSize(0) + 2,
        )
        dialog.auto_assign_layers(None)
        self.assertEqual(dialog.controller.config['layer_mapping']['L1'], 'F.Cu')
        self.assertIn('Auto assigned 1 layer', dialog.status.GetLabel())
        dialog.layer_map.SetCellValue(0, 1, 'In1.Cu')
        class LayerEvent:
            @staticmethod
            def GetCol(): return 1
            @staticmethod
            def GetRow(): return 0
            @staticmethod
            def Skip(): return None
        dialog.layer_assignment_changed(LayerEvent())
        self.assertEqual(dialog.controller.config['layer_mapping']['L1'], 'In1.Cu')
        self.assertEqual(dialog.layer_mapping_status.GetLabel(), '1 of 1 assigned')
        self.assertEqual(dialog.layer_map.GetCellBackgroundColour(0, 1).Get()[:3],
                         palette.assigned.Get()[:3])
        self.assertIn('board files were not modified', dialog.status.GetLabel())
        dialog.auto_assign_layers(None)
        self.assertEqual(dialog.controller.config['layer_mapping']['L1'], 'In1.Cu')
        self.assertIn('Preserved 1 existing assignment', dialog.status.GetLabel())
        dialog.rules.SetCellValue(0, 0, '')
        class PermissionEvent:
            @staticmethod
            def GetCol(): return 0
            @staticmethod
            def GetRow(): return 0
            @staticmethod
            def Skip(): return None
        dialog.routing_permission_changed(PermissionEvent())
        self.assertFalse(dialog.controller.config['layer_rules']['RF']['F.Cu'])
        self.assertIn('not allowed', dialog.status.GetLabel())
        popup = DataDialog(dialog, table)
        native_grid = next(child for child in popup.GetChildren() if isinstance(child, self.wx.grid.Grid))
        self.assertEqual(native_grid.GetNumberRows(), 9998)
        self.assertEqual(native_grid.GetNumberCols(), 8)
        self.assertEqual(tuple(native_grid.GetColLabelValue(column) for column in range(8)),
                         ('Source row',) + HEADERS)
        self.assertGreater(native_grid.GetRowLabelSize(), 0)
        self.assertEqual(native_grid.GetRowLabelValue(0), '1')
        self.assertEqual(native_grid.GetCellValue(0, 0), '3')
        self.assertEqual(native_grid.GetRowLabelValue(9997), '9998')
        self.assertEqual(native_grid.GetCellValue(9997, 0), '10001')
        self.assertEqual(native_grid.GetCellValue(9997, 1), 'SIGNAL_NAME_10000')
        self.assertGreater(native_grid.GetColSize(1), native_grid.GetColSize(2))
        area = self.wx.Display(self.wx.Display.GetFromWindow(popup)).GetClientArea()
        self.assertLessEqual(popup.GetSize().width, min(1600, int(area.width * .9)))
        self.assertLessEqual(popup.GetSize().height, min(900, int(area.height * .9)))
        self.assertFalse(native_grid.IsEditable())
        popup.Destroy()
        dialog.Destroy()

    def test_settings_actions_require_confirmation_and_keep_distinct_scopes(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / 'source.kicad_pcb'
            board.write_text('(kicad_pcb)')
            workbook = root / 'signals.csv'
            workbook.write_text(
                'Signal,NetClass,Interposer,Interposer Pin,Connector,Connector Pin,Layer\n'
                'CLOCK,RF,U1,A1,J101,1,L1\n'
            )
            controller = WorkflowController(board)
            controller.load_workbook(workbook)
            controller.save()
            dialog = WorkflowDialog(controller=controller)
            saved = controller.config_path.read_text()

            controller.config['output_dir'] = 'changed'
            with mock.patch.object(dialog, '_confirm', return_value=False):
                self.assertFalse(dialog.save(None))
                self.assertFalse(dialog.reset_workflow_settings(None))
                self.assertFalse(dialog.delete_workflow_settings(None))
            self.assertEqual(controller.config_path.read_text(), saved)
            self.assertIsNotNone(controller.table)

            with mock.patch.object(dialog, '_confirm', return_value=True):
                self.assertTrue(dialog.reset_workflow_settings(None))
            self.assertEqual(dialog.workbook_path.GetValue(), '')
            self.assertEqual(dialog.header.GetValue(), 1)
            self.assertIsNone(controller.table)
            self.assertTrue(controller.config_path.is_file())

            with mock.patch.object(dialog, '_confirm', return_value=True):
                self.assertTrue(dialog.delete_workflow_settings(None))
            self.assertFalse(controller.config_path.exists())
            dialog.Destroy()
        self.app.Yield()

    def test_spreadsheet_auto_mapping_uses_populated_columns_and_live_examples(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette
        from plugins.connector_interposer_workflow.core.model import HEADERS
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / 'signals.csv'
            workbook.write_text(
                'Signal,NetClass,Interposer,Interposer Pin,Connector,Connector Pin,Layer,\n'
                'CLOCK1,CLOCK,E1,K3,J101,1,L2,\n'
                'DATA1,DATA,E1,M6,J101,2,L3,\n',
                encoding='utf-8',
            )
            dialog = WorkflowDialog()
            try:
                dialog.workbook_path.SetValue(str(workbook))
                deadline = time.monotonic() + 2
                while dialog.controller.workbook is None and time.monotonic() < deadline:
                    self.app.Yield()
                    time.sleep(.01)
                self.assertIsNotNone(dialog.controller.workbook)
                self.assertIsNone(dialog.controller.table)
                self.assertEqual(dialog.sheet.GetStringSelection(), '')
                self.assertEqual(dialog.header.GetValue(), 1)
                self.assertEqual(dialog.spreadsheet_feedback.GetLabel(), 'Choose a netlist worksheet.')
                dialog.sheet.SetStringSelection('signals')
                dialog._parse_spreadsheet('redetect')
                dialog.Show()
                self.app.Yield()
                self.assertIsNotNone(dialog.controller.table)
                self.assertEqual(dialog.sheet.GetStringSelection(), 'signals')
                self.assertLessEqual(dialog.sheet.GetSize().width, 180)
                self.assertEqual(dialog.mapper.GetNumberCols(), 9)
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'A · Signal')
                self.assertEqual(dialog.mapper.GetCellValue(1, 0), '1')
                self.assertEqual(dialog.mapper.GetCellValue(1, 1), '2')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'CLOCK1')
                self.assertEqual(dialog.workbook_filename.GetLabel(), 'signals.csv')
                self.assertEqual(dialog.mapper.GetCellBackgroundColour(0, 2).Get()[:3],
                                 ui_palette().assigned.Get()[:3])
                self.assertEqual(len(dialog._spreadsheet_columns), 7)
                self.assertFalse(any(label.startswith('H ·') for label in dialog._spreadsheet_columns))
                self.assertEqual(dialog.columns_status.GetLabel(), '7 of 7 columns assigned')
                self.assertEqual(dialog.nets_status.GetLabel(), '2 nets parsed')
                self.assertTrue(dialog.mapper.IsReadOnly(0, 2))
                self.assertFalse(dialog.mapper.IsCellEditControlShown())
                menu_labels = []
                checked_labels = []
                def dismiss_menu(_window, menu, _position):
                    menu_labels.extend(item.GetItemLabelText() for item in menu.GetMenuItems())
                    checked_labels.extend(item.GetItemLabelText() for item in menu.GetMenuItems()
                                          if item.IsChecked())
                    return False
                with mock.patch.object(dialog, '_popup_assignment_menu', side_effect=dismiss_menu):
                    dialog._show_assignment_menu(2)
                self.assertIn('Clear assignment', menu_labels)
                self.assertIn('A · Signal', menu_labels)
                self.assertEqual(checked_labels, ['A · Signal'])
                self.assertFalse(dialog.mapper.IsCellEditControlShown())
                expected_width = sum(
                    dialog.mapper.GetColSize(column)
                    for column in range(dialog.mapper.GetNumberCols())
                ) + dialog.mapper.GetNumberCols() + 2
                self.assertEqual(dialog.mapper.GetSize().width, expected_width)
                self.assertLess(dialog.mapper.GetSize().width,
                                dialog.book.GetClientSize().width * .9)

                dialog.ignored.ChangeValue('2')
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.mapper.GetCellValue(1, 1), '3')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'DATA1')
                self.assertEqual(dialog.nets_status.GetLabel(), '1 net parsed')

                dialog._set_mapping_choice(2, 'B · NetClass')
                dialog._set_mapping_choice(3, 'A · Signal')
                dialog._cancel_spreadsheet_timer()
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.controller.table.mapping['Signal'], 1)
                self.assertEqual(dialog.controller.table.mapping['NetClass'], 0)
                self.assertIsNone(self.wx.FindWindowByLabel('Load / parse', dialog))
                self.assertIsNone(self.wx.FindWindowByLabel('Apply manual mapping', dialog))
                self.assertIsNotNone(self.wx.FindWindowByLabel('Reload', dialog))

                workbook.write_text(workbook.read_text(encoding='utf-8').replace('DATA1', 'DATA9'),
                                    encoding='utf-8')
                dialog.reload_workbook(None)
                self.assertEqual(dialog.ignored.GetValue(), '2')
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'A · Signal')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'DATA9')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(7)))

                wide = Path(temporary) / 'wide.csv'
                headings = [f'Other {index}' for index in range(1, 18)] + [
                    'Wire', 'Kind', 'Device', 'Ball', 'Gang', 'Position', 'Route',
                ]
                values = [f'ignored-{index}' for index in range(1, 18)] + [
                    'CLOCK7', 'CLOCK', 'E1', 'K3', 'J101', '1', 'L2',
                ]
                wide.write_text(','.join(headings) + '\n' + ','.join(values) + '\n', encoding='utf-8')
                dialog.header.SetValue(2)
                dialog.workbook_path.SetValue(str(wide))
                deadline = time.monotonic() + 2
                while (dialog.controller.workbook is None or dialog.sheet.GetCount() < 2) and time.monotonic() < deadline:
                    self.app.Yield()
                    time.sleep(.01)
                self.assertEqual(dialog.sheet.GetStringSelection(), '')
                self.assertEqual(dialog.header.GetValue(), 1)
                dialog.sheet.SetStringSelection('wide')
                dialog._parse_spreadsheet('redetect')
                self.assertEqual([dialog.mapper.GetCellValue(0, column) for column in range(2, 9)], [''] * 7)
                self.assertEqual(dialog.mapper.GetCellBackgroundColour(0, 2).Get()[:3],
                                 ui_palette().warning.Get()[:3])
                dialog.mapper.SetCellValue(0, 2, 'R · Wire')
                self.assertTrue(dialog._autofill_consecutive_mapping())
                self.assertEqual(
                    [dialog.mapper.GetCellValue(0, column).split(' · ', 1)[0] for column in range(2, 9)],
                    ['R', 'S', 'T', 'U', 'V', 'W', 'X'],
                )
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(17, 24)))

                dialog.header.SetValue(2)
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(17, 24)))
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'R · CLOCK7')
                dialog.header.SetValue(1)
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(17, 24)))
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'R · Wire')

                dialog.header.SetValue(999)
                dialog._parse_spreadsheet('mapping')
                self.assertIn(
                    "Header row 999 does not exist in worksheet 'wide'",
                    dialog.spreadsheet_feedback.GetLabel(),
                )
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'R · Wire')

                dialog.header.SetValue(1)
                dialog._parse_spreadsheet('mapping')
                dialog.mapper.SetCellValue(0, 2, 'S · Kind')
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.controller.table.mapping['Signal'], 18)
                dialog._parse_spreadsheet('redetect')
                self.assertEqual([dialog.mapper.GetCellValue(0, column) for column in range(2, 9)], [''] * 7)
                dialog.sheet.SetSelection(0)
                dialog._parse_spreadsheet('redetect')
                self.assertIsNone(dialog.controller.table)
                self.assertEqual([dialog.mapper.GetCellValue(0, column) for column in range(2, 9)], [''] * 7)
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_connector_assignments_filter_loaded_footprints_per_gang(self):
        import hashlib
        from plugins.connector_interposer_workflow.ui import (
            WorkflowDialog, TableDividerCell, TableTextCell, ui_palette,
        )
        from plugins.connector_interposer_workflow.core.libraries import read_symbol
        example = Path(__file__).resolve().parents[1] / 'examples/gppo_demo'
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            board = project / 'demo.kicad_pcb'
            board.write_text('(kicad_pcb (version 20240108) (generator pcbnew))')
            settings = project / 'settings'
            settings.mkdir()
            (project / 'fp-lib-table').write_text(
                '(fp_lib_table (lib (name "Example") (type "KiCad") '
                f'(uri "{example / "library/Example.pretty"}") (options "")))'
            )

            class Settings:
                @classmethod
                def GetUserSettingsPath(cls): return str(settings)
            class Board:
                @staticmethod
                def GetProject(): return object()
            class Pcbnew:
                SETTINGS_MANAGER = Settings
                @staticmethod
                def GetBoard(): return Board()
                @staticmethod
                def ResolveUriByEnvVars(value, native_project): return str(value)

            controller = WorkflowController(board)
            controller.load_workbook(example / 'inputs/netlist-A.xlsx')
            for netclass in {record.netclass for record in controller.table.records}:
                controller.set_netclass_type(netclass, 'RF')
            dialog = WorkflowDialog(controller=controller, pcbnew_module=Pcbnew)
            try:
                symbols = example / 'library/Example.kicad_sym'
                digest = hashlib.sha256(symbols.read_bytes()).hexdigest()
                interposer = read_symbol(symbols, 'Example:Interposer', require_two_pin=False)
                interposer_spec = {'path': str(symbols), 'library_id': interposer.library_id,
                                   'sha256': digest}
                dialog.assign_interposer_symbol(interposer_spec, interposer)
                self.assertTrue(dialog.interposer_footprint_assignment.IsEnabled())
                self.assertEqual(
                    [option['library_id'] for option in dialog.interposer_footprint_assignment.options],
                    ['Example:Interposer_800'],
                )
                footprint_menu = []
                def dismiss_footprint_menu(_window, menu, _position):
                    footprint_menu.extend(item.GetItemLabelText() for item in menu.GetMenuItems())
                    return False
                with mock.patch.object(
                        dialog, '_popup_assignment_menu', side_effect=dismiss_footprint_menu):
                    dialog.interposer_footprint_assignment._show_menu()
                self.assertEqual(footprint_menu, ['Example:Interposer_800'])
                dialog.interposer_footprint_assignment.select_index(0)
                self.assertEqual(dialog.interposer_assignment_status.GetLabel(),
                                 '1 of 1 interposers assigned')

                connector = read_symbol(symbols, 'Example:GPPO')
                connector_spec = {'path': str(symbols), 'library_id': connector.library_id,
                                  'sha256': digest}
                for gang in ('J101', 'J102'):
                    dialog.assign_gang_symbol(gang, connector_spec, connector)
                    footprint = dialog.gang_assignment_controls[gang]['footprint']
                    self.assertTrue(footprint.IsEnabled())
                    self.assertEqual([option['library_id'] for option in footprint.options],
                                     ['Example:GPPO_B012_L13_01_EXPERIMENTAL'])
                    footprint.select_index(0)
                self.assertEqual(dialog.gang_assignment_status.GetLabel(),
                                 '2 of 2 gangs assigned')
                self.assertEqual(
                    dialog.gang_assignment_rows.GetBackgroundColour().Get()[:3],
                    ui_palette().window.Get()[:3],
                )
                self.assertTrue(
                    dialog.gang_assignment_rows.GetWindowStyleFlag() & self.wx.BORDER_SIMPLE,
                )
                self.assertGreaterEqual(dialog.gang_assignment_table.GetPosition().x, 4)
                self.assertGreaterEqual(dialog.gang_assignment_table.GetPosition().y, 4)
                self.assertFalse(any(
                    isinstance(child, self.wx.StaticLine)
                    for child in dialog.gang_assignment_table.GetChildren()
                ))
                self.assertEqual(
                    sum(isinstance(child, TableDividerCell)
                        for child in dialog.gang_assignment_table.GetChildren()),
                    4,
                )
                read_only_cells = [
                    child for child in dialog.gang_assignment_table.GetChildren()
                    if isinstance(child, TableTextCell) and child.GetLabel() == 'J101'
                ]
                self.assertEqual(len(read_only_cells), 1)
                j101_cell = read_only_cells[0]
                self.assertEqual(j101_cell.GetSize().height, 38)
                self.assertGreater(j101_cell.text.GetPosition().y, 0)
                self.assertLessEqual(
                    dialog.gang_assignment_controls['J101']['symbol'].GetSize().width, 300,
                )
                self.assertLessEqual(
                    dialog.gang_assignment_controls['J101']['footprint'].GetSize().width, 320,
                )
                self.assertIsNone(self.wx.FindWindowByLabel('Status', dialog.pages[4]))
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_overflow_tooltips_cover_assignment_and_value_entry_cells(self):
        from plugins.connector_interposer_workflow.ui import (
            DimensionCellRenderer, WorkflowDialog,
        )

        dialog = WorkflowDialog()
        try:
            dialog.select_page(4)
            dialog.Show()
            self.app.Yield()
            long_symbol = 'Project:' + 'Very_Long_Connector_Symbol_' * 8
            dialog.interposer_symbol_assignment.set_selection({
                'library_id': long_symbol,
            })
            dialog.interposer_symbol_assignment.Layout()
            dialog.interposer_symbol_assignment._refresh_overflow_tooltip()
            self.assertEqual(
                dialog.interposer_symbol_assignment.display.GetToolTipText(),
                long_symbol,
            )
            dialog.interposer_symbol_assignment.set_selection({'library_id': 'J1'})
            dialog.interposer_symbol_assignment._refresh_overflow_tooltip()
            self.assertEqual(
                dialog.interposer_symbol_assignment.display.GetToolTipText(), '',
            )

            long_footprint = 'Project:' + 'Very_Long_Connector_Footprint_' * 8
            selection = {'library_id': long_footprint, 'path': '/tmp/example.kicad_mod'}
            dialog.interposer_footprint_assignment.set_options([selection], selection)
            dialog.interposer_footprint_assignment.Layout()
            dialog.interposer_footprint_assignment._refresh_overflow_tooltip()
            self.assertEqual(
                dialog.interposer_footprint_assignment.chevron.GetToolTipText(),
                long_footprint,
            )

            long_assignment = 'Workbook column with a deliberately long heading'
            dialog.mapper.SetCellValue(0, 2, long_assignment)
            dialog.mapper.SetColSize(2, 90)
            self.assertEqual(
                dialog._grid_overflow_text(dialog.mapper, 0, 2), long_assignment,
            )
            dialog.mapper.SetColSize(2, 600)
            self.assertEqual(dialog._grid_overflow_text(dialog.mapper, 0, 2), '')

            long_dimension = '123456789.123456 inch'
            dialog.gang_rows.SetCellRenderer(0, 2, DimensionCellRenderer())
            dialog.gang_rows.SetCellValue(0, 2, long_dimension)
            dialog.gang_rows.SetColSize(2, 90)
            self.assertEqual(
                dialog._grid_overflow_text(dialog.gang_rows, 0, 2), long_dimension,
            )
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_footprint_popup_routes_selection_through_its_assignment_cell(self):
        import json
        from plugins.connector_interposer_workflow import diagnostics
        from plugins.connector_interposer_workflow.ui import (
            FootprintAssignmentCtrl, WorkflowDialog,
        )

        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / 'debug.log'
            with mock.patch.object(diagnostics, 'LOG_PATH', log_path):
                diagnostics.reset(test='popup')
                dialog = WorkflowDialog()
                selected = []
                control = FootprintAssignmentCtrl(dialog, selected.append)
                option = {
                    'path': '/tmp/Connector_25.kicad_mod',
                    'library_id': 'Project:Connector_25',
                    'sha256': 'test-revision',
                }
                control.set_options((option,), {})
                control.Enable(True)

                def choose_first(window, menu, _position):
                    self.assertIs(window, control)
                    return menu.GetMenuItems()[0].GetId()

                try:
                    with mock.patch.object(
                            dialog, '_popup_assignment_menu', side_effect=choose_first):
                        control._show_menu()
                    self.assertEqual(control.selection(), option)
                    self.assertEqual(selected, [option])
                    records = [json.loads(line) for line in log_path.read_text().splitlines()]
                    events = [record['event'] for record in records]
                    self.assertIn('footprint_menu_opening', events)
                    self.assertIn('footprint_menu_returned', events)
                    self.assertIn('footprint_select_callback_complete', events)
                    self.assertNotIn(option['library_id'], log_path.read_text())
                finally:
                    control.Destroy()
                    dialog.Destroy()
                    self.app.Yield()

    def test_all_connector_assignment_rows_apply_and_individual_edits_clear_them(self):
        from types import SimpleNamespace
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            footprint = root / 'Connector.kicad_mod'
            footprint.write_text(
                '(footprint "Connector" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) '
                '(layers "*.Cu" "*.Mask")))'
            )
            symbol_path = root / 'Connector.kicad_sym'
            symbol_path.write_text('(kicad_symbol_lib (version 20241209) (generator test))')
            table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('RF1', 'RF', 'E1', 'A1', 'J101', '1', 'L1'),
                ('RF2', 'RF', 'E1', 'A2', 'J102', '1', 'L1'),
                ('DC1', 'DC', 'E1', 'A3', 'J1', '1', 'L1'),
                ('DC2', 'DC', 'E1', 'A4', 'J2', '1', 'L1'),
            )))
            dialog = WorkflowDialog()
            try:
                dialog.controller.table = table
                dialog.controller.set_netclass_type('RF', 'RF')
                dialog.controller.set_netclass_type('DC', 'DC')
                option = {
                    'path': str(footprint), 'library_id': 'Example:Connector',
                    'sha256': '',
                }
                symbol_spec = {
                    'path': str(symbol_path), 'library_id': 'Example:Connector',
                    'sha256': '',
                }
                symbol = SimpleNamespace(
                    library_id='Example:Connector', value='Connector', mpn='',
                    unit_count=1, footprint_filters=('Connector',),
                )
                dialog._allowed_footprint_specs = lambda _symbol: (option,)
                dialog.assignment_filter.ChangeValue('J101')
                dialog.refresh_connector_assignments(True)
                self.assertEqual(set(dialog.gang_assignment_controls), {'J101'})
                self.assertEqual(dialog.dc_assignment_controls, {})

                dialog.assign_all_gang_symbols(symbol_spec, symbol)
                dialog.assign_all_gang_footprints(option)
                for name in ('J101', 'J102'):
                    self.assertEqual(
                        dialog.controller.gang(name)['symbol']['library_id'],
                        'Example:Connector',
                    )
                    self.assertEqual(
                        dialog.controller.gang(name)['library_id'], 'Example:Connector',
                    )
                self.assertEqual(
                    dialog.gang_assignment_all_controls['symbol'].selection['library_id'],
                    'Example:Connector',
                )
                dialog.assign_gang_symbol('J101', symbol_spec, symbol)
                self.assertEqual(dialog.gang_assignment_all_controls['symbol'].selection, {})
                self.assertFalse(dialog.gang_assignment_all_controls['footprint'].IsEnabled())
                self.assertEqual(
                    dialog.gang_assignment_all_controls['symbol'].GetBackgroundColour().Get()[:3],
                    ui_palette().warning.Get()[:3],
                )

                dialog.assign_all_dc_symbols(symbol_spec, symbol)
                dialog.assign_all_dc_footprints(option)
                for name in ('J1', 'J2'):
                    self.assertEqual(
                        dialog.controller.dc_connector(name)['symbol']['library_id'],
                        'Example:Connector',
                    )
                    self.assertEqual(
                        dialog.controller.dc_connector(name)['library_id'],
                        'Example:Connector',
                    )
                dialog.assign_dc_footprint('J1', option)
                self.assertEqual(dialog.dc_assignment_all_controls['symbol'].selection, {})
                self.assertFalse(dialog.dc_assignment_all_controls['footprint'].IsEnabled())
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_netclass_and_layer_tables_fit_their_columns_and_all_rows(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet

        rows = [HEADERS]
        rows.extend(
            (f'S{index}', f'CLASS{index}', 'E1', f'A{index}', f'J{100 + index}',
             '1', f'L{index}')
            for index in range(1, 13)
        )
        dialog = WorkflowDialog()
        try:
            dialog.controller.table = parse_sheet(Worksheet('Signals', tuple(rows)))
            dialog.controller.set_layers(tuple(f'In{index}.Cu' for index in range(1, 13)))
            dialog.refresh_layers()
            dialog.Show()
            self.app.Yield()
            self.assertEqual(dialog.netclass_type_grid.GetNumberRows(), 12)
            self.assertEqual(dialog.layer_map.GetNumberRows(), 12)
            for view in (dialog.netclass_type_grid, dialog.layer_map):
                content_height = (
                    view.GetColLabelSize()
                    + sum(view.GetRowSize(row) for row in range(12)) + 2
                )
                self.assertEqual(view.GetSize().height, content_height)
                self.assertLess(view.GetSize().width,
                                dialog.book.GetClientSize().width * .75)
            self.assertLessEqual(dialog.layer_map.GetColSize(0), 220)
            self.assertLessEqual(dialog.layer_map.GetColSize(1), 220)
            self.assertLessEqual(dialog.netclass_type_grid.GetColSize(0), 240)
            self.assertLessEqual(dialog.netclass_type_grid.GetColSize(2), 260)
            for view, button in (
                (dialog.netclass_type_grid, dialog.netclass_auto_assign),
                (dialog.layer_map, dialog.layer_auto_assign),
            ):
                table_right = view.GetPosition().x + view.GetSize().width
                button_right = button.GetPosition().x + button.GetSize().width
                self.assertLessEqual(abs(table_right - button_right), 8)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_live_gang_definitions_units_and_requirement_transition_assignment(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import (
            DimensionCellEditor, DimensionCellRenderer, WorkflowDialog,
        )

        dialog = WorkflowDialog()
        try:
            from plugins.connector_interposer_workflow.core.libraries import read_footprint
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            part_path = Path(temporary.name) / 'GPPO.kicad_mod'
            part_path.write_text(
                '(footprint "GPPO" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) '
                '(layers "*.Cu" "*.Mask")) '
                '(pad "2" smd circle (at 0 2) (size 1 1) (layers "F.Cu" "F.Mask")))'
            )
            part = read_footprint(part_path, 'Approved:GPPO')
            table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('CLOCK1', 'CLOCK', 'E1', 'A1', 'J101', '1', 'L1'),
                ('CLOCK2', 'CLOCK', 'E1', 'A2', 'J101', '2', 'L1'),
            )))
            dialog.controller.table = table
            dialog.controller.set_netclass_type('CLOCK', 'RF')
            dialog.controller.set_layers(('F.Cu', 'B.Cu'))
            dialog.controller.set_layer_mapping('L1', 'F.Cu')
            dialog.controller.set_gang('J101', {
                'row_lengths': [2], 'pitch_x': 2.54,
                'part_id': str(part_path),
                'library_id': 'Approved:GPPO', 'part_sha256': part.sha256,
            })
            dialog.refresh_gangs()
            dialog.select_page(5)
            dialog.Show()
            self.app.Yield()
            dialog.gang_notebook.SetSelection(1)
            self.app.Yield()
            self.assertEqual(dialog.book.GetSelection(), 5)
            self.assertEqual(dialog.step_list.GetSelection(), 5)
            dialog.gang_notebook.SetSelection(0)
            self.app.Yield()
            self.assertEqual(dialog.book.GetSelection(), 5)
            self.assertEqual(dialog.step_list.GetSelection(), 5)
            self.assertEqual(dialog.current_gang, 'J101')
            self.assertEqual(dialog.gang_list.GetItemText(0, 2), 'Not defined')
            self.assertIsNone(self.wx.FindWindowByLabel('Apply definition', dialog.pages[5]))
            self.assertIsNone(self.wx.FindWindowByLabel('Revert', dialog.pages[5]))
            self.assertEqual(dialog.gang_editor_title.GetLabel(), 'J101 Gang definition')
            self.assertEqual(dialog.gang_rows.GetNumberCols(), 3)
            self.assertEqual(dialog.gang_rows.GetColLabelValue(2), 'X offset')
            self.assertEqual(dialog.gang_rows.GetCellValue(0, 2), '0 mil')
            self.assertEqual(dialog.gang_notebook.GetPageText(1), 'Numbering')
            self.assertEqual(dialog.gang_pitch_x.units.GetStringSelection(), 'mil')
            self.assertEqual(dialog.gang_pitch_x.GetValue(), '100')
            renderer = dialog.gang_rows.GetCellRenderer(0, 2)
            editor = dialog.gang_rows.GetCellEditor(0, 2)
            self.assertIsInstance(renderer, DimensionCellRenderer)
            self.assertIsInstance(editor, DimensionCellEditor)
            renderer.DecRef()
            editor.DecRef()
            self.assertLess(
                sum(dialog.gang_list.GetColumnWidth(column) for column in range(3)),
                dialog.gang_list.GetClientSize().width,
            )
            self.assertLess(dialog.gang_list.GetSize().height, 200)
            dialog.gang_rows.SetCellValue(0, 1, '3')
            dialog.gang_draft_changed()
            self.assertEqual(len(dialog.pattern_view.points), 3)
            self.assertIn('Needs input', dialog.gang_definition_status.GetLabel())
            self.assertEqual(dialog.gang_list.GetItemText(0, 2), 'Invalid')
            dialog.gang_rows.SetCellValue(0, 1, '2')
            dialog.gang_draft_changed()
            dialog.gang_pitch_x.ChangeValue('100 mil')
            dialog.gang_draft_changed()
            self.assertEqual(dialog.controller.gang('J101')['pitch_x'], 2.54)
            self.assertEqual(
                dialog.controller.gang('J101')['display_units']['pitch_x'], 'mil',
            )
            self.assertTrue(dialog.controller.gang('J101')['defined'])
            self.assertEqual(dialog.gang_list.GetItemText(0, 2), 'Defined')
            self.assertEqual(dialog.gang_preview_status.GetLabel(),
                             'Current gang definition')
            self.assertEqual(dialog.gang_rows.GetColLabelValue(0), 'Row')
            visible_grid_height = (dialog.gang_rows.GetColLabelSize() +
                                   5 * dialog.gang_rows.GetDefaultRowSize())
            self.assertGreaterEqual(dialog.gang_rows.GetMinSize().height, visible_grid_height)

            requirements = dialog.controller.rf_transition_requirements()
            self.assertEqual(len(requirements), 1)
            dialog.refresh_rf_transitions()
            self.assertEqual(dialog.transition_list.GetItemText(0), 'Not assigned')
            self.assertEqual(dialog.transition_page_status.GetLabel(),
                             '0 assigned · 1 unassigned (optional)')
            self.assertFalse(dialog.open_transition_designer.IsEnabled())
            artifact = write_transition(
                Path(temporary.name) / 'test.gppo-rf.json', 'Approved:GPPO',
                part.sha256, netclass='CLOCK',
            )
            dialog.controller.set_rf_transition(requirements[0]['key'], artifact)
            dialog.refresh_rf_transitions()
            self.assertEqual(dialog.transition_list.GetItemText(0), 'Ready')
            self.assertEqual(dialog.transition_page_status.GetLabel(),
                             '1 assigned · 0 unassigned (optional)')
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_19_position_gang_offset_editor_activates_and_commits(self):
        """Regression: a wx.Panel editor raised in SWIG and crashed the KiCad host."""
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            rows = [HEADERS]
            rows.extend(
                (f'SIGNAL{pin}', 'RF', 'E1', f'A{pin}', 'J501', str(pin), 'L2')
                for pin in range(1, 20)
            )
            dialog.controller.table = parse_sheet(Worksheet('Signals', tuple(rows)))
            dialog.controller.set_netclass_type('RF', 'RF')
            dialog.refresh_gangs()
            dialog.select_page(5)
            dialog.Show()
            self.app.Yield()
            self.assertLessEqual(
                dialog.pages[5].GetVirtualSize().width,
                dialog.pages[5].GetClientSize().width,
            )

            dialog.gang_pitch_x.text.SetFocus()
            dialog.gang_pitch_x.text.SetValue('200 mil')
            dialog.gang_pitch_y.text.SetFocus()
            self.app.Yield()
            dialog.gang_pitch_y.text.SetValue('173.2 mil')
            dialog.gang_rows.SetCellValue(0, 1, '10')
            dialog.gang_draft_changed()
            dialog.add_gang_row(None)
            dialog.gang_rows.SetCellValue(1, 1, '9')
            dialog.gang_draft_changed()

            class ClickEvent:
                @staticmethod
                def GetRow(): return 1
                @staticmethod
                def GetCol(): return 2
                @staticmethod
                def Skip(): return None
            dialog.gang_cell_clicked(ClickEvent())
            self.app.Yield()
            focused = self.wx.Window.FindFocus()
            self.assertIsInstance(focused, self.wx.TextCtrl)
            self.assertGreaterEqual(dialog.gang_rows.GetRowSize(1), 36)
            unit_choice = next(
                child for child in focused.GetParent().GetChildren()
                if isinstance(child, self.wx.Choice)
            )
            self.assertGreaterEqual(unit_choice.GetSize().height, 28)
            focused.SetValue('100 mil')
            enter = self.wx.CommandEvent(self.wx.EVT_TEXT_ENTER.typeId, focused.GetId())
            enter.SetEventObject(focused)
            focused.GetEventHandler().ProcessEvent(enter)
            self.app.Yield()
            self.assertFalse(dialog.gang_rows.IsCellEditControlShown())

            gang = dialog.controller.gang('J501')
            self.assertEqual(gang['row_lengths'], [10, 9])
            self.assertAlmostEqual(gang['pitch_x'], 5.08)
            self.assertAlmostEqual(gang['pitch_y'], 4.39928)
            self.assertEqual(gang['row_offsets'], [0, 2.54])
            self.assertTrue(gang['defined'])
            first_layout, radius = dialog.pattern_view.screen_layout(285, 300)
            first_center = (
                sum(point[0] for point in first_layout.values()) / len(first_layout),
                sum(point[1] for point in first_layout.values()) / len(first_layout),
            )
            self.assertAlmostEqual(first_center[0], 285 / 2, delta=.6)
            self.assertAlmostEqual(first_center[1], 40 + (300 - 56) / 2, delta=.6)
            minimum_spacing = min(
                math.hypot(first[0] - second[0], first[1] - second[1])
                for index, first in enumerate(first_layout.values())
                for second in tuple(first_layout.values())[index + 1:]
            )
            self.assertGreaterEqual(minimum_spacing, radius * 2)
            preview_points = dialog.pattern_view.points
            dialog.pattern_view.points = {
                str(pin): (float(pin - 1), 0.0) for pin in range(1, 20)
            }
            dense_layout, dense_radius = dialog.pattern_view.screen_layout(330, 300)
            dialog.pattern_view.points = preview_points
            self.assertEqual(len(dense_layout), 19)
            self.assertLessEqual(
                dialog.pattern_view.label_point_size(dense_radius, 19, 10), 8,
            )

            dialog.gang_pattern_rotation.ChangeValue('37')
            dialog.gang_draft_changed()
            rotated_layout, _radius = dialog.pattern_view.screen_layout(285, 300)
            rotated_center = (
                sum(point[0] for point in rotated_layout.values()) / len(rotated_layout),
                sum(point[1] for point in rotated_layout.values()) / len(rotated_layout),
            )
            self.assertAlmostEqual(rotated_center[0], first_center[0], delta=.6)
            self.assertAlmostEqual(rotated_center[1], first_center[1], delta=.6)
            self.assertNotEqual(rotated_layout, first_layout)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_dimension_inputs_normalize_suffixes_and_store_connector_placement_in_mm(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            dialog.controller.table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('CLOCK1', 'CLOCK', 'E1', 'A1', 'J101', '1', 'L1'),
            )))
            dialog.controller.config['interposers']['E1'] = {'symbol': {'path': 'selected'}}
            dialog.interposer_x.ChangeValue('1 inch')
            dialog.interposer_y.ChangeValue('25000um')
            dialog.accept_interposer_placement(None)
            selection = dialog.controller.config['interposers']['E1']
            self.assertEqual(selection['pose'][:2], [25.4, 25.0])
            self.assertEqual(selection['pose_units'], ['inch', 'um'])

            dialog.label_offset_x.ChangeValue('10 mm')
            event = self.wx.CommandEvent()
            dialog.label_offset_x._normalize(event)
            self.assertEqual(dialog.label_offset_x.GetValue(), '10')
            self.assertEqual(dialog.label_offset_x.units.GetStringSelection(), 'mm')
            dialog.label_offset_y.ChangeValue('100 mil')
            self.assertEqual(dialog.label_offset_y.value_mm(), 2.54)
        finally:
            dialog.Destroy()
            self.app.Yield()




class NativeControllerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pcbnew
        except ImportError:
            raise unittest.SkipTest('Native controller integration needs KiCad Python')
        cls.p = pcbnew

    def test_saved_native_population_to_project_copy_and_reopen_noop(self):
        from plugins.connector_interposer_workflow.controller import NativeProcessBackend
        from test_connector_interposer_schematic import fixture
        import csv
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            source.mkdir()
            library = root/'approved.pretty'
            library.mkdir()
            records, gang_parts, interposer_parts = fixture(library, 3)
            path = source/'demo.kicad_pcb'
            board = self.p.BOARD()
            for index, record in enumerate(records):
                center = self.p.VECTOR2I((20+index*4)*1000000, 30*1000000)
                circle = self.p.PCB_SHAPE(board)
                circle.SetShape(self.p.SHAPE_T_CIRCLE)
                circle.SetLayer(self.p.Dwgs_User)
                circle.SetCenter(center)
                circle.SetEnd(self.p.VECTOR2I(center.x+1000000, center.y))
                circle.SetWidth(50000)
                board.Add(circle)
                label = self.p.PCB_TEXT(board)
                label.SetText(record.key)
                label.SetLayer(self.p.Dwgs_User)
                label.SetPosition(self.p.VECTOR2I(center.x-2540000, center.y-2540000))
                board.Add(label)
            self.p.SaveBoard(str(path), board)
            (source/'demo.kicad_pro').write_text('{"board":{"designer_setting":42}}')
            workbook = root/'signals.csv'
            with workbook.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(('Signal','NetClass','Interposer','Interposer Pin','Connector','Connector Pin','Layer'))
                for record in records: writer.writerow((record.signal, record.netclass, record.interposer, record.interposer_pin, record.connector, record.connector_pin, record.layer))
            controller = WorkflowController(path, backend=NativeProcessBackend())
            controller.load_workbook(workbook)
            controller.set_netclass_type('RF', 'RF')
            controller.set_layer_mapping('L1', 'F.Cu')
            symbol = library/'Approved.kicad_sym'
            symbol_sha = hashlib.sha256(symbol.read_bytes()).hexdigest()
            controller.set_gang('J101', {'part_id': gang_parts['J101'].footprint.path, 'library_id': 'Approved:GPPO',
                                       'part_sha256': gang_parts['J101'].footprint.sha256,
                                       'symbol': {'path': str(symbol), 'library_id': 'Approved:GPPO', 'sha256': symbol_sha},
                                       'row_lengths': [3], 'pitch_x': 4, 'anchor': [20, 30],
                                       'anchor_accepted': True, 'defined': True})
            part = interposer_parts['E1'].footprint
            controller.config['interposers']['E1'] = dict(
                part.pin(), approved=True, pose=[70, 60, 0], pose_accepted=True,
                symbol={'path': str(symbol), 'library_id': 'Approved:Interposer', 'sha256': symbol_sha},
            )
            controller.config['mechanical'] = {'layer': 'Dwgs.User', 'accepted': True, 'label_offset': [-2.54, -2.54]}
            before = path.read_bytes()
            controller.set_gang('J101', {'anchor': [21, 30]})
            with self.assertRaisesRegex(ValueError, 'does not match mechanical circle'):
                controller.review()
            controller.set_gang('J101', {'anchor': [20, 30]})
            preview = controller.review()
            self.assertEqual(preview['conflicts'], [])
            self.assertNotIn('rf', preview['desired']['instances']['J101-1'])
            import io
            bom_rows = {row['Reference']: row for row in csv.DictReader(io.StringIO(preview['desired']['project_files']['demo-connector-bom.csv']))}
            for reference, instance in preview['desired']['instances'].items():
                self.assertEqual(instance['symbol_fields'], {name: bom_rows[reference][name] for name in ('Value', 'MPN')})
            controller.config['output_dir'] = str(root/'output')
            result = controller.apply()
            self.assertEqual(result['status'], 'APPLIED')
            self.assertEqual(before, path.read_bytes())
            output = Path(result['board_path'])
            self.assertTrue(output.with_suffix('.kicad_sch').exists())
            footprints = {footprint.GetReference(): footprint for footprint in self.p.LoadBoard(str(output)).GetFootprints()}
            self.assertEqual(len(footprints), 4)
            for reference, footprint in footprints.items():
                self.assertEqual(footprint.GetValue(), bom_rows[reference]['Value'])
                self.assertEqual(footprint.GetFieldText('MPN'), bom_rows[reference]['MPN'])
            import json
            project = json.loads(output.with_suffix('.kicad_pro').read_text())
            self.assertEqual(project['board']['designer_setting'], 42)
            self.assertEqual(project['net_settings']['netclass_assignments']['SIG1'], ['RF'])
            reopened = WorkflowController(output, backend=NativeProcessBackend())
            saved = reopened.config['workbook']
            reopened.load_workbook(saved['path'], saved['worksheet'], saved['header_row'], saved['ignored_rows'], saved['mapping'])
            repeated = reopened.review()
            self.assertEqual(repeated['status'], 'NOOP', str(repeated['changes']) + str(repeated['conflicts']))
            stamp = output.stat().st_mtime_ns
            reopened.config['output_dir'] = str(root/'unused')
            self.assertEqual(reopened.apply()['status'], 'NOOP')
            self.assertEqual(output.stat().st_mtime_ns, stamp)
            import shutil
            shutil.rmtree(library)
            self.assertEqual(reopened.review()['status'], 'NOOP')

    def test_mixed_rf_and_dc_controller_apply_keeps_one_dc_instance_and_nc_pad(self):
        from plugins.connector_interposer_workflow.controller import NativeProcessBackend
        from plugins.connector_interposer_workflow.core import SignalRecord, read_footprint
        from test_connector_interposer_schematic import fixture
        import csv
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'; source.mkdir()
            library = root / 'approved.pretty'; library.mkdir()
            _fixture_records, gang_parts, interposer_parts = fixture(library, 3)
            dc_footprint = library / 'DSub.kicad_mod'
            dc_footprint.write_text(
                '(footprint "DSub" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 5 7) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) '
                '(pad "2" thru_hole circle (at 7 7) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) '
                '(pad "3" thru_hole circle (at 9 7) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")))'
            )
            dc_symbol = root / 'DC.kicad_sym'
            dc_symbol.write_text('''(kicad_symbol_lib (version 20241209) (generator "test")
              (symbol "DSub" (pin_names (offset 0)) (in_bom yes) (on_board yes)
                (property "Reference" "J" (at 0 5 0) (effects (font (size 1 1))))
                (property "Value" "DSub" (at 0 7 0) (effects (font (size 1 1))))
                (property "ki_fp_filters" "DSub" (at 0 0 0) (effects (font (size 1 1)) hide))
                (symbol "DSub_1_1"
                  (pin passive line (at 0 0 180) (length 2.54) (name "1" (effects (font (size .8 .8)))) (number "1" (effects (font (size .8 .8)))))
                  (pin passive line (at 0 -5.08 180) (length 2.54) (name "2" (effects (font (size .8 .8)))) (number "2" (effects (font (size .8 .8)))))
                  (pin passive line (at 0 -10.16 180) (length 2.54) (name "3" (effects (font (size .8 .8)))) (number "3" (effects (font (size .8 .8))))))))''')
            records = (
                SignalRecord('RF1', 'RF', 'E1', 'A1', 'J101', '1', 'L1'),
                SignalRecord('GND', 'DC', 'E1', 'A2', 'J1', '1', 'L1'),
                SignalRecord('PWR', 'DC', 'E1', 'A3', 'J1', '2', 'L1'),
            )
            board = self.p.BOARD()
            pin1_uuid = None
            for x, y, label in ((20, 30, 'J101-1'), (50, 50, ''),
                                (52, 50, ''), (54, 50, '')):
                center = self.p.VECTOR2I(x * 1000000, y * 1000000)
                circle = self.p.PCB_SHAPE(board); circle.SetShape(self.p.SHAPE_T_CIRCLE)
                circle.SetLayer(self.p.Dwgs_User); circle.SetCenter(center)
                circle.SetEnd(self.p.VECTOR2I(center.x + 500000, center.y)); board.Add(circle)
                if x == 50:
                    pin1_uuid = circle.m_Uuid.AsString()
                if label:
                    text = self.p.PCB_TEXT(board); text.SetText(label); text.SetLayer(self.p.Dwgs_User)
                    text.SetPosition(center); board.Add(text)
            board_path = source / 'mixed.kicad_pcb'
            self.p.SaveBoard(str(board_path), board)
            (source / 'mixed.kicad_pro').write_text('{}')
            workbook = root / 'mixed.csv'
            with workbook.open('w', newline='') as stream:
                writer = csv.writer(stream); writer.writerow(
                    ('Signal', 'NetClass', 'Interposer', 'Interposer Pin',
                     'Connector', 'Connector Pin', 'Layer')
                )
                for record in records:
                    writer.writerow(tuple(getattr(record, field) for field in
                                          ('signal', 'netclass', 'interposer', 'interposer_pin',
                                           'connector', 'connector_pin', 'layer')))
            controller = WorkflowController(board_path, backend=NativeProcessBackend())
            controller.load_workbook(workbook)
            controller.set_netclass_type('RF', 'RF'); controller.set_netclass_type('DC', 'DC')
            controller.set_layer_mapping('L1', 'F.Cu')
            approved_symbols = library / 'Approved.kicad_sym'
            approved_sha = hashlib.sha256(approved_symbols.read_bytes()).hexdigest()
            controller.set_gang('J101', {
                'part_id': gang_parts['J101'].footprint.path, 'library_id': 'Approved:GPPO',
                'part_sha256': gang_parts['J101'].footprint.sha256,
                'symbol': {'path': str(approved_symbols), 'library_id': 'Approved:GPPO',
                           'sha256': approved_sha},
                'row_lengths': [1], 'anchor': [20, 30], 'anchor_accepted': True,
                'defined': True,
            })
            transition = write_transition(
                root / 'rf.gppo-rf.json', 'Approved:GPPO',
                gang_parts['J101'].footprint.sha256,
            )
            for requirement in controller.rf_transition_requirements():
                controller.set_rf_transition(requirement['key'], transition)
            dc_part = read_footprint(dc_footprint, 'Approved:DSub')
            controller.set_dc_connector('J1', {
                'path': dc_part.path, 'library_id': dc_part.library_id,
                'sha256': dc_part.sha256,
                'symbol': {'path': str(dc_symbol), 'library_id': 'Approved:DSub',
                           'sha256': hashlib.sha256(dc_symbol.read_bytes()).hexdigest()},
                'anchor': [50, 50], 'anchor_circle': {'uuid': pin1_uuid, 'center': [50, 50]},
                'rotation': 0, 'side': 'F', 'anchor_accepted': True,
            })
            interposer = interposer_parts['E1'].footprint
            controller.config['interposers']['E1'] = dict(
                interposer.pin(), approved=True, pose=[70, 60, 0], pose_accepted=True,
                symbol={'path': str(approved_symbols), 'library_id': 'Approved:Interposer',
                        'sha256': approved_sha},
            )
            controller.config['mechanical'] = {
                'layer': 'Dwgs.User', 'accepted': True, 'label_offset': [0, 0],
            }
            preview = controller.review()
            self.assertEqual(set(preview['desired']['instances']), {'J101-1', 'J1', 'E1'})
            self.assertEqual(preview['desired']['instances']['J1']['pads'], {'1': 'GND', '2': 'PWR'})
            controller.config['output_dir'] = str(root / 'output')
            result = controller.apply()
            output_board = self.p.LoadBoard(result['board_path'])
            placed = {footprint.GetReference(): footprint for footprint in output_board.GetFootprints()}
            self.assertEqual(set(placed), {'J101-1', 'J1', 'E1'})
            dc_pads = {pad.GetNumber(): pad for pad in placed['J1'].Pads()}
            self.assertEqual((dc_pads['1'].GetPosition().x, dc_pads['1'].GetPosition().y),
                             (50000000, 50000000))
            self.assertEqual(dc_pads['3'].GetNetname(), '')
            import json
            import shutil
            import subprocess
            cli = ([os.environ['KICAD_TEST_APP_RUN'], 'kicad-cli']
                   if os.environ.get('KICAD_TEST_APP_RUN') else [shutil.which('kicad-cli')])
            if cli[0]:
                report_path = root / 'mixed-erc.json'
                checked = subprocess.run(
                    cli + ['sch', 'erc', '--format', 'json', '-o', str(report_path),
                           str(Path(result['board_path']).with_suffix('.kicad_sch'))],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                report = json.loads(report_path.read_text())
                errors = [violation for sheet in report['sheets']
                          for violation in sheet['violations']
                          if violation.get('severity') == 'error']
                self.assertEqual(errors, [])


if __name__ == '__main__': unittest.main()
