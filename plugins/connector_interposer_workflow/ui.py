"""Native, modeless wx workflow. Library sources are selected, never authored here."""
import json
import math
from pathlib import Path
import wx
import wx.grid as grid
from .controller import HEADERS, PAGES, WorkflowController, library_id
from .core.library_catalog import KiCadLibraryCatalog

WORKBOOK_SUFFIXES = ('.xlsx', '.csv', '.txt')
SPREADSHEET_PREVIEW_ROWS = 5


class AssignmentRenderer(grid.GridCellStringRenderer):
    """Keep assignment cells visibly interactive when their editor is inactive."""
    def __init__(self, arrow=True):
        super().__init__()
        self.arrow = arrow

    def Clone(self):
        return AssignmentRenderer(self.arrow)

    def Draw(self, table, attr, dc, rect, row, column, selected):
        background = table.GetSelectionBackground() if selected else attr.GetBackgroundColour()
        foreground = table.GetSelectionForeground() if selected else attr.GetTextColour()
        dc.SetPen(wx.Pen(background))
        dc.SetBrush(wx.Brush(background))
        dc.DrawRectangle(rect)
        dc.SetFont(attr.GetFont())
        dc.SetTextForeground(foreground)
        value = table.GetCellValue(row, column)
        text_width = max(0, rect.width - (28 if self.arrow else 12))
        clipped = wx.Control.Ellipsize(value, dc, wx.ELLIPSIZE_END, text_width)
        _, text_height = dc.GetTextExtent(clipped)
        dc.DrawText(clipped, rect.x + 6, rect.y + max(1, (rect.height - text_height) // 2))
        if self.arrow:
            center_x = rect.GetRight() - 12
            center_y = rect.y + rect.height // 2
            dc.SetPen(wx.Pen(foreground))
            dc.SetBrush(wx.Brush(foreground))
            dc.DrawPolygon((wx.Point(center_x - 4, center_y - 2),
                            wx.Point(center_x + 4, center_y - 2),
                            wx.Point(center_x, center_y + 3)))
        dc.SetPen(wx.Pen(wx.Colour(95, 125, 150), 2))
        dc.DrawLine(rect.x, rect.GetBottom() - 1, rect.GetRight(), rect.GetBottom() - 1)


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
    """Virtual table exposes every included parsed row in workflow-field order."""
    def __init__(self, parent, table):
        super().__init__(parent, title='Workbook data — ' + table.worksheet, size=(1100, 650),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(
            self,
            label=(f'{table.included_count} nets parsed. Excluded rows are omitted from this table; '
                   'Source row retains worksheet numbering.'),
        ), 0, wx.ALL, 10)
        rows = [
            (row.source_row, *(row.values[table.mapping[name]] if table.mapping[name] < len(row.values) else ''
                               for name in HEADERS))
            for row in table.rows if not row.ignored
        ]
        headings = ('Source row',) + tuple(HEADERS)
        view = grid.Grid(self)
        view.SetTable(ReadOnlyTable(headings, rows), True)
        view.EnableEditing(False)
        view.AutoSizeColumns(False)
        sizer.Add(view, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(self.CreateButtonSizer(wx.CLOSE), 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)
        self.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self._size_for_table(view, len(rows))

    def _size_for_table(self, view, row_count):
        display_index = wx.Display.GetFromWindow(self)
        area = (wx.Display(display_index).GetClientArea()
                if display_index != wx.NOT_FOUND else wx.GetClientDisplayRect())
        maximum_width = min(1600, max(480, int(area.width * .9)))
        maximum_height = min(900, max(360, int(area.height * .9)))
        scrollbar = max(0, wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X))
        desired_width = (view.GetRowLabelSize()
                         + sum(view.GetColSize(column) for column in range(view.GetNumberCols()))
                         + scrollbar + 34)
        visible_rows = min(max(row_count, 1), 24)
        desired_height = view.GetColLabelSize() + visible_rows * view.GetDefaultRowSize() + 125
        width = max(min(760, maximum_width), min(desired_width, maximum_width))
        height = max(min(450, maximum_height), min(desired_height, maximum_height))
        self.SetMinSize((min(700, maximum_width), min(420, maximum_height)))
        self.SetSize((width, height))
        self.CentreOnParent()


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


class PartPreview(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(300, 260))
        self.items = ()
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.paint)

    def show_footprint(self, footprint):
        self.items = tuple((pad.position[0], pad.position[1], pad.size[0], pad.size[1], pad.number)
                           for pad in footprint.pads)
        self.Refresh()

    def show_symbol(self, symbol):
        self.items = tuple((pin[1], pin[2], .8, .8, pin[0]) for pin in symbol.pin_numbers)
        self.Refresh()

    def clear(self):
        self.items = ()
        self.Refresh()

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)))
        dc.Clear()
        if not self.items:
            dc.DrawText('Select a part to preview its pads or pins.', 12, 12)
            return
        width, height = self.GetClientSize()
        left = min(x-w/2 for x, y, w, h, label in self.items)
        right = max(x+w/2 for x, y, w, h, label in self.items)
        top = min(y-h/2 for x, y, w, h, label in self.items)
        bottom = max(y+h/2 for x, y, w, h, label in self.items)
        scale = min((width-50)/max(right-left, 1), (height-50)/max(bottom-top, 1))
        dc.SetPen(wx.Pen(wx.Colour(20, 115, 150), 1))
        dc.SetBrush(wx.Brush(wx.Colour(120, 190, 210)))
        for x, y, w, h, label in self.items:
            px = round(25+(x-w/2-left)*scale)
            py = round(25+(y-h/2-top)*scale)
            dc.DrawRectangle(px, py, max(2, round(w*scale)), max(2, round(h*scale)))


class LibraryPartPickerDialog(wx.Dialog):
    """Native browser over KiCad's loaded project and global libraries."""
    def __init__(self, parent, catalog, title, current_id='', allow_mechanical=False,
                 require_two_pin=True):
        super().__init__(parent, title=title, size=(1000, 650),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.catalog, self.current_id, self.allow_mechanical = catalog, current_id, allow_mechanical
        self.require_two_pin = require_two_pin
        self.library_entries = catalog.libraries()
        self.loaded_parts, self.filtered_parts = (), ()
        self.selection = self.approved = None
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label='Choose a read-only part from the libraries currently loaded by KiCad.'), 0, wx.ALL, 10)
        self.search = wx.SearchCtrl(self)
        self.search.SetDescriptiveText('Filter parts in the selected library')
        self.search.Bind(wx.EVT_TEXT, self._filter)
        outer.Add(self.search, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        body = wx.BoxSizer(wx.HORIZONTAL)
        self.libraries = wx.ListBox(self, choices=[f'{entry.nickname}  [{entry.scope}]' for entry in self.library_entries])
        self.parts = wx.ListBox(self)
        self.libraries.Bind(wx.EVT_LISTBOX, self._load_library)
        self.parts.Bind(wx.EVT_LISTBOX, self._select_part)
        self.parts.Bind(wx.EVT_LISTBOX_DCLICK, self._accept_double_click)
        body.Add(self.libraries, 1, wx.EXPAND | wx.RIGHT, 8)
        body.Add(self.parts, 1, wx.EXPAND | wx.RIGHT, 8)
        detail = wx.BoxSizer(wx.VERTICAL)
        self.preview = PartPreview(self)
        detail.Add(self.preview, 1, wx.EXPAND | wx.BOTTOM, 8)
        self.details = wx.StaticText(self, label='')
        self.details.Wrap(300)
        detail.Add(self.details, 0, wx.EXPAND)
        body.Add(detail, 1, wx.EXPAND)
        outer.Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        self.status = wx.StaticText(self, label='')
        self.status.Wrap(900)
        outer.Add(self.status, 0, wx.EXPAND | wx.ALL, 10)
        buttons = wx.StdDialogButtonSizer()
        self.ok = wx.Button(self, wx.ID_OK)
        self.ok.Enable(False)
        buttons.AddButton(self.ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL))
        buttons.Realize()
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(outer)
        self.SetMinSize((820, 520))
        if self.library_entries:
            current_nickname = current_id.split(':', 1)[0] if ':' in current_id else ''
            index = next((i for i, entry in enumerate(self.library_entries) if entry.nickname == current_nickname), 0)
            self.libraries.SetSelection(index)
            self._load_library(None)
        else:
            self.status.SetLabel('No supported native libraries are loaded in this project.')

    def _load_library(self, event):
        index = self.libraries.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self.selection = None
        self.ok.Enable(False)
        self.preview.clear()
        self.details.SetLabel('')
        self.status.SetLabel('Loading ' + self.library_entries[index].nickname + '…')
        try:
            self.loaded_parts = self.catalog.parts(self.library_entries[index].nickname)
            self.status.SetLabel(f'{len(self.loaded_parts)} parts loaded from {self.library_entries[index].scope.lower()} libraries.')
            self._filter(None)
            if self.current_id:
                match = next((i for i, part in enumerate(self.filtered_parts) if part.library_id == self.current_id), None)
                if match is not None:
                    self.parts.SetSelection(match)
                    self._select_part(None)
        except Exception as error:
            self.loaded_parts = self.filtered_parts = ()
            self.parts.Set([])
            self.status.SetLabel(str(error))

    def _filter(self, event):
        query = self.search.GetValue().casefold()
        self.filtered_parts = tuple(part for part in self.loaded_parts
                                    if not query or query in part.name.casefold() or query in part.library_id.casefold())
        self.parts.Set([part.name for part in self.filtered_parts])

    def _select_part(self, event):
        index = self.parts.GetSelection()
        if index == wx.NOT_FOUND:
            return
        part = self.filtered_parts[index]
        try:
            if self.catalog.kind == 'footprint':
                from .core.libraries import read_footprint
                approved = read_footprint(part.path, part.library_id, allow_mechanical=self.allow_mechanical)
                self.preview.show_footprint(approved)
                description = f'{len(approved.pad_numbers)} electrical pads'
                digest = approved.sha256
            else:
                from .core.libraries import read_symbol
                approved = read_symbol(part.path, part.library_id,
                                       require_two_pin=self.require_two_pin)
                self.preview.show_symbol(approved)
                description = f'{len(approved.pin_numbers)} pins'
                digest = approved.sha256
            self.selection = {'path': str(part.path), 'library_id': part.library_id, 'sha256': digest}
            self.approved = approved
            self.details.SetLabel(f'{part.library_id}\n{part.scope} library\n{description}\nSHA-256 {digest[:16]}…')
            self.status.SetLabel('Compatible read-only library part selected.')
            self.ok.Enable(True)
        except Exception as error:
            self.selection = None
            self.approved = None
            self.preview.clear()
            self.details.SetLabel(part.library_id)
            self.status.SetLabel(str(error))
            self.ok.Enable(False)

    def _accept_double_click(self, event):
        self._select_part(event)
        if self.selection:
            self.EndModal(wx.ID_OK)


class LibrarySelectionCtrl(wx.Panel):
    """Display a library-qualified identity while retaining its pinned source internally."""
    def __init__(self, parent, owner, label, kind, allow_mechanical=False, on_change=None):
        super().__init__(parent)
        self.owner, self.label, self.kind = owner, label, kind
        self.allow_mechanical, self.on_change = allow_mechanical, on_change
        self.selection = {}
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.display = wx.TextCtrl(self, style=wx.TE_READONLY)
        row.Add(self.display, 1, wx.EXPAND)
        button = wx.Button(self, label='Choose from KiCad libraries…')
        button.Bind(wx.EVT_BUTTON, owner.guard(self.choose))
        row.Add(button, 0, wx.LEFT, 8)
        self.SetSizer(row)

    def choose(self, event):
        if self.owner.pcbnew is None or self.owner.controller.board_path is None:
            raise ValueError('Open and save the PCB before choosing library parts')
        catalog = KiCadLibraryCatalog(self.owner.pcbnew, self.owner.controller.board_path.parent, self.kind)
        with LibraryPartPickerDialog(self.owner, catalog, self.label,
                                     self.selection.get('library_id', ''), self.allow_mechanical) as dialog:
            if dialog.ShowModal() == wx.ID_OK and dialog.selection:
                self.set_selection(dialog.selection)
                if self.on_change:
                    self.on_change()

    def set_selection(self, selection):
        self.selection = dict(selection or {})
        self.display.ChangeValue(self.selection.get('library_id', ''))

    def GetValue(self):
        return self.selection.get('path', '')

    def GetLibraryId(self):
        return self.selection.get('library_id', '')

    def GetSelectionSpec(self):
        return dict(self.selection)

    def ChangeValue(self, value):
        if isinstance(value, dict):
            self.set_selection(value)
        else:
            self.set_selection({'path': str(value), 'library_id': library_id(value) if value else ''})


class SymbolAssignmentCtrl(wx.Panel):
    """Compact table cell that opens the native loaded-library symbol picker."""
    def __init__(self, parent, owner, title, require_two_pin, on_change):
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self.owner, self.title = owner, title
        self.require_two_pin, self.on_change = require_two_pin, on_change
        self.selection = {}
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.display = wx.StaticText(self, label='Choose a symbol', style=wx.ST_ELLIPSIZE_END)
        row.Add(self.display, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 7)
        self.select = wx.Button(self, label='Select…', size=(76, -1))
        self.select.Bind(wx.EVT_BUTTON, owner.guard(self.choose))
        row.Add(self.select, 0, wx.ALL, 4)
        self.SetSizer(row)
        self.set_selection({})

    def choose(self, event):
        if self.owner.pcbnew is None or self.owner.controller.board_path is None:
            raise ValueError('Open and save the PCB before choosing library parts')
        catalog = KiCadLibraryCatalog(
            self.owner.pcbnew, self.owner.controller.board_path.parent, 'symbol',
        )
        with LibraryPartPickerDialog(
            self.owner, catalog, self.title, self.selection.get('library_id', ''),
            require_two_pin=self.require_two_pin,
        ) as dialog:
            if dialog.ShowModal() == wx.ID_OK and dialog.selection:
                self.set_selection(dialog.selection)
                self.on_change(dict(dialog.selection), dialog.approved)

    def set_selection(self, selection):
        self.selection = dict(selection or {})
        self.display.SetLabel(self.selection.get('library_id', 'Choose a symbol'))
        assigned = bool(self.selection.get('library_id'))
        self.SetBackgroundColour(wx.Colour(220, 235, 250) if assigned else wx.Colour(255, 244, 214))
        self.Refresh()


class FootprintAssignmentCtrl(wx.Panel):
    """Filtered footprint dropdown with table-cell interaction styling."""
    def __init__(self, parent, on_change):
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self.on_change, self.options = on_change, []
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.choice = wx.Choice(self)
        self.choice.Bind(wx.EVT_CHOICE, self._changed)
        row.Add(self.choice, 1, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(row)
        self.set_options((), {})

    def set_options(self, options, selected):
        self.options = [dict(option) for option in options]
        selected = dict(selected or {})
        if (selected.get('library_id') and selected.get('path') and
                not any(option.get('library_id') == selected['library_id'] for option in self.options)):
            self.options.append(selected)
            self.options.sort(key=lambda option: option['library_id'])
        labels = ['Choose an allowed footprint'] + [option['library_id'] for option in self.options]
        self.choice.Set(labels)
        selected_id = selected.get('library_id', '')
        index = next((i + 1 for i, option in enumerate(self.options)
                      if option['library_id'] == selected_id), 0)
        self.choice.SetSelection(index)
        self._style(index > 0)

    def selection(self):
        index = self.choice.GetSelection()
        return dict(self.options[index - 1]) if index > 0 else {}

    def _changed(self, event):
        self._style(self.choice.GetSelection() > 0)
        self.on_change(self.selection())
        event.Skip()

    def _style(self, assigned):
        colour = wx.Colour(220, 235, 250) if assigned else wx.Colour(255, 244, 214)
        self.SetBackgroundColour(colour)
        self.choice.SetBackgroundColour(colour)
        self.Refresh()


class WorkflowDialog(wx.Dialog):
    def __init__(self, parent=None, controller=None, pcbnew_module=None):
        super().__init__(parent, title='Connector to Interposer Workflow', size=(1320, 840),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.pcbnew = pcbnew_module
        self.controller = controller or WorkflowController(dispatch=wx.CallAfter)
        self.controller.dispatch = wx.CallAfter
        self.current_gang = None
        self._loading_gang = False
        self._closing = False
        self._applying = False
        self._updating_spreadsheet = False
        self._spreadsheet_timer = None
        self._loaded_workbook_path = ''
        self._spreadsheet_columns = {}
        self._updating_layers = False
        self._assignment_signature = None
        outer = wx.BoxSizer(wx.VERTICAL)

        heading = wx.StaticText(self, label='Connector to Interposer Workflow')
        heading_font = heading.GetFont()
        heading_font.SetPointSize(heading_font.GetPointSize() + 6)
        heading_font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(heading_font)
        outer.Add(heading, 0, wx.ALL, 14)

        body = wx.BoxSizer(wx.HORIZONTAL)
        navigation = wx.Panel(self)
        navigation_sizer = wx.BoxSizer(wx.VERTICAL)
        steps = wx.StaticText(navigation, label='Steps')
        steps_font = steps.GetFont()
        steps_font.SetPointSize(steps_font.GetPointSize() + 2)
        steps_font.SetWeight(wx.FONTWEIGHT_BOLD)
        steps.SetFont(steps_font)
        navigation_sizer.Add(steps, 0, wx.BOTTOM, 6)
        self.step_list = wx.ListBox(navigation, choices=list(PAGES), style=wx.LB_SINGLE)
        dc = wx.ClientDC(self.step_list)
        step_width = max(dc.GetTextExtent(title)[0] for title in PAGES) + 28
        step_height = len(PAGES) * (dc.GetCharHeight() + 5) + 8
        self.step_list.SetMinSize((step_width, step_height))
        self.step_list.SetMaxSize((step_width, step_height))
        self.step_list.SetSelection(0)
        self.step_list.Bind(wx.EVT_LISTBOX, self.select_step)
        navigation_sizer.Add(self.step_list, 0)
        navigation.SetSizer(navigation_sizer)
        body.Add(navigation, 0, wx.TOP | wx.BOTTOM | wx.LEFT | wx.RIGHT, 10)

        self.book = wx.Simplebook(self, style=wx.BORDER_SIMPLE)
        self.pages = []
        for title in PAGES:
            panel = wx.ScrolledWindow(self.book)
            panel.SetScrollRate(8, 8)
            panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
            self.book.AddPage(panel, title)
            self.pages.append(panel)
        self.build_spreadsheet(self.pages[0])
        self.build_layer_assignment(self.pages[1])
        self.build_routing_permissions(self.pages[2])
        self.build_connector_assignments(self.pages[3])
        self.build_gangs(self.pages[4])
        self.build_anchors(self.pages[5])
        self.build_outline(self.pages[6])
        self.build_review(self.pages[7])
        self.build_results(self.pages[8])
        self.book.Bind(wx.EVT_BOOKCTRL_PAGE_CHANGED, self.page_changed)
        body.Add(self.book, 1, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 10)
        outer.Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        status_panel = wx.Panel(self, style=wx.BORDER_SIMPLE)
        status_panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))
        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_heading = wx.StaticText(status_panel, label='Status')
        status_font = status_heading.GetFont()
        status_font.SetWeight(wx.FONTWEIGHT_BOLD)
        status_heading.SetFont(status_font)
        status_sizer.Add(status_heading, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.status = wx.StaticText(
            status_panel,
            label=('Ready. Start with Netlist Source. Save workflow settings persists the current '
                   'setup without changing board files.'),
        )
        self.status.Wrap(860)
        status_sizer.Add(self.status, 1, wx.ALIGN_CENTER_VERTICAL)
        status_panel.SetSizer(status_sizer)
        outer.Add(status_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        self.save_button = self.button(self, 'Save workflow settings', self.save)
        self.save_button.SetToolTip(
            'Save the current workflow settings and accepted source snapshot without applying board changes.'
        )
        footer.Add(self.save_button, 0, wx.RIGHT, 8)
        footer.AddStretchSpacer()
        footer.Add(self.button(self, 'Previous', lambda e: self.navigate(-1)), 0, wx.RIGHT, 8)
        footer.Add(self.button(self, 'Next', lambda e: self.navigate(1)), 0, wx.RIGHT, 8)
        footer.Add(self.button(self, 'Close', self.close), 0)
        outer.Add(footer, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(outer)
        self.SetMinSize((960, 680))
        display_index = wx.Display.GetFromPoint(wx.GetMousePosition())
        if display_index == wx.NOT_FOUND:
            display_index = 0
        display_area = wx.Display(display_index).GetClientArea()
        self.SetSize((min(1320, max(960, int(display_area.width * .94))),
                      min(840, max(680, int(display_area.height * .90)))))
        self.Bind(wx.EVT_CLOSE, self.close)
        self.restore()
        self.Layout()
        for page in self.pages:
            page.FitInside()
        self.refresh()

    def select_step(self, event):
        self.book.SetSelection(event.GetSelection())

    def page_changed(self, event):
        if self.step_list.GetSelection() != event.GetSelection():
            self.step_list.SetSelection(event.GetSelection())
        event.Skip()

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

    def task_box(self, parent, title, subtitle, description):
        box = wx.Panel(parent, style=wx.BORDER_SIMPLE)
        box.SetBackgroundColour(wx.Colour(220, 235, 250))
        sizer = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(box, label=title)
        font = heading.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(font)
        sizer.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        task = wx.StaticText(box, label=subtitle)
        task_font = task.GetFont()
        task_font.SetWeight(wx.FONTWEIGHT_BOLD)
        task.SetFont(task_font)
        sizer.Add(task, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        detail = wx.StaticText(box, label=description)
        detail.Wrap(820)
        sizer.Add(detail, 0, wx.ALL, 10)
        box.SetSizer(sizer)
        parent.GetSizer().Add(box, 0, wx.EXPAND | wx.ALL, 8)
        return box

    @staticmethod
    def status_label(parent, label):
        control = wx.StaticText(parent, label=label, style=wx.BORDER_SIMPLE)
        control.SetBackgroundColour(wx.Colour(232, 244, 234))
        control.SetForegroundColour(wx.Colour(40, 122, 61))
        font = control.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        control.SetFont(font)
        control.SetMinSize((190, -1))
        return control

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

    def library_field(self, parent, label, kind, allow_mechanical=False, on_change=None):
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(parent, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        control = LibrarySelectionCtrl(parent, self, 'Choose ' + label.casefold(), kind,
                                       allow_mechanical, on_change or self.library_changed)
        row.Add(control, 1, wx.EXPAND)
        parent.GetSizer().Add(row, 0, wx.EXPAND | wx.ALL, 8)
        return control

    def library_changed(self):
        self.controller.changed()
        self.refresh()

    def input_changed(self, event):
        self.controller.changed()
        if hasattr(self, 'view_button'): self.refresh()
        event.Skip()

    def build_spreadsheet(self, panel):
        self.task_box(
            panel,
            'Netlist Source',
            'Import connector and nets assignment',
            'Choose the netlist source, assign the seven required columns, and verify the parsed nets before continuing to Layer assignment.',
        )

        source_box = wx.StaticBox(panel, label='Netlist source')
        source = wx.StaticBoxSizer(source_box, wx.VERTICAL)
        self.workbook_path = wx.TextCtrl(source_box)
        self.workbook_path.Hide()
        row = wx.BoxSizer(wx.HORIZONTAL)
        workbook = wx.BoxSizer(wx.VERTICAL)
        workbook.Add(wx.StaticText(source_box, label='Netlist workbook'), 0, wx.BOTTOM, 2)
        self.workbook_filename = wx.StaticText(
            source_box, label='No netlist workbook selected', style=wx.ST_ELLIPSIZE_MIDDLE,
        )
        self.workbook_filename.SetMinSize((240, -1))
        self.workbook_filename.SetMaxSize((280, -1))
        font = self.workbook_filename.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.workbook_filename.SetFont(font)
        workbook.Add(self.workbook_filename, 0)
        row.Add(workbook, 1, wx.EXPAND | wx.RIGHT, 12)
        self.workbook_browse = self.button(source_box, 'Browse…', self.choose_workbook)
        row.Add(self.workbook_browse, 0, wx.ALIGN_BOTTOM | wx.RIGHT, 8)
        self.workbook_reload = self.button(source_box, 'Reload', self.reload_workbook)
        self.workbook_reload.SetToolTip('Re-read this file and redetect exact column headers.')
        row.Add(self.workbook_reload, 0, wx.ALIGN_BOTTOM | wx.RIGHT, 16)

        worksheet = wx.BoxSizer(wx.VERTICAL)
        worksheet.Add(wx.StaticText(source_box, label='Netlist worksheet'), 0, wx.BOTTOM, 4)
        self.sheet = wx.Choice(source_box, size=(160, -1))
        self.sheet.SetMinSize((140, -1))
        worksheet.Add(self.sheet)
        row.Add(worksheet, 0, wx.ALIGN_BOTTOM | wx.RIGHT, 16)

        header = wx.BoxSizer(wx.VERTICAL)
        header.Add(wx.StaticText(source_box, label='Header row'), 0, wx.BOTTOM, 4)
        self.header = wx.SpinCtrl(source_box, min=1, max=1048576, initial=1, size=(100, -1))
        header.Add(self.header)
        row.Add(header, 0, wx.ALIGN_BOTTOM | wx.RIGHT, 16)

        excluded = wx.BoxSizer(wx.VERTICAL)
        excluded.Add(wx.StaticText(source_box, label='Exclude rows'), 0, wx.BOTTOM, 4)
        self.ignored = wx.TextCtrl(source_box, size=(140, -1))
        self.ignored.SetHint('e.g. 17-18, 24')
        self.ignored.SetToolTip('Optional worksheet row numbers or ranges to exclude from this import.')
        excluded.Add(self.ignored)
        row.Add(excluded, 0, wx.ALIGN_BOTTOM)
        source.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        self.spreadsheet_feedback = wx.StaticText(source_box, label=' ')
        self.spreadsheet_feedback.Wrap(780)
        source.Add(self.spreadsheet_feedback, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(source, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        mapping_box = wx.StaticBox(panel, label='Column assignments and parsed nets')
        mapping = wx.StaticBoxSizer(mapping_box, wx.VERTICAL)
        self.mapper = grid.Grid(mapping_box)
        self.mapper.CreateGrid(SPREADSHEET_PREVIEW_ROWS + 1, len(HEADERS) + 2)
        for column, name in enumerate(('#', 'Source row') + tuple(HEADERS)):
            self.mapper.SetColLabelValue(column, name)
        self.mapper.SetRowLabelSize(0)
        self.mapper.SetColSize(0, 42)
        self.mapper.SetColSize(1, 82)
        for column, name in enumerate(HEADERS, 2):
            self.mapper.SetColSize(column, 125 if name in ('Interposer Pin', 'Connector Pin') else 112)
        self.mapper.SetRowSize(0, 34)
        for row_index in range(1, SPREADSHEET_PREVIEW_ROWS + 1):
            self.mapper.SetRowSize(row_index, 28)
        table_height = (self.mapper.GetColLabelSize() + self.mapper.GetRowSize(0)
                        + sum(self.mapper.GetRowSize(row) for row in range(1, SPREADSHEET_PREVIEW_ROWS + 1))
                        + 6)
        self.mapper.SetMinSize((-1, table_height))
        self.mapper.SetMaxSize((-1, table_height))
        self.mapper.SetCellSize(0, 0, 1, 2)
        self.mapper.SetCellValue(0, 0, 'Assignments')
        self.mapper.SetReadOnly(0, 0)
        self.mapper.SetReadOnly(0, 1)
        self.mapper.SetCellBackgroundColour(0, 0, wx.Colour(220, 235, 250))
        self.mapper.SetCellRenderer(0, 0, AssignmentRenderer(False))
        for row_index in range(1, SPREADSHEET_PREVIEW_ROWS + 1):
            for column in range(len(HEADERS) + 2):
                self.mapper.SetReadOnly(row_index, column)
                if row_index % 2 == 0:
                    self.mapper.SetCellBackgroundColour(row_index, column, wx.Colour(248, 249, 250))
        mapping.Add(self.mapper, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.columns_status = self.status_label(mapping_box, '0 of 7 columns assigned')
        self.row_count = self.columns_status
        row.Add(self.columns_status, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.nets_status = self.status_label(mapping_box, '0 nets parsed')
        row.Add(self.nets_status, 0, wx.ALIGN_CENTER_VERTICAL)
        row.AddStretchSpacer()
        self.view_button = self.button(mapping_box, 'Open full table…', self.view_data)
        row.Add(self.view_button, 0, wx.ALIGN_CENTER_VERTICAL)
        mapping.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(mapping, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.workbook_path.Bind(wx.EVT_TEXT, self.workbook_changed)
        self.sheet.Bind(wx.EVT_CHOICE, self.worksheet_changed)
        self.header.Bind(wx.EVT_SPINCTRL, self.header_changed)
        self.header.Bind(wx.EVT_TEXT, self.header_changed)
        self.ignored.Bind(wx.EVT_TEXT, self.excluded_rows_changed)
        self.mapper.Bind(grid.EVT_GRID_CELL_CHANGED, self.mapping_changed)
        self.mapper.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.assignment_clicked)
        self._style_assignment_row()
        self._update_workbook_display()

    def choose_workbook(self, event):
        with wx.FileDialog(
            self,
            'Choose netlist workbook',
            wildcard=('Netlist files (*.xlsx;*.csv;*.txt)|*.xlsx;*.csv;*.txt|'
                      'Excel workbooks (*.xlsx)|*.xlsx|CSV files (*.csv)|*.csv|Text files (*.txt)|*.txt'),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.workbook_path.SetValue(dialog.GetPath())

    def reload_workbook(self, event):
        self._cancel_spreadsheet_timer()
        self._parse_spreadsheet('manual_reload')

    def workbook_changed(self, event):
        if self._updating_spreadsheet:
            event.Skip()
            return
        self._update_workbook_display()
        self._loaded_workbook_path = ''
        self.controller.table = None
        self.controller.workbook = None
        self.controller.changed()
        self._updating_spreadsheet = True
        try:
            self.sheet.Set([])
            self.header.SetValue(1)
        finally:
            self._updating_spreadsheet = False
        self._set_mapping_columns({})
        path = Path(self.workbook_path.GetValue()).expanduser()
        if path.is_file() and path.suffix.casefold() in WORKBOOK_SUFFIXES:
            self._schedule_spreadsheet_parse('reload')
            self._set_spreadsheet_feedback('Reading netlist workbook…')
        elif self.workbook_path.GetValue().strip():
            self._cancel_spreadsheet_timer()
            self._set_spreadsheet_feedback('Choose an existing supported netlist file.', True)
        else:
            self._cancel_spreadsheet_timer()
            self._set_spreadsheet_feedback('')
        self.refresh()
        event.Skip()

    def _update_workbook_display(self):
        value = self.workbook_path.GetValue().strip()
        if value:
            path = Path(value).expanduser()
            self.workbook_filename.SetLabel(path.name or value)
            self.workbook_filename.SetToolTip(str(path))
        else:
            self.workbook_filename.SetLabel('No netlist workbook selected')
            self.workbook_filename.SetToolTip('')
        self.workbook_filename.GetParent().Layout()

    def worksheet_changed(self, event):
        if not self._updating_spreadsheet:
            self._schedule_spreadsheet_parse('redetect', 0)
        event.Skip()

    def header_changed(self, event):
        if not self._updating_spreadsheet:
            self._schedule_spreadsheet_parse('mapping')
        event.Skip()

    def excluded_rows_changed(self, event):
        if not self._updating_spreadsheet:
            self._schedule_spreadsheet_parse('mapping')
        event.Skip()

    def mapping_changed(self, event):
        if not self._updating_spreadsheet and event.GetRow() == 0 and event.GetCol() >= 2:
            if event.GetCol() == 2:
                self._autofill_consecutive_mapping()
            self._schedule_spreadsheet_parse('mapping', 0)
        event.Skip()

    def assignment_clicked(self, event):
        if event.GetRow() == 0 and event.GetCol() >= 2:
            wx.CallAfter(self._enable_assignment_editor, event.GetCol())
        event.Skip()

    def _enable_assignment_editor(self, column):
        if self._closing:
            return
        self.mapper.SetGridCursor(0, column)
        self.mapper.MakeCellVisible(0, column)
        self.mapper.EnableCellEditControl()

    def _autofill_consecutive_mapping(self):
        """Fill a wholly blank mapping from a user-selected Signal column."""
        if any(self.mapper.GetCellValue(0, column).strip() for column in range(3, len(HEADERS) + 2)):
            return False
        signal_label = self.mapper.GetCellValue(0, 2).strip()
        if signal_label not in self._spreadsheet_columns:
            return False
        start = self._spreadsheet_columns[signal_label]
        labels = {column: label for label, column in self._spreadsheet_columns.items()}
        if any(start + offset not in labels for offset in range(len(HEADERS))):
            return False
        self._updating_spreadsheet = True
        try:
            for field, column in enumerate(range(start, start + len(HEADERS)), 2):
                self.mapper.SetCellValue(0, field, labels[column])
        finally:
            self._updating_spreadsheet = False
        return True

    def _cancel_spreadsheet_timer(self):
        if self._spreadsheet_timer is not None:
            self._spreadsheet_timer.Stop()
            self._spreadsheet_timer = None

    def _schedule_spreadsheet_parse(self, mode, delay=300):
        self._cancel_spreadsheet_timer()
        if self._closing:
            return
        self._spreadsheet_timer = wx.CallLater(delay, self._parse_spreadsheet, mode)

    def _set_spreadsheet_feedback(self, message, error=False):
        self.spreadsheet_feedback.SetLabel(message or ' ')
        colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
        if error:
            colour = wx.Colour(180, 35, 35)
        self.spreadsheet_feedback.SetForegroundColour(colour)
        self.spreadsheet_feedback.Wrap(780)
        self.spreadsheet_feedback.GetParent().Layout()

    def _set_worksheet_choices(self, names, selected=''):
        """Populate the native choice with an explicit blank selection."""
        names = list(names)
        self._updating_spreadsheet = True
        try:
            self.sheet.Set([''] + names)
            if selected in names:
                self.sheet.SetStringSelection(selected)
            else:
                self.sheet.SetSelection(0)
        finally:
            self._updating_spreadsheet = False

    def _source_error(self, error, path, worksheet):
        message = str(error)
        if message == 'Header row must identify an existing worksheet row':
            count = 0
            if self.controller.workbook is not None and worksheet:
                try:
                    count = len(self.controller.workbook.sheet(worksheet).rows)
                except StopIteration:
                    pass
            limit = f' between 1 and {count}' if count else ''
            return f'Header row {self.header.GetValue()} does not exist in worksheet {worksheet!r}. Choose a row{limit}.'
        if message.startswith('Excluded rows must be numbers or ranges'):
            return 'Exclude rows accepts worksheet row numbers and ranges, for example 3, 8-10.'
        if message in ('Excluded row range is invalid', 'Excluded rows must be data rows within the worksheet'):
            return (f'Excluded rows must be below header row {self.header.GetValue()} and within '
                    f'worksheet {worksheet!r}.')
        if message == 'Workbook contains no worksheets':
            return f'{path.name} contains no worksheets.'
        return f'Could not read {path.name}: {message}'

    @staticmethod
    def _column_name(index):
        result = ''
        number = index + 1
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _available_columns(self):
        if self.controller.workbook is None or not self.sheet.GetStringSelection():
            return {}
        worksheet = self.controller.workbook.sheet(self.sheet.GetStringSelection())
        start = max(0, self.header.GetValue() - 1)
        width = max((len(row) for row in worksheet.rows[start:]), default=0)
        columns = {}
        for column in range(width):
            values = [str(row[column]).strip() for row in worksheet.rows[start:] if column < len(row)]
            populated = [value for value in values if value]
            if not populated:
                continue
            heading = values[0] if values and values[0] else populated[0]
            label = f'{self._column_name(column)} · {heading}'
            columns[label] = column
        return columns

    def _set_mapping_columns(self, mapped, table=None):
        self._spreadsheet_columns = self._available_columns()
        choices = [''] + list(self._spreadsheet_columns)
        by_column = {column: label for label, column in self._spreadsheet_columns.items()}
        self._updating_spreadsheet = True
        try:
            for field_offset, header in enumerate(HEADERS):
                grid_column = field_offset + 2
                self.mapper.SetCellEditor(0, grid_column, grid.GridCellChoiceEditor(choices, False))
                source_column = mapped.get(header)
                self.mapper.SetCellValue(0, grid_column, by_column.get(source_column, ''))
            self._render_mapping_preview(table, mapped)
            self._style_assignment_row()
        finally:
            self._updating_spreadsheet = False

    def _style_assignment_row(self):
        values = [self.mapper.GetCellValue(0, column).strip()
                  for column in range(2, len(HEADERS) + 2)]
        counts = {value: values.count(value) for value in set(values) if value}
        for column, value in enumerate(values, 2):
            if not value:
                background = wx.Colour(255, 244, 214)
            elif counts[value] > 1:
                background = wx.Colour(252, 232, 230)
            else:
                background = wx.Colour(220, 235, 250)
            self.mapper.SetCellBackgroundColour(0, column, background)
            self.mapper.SetCellRenderer(0, column, AssignmentRenderer(True))
        self.mapper.ForceRefresh()

    def _render_mapping_preview(self, table, mapped=None):
        mapped = mapped if mapped is not None else self._mapping_from_grid()
        rows = [row for row in table.rows if not row.ignored][:SPREADSHEET_PREVIEW_ROWS] if table else []
        for preview_index in range(SPREADSHEET_PREVIEW_ROWS):
            grid_row = preview_index + 1
            parsed = rows[preview_index] if preview_index < len(rows) else None
            self.mapper.SetCellValue(grid_row, 0, str(preview_index + 1) if parsed else '')
            self.mapper.SetCellValue(grid_row, 1, str(parsed.source_row) if parsed else '')
            for field_index, header in enumerate(HEADERS, 2):
                source_column = mapped.get(header)
                value = ''
                if parsed is not None and source_column is not None and source_column < len(parsed.values):
                    value = str(parsed.values[source_column])
                self.mapper.SetCellValue(grid_row, field_index, value)

    def _mapping_from_grid(self):
        mapping = {}
        for column, header in enumerate(HEADERS, 2):
            label = self.mapper.GetCellValue(0, column).strip()
            if label in self._spreadsheet_columns:
                mapping[header] = self._spreadsheet_columns[label]
        return mapping

    def _parse_spreadsheet(self, mode='mapping'):
        self._spreadsheet_timer = None
        path = Path(self.workbook_path.GetValue()).expanduser()
        if not path.is_file() or path.suffix.casefold() not in WORKBOOK_SUFFIXES:
            self.controller.table = None
            self._set_spreadsheet_feedback(
                'The selected netlist file does not exist or is unsupported. Use Browse… to choose an .xlsx, .csv, or .txt file.',
                True,
            )
            self.refresh()
            return
        selected = self.sheet.GetStringSelection()
        current_mapping = self._mapping_from_grid()
        try:
            resolved = str(path.resolve())
            worksheet_changed = False
            if mode in ('reload', 'manual_reload') or self.controller.workbook is None or self._loaded_workbook_path != resolved:
                self._loaded_workbook_path = resolved
                self.controller.open_workbook(path)
                names = [worksheet.name for worksheet in self.controller.workbook.sheets]
                if not names:
                    raise ValueError('Workbook contains no worksheets')
                if selected not in names:
                    if mode == 'manual_reload' and selected:
                        selected = names[0]
                        worksheet_changed = True
                    else:
                        selected = ''
                self._set_worksheet_choices(names, selected)

            if not selected:
                self.controller.table = None
                self._set_mapping_columns({})
                self._set_spreadsheet_feedback('Choose a netlist worksheet.')
                self.refresh_layers()
                self.refresh_gangs()
                self.refresh()
                return

            if mode in ('reload', 'manual_reload', 'redetect'):
                table = self.controller.parse(
                    selected, self.header.GetValue(), self.ignored.GetValue(), None, resolved,
                )
            else:
                table = self.controller.parse(
                    selected, self.header.GetValue(), self.ignored.GetValue(), current_mapping, resolved,
                )
            self.update_mapping(table)
            if mode == 'manual_reload':
                if worksheet_changed:
                    self._set_spreadsheet_feedback(
                        f'Workbook reloaded. The previous worksheet is gone, so {table.worksheet} was selected; '
                        'header row and excluded rows were retained, and column mappings were redetected.'
                    )
                else:
                    self._set_spreadsheet_feedback(
                        'Workbook reloaded. Worksheet, header row and excluded rows were retained; '
                        'column mappings were redetected.'
                    )
            else:
                self._set_spreadsheet_feedback('')
            self.refresh_layers()
            self.refresh_gangs()
        except Exception as error:
            self.controller.table = None
            if self.controller.workbook is not None:
                names = [worksheet.name for worksheet in self.controller.workbook.sheets]
                self._set_worksheet_choices(names, selected)
            self._render_mapping_preview(None, current_mapping)
            self._style_assignment_row()
            self._set_spreadsheet_feedback(self._source_error(error, path, selected), True)
        self.refresh()

    def load_workbook(self, event):
        """Compatibility entry point for callers that explicitly request a reload."""
        self._parse_spreadsheet('reload')

    def update_mapping(self, table):
        self._set_mapping_columns(table.mapping, table)

    def map_columns(self, event):
        """Compatibility entry point; grid edits now parse immediately."""
        self._parse_spreadsheet('mapping')

    def view_data(self, event):
        if self.controller.view_data_enabled:
            with DataDialog(self, self.controller.table) as dialog:
                dialog.ShowModal()

    def build_layer_assignment(self, panel):
        self.task_box(
            panel, 'Layer assignment', 'Assign each netlist layer to the board stackup',
            'Map layer names from the imported netlist to enabled KiCad copper layers. Changes take effect immediately and are used by validation and RF artifact checks.',
        )
        layer_box = wx.StaticBox(panel, label='Layer mappings')
        layer_sizer = wx.StaticBoxSizer(layer_box, wx.VERTICAL)
        layer_sizer.Add(
            wx.StaticText(layer_box, label='Choose one enabled board layer for each netlist layer.'),
            0, wx.EXPAND | wx.ALL, 8,
        )

        self.layer_map = grid.Grid(layer_box)
        self.layer_map.CreateGrid(0, 2)
        self.layer_map.SetRowLabelSize(0)
        self.layer_map.SetColLabelValue(0, 'Netlist layer')
        self.layer_map.SetColLabelValue(1, 'KiCad layer')
        self.layer_map.SetColSize(0, 280)
        self.layer_map.SetColSize(1, 320)
        self.layer_map.SetMinSize((-1, self.layer_map.GetColLabelSize() + 2))
        self.layer_map.SetMaxSize((-1, self.layer_map.GetColLabelSize() + 2))
        layer_sizer.Add(self.layer_map, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        status_row = wx.BoxSizer(wx.HORIZONTAL)
        self.layer_mapping_status = self.status_label(layer_box, '0 of 0 assigned')
        status_row.Add(self.layer_mapping_status, 0, wx.ALIGN_CENTER_VERTICAL)
        status_row.AddStretchSpacer()
        layer_sizer.Add(status_row, 0, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(layer_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.layer_map.Bind(grid.EVT_GRID_CELL_CHANGED, self.layer_assignment_changed)
        self.layer_map.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.layer_assignment_clicked)

    def build_routing_permissions(self, panel):
        self.task_box(
            panel, 'Routing permissions', 'Review the current routing validation policy',
            'This release evaluates permissions by netclass and copper layer. Disallowed combinations block workflow validation only; a future per-net design will add search, filters and previewed KiCad rule updates.',
        )
        self.rules = grid.Grid(panel)
        self.rules.CreateGrid(0, 1)
        self.rules.Bind(grid.EVT_GRID_CELL_CHANGED, self.routing_permission_changed)
        panel.GetSizer().Add(self.rules, 1, wx.EXPAND | wx.ALL, 8)
        self.new_rules = self.note(panel, '')
        self.new_rules.Hide()

    def refresh_layers(self):
        self._updating_layers = True
        try:
            if self.layer_map.GetNumberRows(): self.layer_map.DeleteRows(0, self.layer_map.GetNumberRows())
            names = sorted({r.layer for r in self.controller.table.records}) if self.controller.table else []
            if names: self.layer_map.AppendRows(len(names))
            for row, name in enumerate(names):
                self.layer_map.SetCellValue(row, 0, name)
                self.layer_map.SetReadOnly(row, 0)
                self.layer_map.SetCellValue(row, 1, self.controller.config['layer_mapping'].get(name, ''))
                self.layer_map.SetCellEditor(
                    row, 1, grid.GridCellChoiceEditor(list(self.controller.copper_layers), False),
                )
                self._style_layer_assignment_cell(row)
            table_height = (self.layer_map.GetColLabelSize()
                            + sum(self.layer_map.GetRowSize(row) for row in range(len(names))) + 2)
            self.layer_map.SetMinSize((-1, table_height))
            self.layer_map.SetMaxSize((-1, table_height))
            assigned = sum(bool(self.controller.config['layer_mapping'].get(name)) for name in names)
            self.layer_mapping_status.SetLabel(f'{assigned} of {len(names)} assigned')
            self.layer_map.GetParent().Layout()
            self.pages[1].FitInside()

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
                    self.rules.SetCellValue(
                        row, col,
                        '1' if self.controller.config['layer_rules'][netclass].get(layer, True) else '',
                    )
            if self.controller.new_rules:
                self.new_rules.SetLabel(
                    f'{len(self.controller.new_rules)} new combinations require review.'
                )
                self.new_rules.Show()
            else:
                self.new_rules.SetLabel('')
                self.new_rules.Hide()
            self.pages[2].Layout()
        finally:
            self._updating_layers = False

    def _style_layer_assignment_cell(self, row):
        assigned = bool(self.layer_map.GetCellValue(row, 1).strip())
        self.layer_map.SetCellBackgroundColour(
            row, 1, wx.Colour(220, 235, 250) if assigned else wx.Colour(255, 244, 214),
        )
        self.layer_map.SetCellRenderer(row, 1, AssignmentRenderer(True))
        self.layer_map.ForceRefresh()

    def layer_assignment_clicked(self, event):
        if event.GetCol() == 1:
            wx.CallAfter(self._enable_layer_assignment_editor, event.GetRow())
        event.Skip()

    def _enable_layer_assignment_editor(self, row):
        if self._closing:
            return
        self.layer_map.SetGridCursor(row, 1)
        self.layer_map.MakeCellVisible(row, 1)
        self.layer_map.EnableCellEditControl()

    def layer_assignment_changed(self, event):
        if self._updating_layers or event.GetCol() != 1:
            event.Skip()
            return
        row = event.GetRow()
        netlist_layer = self.layer_map.GetCellValue(row, 0)
        kicad_layer = self.layer_map.GetCellValue(row, 1)
        if kicad_layer:
            self.controller.set_layer_mapping(netlist_layer, kicad_layer)
        else:
            self.controller.config['layer_mapping'].pop(netlist_layer, None)
            self.controller.changed()
        names = [self.layer_map.GetCellValue(index, 0)
                 for index in range(self.layer_map.GetNumberRows())]
        assigned = sum(bool(self.controller.config['layer_mapping'].get(name)) for name in names)
        self.layer_mapping_status.SetLabel(f'{assigned} of {len(names)} assigned')
        self._style_layer_assignment_cell(row)
        self.status.SetLabel(
            f'{netlist_layer} now maps to {kicad_layer}. The current workflow changed; '
            'board files were not modified.' if kicad_layer else
            f'{netlist_layer} is unassigned. The current workflow changed; board files were not modified.'
        )
        self.refresh()
        event.Skip()

    def routing_permission_changed(self, event):
        if self._updating_layers:
            event.Skip()
            return
        row, col = event.GetRow(), event.GetCol()
        netclass = self.rules.GetRowLabelValue(row)
        layer = self.controller.copper_layers[col]
        allowed = self.rules.GetCellValue(row, col) == '1'
        self.controller.set_rule(netclass, layer, allowed)
        self.status.SetLabel(
            f'{netclass} is {"allowed" if allowed else "not allowed"} on {layer}. '
            'The current workflow changed; board files were not modified.'
        )
        self.refresh()
        event.Skip()

    def build_connector_assignments(self, panel):
        self.task_box(
            panel, 'Connector assignments', 'Map library symbols to their allowed footprints',
            'Assign the workbook interposer and each connector gang to read-only KiCad library parts. '
            'Use Select… inside a symbol cell to open the library picker; the footprint cell then offers '
            'only matching choices. No placement or BOM overrides are entered here.',
        )
        interposer_box = wx.StaticBox(panel, label='Interposer assignment')
        interposer = wx.StaticBoxSizer(interposer_box, wx.VERTICAL)
        self.interposer_assignment_grid = wx.FlexGridSizer(cols=3, hgap=0, vgap=0)
        self.interposer_assignment_grid.AddGrowableCol(1, 1)
        self.interposer_assignment_grid.AddGrowableCol(2, 1)
        for label in ('Ref des', 'Symbol', 'Footprint'):
            heading = wx.StaticText(interposer_box, label=label, style=wx.BORDER_SIMPLE)
            font = heading.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD); heading.SetFont(font)
            heading.SetBackgroundColour(wx.Colour(246, 246, 246))
            heading.SetMinSize((120 if label == 'Ref des' else 360, 34))
            self.interposer_assignment_grid.Add(heading, 0 if label == 'Ref des' else 1,
                                                wx.EXPAND | wx.ALL, 0)
        self.interposer_ref_display = wx.StaticText(interposer_box, label='—', style=wx.BORDER_SIMPLE)
        self.interposer_ref_display.SetMinSize((120, 38))
        self.interposer_assignment_grid.Add(self.interposer_ref_display, 0, wx.EXPAND)
        self.interposer_symbol_assignment = SymbolAssignmentCtrl(
            interposer_box, self, 'Choose interposer symbol', False,
            lambda selection, symbol: self.assign_interposer_symbol(selection, symbol),
        )
        self.interposer_assignment_grid.Add(self.interposer_symbol_assignment, 1, wx.EXPAND)
        self.interposer_footprint_assignment = FootprintAssignmentCtrl(
            interposer_box, self.assign_interposer_footprint,
        )
        self.interposer_assignment_grid.Add(self.interposer_footprint_assignment, 1, wx.EXPAND)
        interposer.Add(self.interposer_assignment_grid, 0, wx.EXPAND | wx.ALL, 8)
        self.interposer_assignment_status = self.status_label(interposer_box, '0 of 0 interposers assigned')
        interposer.Add(self.interposer_assignment_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(interposer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        gangs_box = wx.StaticBox(panel, label='Connector gang assignments')
        gangs = wx.StaticBoxSizer(gangs_box, wx.VERTICAL)
        self.assignment_filter = wx.SearchCtrl(gangs_box, style=wx.TE_PROCESS_ENTER)
        self.assignment_filter.SetDescriptiveText('Filter gangs')
        self.assignment_filter.SetMinSize((330, -1))
        self.assignment_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_connector_assignments(True))
        gangs.Add(self.assignment_filter, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.gang_assignment_rows = wx.Panel(gangs_box)
        assignment_grid = wx.FlexGridSizer(cols=4, hgap=0, vgap=0)
        assignment_grid.AddGrowableCol(2, 1)
        assignment_grid.AddGrowableCol(3, 1)
        self.gang_assignment_rows.SetSizer(assignment_grid)
        gangs.Add(self.gang_assignment_rows, 0, wx.EXPAND | wx.ALL, 8)
        self.gang_assignment_status = self.status_label(gangs_box, '0 of 0 gangs assigned')
        gangs.Add(self.gang_assignment_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(gangs, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.gang_assignment_controls = {}

    def _symbol_from_spec(self, selection, require_two_pin):
        from .core.libraries import read_symbol
        path = self.controller.resolve_path(selection['path'], selection.get('sha256'),
                                            'symbols', '.kicad_sym')
        return read_symbol(path, selection['library_id'], selection.get('sha256'),
                           require_two_pin=require_two_pin)

    def _allowed_footprint_specs(self, symbol):
        from .core.libraries import footprint_matches_filters, read_footprint
        if not symbol or not symbol.footprint_filters or self.pcbnew is None or not self.controller.board_path:
            return ()
        catalog = KiCadLibraryCatalog(
            self.pcbnew, self.controller.board_path.parent, 'footprint',
        )
        matches = {}
        for entry in catalog.libraries():
            for part in catalog.parts(entry.nickname):
                if footprint_matches_filters(part.library_id, symbol.footprint_filters):
                    approved = read_footprint(part.path, part.library_id)
                    if {pin[0] for pin in symbol.pin_numbers} != set(approved.pad_numbers):
                        continue
                    matches[part.library_id] = {
                        'path': approved.path, 'library_id': approved.library_id,
                        'sha256': approved.sha256,
                    }
        return tuple(matches[name] for name in sorted(matches))

    def _interposer_reference(self):
        references = sorted({record.interposer for record in self.controller.table.records}) \
            if self.controller.table else []
        return references[0] if len(references) == 1 else ''

    def assign_interposer_symbol(self, selection, symbol):
        reference = self._interposer_reference()
        if not reference:
            raise ValueError('The imported netlist must contain exactly one interposer reference')
        prior = self.controller.config['interposers'].get(reference, {})
        self.controller.config['interposers'] = {
            reference: {
                'symbol': dict(selection), 'pose': prior.get('pose', [0, 0, 0]),
                'pose_accepted': prior.get('pose_accepted', False),
                'value': symbol.value, 'symbol_mpn': symbol.mpn,
            },
        }
        options = self._allowed_footprint_specs(symbol)
        self.interposer_footprint_assignment.set_options(
            options, {},
        )
        self.interposer_footprint_assignment.Enable(True)
        if not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self.controller.changed()
        self._refresh_assignment_status()

    def assign_interposer_footprint(self, selection):
        reference = self._interposer_reference()
        if not reference or not selection:
            return
        from .core.libraries import read_footprint
        approved = read_footprint(selection['path'], selection['library_id'], selection.get('sha256'))
        current = self.controller.config['interposers'].setdefault(reference, {})
        current.update(dict(approved.pin(), approved=True))
        current['footprint_mpn'] = approved.mpn
        current.setdefault('pose', [0, 0, 0])
        self.controller.changed()
        self._refresh_assignment_status()

    def assign_gang_symbol(self, gang, selection, symbol):
        self.controller.set_gang(gang, {
            'symbol': dict(selection), 'part_id': '', 'library_id': '',
            'part_sha256': '', 'footprint_mpn': '',
            'value': symbol.value, 'symbol_mpn': symbol.mpn,
        })
        controls = self.gang_assignment_controls.get(gang)
        options = self._allowed_footprint_specs(symbol)
        if controls:
            controls['footprint'].set_options(options, {})
            controls['footprint'].Enable(True)
        if not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_gang_footprint(self, gang, selection):
        if not selection:
            return
        self.controller.set_gang(gang, {
            'part_id': selection['path'], 'library_id': selection['library_id'],
            'part_sha256': selection.get('sha256', ''),
        })
        self._refresh_assignment_status()

    def _configured_symbol(self, selection, require_two_pin):
        if not selection.get('path') or not selection.get('library_id'):
            return None
        try:
            return self._symbol_from_spec(selection, require_two_pin)
        except (OSError, ValueError):
            return None

    def refresh_connector_assignments(self, force=False):
        reference = self._interposer_reference()
        names = self.controller.gang_names()
        query = self.assignment_filter.GetValue().casefold() if hasattr(self, 'assignment_filter') else ''
        signature = (reference, names, query)
        if not force and signature == self._assignment_signature:
            self._refresh_assignment_status()
            return
        self._assignment_signature = signature
        self.interposer_ref_display.SetLabel(reference or '—')
        interposer = self.controller.config['interposers'].get(reference, {}) if reference else {}
        if hasattr(self, 'anchor_interposer_ref'):
            self.anchor_interposer_ref.SetLabel('Ref des: ' + (reference or '—'))
            pose = interposer.get('pose', [0, 0, 0])
            self.interposer_x.ChangeValue(str(pose[0]))
            self.interposer_y.ChangeValue(str(pose[1]))
            self.interposer_rotation.ChangeValue(str(pose[2]))
            self.interposer_pose_status.SetLabel(
                f'{reference} placement accepted' if reference and interposer.get('pose_accepted')
                else 'Interposer placement required'
            )
        symbol_spec = interposer.get('symbol', {})
        self.interposer_symbol_assignment.set_selection(symbol_spec)
        symbol = self._configured_symbol(symbol_spec, False)
        self.interposer_footprint_assignment.set_options(
            self._allowed_footprint_specs(symbol), interposer,
        )
        self.interposer_symbol_assignment.Enable(bool(reference))
        self.interposer_footprint_assignment.Enable(bool(reference and symbol))

        sizer = self.gang_assignment_rows.GetSizer()
        sizer.Clear(True)
        self.gang_assignment_controls = {}
        for label, width in (('Gang', 120), ('Positions', 100), ('Symbol', 340), ('Footprint', 380)):
            heading = wx.StaticText(self.gang_assignment_rows, label=label, style=wx.BORDER_SIMPLE)
            font = heading.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD); heading.SetFont(font)
            heading.SetBackgroundColour(wx.Colour(246, 246, 246)); heading.SetMinSize((width, 34))
            sizer.Add(heading, 1 if label in ('Symbol', 'Footprint') else 0, wx.EXPAND)
        for name in names:
            if query and query not in name.casefold():
                continue
            settings = self.controller.gang(name)
            count = sum(record.connector == name for record in self.controller.table.records)
            gang_label = wx.StaticText(self.gang_assignment_rows, label=name, style=wx.BORDER_SIMPLE)
            gang_label.SetMinSize((120, 38))
            positions = wx.StaticText(self.gang_assignment_rows, label=str(count), style=wx.BORDER_SIMPLE)
            positions.SetMinSize((100, 38))
            symbol_control = SymbolAssignmentCtrl(
                self.gang_assignment_rows, self, 'Choose connector symbol', True,
                lambda selection, symbol, gang=name: self.assign_gang_symbol(gang, selection, symbol),
            )
            footprint_control = FootprintAssignmentCtrl(
                self.gang_assignment_rows,
                lambda selection, gang=name: self.assign_gang_footprint(gang, selection),
            )
            selected_symbol = settings.get('symbol', {})
            symbol_control.set_selection(selected_symbol)
            symbol = self._configured_symbol(selected_symbol, True)
            footprint_control.set_options(self._allowed_footprint_specs(symbol), {
                'path': settings.get('part_id', ''), 'library_id': settings.get('library_id', ''),
                'sha256': settings.get('part_sha256', ''),
            })
            footprint_control.Enable(bool(symbol))
            for control in (gang_label, positions, symbol_control, footprint_control):
                sizer.Add(control, 1 if control in (symbol_control, footprint_control) else 0, wx.EXPAND)
            self.gang_assignment_controls[name] = {
                'symbol': symbol_control, 'footprint': footprint_control,
            }
        self.gang_assignment_rows.Layout()
        self.pages[3].FitInside()
        self._refresh_assignment_status()

    def _refresh_assignment_status(self):
        reference = self._interposer_reference()
        interposer = self.controller.config['interposers'].get(reference, {}) if reference else {}
        assigned_interposer = int(bool(interposer.get('symbol', {}).get('library_id') and
                                       interposer.get('library_id')))
        interposer_total = int(bool(reference))
        self.interposer_assignment_status.SetLabel(
            f'{assigned_interposer} of {interposer_total} interposers assigned',
        )
        names = self.controller.gang_names()
        assigned_gangs = sum(bool(self.controller.gang(name).get('symbol', {}).get('library_id') and
                                  self.controller.gang(name).get('library_id')) for name in names)
        self.gang_assignment_status.SetLabel(f'{assigned_gangs} of {len(names)} gangs assigned')
        for control, complete in (
            (self.interposer_assignment_status, assigned_interposer == interposer_total and bool(interposer_total)),
            (self.gang_assignment_status, assigned_gangs == len(names) and bool(names)),
        ):
            control.SetBackgroundColour(wx.Colour(232, 244, 234) if complete else wx.Colour(255, 244, 214))
            control.SetForegroundColour(wx.Colour(40, 122, 61) if complete else wx.Colour(140, 87, 8))
            control.Refresh()

    def build_gangs(self, panel):
        self.task_box(
            panel, 'Gangs', 'Define connector gang geometry',
            'Configure each gang pattern, numbering, orientation and RF settings. Library part assignments are managed in Connector assignments. Preview labels are position indices.',
        )
        self.gang_filter = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.gang_filter.SetDescriptiveText('Filter gangs')
        self.gang_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_gang_list())
        panel.GetSizer().Add(self.gang_filter, 0, wx.EXPAND | wx.ALL, 8)
        self.gang_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 115))
        for col, (heading, width) in enumerate((('Gang', 100), ('Positions', 85), ('Pattern', 140), ('Anchor', 110), ('Status', 200))): self.gang_list.InsertColumn(col, heading, width=width)
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
            ('row_lengths', 'Row lengths', '4', False),
            ('pitch_x', 'Horizontal pitch', '2.54', False), ('pitch_y', 'Vertical pitch', '2.54', False),
            ('row_offsets', 'Per-row offsets', '', False), ('numbering', 'Explicit location traversal', '', False),
            ('rotation', 'Orientation (degrees)', '0', False), ('rf_artifact', 'Approved RF artifact', '', True),
            ('rf_clock', 'RF default clock (degrees)', '0', False), ('rf_overrides', 'Per-location clocks (1:90, 2:180)', '', False)):
            self.gang_fields[key] = self.field(form, label, value, picker)
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
        for control in self.gang_fields.values():
            if not isinstance(control, LibrarySelectionCtrl): control.Bind(wx.EVT_TEXT, self.gang_draft_changed)
        self.mirror.Bind(wx.EVT_CHECKBOX, self.gang_draft_changed)
        self.gang_units.Bind(wx.EVT_CHOICE, self.gang_draft_changed)
        self.gang_side.Bind(wx.EVT_CHOICE, self.gang_draft_changed)

    def gang_draft_changed(self, event=None):
        if not self._loading_gang:
            self.controller.config.setdefault('drafts', {})[self.current_gang or ''] = {key: control.GetValue() for key, control in self.gang_fields.items()}
            self.controller.changed()
            try:
                self.store_gang()
                self.pattern_view.show(self.current_gang, self.controller.pattern(self.current_gang))
            except Exception as error:
                self.pattern_view.show(self.current_gang or '', error=str(error))
            self.refresh()
        if event is not None: event.Skip()

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
            for col, value in enumerate((count, ' × '.join(map(str, settings['row_lengths'])), 'Accepted' if settings.get('anchor_accepted') else 'Required', status), 1): self.gang_list.SetItem(index, col, str(value))

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
        self.task_box(
            panel, 'DXF Anchors', 'Associate imported connector locations',
            'Use KiCad File → Import → Graphics to import the whole DXF to a user layer first. Place the interposer, then capture one native connector circle or enter a numeric gang anchor.',
        )
        interposer_box = wx.StaticBox(panel, label='Interposer placement')
        interposer = wx.StaticBoxSizer(interposer_box, wx.VERTICAL)
        self.anchor_interposer_ref = wx.StaticText(interposer_box, label='Ref des: —')
        interposer.Add(self.anchor_interposer_ref, 0, wx.ALL, 8)
        placement = wx.FlexGridSizer(cols=6, hgap=8, vgap=6)
        placement.Add(wx.StaticText(interposer_box, label='X (mm)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.interposer_x = wx.TextCtrl(interposer_box, value='0')
        placement.Add(self.interposer_x, 1, wx.EXPAND)
        placement.Add(wx.StaticText(interposer_box, label='Y (mm)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.interposer_y = wx.TextCtrl(interposer_box, value='0')
        placement.Add(self.interposer_y, 1, wx.EXPAND)
        placement.Add(wx.StaticText(interposer_box, label='Orientation (degrees)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.interposer_rotation = wx.TextCtrl(interposer_box, value='0')
        placement.Add(self.interposer_rotation, 1, wx.EXPAND)
        for column in (1, 3, 5): placement.AddGrowableCol(column, 1)
        interposer.Add(placement, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        interposer.Add(self.button(interposer_box, 'Accept interposer placement', self.accept_interposer_placement),
                       0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.interposer_pose_status = self.status_label(interposer_box, 'Interposer placement required')
        interposer.Add(self.interposer_pose_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(interposer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

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
        self.hole_path = self.library_field(panel, 'Approved mounting-hole footprint (optional)', 'footprint', allow_mechanical=True)
        self.hole_ref = self.field(panel, 'Mounting-hole reference', 'H1')
        panel.GetSizer().Add(self.button(panel, 'Add mounting hole at this anchor', self.add_hole), 0, wx.ALL, 8)

    def accept_interposer_placement(self, event):
        reference = self._interposer_reference()
        if not reference:
            raise ValueError('The imported netlist must contain exactly one interposer reference')
        selection = self.controller.config['interposers'].get(reference)
        if not selection:
            raise ValueError('Assign the interposer symbol and footprint first')
        pose = [float(self.interposer_x.GetValue()), float(self.interposer_y.GetValue()),
                float(self.interposer_rotation.GetValue())]
        if not all(math.isfinite(value) for value in pose):
            raise ValueError('Interposer placement values must be finite numbers')
        selection['pose'] = pose
        selection['pose_accepted'] = True
        self.controller.changed()
        self.interposer_pose_status.SetLabel(f'{reference} placement accepted')
        self.interposer_pose_status.SetBackgroundColour(wx.Colour(232, 244, 234))
        self.interposer_pose_status.SetForegroundColour(wx.Colour(40, 122, 61))

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
        selection = self.hole_path.GetSelectionSpec()
        if not path or not selection.get('library_id'):
            raise ValueError('Select a mounting-hole footprint from KiCad libraries')
        part = read_footprint(path, selection['library_id'], selection.get('sha256'), allow_mechanical=True)
        reference = self.hole_ref.GetValue().strip()
        if not reference: raise ValueError('Mounting-hole reference is required')
        holes = self.controller.config['mounting_holes']
        if any(h['reference'] == reference for h in holes): raise ValueError('Mounting-hole reference already exists')
        holes.append({'reference': reference, 'path': str(Path(path).resolve()), 'sha256': part.sha256,
                      'library_id': part.library_id, 'pose': [float(self.anchor_x.GetValue()), float(self.anchor_y.GetValue()), 0]})
        self.controller.changed()
        self.anchor_status.SetLabel(f'{reference} added as board-only, excluded from BOM and position files.')

    def build_outline(self, panel):
        self.task_box(
            panel, 'Board Outline', 'Capture managed board contours',
            'Select imported native lines or arcs in the PCB canvas. Capture the outer boundary and optional center cutout separately. Exact closure and ownership are checked before Apply; designer Edge.Cuts stay untouched.',
        )
        self.outline_enable = wx.CheckBox(panel, label='Explicitly enable managed board outline')
        panel.GetSizer().Add(self.outline_enable, 0, wx.ALL, 8)
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
        self.task_box(
            panel, 'Review Changes', 'Validate and compare the desired-state delta',
            'Validate the complete workflow, then compare desired state against saved board ownership. Configuration errors, conflicts, stale inputs and affected designer routing block Apply.',
        )
        validation_box = wx.StaticBox(panel, label='Workflow validation')
        validation = wx.StaticBoxSizer(validation_box, wx.VERTICAL)
        self.validation = wx.ListCtrl(validation_box, style=wx.LC_REPORT, size=(-1, 135))
        for column, (name, width) in enumerate(
            (('Severity', 80), ('Source row', 85), ('Code', 170), ('Details', 620)),
        ):
            self.validation.InsertColumn(column, name, width=width)
        validation.Add(self.validation, 1, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(validation, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
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
        self.task_box(
            panel, 'Apply / Results', 'Apply to a project copy and inspect results',
            'Apply saves a separate controlled project copy, a reconciliation report and a recovery journal. Keep the source project separate when opening the result.',
        )
        self.results = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        panel.GetSizer().Add(self.results, 1, wx.EXPAND | wx.ALL, 8)
        self.journal = self.field(panel, 'Recovery journal', picker=True)
        panel.GetSizer().Add(self.button(panel, 'Check / recover closed output project', self.recover), 0, wx.ALL, 8)
        self.abort_button = self.button(panel, 'Cancel analysis', self.abort)
        panel.GetSizer().Add(self.abort_button, 0, wx.ALL, 8)

    def compare(self, event):
        self.save(None)
        self.confirm_review.SetValue(False)
        issues = self.controller.validate()
        self.render_validation(issues)
        blocking = [issue for issue in issues if issue.severity == 'error']
        if blocking:
            self.status.SetLabel(f'{len(blocking)} blocking validation errors. Review the issue table.')
            raise ValueError(f'Resolve {len(blocking)} listed workflow validation errors before comparing changes')
        self.status.SetLabel('Analysing saved project and library inputs…')
        def complete(preview):
            if self._closing: return
            self.render_changes(preview)
            self.status.SetLabel('Comparison complete. Review conflicts and changes before Apply.')
            self.refresh()
        self.controller.run_async(self.controller.review, complete, self.failed)
        self.refresh()

    def render_validation(self, issues):
        self.validation.DeleteAllItems()
        for issue in issues:
            index = self.validation.InsertItem(self.validation.GetItemCount(), issue.severity)
            for column, value in enumerate((issue.source_row or '', issue.code, issue.message), 1):
                self.validation.SetItem(index, column, str(value))

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
        self.select_page(8)
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
        self.status.SetLabel('Workflow settings saved. Board files were not changed.')

    def restore(self):
        cfg = self.controller.config
        source = cfg.get('workbook', {})
        self._updating_spreadsheet = True
        try:
            self.workbook_path.ChangeValue(source.get('path', ''))
            self._update_workbook_display()
            self.header.SetValue(source.get('header_row', 1))
            self.ignored.ChangeValue(', '.join(map(str, source.get('ignored_rows', []))))
        finally:
            self._updating_spreadsheet = False
        source_path = source.get('path', '')
        if source_path and not Path(source_path).exists() and source.get('snapshot') and self.controller.board_path:
            source_path = str(self.controller.board_path.parent / source['snapshot'])
        if source_path and Path(source_path).exists():
            self.workbook_path.ChangeValue(source_path)
            self._update_workbook_display()
            try:
                table = self.controller.load_workbook(source_path, source.get('worksheet'), source.get('header_row', 1), source.get('ignored_rows', []), source.get('mapping'))
                self._set_worksheet_choices(
                    [s.name for s in self.controller.workbook.sheets], table.worksheet,
                )
                self._loaded_workbook_path = str(Path(source_path).resolve())
                self.update_mapping(table)
                self._set_spreadsheet_feedback('')
            except Exception as error:
                self.status.SetLabel('Saved workbook requires attention: ' + str(error))
                self._set_spreadsheet_feedback(
                    self._source_error(error, Path(source_path), source.get('worksheet', '')),
                    True,
                )
        if cfg.get('interposers'):
            ref, part = next(iter(cfg['interposers'].items()))
            pose = part.get('pose', [0, 0, 0])
            self.interposer_x.ChangeValue(str(pose[0]))
            self.interposer_y.ChangeValue(str(pose[1]))
            self.interposer_rotation.ChangeValue(str(pose[2]))
            self.anchor_interposer_ref.SetLabel('Ref des: ' + ref)
            if part.get('pose_accepted'):
                self.interposer_pose_status.SetLabel(f'{ref} placement accepted')
        self.output_dir.ChangeValue(cfg.get('output_dir', ''))
        self.outline_enable.SetValue(cfg['outline'].get('enabled', False))
        self.outline_view.show(cfg['outline'].get('source_preview', {}))
        self.user_layer.ChangeValue(cfg['mechanical'].get('layer', 'Dwgs.User'))
        offset = cfg['mechanical'].get('label_offset', [0, 0])
        self.label_offset_x.ChangeValue(str(offset[0]))
        self.label_offset_y.ChangeValue(str(offset[1]))
        self.refresh_layers()
        self.refresh_gangs()
        self.refresh_connector_assignments(True)

    def refresh(self):
        self.refresh_connector_assignments()
        self.view_button.Enable(self.controller.view_data_enabled)
        workbook_path = Path(self.workbook_path.GetValue()).expanduser()
        self.workbook_reload.Enable(
            not self.controller.busy and workbook_path.is_file()
            and workbook_path.suffix.casefold() in WORKBOOK_SUFFIXES
        )
        table = self.controller.table
        if table:
            mapped = len(table.mapping)
            self.columns_status.SetLabel(f'{mapped} of {len(HEADERS)} columns assigned')
            noun = 'net' if table.included_count == 1 else 'nets'
            self.nets_status.SetLabel(f'{table.included_count} {noun} parsed')
        else:
            self.columns_status.SetLabel(f'0 of {len(HEADERS)} columns assigned')
            self.nets_status.SetLabel('0 nets parsed')
        self.review_button.Enable(not self.controller.busy)
        self.apply_button.Enable(not self.controller.busy and self.controller.preview is not None and not self.controller.preview.get('conflicts') and self.confirm_review.GetValue())
        self.abort_button.Enable(self.controller.busy and not self._applying)
        self.book.Enable(not self._applying)
        self.step_list.Enable(not self._applying)

    def navigate(self, step):
        self.select_page(self.book.GetSelection() + step)

    def select_page(self, index):
        selection = max(0, min(len(PAGES) - 1, index))
        self.book.SetSelection(selection)
        self.step_list.SetSelection(selection)

    def close(self, event):
        if self.controller.busy:
            self.status.SetLabel('Wait for the current operation, or cancel analysis before closing.')
            if isinstance(event, wx.CloseEvent): event.Veto()
            return
        self._closing = True
        self._cancel_spreadsheet_timer()
        self.controller.cancel()
        self.Destroy()
