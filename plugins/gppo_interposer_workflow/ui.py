"""Native, modeless wx workflow. Library sources are selected, never authored here."""
import json
import math
from pathlib import Path
import wx
import wx.grid as grid
from .controller import HEADERS, PAGES, WorkflowController, library_id


class ReadOnlyTable(grid.GridTableBase):
    def __init__(self, headings, rows):
        super().__init__()
        self.headings, self.rows = tuple(headings), rows
    def GetNumberRows(self): return len(self.rows)
    def GetNumberCols(self): return len(self.headings)
    def GetColLabelValue(self, col): return self.headings[col]
    def GetValue(self, row, col):
        return str(self.rows[row][col]) if col < len(self.rows[row]) else ''
    def IsEmptyCell(self, row, col): return not self.GetValue(row, col)
    def SetValue(self, row, col, value): pass


class DataDialog(wx.Dialog):
    """Virtual table exposes every parsed row including ignored and invalid rows."""
    def __init__(self, parent, table):
        super().__init__(parent, title='Workbook data — ' + table.worksheet, size=(1100, 650),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=f'{table.included_count} included · {table.ignored_count} ignored. Source rows retain worksheet numbering.'), 0, wx.ALL, 10)
        rows = []
        width = max((len(r.values) for r in table.rows), default=0)
        for row in table.rows:
            rows.append((row.source_row, 'Ignored' if row.ignored else 'Included',
                         '; '.join(i.message for i in row.issues), *row.values, *([''] * (width-len(row.values)))))
        mapped = {column: name for name, column in table.mapping.items()}
        headings = ('Source row', 'Status', 'Validation issues') + tuple(mapped.get(i, f'Column {i+1}') for i in range(width))
        view = grid.Grid(self)
        view.SetTable(ReadOnlyTable(headings, rows), True)
        view.EnableEditing(False)
        view.SetColSize(0, 90)
        view.SetColSize(1, 85)
        view.SetColSize(2, 330)
        for col in range(3, len(headings)):
            view.SetColSize(col, 145)
        sizer.Add(view, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)
        self.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)


class PatternPreview(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(360, 260))
        self.name, self.points, self.error = '', {}, ''
        self.clocks = {}
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.paint)
    def show(self, name, points=None, error=''):
        self.name, self.points, self.error = name, points or {}, error
        self.Refresh()
    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)))
        dc.Clear()
        dc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
        dc.DrawText(self.name + ' — location preview', 10, 10)
        if self.error:
            dc.DrawText(self.error, 10, 40)
            return
        if not self.points:
            return
        width, height = self.GetClientSize()
        xs, ys = zip(*self.points.values())
        spanx, spany = max(max(xs)-min(xs), 1), max(max(ys)-min(ys), 1)
        scale = max(.01, min((width-90)/spanx, (height-110)/spany))
        for pin, (x, y) in self.points.items():
            px = int((x-min(xs))*scale+45)
            py = int((y-min(ys))*scale+60)
            dc.SetPen(wx.Pen(wx.Colour(20, 115, 150), 2))
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.DrawCircle(px, py, 12)
            dc.DrawText(str(pin), px-4, py-8)
            angle = math.radians(self.clocks.get(str(pin), 0))
            tx, ty = px + int(25*math.cos(angle)), py - int(25*math.sin(angle))
            dc.DrawLine(px, py, tx, ty)
            for offset in (-.45, .45): dc.DrawLine(tx, ty, tx-int(7*math.cos(angle+offset)), ty+int(7*math.sin(angle+offset)))
            if str(pin) == '1':
                dc.DrawLine(px-18, py, px+18, py)
                dc.DrawLine(px, py-18, px, py+18)
                dc.DrawText('Anchor', px+18, py+8)


class OutlinePreview(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(-1, 220))
        self.contours = {}
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.paint)
    def show(self, contours):
        self.contours = contours
        self.Refresh()
    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)))
        dc.Clear()
        paths = []
        from .core.outline import Segment, _arc
        for role, segments in self.contours.items():
            for item in segments:
                points = [tuple(item['start']), tuple(item['end'])]
                if item.get('mid') is not None:
                    try:
                        center, radius, start, sweep = _arc(Segment(tuple(item['start']), tuple(item['end']), tuple(item['mid'])), 1e-6)
                        points = [(center[0]+radius*math.cos(start+sweep*i/48), center[1]+radius*math.sin(start+sweep*i/48)) for i in range(49)]
                    except ValueError: pass
                paths.append((role, points))
        if not paths: return
        points = [point for role, line in paths for point in line]
        xs, ys = zip(*points)
        width, height = self.GetClientSize()
        scale = min((width-50)/max(max(xs)-min(xs), 1), (height-50)/max(max(ys)-min(ys), 1))
        for role, line in paths:
            dc.SetPen(wx.Pen(wx.Colour(25, 110, 145) if role == 'outer' else wx.Colour(190, 90, 20), 2))
            dc.DrawLines([wx.Point(round(25+(x-min(xs))*scale), round(25+(y-min(ys))*scale)) for x, y in line])


class WorkflowDialog(wx.Dialog):
    def __init__(self, parent=None, controller=None, pcbnew_module=None):
        super().__init__(parent, title='GPPO Interposer Workflow', size=(1140, 800),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.pcbnew = pcbnew_module
        self.controller = controller or WorkflowController(dispatch=wx.CallAfter)
        self.controller.dispatch = wx.CallAfter
        self.current_gang = None
        self._loading_gang = False
        self._closing = False
        self._applying = False
        outer = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(self, label='Saved-state workflow: save the source PCB before analysis. Apply creates a separate project copy.')
        outer.Add(self.status, 0, wx.ALL, 10)
        self.book = wx.Listbook(self, style=wx.LB_LEFT)
        self.pages = []
        for title in PAGES:
            panel = wx.ScrolledWindow(self.book)
            panel.SetScrollRate(8, 8)
            panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
            self.book.AddPage(panel, title)
            self.pages.append(panel)
        self.build_spreadsheet(self.pages[0])
        self.build_layers(self.pages[1])
        self.build_validation(self.pages[2])
        self.build_gangs(self.pages[3])
        self.build_anchors(self.pages[4])
        self.build_outline(self.pages[5])
        self.build_review(self.pages[6])
        self.build_results(self.pages[7])
        outer.Add(self.book, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        footer = wx.BoxSizer(wx.HORIZONTAL)
        self.save_button = self.button(self, 'Save draft', self.save)
        footer.Add(self.save_button, 0, wx.RIGHT, 8)
        footer.AddStretchSpacer()
        footer.Add(self.button(self, 'Previous', lambda e: self.navigate(-1)), 0, wx.RIGHT, 8)
        footer.Add(self.button(self, 'Next', lambda e: self.navigate(1)), 0, wx.RIGHT, 8)
        footer.Add(self.button(self, 'Close', self.close), 0)
        outer.Add(footer, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(outer)
        self.SetMinSize((960, 680))
        self.Bind(wx.EVT_CLOSE, self.close)
        self.restore()
        self.refresh()

    def button(self, parent, label, callback):
        control = wx.Button(parent, label=label)
        control.Bind(wx.EVT_BUTTON, self.guard(callback))
        return control

    def guard(self, callback):
        def run(event):
            try:
                callback(event)
            except Exception as error:
                self.status.SetLabel(str(error))
                wx.MessageBox(str(error), 'Workflow requires attention', wx.OK | wx.ICON_ERROR, self)
            self.refresh()
        return run

    def note(self, parent, text):
        label = wx.StaticText(parent, label=text)
        label.Wrap(820)
        parent.GetSizer().Add(label, 0, wx.ALL | wx.EXPAND, 8)
        return label

    def field(self, parent, label, value='', picker=False, invalidate=True):
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(parent, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        control = wx.TextCtrl(parent, value=str(value))
        row.Add(control, 1, wx.EXPAND)
        if invalidate:
            control.Bind(wx.EVT_TEXT, self.input_changed)
        if picker:
            def choose(event):
                with wx.FileDialog(self, label, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
                    if dialog.ShowModal() == wx.ID_OK:
                        control.SetValue(dialog.GetPath())
            row.Add(self.button(parent, 'Browse…', choose), 0, wx.LEFT, 8)
        parent.GetSizer().Add(row, 0, wx.EXPAND | wx.ALL, 8)
        return control

    def input_changed(self, event):
        self.controller.changed()
        if hasattr(self, 'view_button'): self.refresh()
        event.Skip()

    def build_spreadsheet(self, panel):
        self.note(panel, 'Select the authoritative workbook. Preamble rows precede the chosen header; ignored data rows remain visible in View data.')
        self.workbook_path = self.field(panel, 'Workbook (.xlsx or .csv)', picker=True)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label='Worksheet'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.sheet = wx.Choice(panel)
        row.Add(self.sheet, 1, wx.RIGHT, 12)
        row.Add(wx.StaticText(panel, label='Header row'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.header = wx.SpinCtrl(panel, min=1, max=1048576, initial=1)
        row.Add(self.header)
        row.Add(self.button(panel, 'Load / parse', self.load_workbook), 0, wx.LEFT, 12)
        panel.GetSizer().Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.ignored = self.field(panel, 'Ignored source rows (e.g. 3, 8-10)')
        self.mapper = grid.Grid(panel)
        self.mapper.CreateGrid(len(HEADERS), 2)
        self.mapper.SetColLabelValue(0, 'Required field')
        self.mapper.SetColLabelValue(1, 'Worksheet column (1-based)')
        self.mapper.SetColSize(0, 230)
        self.mapper.SetColSize(1, 230)
        for row, name in enumerate(HEADERS):
            self.mapper.SetCellValue(row, 0, name)
            self.mapper.SetReadOnly(row, 0)
        panel.GetSizer().Add(self.mapper, 1, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.button(panel, 'Apply manual mapping', self.map_columns), 0, wx.RIGHT, 8)
        self.view_button = self.button(panel, 'View data…', self.view_data)
        row.Add(self.view_button)
        self.row_count = wx.StaticText(panel)
        row.Add(self.row_count, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)
        panel.GetSizer().Add(row, 0, wx.ALL, 8)
        for control, event in ((self.header, wx.EVT_SPINCTRL), (self.ignored, wx.EVT_TEXT), (self.sheet, wx.EVT_CHOICE), (self.workbook_path, wx.EVT_TEXT)):
            control.Bind(event, self.invalidate_parse)
        self.mapper.Bind(grid.EVT_GRID_CELL_CHANGED, self.invalidate_parse)

    def invalidate_parse(self, event):
        self.controller.table = None
        self.controller.changed()
        self.refresh()
        event.Skip()

    def load_workbook(self, event):
        wanted = self.sheet.GetStringSelection()
        table = self.controller.load_workbook(self.workbook_path.GetValue(), wanted or None, self.header.GetValue(), self.ignored.GetValue())
        self.sheet.Set([s.name for s in self.controller.workbook.sheets])
        self.sheet.SetStringSelection(table.worksheet)
        self.update_mapping(table)
        self.refresh_layers()
        self.refresh_gangs()

    def update_mapping(self, table):
        for row, header in enumerate(HEADERS):
            self.mapper.SetCellValue(row, 1, str(table.mapping[header]+1) if header in table.mapping else '')

    def map_columns(self, event):
        mapping = {}
        for row, header in enumerate(HEADERS):
            value = self.mapper.GetCellValue(row, 1).strip()
            if value:
                mapping[header] = int(value)-1
        if self.controller.workbook is None:
            self.controller.load_workbook(self.workbook_path.GetValue(), self.sheet.GetStringSelection() or None, self.header.GetValue(), self.ignored.GetValue(), mapping)
        else:
            self.controller.parse(self.sheet.GetStringSelection(), self.header.GetValue(), self.ignored.GetValue(), mapping)
        self.refresh_layers()
        self.refresh_gangs()

    def view_data(self, event):
        if self.controller.view_data_enabled:
            with DataDialog(self, self.controller.table) as dialog:
                dialog.ShowModal()

    def build_layers(self, panel):
        self.note(panel, 'Map architecture names to actual copper layers. New netclass/layer combinations start allowed; an explicit prohibition is a validation error.')
        self.layer_map = grid.Grid(panel)
        self.layer_map.CreateGrid(0, 2)
        self.layer_map.SetColLabelValue(0, 'Architecture layer')
        self.layer_map.SetColLabelValue(1, 'KiCad copper layer')
        self.layer_map.SetColSize(0, 250)
        self.layer_map.SetColSize(1, 250)
        panel.GetSizer().Add(self.layer_map, 1, wx.EXPAND | wx.ALL, 8)
        self.rules = grid.Grid(panel)
        self.rules.CreateGrid(0, 1)
        self.rules.Bind(grid.EVT_GRID_CELL_CHANGED, self.input_changed)
        self.layer_map.Bind(grid.EVT_GRID_CELL_CHANGED, self.input_changed)
        panel.GetSizer().Add(self.rules, 1, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(self.button(panel, 'Accept layer mapping and rules', self.accept_layers), 0, wx.ALL, 8)
        self.new_rules = self.note(panel, '')

    def refresh_layers(self):
        if self.layer_map.GetNumberRows(): self.layer_map.DeleteRows(0, self.layer_map.GetNumberRows())
        names = sorted({r.layer for r in self.controller.table.records}) if self.controller.table else []
        if names: self.layer_map.AppendRows(len(names))
        for row, name in enumerate(names):
            self.layer_map.SetCellValue(row, 0, name)
            self.layer_map.SetReadOnly(row, 0)
            self.layer_map.SetCellValue(row, 1, self.controller.config['layer_mapping'].get(name, ''))
            self.layer_map.SetCellEditor(row, 1, grid.GridCellChoiceEditor(list(self.controller.copper_layers), False))
        if self.rules.GetNumberRows(): self.rules.DeleteRows(0, self.rules.GetNumberRows())
        count = self.rules.GetNumberCols()
        if count: self.rules.DeleteCols(0, count)
        layers = self.controller.copper_layers
        self.rules.AppendCols(len(layers))
        classes = sorted(self.controller.config['layer_rules'])
        if classes: self.rules.AppendRows(len(classes))
        for col, layer in enumerate(layers): self.rules.SetColLabelValue(col, layer)
        for row, netclass in enumerate(classes):
            self.rules.SetRowLabelValue(row, netclass)
            self.rules.SetRowLabelSize(140)
            for col, layer in enumerate(layers):
                self.rules.SetCellEditor(row, col, grid.GridCellBoolEditor())
                self.rules.SetCellRenderer(row, col, grid.GridCellBoolRenderer())
                self.rules.SetCellValue(row, col, '1' if self.controller.config['layer_rules'][netclass].get(layer, True) else '')
        self.new_rules.SetLabel(f'{len(self.controller.new_rules)} new combinations require review.' if self.controller.new_rules else 'Saved permissions retained.')

    def accept_layers(self, event):
        for row in range(self.layer_map.GetNumberRows()):
            self.controller.set_layer_mapping(self.layer_map.GetCellValue(row, 0), self.layer_map.GetCellValue(row, 1))
        for row in range(self.rules.GetNumberRows()):
            for col, layer in enumerate(self.controller.copper_layers):
                self.controller.set_rule(self.rules.GetRowLabelValue(row), layer, self.rules.GetCellValue(row, col) == '1')
        self.status.SetLabel('Layer mapping and permissions accepted. Validation and preview invalidated.')

    def build_validation(self, panel):
        self.note(panel, 'Validation identifies the source worksheet row. Missing parts require librarian resolution; the workflow cannot author them.')
        self.validation = wx.ListCtrl(panel, style=wx.LC_REPORT)
        for col, (name, width) in enumerate((('Severity', 80), ('Source row', 85), ('Code', 150), ('Details', 600))): self.validation.InsertColumn(col, name, width=width)
        panel.GetSizer().Add(self.validation, 1, wx.EXPAND | wx.ALL, 8)
        self.interposer_path = self.field(panel, 'Approved interposer footprint', picker=True)
        self.interposer_ref = self.field(panel, 'Interposer reference', 'U1')
        self.interposer_x = self.field(panel, 'Interposer X (mm)', '0')
        self.interposer_y = self.field(panel, 'Interposer Y (mm)', '0')
        self.interposer_rotation = self.field(panel, 'Interposer orientation (degrees)', '0')
        self.gppo_symbol = self.field(panel, 'Approved two-pin GPPO symbol library', picker=True)
        self.gppo_symbol_id = self.field(panel, 'GPPO symbol library ID', 'Connector:Conn_Coaxial')
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.button(panel, 'Accept library selections', self.accept_libraries), 0, wx.RIGHT, 8)
        row.Add(self.button(panel, 'Run validation', self.validate))
        panel.GetSizer().Add(row, 0, wx.ALL, 8)

    def accept_libraries(self, event):
        path, ref = self.interposer_path.GetValue().strip(), self.interposer_ref.GetValue().strip()
        if not path or not ref: raise ValueError('Select an approved interposer footprint and reference')
        from .core.libraries import read_footprint
        approved = read_footprint(path, library_id(path))
        self.controller.config['interposers'][ref] = {'path': str(Path(path).resolve()), 'sha256': approved.sha256, 'library_id': approved.library_id, 'approved': True, 'pose': [float(self.interposer_x.GetValue()), float(self.interposer_y.GetValue()), float(self.interposer_rotation.GetValue())]}
        from .core.libraries import read_symbol
        symbol = read_symbol(self.gppo_symbol.GetValue(), self.gppo_symbol_id.GetValue())
        self.controller.config['gppo_symbol'] = {'path': self.gppo_symbol.GetValue(), 'library_id': self.gppo_symbol_id.GetValue(), 'sha256': symbol.sha256}
        self.controller.changed()

    def validate(self, event):
        issues = self.controller.validate()
        self.validation.DeleteAllItems()
        for issue in issues:
            index = self.validation.InsertItem(self.validation.GetItemCount(), issue.severity)
            for col, value in enumerate((issue.source_row or '', issue.code, issue.message), 1): self.validation.SetItem(index, col, str(value))
        self.status.SetLabel(f'{sum(i.severity == "error" for i in issues)} blocking validation errors.' if issues else 'Validation passed.')

    def build_gangs(self, panel):
        self.note(panel, 'Each gang retains its own approved part, pattern, numbering, orientation and RF settings. Preview labels are location indices.')
        self.gang_filter = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.gang_filter.SetDescriptiveText('Filter gangs')
        self.gang_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_gang_list())
        panel.GetSizer().Add(self.gang_filter, 0, wx.EXPAND | wx.ALL, 8)
        self.gang_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 115))
        for col, (heading, width) in enumerate((('Gang', 80), ('Count', 65), ('Approved part', 180), ('Pattern', 110), ('Anchor', 100), ('Status', 180))): self.gang_list.InsertColumn(col, heading, width=width)
        self.gang_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.guard(self.select_dashboard_gang))
        panel.GetSizer().Add(self.gang_list, 0, wx.EXPAND | wx.ALL, 8)
        self.gang_choice = wx.Choice(panel)
        self.gang_choice.Bind(wx.EVT_CHOICE, self.guard(self.select_gang))
        panel.GetSizer().Add(self.gang_choice, 0, wx.EXPAND | wx.ALL, 8)
        body = wx.BoxSizer(wx.HORIZONTAL)
        form = wx.Panel(panel)
        form.SetSizer(wx.BoxSizer(wx.VERTICAL))
        self.gang_fields = {}
        for key, label, value, picker in (
            ('part_id', 'Approved GPPO footprint', '', True), ('mpn', 'Project BOM MPN override (if absent)', '', False), ('row_lengths', 'Row lengths', '4', False),
            ('pitch_x', 'Horizontal pitch', '2.54', False), ('pitch_y', 'Vertical pitch', '2.54', False),
            ('row_offsets', 'Per-row offsets', '', False), ('numbering', 'Explicit location traversal', '', False),
            ('rotation', 'Orientation (degrees)', '0', False), ('rf_artifact', 'Approved RF artifact', '', True),
            ('rf_clock', 'RF default clock (degrees)', '0', False), ('rf_overrides', 'Per-location clocks (1:90, 2:180)', '', False)):
            self.gang_fields[key] = self.field(form, label, value, picker)
        self.approved_mpn = wx.StaticText(form, label='Approved library MPN: unavailable')
        form.GetSizer().Add(self.approved_mpn, 0, wx.ALL, 8)
        self.gang_units = wx.Choice(form, choices=['mm', 'mil', 'inch'])
        self.gang_units.SetSelection(0)
        form.GetSizer().Add(self.gang_units, 0, wx.EXPAND | wx.ALL, 8)
        self.gang_side = wx.Choice(form, choices=['Front (F)', 'Back (B)'])
        self.gang_side.SetSelection(0)
        form.GetSizer().Add(self.gang_side, 0, wx.EXPAND | wx.ALL, 8)
        self.mirror = wx.CheckBox(form, label='Explicitly mirror this gang pattern')
        form.GetSizer().Add(self.mirror, 0, wx.ALL, 8)
        body.Add(form, 1, wx.EXPAND)
        self.pattern_view = PatternPreview(panel)
        body.Add(self.pattern_view, 1, wx.EXPAND | wx.ALL, 10)
        panel.GetSizer().Add(body, 1, wx.EXPAND)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.button(panel, 'Accept pattern / refresh preview', self.accept_gang), 0, wx.RIGHT, 8)
        row.Add(self.button(panel, 'Copy settings to selected gangs…', self.copy_gang), 0, wx.RIGHT, 8)
        row.Add(self.button(panel, 'Per-location RF assignments…', self.rf_assignments))
        panel.GetSizer().Add(row, 0, wx.ALL, 8)
        for control in self.gang_fields.values(): control.Bind(wx.EVT_TEXT, self.gang_draft_changed)
        self.mirror.Bind(wx.EVT_CHECKBOX, self.gang_draft_changed)
        self.gang_units.Bind(wx.EVT_CHOICE, self.gang_draft_changed)
        self.gang_side.Bind(wx.EVT_CHOICE, self.gang_draft_changed)

    def gang_draft_changed(self, event):
        if not self._loading_gang:
            self.controller.config.setdefault('drafts', {})[self.current_gang or ''] = {key: control.GetValue() for key, control in self.gang_fields.items()}
            self.controller.changed()
            try:
                self.store_gang()
                self.pattern_view.show(self.current_gang, self.controller.pattern(self.current_gang))
            except Exception as error:
                self.pattern_view.show(self.current_gang or '', error=str(error))
            self.refresh()
        event.Skip()

    def refresh_gangs(self):
        self.refresh_gang_list()
        names = self.controller.gang_names()
        self.gang_choice.Set(list(names))
        if names:
            self.gang_choice.SetStringSelection(self.current_gang if self.current_gang in names else names[0])
            self.select_gang(None)

    def refresh_gang_list(self):
        self.gang_list.DeleteAllItems()
        query = self.gang_filter.GetValue().casefold()
        for name in self.controller.gang_names():
            if query and query not in name.casefold(): continue
            settings = self.controller.gang(name)
            count = sum(record.connector == name for record in self.controller.table.records)
            try:
                points = self.controller.pattern(name)
                status = 'Valid pattern' if len(points) == count else 'Population mismatch'
            except ValueError:
                status = 'Invalid pattern'
            index = self.gang_list.InsertItem(self.gang_list.GetItemCount(), name)
            for col, value in enumerate((count, Path(settings.get('part_id', '')).stem, ' × '.join(map(str, settings['row_lengths'])), 'Accepted' if settings.get('anchor_accepted') else 'Required', status), 1): self.gang_list.SetItem(index, col, str(value))

    def select_dashboard_gang(self, event):
        name = self.gang_list.GetItemText(event.GetIndex())
        self.gang_choice.SetStringSelection(name)
        self.select_gang(None)

    def store_gang(self):
        if not self.current_gang: return
        values = {}
        for key, control in self.gang_fields.items():
            value = control.GetValue().strip()
            if key in ('row_lengths', 'numbering', 'row_offsets'):
                values[key] = [int(v) if key == 'row_lengths' else float(v) if key == 'row_offsets' else v.strip() for v in value.split(',') if v.strip()]
            elif key in ('pitch_x', 'pitch_y', 'rotation', 'rf_clock'):
                values[key] = float(value)
            elif key == 'rf_overrides':
                values[key] = dict((pair.split(':')[0].strip(), float(pair.split(':')[1])) for pair in value.split(',') if pair.strip())
            else: values[key] = value
        values['mirror'] = self.mirror.GetValue()
        values['units'] = self.gang_units.GetStringSelection()
        values['side'] = 'B' if self.gang_side.GetSelection() == 1 else 'F'
        self.controller.set_gang(self.current_gang, values)

    def select_gang(self, event):
        self.current_gang = self.gang_choice.GetStringSelection()
        if not self.current_gang: return
        values = self.controller.gang(self.current_gang)
        self._loading_gang = True
        try:
            for key, control in self.gang_fields.items():
                value = self.controller.config.get('drafts', {}).get(self.current_gang, {}).get(key, values.get(key, ''))
                if isinstance(value, list): value = ', '.join(map(str, value))
                elif isinstance(value, dict): value = ', '.join(f'{k}:{v}' for k, v in value.items())
                control.ChangeValue(str(value))
            self.approved_mpn.SetLabel('Approved library MPN: ' + (values.get('approved_mpn') or 'unavailable'))
            self.gang_fields['mpn'].Enable(not bool(values.get('approved_mpn')))
            self.mirror.SetValue(values['mirror'])
            self.gang_units.SetStringSelection(values.get('units', 'mm'))
            self.gang_side.SetSelection(1 if values.get('side') == 'B' else 0)
        finally: self._loading_gang = False
        self.anchor_gang.Set(list(self.controller.gang_names()))
        self.anchor_gang.SetStringSelection(self.current_gang)
        try:
            points = self.controller.pattern(self.current_gang)
            self.pattern_view.clocks = {str(pin): values.get('rf_overrides', {}).get(str(pin), values.get('rf_clock', 0)) for pin in points}
            self.pattern_view.show(self.current_gang, points)
        except (ValueError, TypeError) as error:
            self.pattern_view.show(self.current_gang, error=str(error))

    def accept_gang(self, event):
        self.store_gang()
        self.controller.config.get('drafts', {}).pop(self.current_gang, None)
        settings = self.controller.gang(self.current_gang)
        self.approved_mpn.SetLabel('Approved library MPN: ' + (settings.get('approved_mpn') or 'unavailable'))
        self.gang_fields['mpn'].Enable(not bool(settings.get('approved_mpn')))
        self.pattern_view.show(self.current_gang, self.controller.pattern(self.current_gang))

    def copy_gang(self, event):
        self.store_gang()
        names = [name for name in self.controller.gang_names() if name != self.current_gang]
        with wx.MultiChoiceDialog(self, 'Select explicit targets; anchors remain independent.', 'Copy gang settings', names) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.controller.copy_gang(self.current_gang, [names[i] for i in dialog.GetSelections()])

    def rf_assignments(self, event):
        self.store_gang()
        if not self.current_gang: raise ValueError('Select a gang')
        settings = self.controller.gang(self.current_gang)
        records = sorted((r for r in self.controller.table.records if r.connector == self.current_gang), key=lambda r: int(r.connector_pin))
        with wx.Dialog(self, title=self.current_gang + ' — RF instance assignments', size=(920, 540), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER) as dialog:
            box = wx.BoxSizer(wx.VERTICAL)
            box.Add(wx.StaticText(dialog, label='Optional per-location overrides. Blank entries use the gang default. Artifact sources remain read-only.'), 0, wx.ALL, 10)
            view = grid.Grid(dialog)
            view.CreateGrid(len(records), 4)
            for col, heading in enumerate(('Location', 'Mapped signal layer', 'Clock override (degrees)', 'Approved artifact path')):
                view.SetColLabelValue(col, heading)
                view.SetColSize(col, (85, 140, 175, 420)[col])
            for row, record in enumerate(records):
                view.SetCellValue(row, 0, record.connector_pin)
                view.SetCellValue(row, 1, self.controller.config['layer_mapping'].get(record.layer, 'UNMAPPED'))
                view.SetReadOnly(row, 0)
                view.SetReadOnly(row, 1)
                view.SetCellValue(row, 2, str(settings.get('rf_overrides', {}).get(record.connector_pin, '')))
                view.SetCellValue(row, 3, settings.get('rf_artifacts', {}).get(record.connector_pin, ''))
            box.Add(view, 1, wx.EXPAND | wx.ALL, 10)
            def browse(event):
                row = view.GetGridCursorRow()
                if row < 0: return
                with wx.FileDialog(dialog, 'Select approved RF artifact', wildcard='RF artifacts (*.json)|*.json', style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as picker:
                    if picker.ShowModal() == wx.ID_OK: view.SetCellValue(row, 3, picker.GetPath())
            box.Add(self.button(dialog, 'Browse artifact for selected location…', browse), 0, wx.ALL, 10)
            box.Add(dialog.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 10)
            dialog.SetSizer(box)
            if dialog.ShowModal() == wx.ID_OK:
                view.SaveEditControlValue()
                clocks, artifacts = {}, {}
                for row, record in enumerate(records):
                    clock, artifact = view.GetCellValue(row, 2).strip(), view.GetCellValue(row, 3).strip()
                    if clock: clocks[record.connector_pin] = float(clock)
                    if artifact: artifacts[record.connector_pin] = artifact
                self.controller.set_gang(self.current_gang, {'rf_overrides': clocks, 'rf_artifacts': artifacts})
                self.select_gang(None)

    def build_anchors(self, panel):
        self.note(panel, 'Use KiCad File → Import → Graphics to import the whole DXF to a user layer first. Select one native circle in the PCB canvas, then capture its center. Numeric anchors are also supported.')
        self.anchor_gang = wx.Choice(panel)
        panel.GetSizer().Add(self.anchor_gang, 0, wx.EXPAND | wx.ALL, 8)
        self.user_layer = self.field(panel, 'Imported mechanical user layer', 'Dwgs.User')
        self.label_offset_x = self.field(panel, 'Label offset X from circle (mm)', '0')
        self.label_offset_y = self.field(panel, 'Label offset Y from circle (mm)', '0')
        self.anchor_x = self.field(panel, 'Location 1 X (mm)', '0')
        self.anchor_y = self.field(panel, 'Location 1 Y (mm)', '0')
        panel.GetSizer().Add(self.button(panel, 'Use selected circle center', self.capture_circle), 0, wx.ALL, 8)
        panel.GetSizer().Add(self.button(panel, 'Accept anchor', self.accept_anchor), 0, wx.ALL, 8)
        self.anchor_status = self.note(panel, 'No anchor captured.')
        panel.GetSizer().Add(self.button(panel, 'Inspect saved mechanical population', self.inspect_mechanical), 0, wx.ALL, 8)
        self.mechanical_list = wx.ListCtrl(panel, style=wx.LC_REPORT, size=(-1, 140))
        for col, (name, width) in enumerate((('Physical key', 120), ('Center X (mm)', 130), ('Center Y (mm)', 130), ('Source circle UUID', 280), ('Source text UUID', 280))): self.mechanical_list.InsertColumn(col, name, width=width)
        panel.GetSizer().Add(self.mechanical_list, 0, wx.EXPAND | wx.ALL, 8)
        self.hole_path = self.field(panel, 'Approved mounting-hole footprint (optional)', picker=True)
        self.hole_ref = self.field(panel, 'Mounting-hole reference', 'H1')
        panel.GetSizer().Add(self.button(panel, 'Add mounting hole at this anchor', self.add_hole), 0, wx.ALL, 8)

    def capture_circle(self, event):
        if self.pcbnew is None: raise ValueError('Open this workflow from the KiCad PCB Editor')
        selected = list(self.pcbnew.GetCurrentSelection())
        if len(selected) != 1 or not isinstance(selected[0], self.pcbnew.PCB_SHAPE) or selected[0].GetShape() != self.pcbnew.SHAPE_T_CIRCLE:
            raise ValueError('Select exactly one imported native circle in the PCB canvas')
        circle = selected[0]
        if self.pcbnew.GetBoard().GetLayerName(circle.GetLayer()) != self.user_layer.GetValue():
            raise ValueError('Circle must belong to the selected mechanical user layer')
        center = circle.GetCenter()
        self.anchor_x.ChangeValue(str(center.x/1e6))
        self.anchor_y.ChangeValue(str(center.y/1e6))
        self._circle_draft = {'uuid': circle.m_Uuid.AsString(), 'center_nm': [center.x, center.y]}
        self.anchor_status.SetLabel('Draft circle center captured; accept to persist it. Board unchanged.')

    def accept_anchor(self, event):
        gang = self.anchor_gang.GetStringSelection()
        if not gang: raise ValueError('Select a gang')
        if hasattr(self, '_circle_draft') and self.pcbnew:
            drawings = {s.m_Uuid.AsString(): s for s in self.pcbnew.GetBoard().GetDrawings()}
            source = drawings.get(self._circle_draft['uuid'])
            if source is None or [source.GetCenter().x, source.GetCenter().y] != self._circle_draft['center_nm']:
                raise ValueError('Circle changed; capture its center again')
        settings = self.controller.gang(gang)
        scale = {'mm': 1, 'mil': .0254, 'inch': 25.4}[settings.get('units', 'mm')]
        self.controller.set_gang(gang, {'anchor': [float(self.anchor_x.GetValue())/scale, float(self.anchor_y.GetValue())/scale], 'anchor_accepted': True})
        self.controller.config['mechanical']['layer'] = self.user_layer.GetValue()
        self.controller.config['mechanical']['label_offset'] = [float(self.label_offset_x.GetValue()), float(self.label_offset_y.GetValue())]
        self.controller.config['mechanical']['accepted'] = True
        self.controller.changed()
        self.anchor_status.SetLabel(f'{gang} anchor accepted. Exact mechanical identities are checked during review.')

    def inspect_mechanical(self, event):
        backend = self.controller.backend
        if backend is None or not hasattr(backend, 'call'): raise ValueError('KiCad native backend is unavailable')
        layer = self.user_layer.GetValue()
        offset = [float(self.label_offset_x.GetValue()), float(self.label_offset_y.GetValue())]
        def complete(result):
            if self._closing: return
            self.mechanical_list.DeleteAllItems()
            for location in result['locations']:
                row = self.mechanical_list.InsertItem(self.mechanical_list.GetItemCount(), location['key'])
                for col, value in enumerate((*location['center'], location['circle_uuid'], location['text_uuid']), 1): self.mechanical_list.SetItem(row, col, str(value))
            errors = list(result.get('errors', []))
            if self.controller.table:
                from .core.geometry import validate_mechanical_population
                try: validate_mechanical_population(self.controller.table.records, [p['key'] for p in result['locations']])
                except ValueError as error: errors.append(str(error))
            self.anchor_status.SetLabel('Blocked: ' + '; '.join(errors) if errors else f"{len(result['locations'])} exact mechanical identities matched. Review their circle/text associations.")
            self.anchor_status.Wrap(820)
            self.refresh()
        self.controller.run_async(lambda: backend.call('inspect', board_path=str(self.controller.board_path), source_layer=layer, label_offset=offset), complete, self.failed)
        self.refresh()

    def add_hole(self, event):
        from .core.libraries import read_footprint
        path = self.hole_path.GetValue()
        part = read_footprint(path, library_id(path), allow_mechanical=True)
        reference = self.hole_ref.GetValue().strip()
        if not reference: raise ValueError('Mounting-hole reference is required')
        holes = self.controller.config['mounting_holes']
        if any(h['reference'] == reference for h in holes): raise ValueError('Mounting-hole reference already exists')
        holes.append({'reference': reference, 'path': str(Path(path).resolve()), 'sha256': part.sha256,
                      'library_id': part.library_id, 'pose': [float(self.anchor_x.GetValue()), float(self.anchor_y.GetValue()), 0]})
        self.controller.changed()
        self.anchor_status.SetLabel(f'{reference} added as board-only, excluded from BOM and position files.')

    def build_outline(self, panel):
        self.outline_enable = wx.CheckBox(panel, label='Explicitly enable managed board outline')
        panel.GetSizer().Add(self.outline_enable, 0, wx.ALL, 8)
        self.note(panel, 'Select imported native lines/arcs in the PCB canvas. Capture outer boundary and optional center cutout separately. Exact closure and ownership are checked before Apply; designer Edge.Cuts stay untouched.')
        panel.GetSizer().Add(self.button(panel, 'Capture selected outer boundary', lambda e: self.capture_outline('outer')), 0, wx.ALL, 8)
        panel.GetSizer().Add(self.button(panel, 'Capture selected center cutout', lambda e: self.capture_outline('cutout')), 0, wx.ALL, 8)
        self.outline_status = self.note(panel, 'Managed outline disabled. No Edge.Cuts changes requested.')
        self.outline_view = OutlinePreview(panel)
        panel.GetSizer().Add(self.outline_view, 1, wx.EXPAND | wx.ALL, 8)
        self.outline_preview = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 95))
        panel.GetSizer().Add(self.outline_preview, 0, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(self.button(panel, 'Validate / accept outline selection', self.accept_outline), 0, wx.ALL, 8)
        self.outline_enable.Bind(wx.EVT_CHECKBOX, self.guard(self.toggle_outline))

    def toggle_outline(self, event):
        self.controller.config['outline']['enabled'] = self.outline_enable.GetValue()
        self.controller.changed()
        self.outline_status.SetLabel('Enabled: capture sources and validate during comparison.' if self.outline_enable.GetValue() else 'Managed outline disabled.')

    def capture_outline(self, role):
        if not self.pcbnew: raise ValueError('Open this workflow in the PCB Editor')
        selected = list(self.pcbnew.GetCurrentSelection())
        if not selected: raise ValueError('Select imported lines/arcs first')
        if any(not isinstance(item, self.pcbnew.PCB_SHAPE) for item in selected): raise ValueError('Outline selection must contain only native graphics')
        if any(self.pcbnew.GetBoard().GetLayerName(item.GetLayer()) != self.user_layer.GetValue() for item in selected): raise ValueError('Outline sources must be on the selected mechanical user layer')
        supported = (self.pcbnew.SHAPE_T_SEGMENT, self.pcbnew.SHAPE_T_ARC)
        if any(item.GetShape() not in supported for item in selected): raise ValueError('Select only line segments and arcs; split full circles into arcs')
        self.controller.config['outline'][role + '_uuids'] = [item.m_Uuid.AsString() for item in selected]
        segments = []
        for item in selected:
            segment = {'start': [item.GetStart().x/1e6, item.GetStart().y/1e6], 'end': [item.GetEnd().x/1e6, item.GetEnd().y/1e6], 'source_uuid': item.m_Uuid.AsString(), 'owner': 'board'}
            if item.GetShape() == self.pcbnew.SHAPE_T_ARC: segment['mid'] = [item.GetArcMid().x/1e6, item.GetArcMid().y/1e6]
            segments.append(segment)
        contours = self.controller.config['outline'].setdefault('source_preview', {})
        contours[role] = segments
        self.outline_view.show(contours)
        self.controller.changed()
        self.outline_preview.ChangeValue(json.dumps(self.controller.config['outline'], indent=2))

    def accept_outline(self, event):
        self.toggle_outline(event)
        if self.outline_enable.GetValue() and not self.controller.config['outline'].get('outer_uuids'):
            raise ValueError('Select a complete outer boundary')
        if self.outline_enable.GetValue():
            from .core.outline import validate_outline
            contours = self.controller.config['outline'].get('source_preview', {})
            validate_outline(contours.get('outer', []), [contours['cutout']] if contours.get('cutout') else [])
        self.outline_status.SetLabel('Contour topology validated. Saved source UUIDs and ownership are rechecked during comparison.')

    def build_review(self, panel):
        self.note(panel, 'Compare complete desired state against saved board ownership. Conflicts, stale inputs and affected designer routing block Apply.')
        self.diff_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for col, (name, width) in enumerate((('Gang', 90), ('Connector / file', 280), ('Change', 150), ('Status', 230))):
            self.diff_list.InsertColumn(col, name, width=width)
        self.diff_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.show_change)
        panel.GetSizer().Add(self.diff_list, 1, wx.EXPAND | wx.ALL, 8)
        self.review_view = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        panel.GetSizer().Add(self.review_view, 1, wx.EXPAND | wx.ALL, 8)
        self.review_button = self.button(panel, 'Compare changes', self.compare)
        panel.GetSizer().Add(self.review_button, 0, wx.ALL, 8)
        self.output_dir = self.field(panel, 'New output project directory', invalidate=False)
        self.confirm_review = wx.CheckBox(panel, label='I reviewed these changes and the destination project copy')
        panel.GetSizer().Add(self.confirm_review, 0, wx.ALL, 8)
        self.apply_button = self.button(panel, 'Apply reviewed changes to project copy', self.apply)
        panel.GetSizer().Add(self.apply_button, 0, wx.ALL, 8)
        self.confirm_review.Bind(wx.EVT_CHECKBOX, lambda e: self.refresh())

    def build_results(self, panel):
        self.note(panel, 'Apply saves a separate controlled project copy, a reconciliation report and a recovery journal. Keep the source project separate when opening the result.')
        self.results = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        panel.GetSizer().Add(self.results, 1, wx.EXPAND | wx.ALL, 8)
        self.journal = self.field(panel, 'Recovery journal', picker=True)
        panel.GetSizer().Add(self.button(panel, 'Check / recover closed output project', self.recover), 0, wx.ALL, 8)
        self.abort_button = self.button(panel, 'Cancel analysis', self.abort)
        panel.GetSizer().Add(self.abort_button, 0, wx.ALL, 8)

    def compare(self, event):
        self.save(None)
        self.confirm_review.SetValue(False)
        self.status.SetLabel('Analysing saved project and library inputs…')
        def complete(preview):
            if self._closing: return
            self.render_changes(preview)
            self.status.SetLabel('Comparison complete. Review conflicts and changes before Apply.')
            self.refresh()
        self.controller.run_async(self.controller.review, complete, self.failed)
        self.refresh()

    def render_changes(self, preview):
        self.diff_list.DeleteAllItems()
        self._review_details = []
        conflicts = preview.get('conflicts', [])
        for change in preview.get('changes', []):
            key = change.get('key', '')
            instance = preview.get('desired', {}).get('instances', {}).get(key, {})
            prior = preview.get('registry', {}).get('instances', {}).get(key, {}).get('spec', {})
            affected = [c for c in conflicts if c.get('key') == key]
            row = self.diff_list.InsertItem(self.diff_list.GetItemCount(), instance.get('gang', prior.get('gang', '')))
            for col, value in enumerate((key, change.get('kind', ''), 'BLOCKED' if affected else 'Ready'), 1): self.diff_list.SetItem(row, col, str(value))
            self._review_details.append({'change': change, 'before': prior, 'after': instance, 'conflicts': affected})
        for conflict in conflicts:
            row = self.diff_list.InsertItem(self.diff_list.GetItemCount(), '')
            for col, value in enumerate((conflict.get('key', conflict.get('uuid', 'Project')), conflict.get('kind', ''), 'BLOCKED'), 1): self.diff_list.SetItem(row, col, str(value))
            self._review_details.append(conflict)
        self.review_view.ChangeValue(f"{preview.get('status', '')}: {len(preview.get('changes', []))} changes, {len(conflicts)} conflicts.\nSelect a connector or file for before/after detail.")

    def show_change(self, event):
        index = event.GetIndex()
        if 0 <= index < len(self._review_details):
            self.review_view.ChangeValue(json.dumps(self._review_details[index], indent=2, default=str))

    def apply(self, event):
        if not self.confirm_review.GetValue(): raise ValueError('Review and explicitly accept the current change list first')
        self.controller.config['output_dir'] = self.output_dir.GetValue()
        # Destination is operational, not a desired-state input; backend verifies it separately.
        self.status.SetLabel('Applying the reviewed delta to a separate project copy…')
        self._applying = True
        self.controller.run_async(self.controller.apply, self.applied, self.failed)
        self.refresh()

    def applied(self, result):
        self._applying = False
        if self._closing: return
        self.results.ChangeValue(json.dumps(result, indent=2, default=str))
        self.book.SetSelection(7)
        self.status.SetLabel('Project copy saved. Review the reconciliation report and validation results.')
        if isinstance(result, dict) and result.get('journal'): self.journal.ChangeValue(str(result['journal']))
        self.refresh()

    def failed(self, error):
        self._applying = False
        if self._closing: return
        self.status.SetLabel(str(error))
        self.results.ChangeValue('Operation stopped safely:\n' + str(error))
        self.refresh()

    def recover(self, event):
        from .recovery import recover
        with wx.MessageDialog(self, 'Close the output project in all KiCad editors before recovery. Recover the selected journal now?', 'Recover output project', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) as dialog:
            if dialog.ShowModal() == wx.ID_YES:
                self._applying = True
                self.controller.run_async(lambda: recover(self.journal.GetValue(), closed_project=True), self.applied, self.failed)
                self.refresh()

    def abort(self, event):
        self.controller.cancel()
        self.status.SetLabel('Analysis cancelled; pending results will be discarded.')

    def save(self, event):
        self.controller.save()
        self.status.SetLabel('Project draft saved.')

    def restore(self):
        cfg = self.controller.config
        source = cfg.get('workbook', {})
        self.workbook_path.ChangeValue(source.get('path', ''))
        self.header.SetValue(source.get('header_row', 1))
        self.ignored.ChangeValue(', '.join(map(str, source.get('ignored_rows', []))))
        source_path = source.get('path', '')
        if source_path and not Path(source_path).exists() and source.get('snapshot') and self.controller.board_path:
            source_path = str(self.controller.board_path.parent / source['snapshot'])
        if source_path and Path(source_path).exists():
            self.workbook_path.ChangeValue(source_path)
            try:
                table = self.controller.load_workbook(source_path, source.get('worksheet'), source.get('header_row', 1), source.get('ignored_rows', []), source.get('mapping'))
                self.sheet.Set([s.name for s in self.controller.workbook.sheets])
                self.sheet.SetStringSelection(table.worksheet)
                self.update_mapping(table)
            except Exception as error: self.status.SetLabel('Saved workbook requires attention: ' + str(error))
        symbol = cfg.get('gppo_symbol', {})
        self.gppo_symbol.ChangeValue(symbol.get('path', ''))
        self.gppo_symbol_id.ChangeValue(symbol.get('library_id', 'Connector:Conn_Coaxial'))
        if cfg.get('interposers'):
            ref, part = next(iter(cfg['interposers'].items()))
            self.interposer_ref.ChangeValue(ref)
            self.interposer_path.ChangeValue(part.get('path', ''))
            pose = part.get('pose', [0, 0, 0])
            self.interposer_x.ChangeValue(str(pose[0]))
            self.interposer_y.ChangeValue(str(pose[1]))
            self.interposer_rotation.ChangeValue(str(pose[2]))
        self.output_dir.ChangeValue(cfg.get('output_dir', ''))
        self.outline_enable.SetValue(cfg['outline'].get('enabled', False))
        self.outline_view.show(cfg['outline'].get('source_preview', {}))
        self.user_layer.ChangeValue(cfg['mechanical'].get('layer', 'Dwgs.User'))
        offset = cfg['mechanical'].get('label_offset', [0, 0])
        self.label_offset_x.ChangeValue(str(offset[0]))
        self.label_offset_y.ChangeValue(str(offset[1]))
        self.refresh_layers()
        self.refresh_gangs()

    def refresh(self):
        self.view_button.Enable(self.controller.view_data_enabled)
        table = self.controller.table
        self.row_count.SetLabel(f'{table.included_count} included / {table.ignored_count} ignored' if table else 'Parse to inspect data')
        self.review_button.Enable(not self.controller.busy)
        self.apply_button.Enable(not self.controller.busy and self.controller.preview is not None and not self.controller.preview.get('conflicts') and self.confirm_review.GetValue())
        self.abort_button.Enable(self.controller.busy and not self._applying)
        self.book.Enable(not self._applying)

    def navigate(self, step):
        self.book.SetSelection(max(0, min(len(PAGES)-1, self.book.GetSelection()+step)))

    def close(self, event):
        if self.controller.busy:
            self.status.SetLabel('Wait for the current operation, or cancel analysis before closing.')
            if isinstance(event, wx.CloseEvent): event.Veto()
            return
        self._closing = True
        self.controller.cancel()
        self.Destroy()
