"""Native, modeless wx workflow. Library sources are selected, never authored here."""
import json
import math
from pathlib import Path
import wx
import wx.grid as grid
from .controller import HEADERS, PAGES, WorkflowController, library_id
from .core.library_catalog import KiCadLibraryCatalog
from .core.units import DIMENSION_UNITS, dimension_text, parse_dimension

WORKBOOK_SUFFIXES = ('.xlsx', '.csv', '.txt')
SPREADSHEET_PREVIEW_ROWS = 5


def _system_colour(name, fallback):
    key = getattr(wx, 'SYS_COLOUR_' + name, None)
    return wx.SystemSettings.GetColour(key) if key is not None else wx.Colour(*fallback)


def _mix_colour(base, accent, weight):
    """Blend a semantic accent into the current system surface."""
    return wx.Colour(*(round(base.Get()[index] * (1 - weight) + accent[index] * weight)
                       for index in range(3)))


def _dark_appearance():
    appearance = (wx.SystemSettings.GetAppearance()
                  if hasattr(wx.SystemSettings, 'GetAppearance') else None)
    if appearance is not None and hasattr(appearance, 'IsDark'):
        return appearance.IsDark()
    colour = _system_colour('WINDOW', (255, 255, 255))
    red, green, blue = colour.Get()[:3]
    return ((red / 255) * .299 + (green / 255) * .587 + (blue / 255) * .117) < .5


class UiPalette:
    """Semantic colours derived from wx/GTK rather than a fixed light palette."""
    def __init__(self):
        self.dark = _dark_appearance()
        self.window = _system_colour('WINDOW', (255, 255, 255))
        self.text = _system_colour('WINDOWTEXT', (0, 0, 0))
        self.panel = _system_colour('BTNFACE', (240, 240, 240))
        self.panel_text = _system_colour('BTNTEXT', (0, 0, 0))
        self.frame = _system_colour('FRAMEBK', self.panel.Get()[:3])
        self.highlight = _system_colour('HIGHLIGHT', (40, 110, 190))
        self.highlight_text = _system_colour('HIGHLIGHTTEXT', (255, 255, 255))
        self.accent = _system_colour('HOTLIGHT', self.highlight.Get()[:3])
        weight = .30 if self.dark else .13
        self.info = _mix_colour(self.panel, self.accent.Get()[:3], weight)
        self.assigned = self.info
        self.warning = _mix_colour(self.panel, (214, 145, 20), .30 if self.dark else .17)
        self.error = _mix_colour(self.panel, (190, 55, 48), .30 if self.dark else .14)
        self.success = _mix_colour(self.panel, (45, 145, 70), .28 if self.dark else .14)
        self.error_text = wx.Colour(255, 185, 178) if self.dark else wx.Colour(155, 35, 35)
        self.alternate = self.window.ChangeLightness(105 if self.dark else 96)
        self.heading = self.frame


def ui_palette():
    return UiPalette()


class StepList(wx.VListBox):
    """Compact native navigation with one border and a bold active step."""
    def __init__(self, parent, choices):
        super().__init__(parent, style=wx.BORDER_SIMPLE | wx.WANTS_CHARS)
        self.choices = tuple(choices)
        self.SetItemCount(len(self.choices))
        self.apply_theme()

    def GetCount(self):
        return len(self.choices)

    def item_font(self, index):
        font = wx.Font(self.GetFont())
        font.SetWeight(
            wx.FONTWEIGHT_BOLD if index == self.GetSelection() else wx.FONTWEIGHT_NORMAL
        )
        return font

    def OnMeasureItem(self, index):
        dc = wx.ClientDC(self)
        dc.SetFont(self.item_font(index))
        return dc.GetCharHeight() + 8

    def OnDrawBackground(self, dc, rect, index):
        dc.SetPen(wx.Pen(ui_palette().panel))
        dc.SetBrush(wx.Brush(ui_palette().panel))
        dc.DrawRectangle(rect)

    def OnDrawItem(self, dc, rect, index):
        palette = ui_palette()
        dc.SetFont(self.item_font(index))
        dc.SetTextForeground(palette.panel_text)
        _width, height = dc.GetTextExtent(self.choices[index])
        dc.DrawText(self.choices[index], rect.x + 8,
                    rect.y + max(0, (rect.height - height) // 2))

    def apply_theme(self):
        palette = ui_palette()
        self.SetBackgroundColour(palette.panel)
        self.SetForegroundColour(palette.panel_text)
        self.Refresh()


class DimensionInput(wx.Panel):
    """A numeric linear-dimension input with an independent unit selector."""
    def __init__(self, parent, value='0', unit='mil', compact=False):
        super().__init__(parent)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        self.text = wx.TextCtrl(self, value=str(value), style=wx.TE_PROCESS_ENTER)
        self.units = wx.Choice(self, choices=list(DIMENSION_UNITS))
        self.units.SetStringSelection(unit)
        self.units.SetMinSize((70, -1))
        if compact:
            self.text.SetMinSize((86, -1))
            self.text.SetMaxSize((110, -1))
        layout.Add(self.text, 0 if compact else 1, wx.RIGHT, 4)
        layout.Add(self.units, 0)
        self.SetSizer(layout)
        if compact:
            self.SetMinSize((180, -1))
        self._change_handler = None
        self.text.Bind(wx.EVT_TEXT_ENTER, self._normalize)
        self.text.Bind(wx.EVT_KILL_FOCUS, self._normalize)

    def bind_changed(self, handler):
        self._change_handler = handler
        self.text.Bind(wx.EVT_TEXT, handler)
        self.units.Bind(wx.EVT_CHOICE, handler)

    def _normalize(self, event):
        try:
            _value_mm, unit, number_text = parse_dimension(
                self.text.GetValue(), self.units.GetStringSelection(),
            )
        except ValueError:
            pass
        else:
            self.text.ChangeValue(number_text)
            self.units.SetStringSelection(unit)
            if self._change_handler is not None:
                self._change_handler()
        event.Skip()

    def value_mm(self):
        value_mm, _unit, _number_text = parse_dimension(
            self.text.GetValue(), self.units.GetStringSelection(),
        )
        return value_mm

    def selected_unit(self):
        _value_mm, unit, _number_text = parse_dimension(
            self.text.GetValue(), self.units.GetStringSelection(),
        )
        return unit

    def set_mm(self, value_mm, unit=None):
        unit = unit or self.units.GetStringSelection() or 'mm'
        self.units.SetStringSelection(unit)
        self.text.ChangeValue(dimension_text(value_mm, unit))

    def ChangeValue(self, value):
        self.text.ChangeValue(str(value))

    def GetValue(self):
        return self.text.GetValue()


class DimensionCellRenderer(grid.GridCellRenderer):
    """Draw one dimension cell as a left value and right unit selector."""
    def Draw(self, table, attr, dc, rect, row, col, selected):
        background = (table.GetSelectionBackground() if selected
                      else attr.GetBackgroundColour())
        foreground = (table.GetSelectionForeground() if selected
                      else attr.GetTextColour())
        dc.SetPen(wx.Pen(background))
        dc.SetBrush(wx.Brush(background))
        dc.DrawRectangle(rect)
        try:
            _value_mm, unit, number_text = parse_dimension(
                table.GetCellValue(row, col) or '0', 'mil',
            )
        except ValueError:
            number_text, unit = table.GetCellValue(row, col), ''
        dc.SetFont(attr.GetFont())
        dc.SetTextForeground(foreground)
        _width, height = dc.GetTextExtent(number_text)
        y = rect.y + max(0, (rect.height - height) // 2)
        dc.DrawText(number_text, rect.x + 6, y)
        unit_text = unit + ' ▾' if unit else ''
        unit_width, _height = dc.GetTextExtent(unit_text)
        dc.DrawText(unit_text, rect.x + rect.width - unit_width - 6, y)

    def GetBestSize(self, table, attr, dc, row, col):
        dc.SetFont(attr.GetFont())
        width, height = dc.GetTextExtent('000000 inch ▾')
        return wx.Size(width + 16, height + 8)

    def Clone(self):
        return DimensionCellRenderer()


class DimensionCellEditor(grid.GridCellEditor):
    """Edit a grid dimension with a text box and unit choice in one cell."""
    def __init__(self):
        super().__init__()
        self.panel = self.text = self.units = None
        self.start = self.pending = ''

    def Create(self, parent, control_id, event_handler):
        # GridCellEditor.SetControl requires wx.Control, not a generic wx.Window.
        # Passing wx.Panel raises inside the SWIG callback and can leave wx.Grid
        # unwinding through a half-created editor, which crashes the host process.
        self.panel = wx.Control(parent, control_id, style=wx.BORDER_NONE)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        self.text = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER | wx.BORDER_NONE)
        self.units = wx.Choice(self.panel, choices=list(DIMENSION_UNITS))
        self.units.SetMinSize((68, -1))
        layout.Add(self.text, 1, wx.RIGHT, 2)
        layout.Add(self.units, 0)
        self.panel.SetSizer(layout)
        self.SetControl(self.panel)
        self.panel.PushEventHandler(event_handler)

    def Destroy(self):
        if self.panel is not None:
            self.panel.PopEventHandler()
        super().Destroy()

    def SetSize(self, rect):
        self.panel.SetSize(rect)
        self.panel.Layout()

    def BeginEdit(self, row, col, table):
        self.start = table.GetCellValue(row, col) or '0 mil'
        try:
            _value_mm, unit, number_text = parse_dimension(self.start, 'mil')
        except ValueError:
            unit, number_text = 'mil', self.start
        self.text.ChangeValue(number_text)
        self.units.SetStringSelection(unit)
        self.text.SetFocus()
        self.text.SetSelection(-1, -1)

    def EndEdit(self, row, col, table, old_value):
        try:
            _value_mm, unit, number_text = parse_dimension(
                self.text.GetValue(), self.units.GetStringSelection() or 'mil',
            )
            self.pending = f'{number_text} {unit}'
        except ValueError:
            self.pending = self.text.GetValue()
        return self.pending if self.pending != old_value else None

    def ApplyEdit(self, row, col, table):
        table.SetCellValue(row, col, self.pending)
        self.start = self.pending

    def Reset(self):
        try:
            _value_mm, unit, number_text = parse_dimension(self.start, 'mil')
        except ValueError:
            unit, number_text = 'mil', self.start
        self.text.ChangeValue(number_text)
        self.units.SetStringSelection(unit)

    def GetValue(self):
        return self.text.GetValue()

    def Clone(self):
        return DimensionCellEditor()


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
        dc.SetPen(wx.Pen(ui_palette().accent, 2))
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
        super().__init__(parent, size=(-1, 300))
        self.name, self.points = '', {}
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.paint)
    def show(self, name, points=None):
        self.name = name
        if points is not None:
            self.points = points
        self.Refresh()
    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        background = wx.Colour(18, 18, 20)
        foreground = wx.Colour(238, 238, 238)
        geometry = wx.Colour(30, 205, 255)
        anchor = wx.Colour(255, 215, 55)
        dc.SetBackground(wx.Brush(background))
        dc.Clear()
        dc.SetTextForeground(foreground)
        dc.DrawText(self.name, 10, 10)
        if not self.points:
            return
        width, height = self.GetClientSize()
        xs, ys = zip(*self.points.values())
        spanx, spany = max(max(xs)-min(xs), 1), max(max(ys)-min(ys), 1)
        # Keep extra room for platform DPI scaling and multi-digit labels.
        scale = .72 * max(
            .01, min(max(20, width - 100) / spanx, max(20, height - 110) / spany),
        )
        for pin, (x, y) in self.points.items():
            px = int((x-min(xs))*scale+45)
            py = int((y-min(ys))*scale+60)
            colour = anchor if str(pin) == '1' else geometry
            dc.SetPen(wx.Pen(colour, 2))
            # An explicit background brush stays hollow on Windows/GTK themes
            # where the stock transparent brush can inherit a light fill.
            dc.SetBrush(wx.Brush(background))
            dc.DrawCircle(px, py, 12)
            dc.SetTextForeground(colour)
            label = str(pin)
            label_width, label_height = dc.GetTextExtent(label)
            dc.DrawText(label, px-label_width//2, py-label_height//2)


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
        palette = ui_palette()
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(palette.window))
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
            dc.SetPen(wx.Pen(palette.accent if role == 'outer'
                             else _mix_colour(palette.text, (230, 125, 30), .70), 2))
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
        palette = ui_palette()
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(palette.window))
        dc.Clear()
        dc.SetTextForeground(palette.text)
        if not self.items:
            dc.DrawText('Select a part to preview its pads or pins.', 12, 12)
            return
        width, height = self.GetClientSize()
        left = min(x-w/2 for x, y, w, h, label in self.items)
        right = max(x+w/2 for x, y, w, h, label in self.items)
        top = min(y-h/2 for x, y, w, h, label in self.items)
        bottom = max(y+h/2 for x, y, w, h, label in self.items)
        scale = min((width-50)/max(right-left, 1), (height-50)/max(bottom-top, 1))
        dc.SetPen(wx.Pen(palette.accent, 1))
        dc.SetBrush(wx.Brush(_mix_colour(palette.window, palette.accent.Get()[:3], .45)))
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
        palette = ui_palette()
        self.SetBackgroundColour(palette.assigned if assigned else palette.warning)
        self.SetForegroundColour(palette.text)
        self.display.SetForegroundColour(palette.text)
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
        palette = ui_palette()
        colour = palette.assigned if assigned else palette.warning
        self.SetBackgroundColour(colour)
        self.SetForegroundColour(palette.text)
        self.choice.SetBackgroundColour(colour)
        self.choice.SetForegroundColour(palette.text)
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
        self._gang_invalid_edits = {}
        self._closing = False
        self._applying = False
        self._updating_spreadsheet = False
        self._spreadsheet_timer = None
        self._loaded_workbook_path = ''
        self._spreadsheet_columns = {}
        self._updating_layers = False
        self._assignment_signature = None
        self._bulk_assignments = {
            'rf': {'names': (), 'symbol': {}, 'footprint': {}},
            'dc': {'names': (), 'symbol': {}, 'footprint': {}},
        }
        self._task_box_controls = []
        self._status_labels = []
        self._persistent_headings = []
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
        self.step_list = StepList(navigation, PAGES)
        dc = wx.ClientDC(self.step_list)
        step_width = max(dc.GetTextExtent(title)[0] for title in PAGES) + 28
        step_height = sum(self.step_list.OnMeasureItem(index) for index in range(len(PAGES))) + 2
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
        self.build_netclass_types(self.pages[1])
        self.build_layer_assignment(self.pages[2])
        self.build_routing_permissions(self.pages[3])
        self.build_connector_assignments(self.pages[4])
        self.build_gangs(self.pages[5])
        self.build_rf_transitions(self.pages[6])
        self.build_anchors(self.pages[7])
        self.build_outline(self.pages[8])
        self.build_review(self.pages[9])
        self.build_results(self.pages[10])
        self.book.Bind(wx.EVT_BOOKCTRL_PAGE_CHANGED, self.page_changed)
        body.Add(self.book, 1, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 10)
        outer.Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        status_panel = wx.Panel(self, style=wx.BORDER_SIMPLE)
        self.status_panel = status_panel
        status_panel.SetBackgroundColour(ui_palette().panel)
        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_heading = wx.StaticText(status_panel, label='Status')
        status_font = status_heading.GetFont()
        status_font.SetWeight(wx.FONTWEIGHT_BOLD)
        status_heading.SetFont(status_font)
        status_sizer.Add(status_heading, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.status = wx.StaticText(
            status_panel,
            label='Ready',
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
        self.delete_settings_button = self.button(
            self, 'Delete workflow settings', self.delete_workflow_settings,
        )
        self.delete_settings_button.SetToolTip(
            'Delete the saved project settings sidecar. Current settings remain open until this dialog closes.'
        )
        footer.Add(self.delete_settings_button, 0, wx.RIGHT, 8)
        self.reset_settings_button = self.button(
            self, 'Reset workflow settings', self.reset_workflow_settings,
        )
        self.reset_settings_button.SetToolTip(
            'Clear the current workflow to its initial state. The saved settings sidecar is unchanged.'
        )
        footer.Add(self.reset_settings_button, 0, wx.RIGHT, 8)
        footer.AddStretchSpacer()
        footer.Add(self.button(self, 'Previous', lambda e: self.navigate(-1)), 0, wx.RIGHT, 8)
        footer.Add(self.button(self, 'Next', lambda e: self.navigate(1)), 0, wx.RIGHT, 8)
        footer.Add(self.button(self, 'Close', self.close), 0)
        outer.Add(footer, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(outer)
        display_index = wx.Display.GetFromPoint(wx.GetMousePosition())
        if display_index == wx.NOT_FOUND:
            display_index = 0
        display_area = wx.Display(display_index).GetClientArea()
        minimum_width = min(960, max(720, int(display_area.width * .82)))
        minimum_height = min(680, max(560, int(display_area.height * .74)))
        self.SetMinSize((minimum_width, minimum_height))
        self.SetSize((min(1600, max(minimum_width, int(display_area.width * .94))),
                      min(920, max(minimum_height, int(display_area.height * .90)))))
        self.Bind(wx.EVT_CLOSE, self.close)
        self.Bind(wx.EVT_SYS_COLOUR_CHANGED, self.system_colours_changed)
        self.apply_theme()
        self.restore()
        self.Layout()
        for page in self.pages:
            page.FitInside()
        self.refresh()

    def select_step(self, event):
        self.book.SetSelection(event.GetSelection())
        self.step_list.Refresh()

    def page_changed(self, event):
        # Book events are command events and nested notebooks can propagate their
        # page index here. Only the workflow book owns the step selection.
        if event.GetEventObject() is not self.book:
            event.Skip()
            return
        if self.step_list.GetSelection() != event.GetSelection():
            self.step_list.SetSelection(event.GetSelection())
            self.step_list.Refresh()
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

    def _style_grid(self, view):
        palette = ui_palette()
        view.SetDefaultCellBackgroundColour(palette.window)
        view.SetDefaultCellTextColour(palette.text)
        view.SetLabelBackgroundColour(palette.heading)
        view.SetLabelTextColour(palette.panel_text)
        view.SetSelectionBackground(palette.highlight)
        view.SetSelectionForeground(palette.highlight_text)

    @staticmethod
    def _fit_grid_contents(view, columns):
        """Size a compact grid from its headers, values and bounded column roles."""
        dc = wx.ClientDC(view)
        for column, (values, minimum, maximum) in enumerate(columns):
            dc.SetFont(view.GetLabelFont())
            measured = dc.GetTextExtent(view.GetColLabelValue(column))[0]
            dc.SetFont(view.GetDefaultCellFont())
            measured = max(
                [measured] + [dc.GetTextExtent(str(value))[0] for value in values],
            )
            view.SetColSize(column, max(minimum, min(maximum, measured + 28)))
        width = sum(view.GetColSize(column) for column in range(view.GetNumberCols()))
        width += view.GetNumberCols() + 2
        height = view.GetColLabelSize() + sum(
            view.GetRowSize(row) for row in range(view.GetNumberRows())
        ) + 2
        view.SetMinSize((width, height))
        view.SetMaxSize((width, height))

    def _style_status(self, control, state):
        palette = ui_palette()
        background = {
            'success': palette.success,
            'warning': palette.warning,
            'error': palette.error,
        }.get(state, palette.panel)
        control._workflow_status_state = state
        control.SetBackgroundColour(background)
        control.SetForegroundColour(palette.text if state != 'neutral' else palette.panel_text)
        control.Refresh()

    def _style_heading(self, control):
        palette = ui_palette()
        control.SetBackgroundColour(palette.heading)
        control.SetForegroundColour(palette.panel_text)

    def _style_mapper_theme(self):
        palette = ui_palette()
        for column in range(self.mapper.GetNumberCols()):
            self.mapper.SetCellTextColour(0, column, palette.text)
        self.mapper.SetCellBackgroundColour(0, 0, palette.assigned)
        self.mapper.SetCellBackgroundColour(0, 1, palette.assigned)
        for row in range(1, self.mapper.GetNumberRows()):
            background = palette.alternate if row % 2 == 0 else palette.window
            for column in range(self.mapper.GetNumberCols()):
                self.mapper.SetCellBackgroundColour(row, column, background)
                self.mapper.SetCellTextColour(row, column, palette.text)
        self._style_assignment_row()

    def apply_theme(self):
        """Reapply semantic colours after startup or an operating-system theme change."""
        palette = ui_palette()
        self.step_list.apply_theme()
        self.status_panel.SetBackgroundColour(palette.panel)
        self.status_panel.SetForegroundColour(palette.panel_text)
        self.status.SetForegroundColour(palette.panel_text)
        for box, controls in self._task_box_controls:
            box.SetBackgroundColour(palette.info)
            box.SetForegroundColour(palette.text)
            for control in controls:
                control.SetForegroundColour(palette.text)
        for control in self._persistent_headings:
            self._style_heading(control)
        for control in self._status_labels:
            self._style_status(control, getattr(control, '_workflow_status_state', 'neutral'))
        for view in (self.mapper, self.netclass_type_grid, self.layer_map, self.rules,
                     self.gang_rows):
            self._style_grid(view)
        self._style_mapper_theme()
        if hasattr(self, 'spreadsheet_feedback'):
            self._set_spreadsheet_feedback(
                self.spreadsheet_feedback.GetLabel().strip(),
                getattr(self, '_spreadsheet_feedback_error', False),
            )
        self.refresh_layers()
        self.refresh_gangs()
        self.refresh_rf_transitions()
        self.refresh_connector_assignments(True)
        for preview in (self.pattern_view, self.outline_view):
            preview.Refresh()
        self.Layout()
        for page in self.pages:
            page.Refresh()

    def system_colours_changed(self, event):
        self.apply_theme()
        event.Skip()

    def note(self, parent, text):
        label = wx.StaticText(parent, label=text)
        label.Wrap(820)
        parent.GetSizer().Add(label, 0, wx.ALL | wx.EXPAND, 8)
        return label

    def task_box(self, parent, title, subtitle, description):
        box = wx.Panel(parent, style=wx.BORDER_SIMPLE)
        palette = ui_palette()
        box.SetBackgroundColour(palette.info)
        box.SetForegroundColour(palette.text)
        sizer = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(box, label=title)
        font = heading.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(font)
        heading.SetForegroundColour(palette.text)
        sizer.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        task = wx.StaticText(box, label=subtitle)
        task_font = task.GetFont()
        task_font.SetWeight(wx.FONTWEIGHT_BOLD)
        task.SetFont(task_font)
        task.SetForegroundColour(palette.text)
        sizer.Add(task, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        detail = wx.StaticText(box, label=description)
        detail.SetForegroundColour(palette.text)
        detail.Wrap(820)
        sizer.Add(detail, 0, wx.ALL, 10)
        box.SetSizer(sizer)
        parent.GetSizer().Add(box, 0, wx.EXPAND | wx.ALL, 8)
        self._task_box_controls.append((box, (heading, task, detail)))
        return box

    def status_label(self, parent, label):
        control = wx.StaticText(parent, label=label, style=wx.BORDER_SIMPLE)
        font = control.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        control.SetFont(font)
        control.SetMinSize((190, -1))
        self._status_labels.append(control)
        self._style_status(control, 'neutral')
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

    def dimension_field(self, parent, label, value='0', unit='mil', invalidate=True):
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(parent, label=label), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        control = DimensionInput(parent, value, unit)
        row.Add(control, 1, wx.EXPAND)
        if invalidate:
            control.bind_changed(self.input_changed)
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

    def input_changed(self, event=None):
        self.controller.changed()
        if hasattr(self, 'view_button'): self.refresh()
        if event is not None:
            event.Skip()

    def build_spreadsheet(self, panel):
        self.task_box(
            panel,
            'Netlist source',
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
        palette = ui_palette()
        self.mapper.SetCellBackgroundColour(0, 0, palette.assigned)
        self.mapper.SetCellTextColour(0, 0, palette.text)
        self.mapper.SetCellRenderer(0, 0, AssignmentRenderer(False))
        for row_index in range(1, SPREADSHEET_PREVIEW_ROWS + 1):
            for column in range(len(HEADERS) + 2):
                self.mapper.SetReadOnly(row_index, column)
                self.mapper.SetCellBackgroundColour(
                    row_index, column,
                    palette.alternate if row_index % 2 == 0 else palette.window,
                )
                self.mapper.SetCellTextColour(row_index, column, palette.text)
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
        self._spreadsheet_feedback_error = error
        self.spreadsheet_feedback.SetLabel(message or ' ')
        palette = ui_palette()
        colour = palette.error_text if error else palette.text
        self.spreadsheet_feedback.SetForegroundColour(colour)
        self.spreadsheet_feedback.Wrap(780)
        self.spreadsheet_feedback.GetParent().Layout()

    def _set_worksheet_choices(self, names, selected=''):
        """Populate the native choice with an explicit blank selection."""
        names = list(names)
        was_updating = self._updating_spreadsheet
        self._updating_spreadsheet = True
        try:
            self.sheet.Set([''] + names)
            if selected in names:
                self.sheet.SetStringSelection(selected)
            else:
                self.sheet.SetSelection(0)
        finally:
            self._updating_spreadsheet = was_updating

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
        was_updating = self._updating_spreadsheet
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
            self._updating_spreadsheet = was_updating

    def _style_assignment_row(self):
        values = [self.mapper.GetCellValue(0, column).strip()
                  for column in range(2, len(HEADERS) + 2)]
        counts = {value: values.count(value) for value in set(values) if value}
        palette = ui_palette()
        for column, value in enumerate(values, 2):
            if not value:
                background = palette.warning
            elif counts[value] > 1:
                background = palette.error
            else:
                background = palette.assigned
            self.mapper.SetCellBackgroundColour(0, column, background)
            self.mapper.SetCellTextColour(0, column, palette.text)
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

    def build_netclass_types(self, panel):
        self.task_box(
            panel, 'Netclass type', 'Classify each imported netclass as RF or DC',
            'Type controls connector handling: RF uses individual arrayed connector locations; DC uses one approved multi-pin connector. Auto assign follows the project reference convention only when its evidence is unambiguous.',
        )
        box = wx.StaticBox(panel, label='Netclass classifications')
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        instruction = wx.BoxSizer(wx.HORIZONTAL)
        instruction.AddStretchSpacer()
        self.netclass_auto_assign = self.button(box, 'Auto assign', self.auto_assign_netclass_types)
        self.netclass_auto_assign.SetToolTip(
            'Fill blank types from exact J# / J### reference conventions and the exact DC netclass; preserve manual selections.'
        )
        instruction.Add(self.netclass_auto_assign, 0, wx.LEFT, 12)
        sizer.Add(instruction, 0, wx.EXPAND | wx.ALL, 8)
        self.netclass_type_grid = grid.Grid(box)
        self.netclass_type_grid.CreateGrid(0, 4)
        self.netclass_type_grid.SetRowLabelSize(0)
        for column, (label, width) in enumerate(
            (('Netclass', 140), ('Nets', 70), ('Connectors', 140), ('Type', 90)),
        ):
            self.netclass_type_grid.SetColLabelValue(column, label)
            self.netclass_type_grid.SetColSize(column, width)
        self.netclass_type_grid.SetMinSize((-1, self.netclass_type_grid.GetColLabelSize() + 2))
        self.netclass_type_grid.SetMaxSize((-1, self.netclass_type_grid.GetColLabelSize() + 2))
        self.netclass_type_grid.Bind(grid.EVT_GRID_CELL_CHANGED, self.netclass_type_changed)
        self.netclass_type_grid.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.netclass_type_clicked)
        sizer.Add(self.netclass_type_grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.netclass_type_status = self.status_label(box, '0 of 0 assigned')
        sizer.Add(self.netclass_type_status, 0, wx.ALL, 8)
        panel.GetSizer().Add(sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

    def refresh_netclass_types(self):
        view = self.netclass_type_grid
        if view.GetNumberRows():
            view.DeleteRows(0, view.GetNumberRows())
        records = self.controller.table.records if self.controller.table else ()
        names = sorted({record.netclass for record in records})
        if names:
            view.AppendRows(len(names))
        column_values = [[], [], [], ['RF', 'DC']]
        for row, name in enumerate(names):
            matching = [record for record in records if record.netclass == name]
            values = (name, str(len(matching)),
                      ', '.join(sorted({record.connector for record in matching})),
                      self.controller.config['netclass_types'].get(name, ''))
            for column, value in enumerate(values):
                column_values[column].append(value)
            for column, value in enumerate(values):
                view.SetCellValue(row, column, value)
                if column < 3:
                    view.SetReadOnly(row, column)
            view.SetCellEditor(row, 3, grid.GridCellChoiceEditor(['RF', 'DC'], False))
            self._style_netclass_type_cell(row)
        self._fit_grid_contents(view, (
            (column_values[0], 110, 240),
            (column_values[1], 60, 90),
            (column_values[2], 110, 260),
            (column_values[3], 80, 110),
        ))
        assigned = sum(self.controller.config['netclass_types'].get(name) in ('RF', 'DC')
                       for name in names)
        self.netclass_type_status.SetLabel(f'{assigned} of {len(names)} assigned')
        complete = bool(names) and assigned == len(names)
        self._style_status(self.netclass_type_status, 'success' if complete else 'warning')
        self.pages[1].FitInside()

    def _style_netclass_type_cell(self, row):
        assigned = self.netclass_type_grid.GetCellValue(row, 3) in ('RF', 'DC')
        palette = ui_palette()
        self.netclass_type_grid.SetCellBackgroundColour(
            row, 3, palette.assigned if assigned else palette.warning,
        )
        self.netclass_type_grid.SetCellTextColour(row, 3, palette.text)
        self.netclass_type_grid.SetCellRenderer(row, 3, AssignmentRenderer(True))
        self.netclass_type_grid.ForceRefresh()

    def auto_assign_netclass_types(self, event):
        if not self.controller.table:
            raise ValueError('Import a netlist before auto assigning netclass types')
        from .core.connector_types import suggest_netclass_types
        suggestions, conflicts, _unresolved = suggest_netclass_types(self.controller.table.records)
        assigned = preserved = 0
        for row in range(self.netclass_type_grid.GetNumberRows()):
            name = self.netclass_type_grid.GetCellValue(row, 0)
            if self.netclass_type_grid.GetCellValue(row, 3):
                preserved += 1
                continue
            if name in suggestions:
                self.netclass_type_grid.SetCellValue(row, 3, suggestions[name])
                self.controller.config['netclass_types'][name] = suggestions[name]
                self._style_netclass_type_cell(row)
                assigned += 1
        if assigned:
            self.controller.changed()
        self.refresh_netclass_types()
        self.refresh_gangs()
        self.refresh_connector_assignments(True)
        remaining = self.netclass_type_grid.GetNumberRows() - sum(
            bool(self.netclass_type_grid.GetCellValue(row, 3))
            for row in range(self.netclass_type_grid.GetNumberRows())
        )
        message = f'Auto assigned {assigned} netclass type{("" if assigned == 1 else "s")}.'
        if preserved:
            message += f' Preserved {preserved} existing assignment{("" if preserved == 1 else "s")}.'
        if conflicts:
            message += f' {len(conflicts)} conflicting netclass{("" if len(conflicts) == 1 else "es")} require review.'
        elif remaining:
            message += f' {remaining} require manual selection.'
        self.status.SetLabel(message)

    def netclass_type_clicked(self, event):
        if event.GetCol() == 3:
            wx.CallAfter(self._enable_netclass_type_editor, event.GetRow())
        event.Skip()

    def _enable_netclass_type_editor(self, row):
        if not self._closing:
            self.netclass_type_grid.SetGridCursor(row, 3)
            self.netclass_type_grid.MakeCellVisible(row, 3)
            self.netclass_type_grid.EnableCellEditControl()

    def netclass_type_changed(self, event):
        if event.GetCol() != 3:
            event.Skip(); return
        row = event.GetRow()
        name = self.netclass_type_grid.GetCellValue(row, 0)
        kind = self.netclass_type_grid.GetCellValue(row, 3)
        self.controller.set_netclass_type(name, kind)
        self._style_netclass_type_cell(row)
        self.refresh_netclass_types()
        self.refresh_gangs()
        self.refresh_connector_assignments(True)
        self.status.SetLabel(f'{name} classified as {kind}. Board files were not modified.')
        event.Skip()

    def build_layer_assignment(self, panel):
        self.task_box(
            panel, 'Layer assignment', 'Assign each netlist layer to the board stackup',
            'Map layer names from the imported netlist to enabled KiCad copper layers. Auto assign fills only unambiguous numeric matches and preserves manual selections. Changes take effect immediately and are used by validation and RF artifact checks.',
        )
        layer_box = wx.StaticBox(panel, label='Layer mappings')
        layer_sizer = wx.StaticBoxSizer(layer_box, wx.VERTICAL)
        instruction = wx.BoxSizer(wx.HORIZONTAL)
        instruction.AddStretchSpacer()
        self.layer_auto_assign = self.button(layer_box, 'Auto assign', self.auto_assign_layers)
        self.layer_auto_assign.SetToolTip(
            'Fill blank mappings with unambiguous layer-number matches; preserve manual selections.'
        )
        instruction.Add(self.layer_auto_assign, 0, wx.LEFT, 12)
        layer_sizer.Add(instruction, 0, wx.EXPAND | wx.ALL, 8)

        self.layer_map = grid.Grid(layer_box)
        self.layer_map.CreateGrid(0, 2)
        self.layer_map.SetRowLabelSize(0)
        self.layer_map.SetColLabelValue(0, 'Netlist layer')
        self.layer_map.SetColLabelValue(1, 'KiCad layer')
        self.layer_map.SetColSize(0, 140)
        self.layer_map.SetColSize(1, 140)
        self.layer_map.SetMinSize((-1, self.layer_map.GetColLabelSize() + 2))
        self.layer_map.SetMaxSize((-1, self.layer_map.GetColLabelSize() + 2))
        layer_sizer.Add(self.layer_map, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        status_row = wx.BoxSizer(wx.HORIZONTAL)
        self.layer_mapping_status = self.status_label(layer_box, '0 of 0 assigned')
        status_row.Add(self.layer_mapping_status, 0, wx.ALIGN_CENTER_VERTICAL)
        status_row.AddStretchSpacer()
        layer_sizer.Add(status_row, 0, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(layer_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
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
        self.refresh_netclass_types()
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
            self._fit_grid_contents(self.layer_map, (
                (names, 120, 220),
                (self.controller.copper_layers, 110, 220),
            ))
            assigned = sum(bool(self.controller.config['layer_mapping'].get(name)) for name in names)
            self.layer_mapping_status.SetLabel(f'{assigned} of {len(names)} assigned')
            self._style_status(
                self.layer_mapping_status,
                'success' if names and assigned == len(names) else 'warning',
            )
            self.layer_map.GetParent().Layout()
            self.pages[2].FitInside()

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
            self.pages[3].Layout()
        finally:
            self._updating_layers = False
        if hasattr(self, 'transition_list'):
            self.refresh_rf_transitions()

    def _style_layer_assignment_cell(self, row):
        assigned = bool(self.layer_map.GetCellValue(row, 1).strip())
        palette = ui_palette()
        self.layer_map.SetCellBackgroundColour(
            row, 1, palette.assigned if assigned else palette.warning,
        )
        self.layer_map.SetCellTextColour(row, 1, palette.text)
        self.layer_map.SetCellRenderer(row, 1, AssignmentRenderer(True))
        self.layer_map.ForceRefresh()

    def auto_assign_layers(self, event):
        from .core.layers import suggest_layer_mappings
        names = [self.layer_map.GetCellValue(row, 0)
                 for row in range(self.layer_map.GetNumberRows())]
        if not names:
            raise ValueError('Import a netlist before auto assigning layers')
        suggestions, _unresolved = suggest_layer_mappings(names, self.controller.copper_layers)
        assigned = preserved = 0
        for row, name in enumerate(names):
            if self.layer_map.GetCellValue(row, 1).strip():
                preserved += 1
                continue
            layer = suggestions.get(name)
            if not layer:
                continue
            self.layer_map.SetCellValue(row, 1, layer)
            self.controller.config['layer_mapping'][name] = layer
            self._style_layer_assignment_cell(row)
            assigned += 1
        if assigned:
            self.controller.changed()
        total_assigned = sum(bool(self.layer_map.GetCellValue(row, 1).strip())
                             for row in range(self.layer_map.GetNumberRows()))
        self.layer_mapping_status.SetLabel(f'{total_assigned} of {len(names)} assigned')
        remaining = len(names) - total_assigned
        message = f'Auto assigned {assigned} layer{"" if assigned == 1 else "s"}.'
        if preserved:
            message += f' Preserved {preserved} existing assignment{"" if preserved == 1 else "s"}.'
        if remaining:
            message += (f' {remaining} layer{"" if remaining == 1 else "s"} '
                        f'{"needs" if remaining == 1 else "need"} manual selection.')
        self.status.SetLabel(message)
        self.refresh()

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
            'Assign the workbook interposer, RF gangs and DC connectors to read-only KiCad library parts. '
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
            self._style_heading(heading)
            self._persistent_headings.append(heading)
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

        gangs_box = wx.StaticBox(panel, label='RF connector gang assignments')
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
        self.gang_assignment_all_controls = {}

        dc_box = wx.StaticBox(panel, label='DC connector assignments')
        dc = wx.StaticBoxSizer(dc_box, wx.VERTICAL)
        self.dc_assignment_rows = wx.Panel(dc_box)
        dc_grid = wx.FlexGridSizer(cols=4, hgap=0, vgap=0)
        dc_grid.AddGrowableCol(2, 1); dc_grid.AddGrowableCol(3, 1)
        self.dc_assignment_rows.SetSizer(dc_grid)
        dc.Add(self.dc_assignment_rows, 0, wx.EXPAND | wx.ALL, 8)
        self.dc_assignment_status = self.status_label(dc_box, '0 of 0 DC connectors assigned')
        dc.Add(self.dc_assignment_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(dc, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.dc_assignment_controls = {}
        self.dc_assignment_all_controls = {}

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

    def _clear_bulk_assignment(self, kind):
        self._bulk_assignments[kind] = {'names': (), 'symbol': {}, 'footprint': {}}
        controls = getattr(
            self,
            'gang_assignment_all_controls' if kind == 'rf' else 'dc_assignment_all_controls',
            {},
        )
        if controls:
            controls['symbol'].set_selection({})
            controls['footprint'].set_options((), {})
            controls['footprint'].Enable(False)

    def _set_gang_symbol(self, gang, selection, symbol, options):
        self.controller.set_gang(gang, {
            'symbol': dict(selection), 'part_id': '', 'library_id': '',
            'part_sha256': '', 'footprint_mpn': '',
            'value': symbol.value, 'symbol_mpn': symbol.mpn,
        })
        controls = self.gang_assignment_controls.get(gang)
        if controls:
            controls['symbol'].set_selection(selection)
            controls['footprint'].set_options(options, {})
            controls['footprint'].Enable(True)

    def assign_gang_symbol(self, gang, selection, symbol):
        self._clear_bulk_assignment('rf')
        options = self._allowed_footprint_specs(symbol)
        self._set_gang_symbol(gang, selection, symbol, options)
        if not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_all_gang_symbols(self, selection, symbol):
        names = self.controller.gang_names()
        options = self._allowed_footprint_specs(symbol)
        self._bulk_assignments['rf'] = {
            'names': names, 'symbol': dict(selection), 'footprint': {},
        }
        for gang in names:
            self._set_gang_symbol(gang, selection, symbol, options)
        controls = self.gang_assignment_all_controls
        controls['symbol'].set_selection(selection)
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
        self._clear_bulk_assignment('rf')
        self.controller.set_gang(gang, {
            'part_id': selection['path'], 'library_id': selection['library_id'],
            'part_sha256': selection.get('sha256', ''),
        })
        self._refresh_assignment_status()

    def assign_all_gang_footprints(self, selection):
        if not selection:
            return
        names = self.controller.gang_names()
        state = self._bulk_assignments['rf']
        state['names'] = names
        state['footprint'] = dict(selection)
        for gang in names:
            self.controller.set_gang(gang, {
                'part_id': selection['path'], 'library_id': selection['library_id'],
                'part_sha256': selection.get('sha256', ''),
            })
            controls = self.gang_assignment_controls.get(gang)
            if controls:
                controls['footprint'].set_options(controls['footprint'].options, selection)
        self.gang_assignment_all_controls['footprint'].set_options(
            self.gang_assignment_all_controls['footprint'].options, selection,
        )
        self._refresh_assignment_status()

    def _set_dc_symbol(self, reference, selection, symbol, options):
        prior = self.controller.dc_connector(reference)
        self.controller.set_dc_connector(reference, {
            'symbol': dict(selection), 'path': '', 'library_id': '', 'sha256': '',
            'footprint_mpn': '', 'value': symbol.value, 'symbol_mpn': symbol.mpn,
            'anchor': prior.get('anchor', [0, 0]),
            'anchor_circle': prior.get('anchor_circle', {}),
            'anchor_accepted': prior.get('anchor_accepted', False),
        })
        controls = self.dc_assignment_controls.get(reference)
        if controls:
            controls['symbol'].set_selection(selection)
            controls['footprint'].set_options(options, {})
            controls['footprint'].Enable(True)

    def assign_dc_symbol(self, reference, selection, symbol):
        self._clear_bulk_assignment('dc')
        options = self._allowed_footprint_specs(symbol)
        self._set_dc_symbol(reference, selection, symbol, options)
        if symbol.unit_count != 1:
            self.status.SetLabel(f'{symbol.library_id} is multi-unit; choose a single-unit DC connector symbol.')
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_all_dc_symbols(self, selection, symbol):
        names = self.controller.dc_connector_names()
        options = self._allowed_footprint_specs(symbol)
        self._bulk_assignments['dc'] = {
            'names': names, 'symbol': dict(selection), 'footprint': {},
        }
        for reference in names:
            self._set_dc_symbol(reference, selection, symbol, options)
        controls = self.dc_assignment_all_controls
        controls['symbol'].set_selection(selection)
        controls['footprint'].set_options(options, {})
        controls['footprint'].Enable(True)
        if symbol.unit_count != 1:
            self.status.SetLabel(f'{symbol.library_id} is multi-unit; choose a single-unit DC connector symbol.')
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_dc_footprint(self, reference, selection):
        if not selection:
            return
        self._clear_bulk_assignment('dc')
        from .core.libraries import read_footprint
        approved = read_footprint(selection['path'], selection['library_id'],
                                  selection.get('sha256'))
        self.controller.set_dc_connector(reference, {
            'path': approved.path, 'library_id': approved.library_id,
            'sha256': approved.sha256, 'footprint_mpn': approved.mpn,
        })
        self._refresh_assignment_status()

    def assign_all_dc_footprints(self, selection):
        if not selection:
            return
        from .core.libraries import read_footprint
        approved = read_footprint(selection['path'], selection['library_id'],
                                  selection.get('sha256'))
        names = self.controller.dc_connector_names()
        state = self._bulk_assignments['dc']
        state['names'] = names
        state['footprint'] = dict(selection)
        for reference in names:
            self.controller.set_dc_connector(reference, {
                'path': approved.path, 'library_id': approved.library_id,
                'sha256': approved.sha256, 'footprint_mpn': approved.mpn,
            })
            controls = self.dc_assignment_controls.get(reference)
            if controls:
                controls['footprint'].set_options(controls['footprint'].options, selection)
        self.dc_assignment_all_controls['footprint'].set_options(
            self.dc_assignment_all_controls['footprint'].options, selection,
        )
        self._refresh_assignment_status()

    def _configured_symbol(self, selection, require_two_pin):
        if not selection.get('path') or not selection.get('library_id'):
            return None
        try:
            return self._symbol_from_spec(selection, require_two_pin)
        except (OSError, ValueError):
            return None

    def _add_all_assignment_row(self, parent, sizer, kind, names):
        """Add a visually separated action row that assigns every listed connector."""
        controls_attribute = (
            'gang_assignment_all_controls' if kind == 'rf'
            else 'dc_assignment_all_controls'
        )
        if not names:
            self._bulk_assignments[kind] = {'names': (), 'symbol': {}, 'footprint': {}}
            setattr(self, controls_attribute, {})
            return
        state = self._bulk_assignments[kind]
        if tuple(state.get('names', ())) != tuple(names):
            state = {'names': (), 'symbol': {}, 'footprint': {}}
            self._bulk_assignments[kind] = state
        label = wx.StaticText(parent, label='All', style=wx.BORDER_SIMPLE)
        quantity = wx.StaticText(parent, label='—', style=wx.BORDER_SIMPLE)
        for control, width in ((label, 120), (quantity, 100)):
            font = control.GetFont()
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            control.SetFont(font)
            control.SetMinSize((width, 38))
            control.SetBackgroundColour(ui_palette().info)
            control.SetForegroundColour(ui_palette().text)
        if kind == 'rf':
            symbol_control = SymbolAssignmentCtrl(
                parent, self, 'Choose symbol for all RF gangs', True,
                self.assign_all_gang_symbols,
            )
            footprint_control = FootprintAssignmentCtrl(
                parent, self.assign_all_gang_footprints,
            )
        else:
            symbol_control = SymbolAssignmentCtrl(
                parent, self, 'Choose symbol for all DC connectors', False,
                self.assign_all_dc_symbols,
            )
            footprint_control = FootprintAssignmentCtrl(
                parent, self.assign_all_dc_footprints,
            )
        symbol_spec = state.get('symbol', {})
        symbol_control.set_selection(symbol_spec)
        symbol = self._configured_symbol(symbol_spec, kind == 'rf')
        footprint_control.set_options(
            self._allowed_footprint_specs(symbol), state.get('footprint', {}),
        )
        footprint_control.Enable(bool(symbol))
        for control in (label, quantity, symbol_control, footprint_control):
            sizer.Add(
                control,
                1 if control in (symbol_control, footprint_control) else 0,
                wx.EXPAND,
            )
        setattr(self, controls_attribute, {
            'symbol': symbol_control, 'footprint': footprint_control,
        })
        for _column in range(4):
            separator = wx.StaticLine(parent)
            separator.SetMinSize((-1, 9))
            sizer.Add(separator, 1, wx.EXPAND | wx.TOP | wx.BOTTOM, 4)

    def refresh_connector_assignments(self, force=False):
        reference = self._interposer_reference()
        names = self.controller.gang_names()
        dc_names = self.controller.dc_connector_names()
        query = self.assignment_filter.GetValue().casefold() if hasattr(self, 'assignment_filter') else ''
        signature = (reference, names, dc_names, query)
        if not force and signature == self._assignment_signature:
            self._refresh_assignment_status()
            return
        self._assignment_signature = signature
        self.interposer_ref_display.SetLabel(reference or '—')
        interposer = self.controller.config['interposers'].get(reference, {}) if reference else {}
        if hasattr(self, 'anchor_interposer_ref'):
            self.anchor_interposer_ref.SetLabel('Ref des: ' + (reference or '—'))
            pose = interposer.get('pose', [0, 0, 0])
            pose_units = interposer.get('pose_units', ['mil', 'mil'])
            self.interposer_x.set_mm(pose[0], pose_units[0])
            self.interposer_y.set_mm(pose[1], pose_units[1])
            self.interposer_rotation.ChangeValue(str(pose[2]))
            self.interposer_pose_status.SetLabel(
                f'{reference} placement accepted' if reference and interposer.get('pose_accepted')
                else 'Interposer placement required'
            )
            self._style_status(
                self.interposer_pose_status,
                'success' if reference and interposer.get('pose_accepted') else 'warning',
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
            self._style_heading(heading); heading.SetMinSize((width, 34))
            sizer.Add(heading, 1 if label in ('Symbol', 'Footprint') else 0, wx.EXPAND)
        self._add_all_assignment_row(
            self.gang_assignment_rows, sizer, 'rf', names,
        )
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

        dc_sizer = self.dc_assignment_rows.GetSizer()
        dc_sizer.Clear(True)
        self.dc_assignment_controls = {}
        for label, width in (('Ref des', 120), ('Pins used', 100), ('Symbol', 340), ('Footprint', 380)):
            heading = wx.StaticText(self.dc_assignment_rows, label=label, style=wx.BORDER_SIMPLE)
            font = heading.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD); heading.SetFont(font)
            self._style_heading(heading); heading.SetMinSize((width, 34))
            dc_sizer.Add(heading, 1 if label in ('Symbol', 'Footprint') else 0, wx.EXPAND)
        self._add_all_assignment_row(
            self.dc_assignment_rows, dc_sizer, 'dc', dc_names,
        )
        for name in dc_names:
            if query and query not in name.casefold():
                continue
            selection = self.controller.dc_connector(name)
            used = sum(record.connector == name for record in self.controller.table.records)
            reference_label = wx.StaticText(self.dc_assignment_rows, label=name, style=wx.BORDER_SIMPLE)
            reference_label.SetMinSize((120, 38))
            pins = wx.StaticText(self.dc_assignment_rows, label=str(used), style=wx.BORDER_SIMPLE)
            pins.SetMinSize((100, 38))
            symbol_control = SymbolAssignmentCtrl(
                self.dc_assignment_rows, self, 'Choose DC connector symbol', False,
                lambda spec, symbol, connector=name: self.assign_dc_symbol(connector, spec, symbol),
            )
            footprint_control = FootprintAssignmentCtrl(
                self.dc_assignment_rows,
                lambda spec, connector=name: self.assign_dc_footprint(connector, spec),
            )
            symbol_spec = selection.get('symbol', {})
            symbol_control.set_selection(symbol_spec)
            symbol = self._configured_symbol(symbol_spec, False)
            footprint_control.set_options(self._allowed_footprint_specs(symbol), selection)
            footprint_control.Enable(bool(symbol))
            for control in (reference_label, pins, symbol_control, footprint_control):
                dc_sizer.Add(control, 1 if control in (symbol_control, footprint_control) else 0,
                             wx.EXPAND)
            self.dc_assignment_controls[name] = {
                'symbol': symbol_control, 'footprint': footprint_control,
            }
        self.dc_assignment_rows.Layout()
        self.pages[4].FitInside()
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
        dc_names = self.controller.dc_connector_names()
        assigned_dc = sum(bool(self.controller.dc_connector(name).get('symbol', {}).get('library_id') and
                               self.controller.dc_connector(name).get('library_id'))
                          for name in dc_names)
        self.dc_assignment_status.SetLabel(
            f'{assigned_dc} of {len(dc_names)} DC connectors assigned'
        )
        for control, complete in (
            (self.interposer_assignment_status, assigned_interposer == interposer_total and bool(interposer_total)),
            (self.gang_assignment_status, assigned_gangs == len(names) and bool(names)),
            (self.dc_assignment_status, assigned_dc == len(dc_names) and bool(dc_names)),
        ):
            self._style_status(control, 'success' if complete else 'warning')
        if hasattr(self, 'transition_list'):
            self.refresh_rf_transitions()

    def build_gangs(self, panel):
        self.task_box(
            panel, 'Define RF gangs', 'Define each RF connector gang',
            'Set the position pattern, numbering, connector orientation and mounting side. '
            'Valid changes take effect immediately for the selected gang.',
        )
        body = wx.BoxSizer(wx.HORIZONTAL)

        selector_box = wx.StaticBox(panel, label='RF gangs')
        selector = wx.StaticBoxSizer(selector_box, wx.VERTICAL)
        self.gang_filter = wx.SearchCtrl(selector_box, style=wx.TE_PROCESS_ENTER)
        self.gang_filter.SetDescriptiveText('Search gangs')
        self.gang_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_gang_list())
        selector.Add(self.gang_filter, 0, wx.EXPAND | wx.ALL, 8)
        selector_box.SetMinSize((250, -1))
        selector_box.SetMaxSize((270, -1))
        self.gang_list = wx.ListCtrl(
            selector_box, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
        )
        self.gang_list.SetMinSize((230, 60))
        self.gang_list.SetMaxSize((250, 60))
        for column, (heading, width) in enumerate((
            ('Gang', 58), ('Positions', 68), ('Status', 94),
        )):
            self.gang_list.InsertColumn(column, heading, width=width)
        self.gang_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.guard(self.select_dashboard_gang))
        selector.Add(self.gang_list, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.copy_gang_button = self.button(
            selector_box, 'Copy settings…', self.copy_gang,
        )
        selector.Add(self.copy_gang_button, 0, wx.ALL, 8)
        body.Add(selector, 0, wx.ALIGN_TOP | wx.RIGHT, 8)

        editor_box = wx.StaticBox(panel, label='Select an RF gang')
        editor = wx.StaticBoxSizer(editor_box, wx.VERTICAL)
        self.gang_editor_title = editor_box
        self.gang_notebook = wx.Notebook(editor_box)

        pattern_page = wx.Panel(self.gang_notebook)
        pattern_sizer = wx.BoxSizer(wx.VERTICAL)
        pattern_page.SetSizer(pattern_sizer)
        self.gang_rows = grid.Grid(pattern_page)
        self.gang_rows.CreateGrid(1, 3)
        self.gang_rows.SetRowLabelSize(0)
        self.gang_rows.SetColLabelValue(0, 'Row')
        self.gang_rows.SetColLabelValue(1, 'Positions')
        self.gang_rows.SetColLabelValue(2, 'X offset')
        self.gang_rows.SetColSize(0, 48)
        self.gang_rows.SetColSize(1, 80)
        self.gang_rows.SetColSize(2, 150)
        self.gang_rows.SetReadOnly(0, 0)
        self.gang_rows.SetMinSize((292, 180))
        self.gang_rows.Bind(grid.EVT_GRID_CELL_CHANGED, self.gang_draft_changed)
        pattern_sizer.Add(self.gang_rows, 0, wx.EXPAND | wx.ALL, 8)
        row_actions = wx.BoxSizer(wx.HORIZONTAL)
        row_actions.Add(self.button(pattern_page, '+', self.add_gang_row), 0, wx.RIGHT, 5)
        row_actions.Add(self.button(pattern_page, '−', self.remove_gang_row), 0, wx.RIGHT, 12)
        self.gang_position_total = wx.StaticText(pattern_page, label='0 positions total')
        row_actions.Add(self.gang_position_total, 0, wx.ALIGN_CENTER_VERTICAL)
        pattern_sizer.Add(row_actions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        dimensions = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        dimensions.Add(wx.StaticText(pattern_page, label='Position pitch'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_pitch_x = DimensionInput(pattern_page, value='100', compact=True)
        dimensions.Add(self.gang_pitch_x, 0, wx.ALIGN_LEFT)
        dimensions.Add(wx.StaticText(pattern_page, label='Row pitch'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_pitch_y = DimensionInput(pattern_page, value='100', compact=True)
        dimensions.Add(self.gang_pitch_y, 0, wx.ALIGN_LEFT)
        pattern_sizer.Add(dimensions, 0, wx.EXPAND | wx.ALL, 8)
        self.gang_notebook.AddPage(pattern_page, 'Pattern')

        placement_page = wx.Panel(self.gang_notebook)
        placement = wx.FlexGridSizer(cols=2, hgap=12, vgap=10)
        placement_page.SetSizer(placement)
        placement.Add(wx.StaticText(placement_page, label='Numbering'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_numbering_mode = wx.Choice(
            placement_page,
            choices=['Row major', 'Serpentine', 'Column major', 'Custom'],
        )
        placement.Add(self.gang_numbering_mode, 1, wx.EXPAND)
        placement.Add(wx.StaticText(placement_page, label='Custom numbering'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_custom_numbering = wx.TextCtrl(placement_page)
        self.gang_custom_numbering.SetToolTip('Comma-separated position indices in traversal order.')
        placement.Add(self.gang_custom_numbering, 1, wx.EXPAND)
        placement.Add(wx.StaticText(placement_page, label='Pattern rotation (degrees)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_pattern_rotation = wx.TextCtrl(placement_page, value='0')
        placement.Add(self.gang_pattern_rotation, 1, wx.EXPAND)
        placement.Add(wx.StaticText(placement_page, label='Connector orientation (degrees)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_connector_rotation = wx.TextCtrl(placement_page, value='0')
        placement.Add(self.gang_connector_rotation, 1, wx.EXPAND)
        placement.Add(wx.StaticText(placement_page, label='Connector mounting side'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_side = wx.Choice(placement_page, choices=['Front (F)', 'Back (B)'])
        placement.Add(self.gang_side, 1, wx.EXPAND)
        placement.Add((1, 1))
        self.mirror = wx.CheckBox(placement_page, label='Mirror position layout')
        placement.Add(self.mirror, 0, wx.ALIGN_CENTER_VERTICAL)
        placement.AddGrowableCol(1, 1)
        self.gang_notebook.AddPage(placement_page, 'Numbering')
        editor.Add(self.gang_notebook, 1, wx.EXPAND | wx.ALL, 8)
        self.gang_definition_status = self.status_label(editor_box, 'Select a gang')
        editor.Add(self.gang_definition_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        body.Add(editor, 1, wx.EXPAND | wx.RIGHT, 8)

        preview_box = wx.StaticBox(panel, label='Gang preview')
        preview_box.SetMinSize((285, -1))
        preview = wx.StaticBoxSizer(preview_box, wx.VERTICAL)
        self.pattern_view = PatternPreview(preview_box)
        preview.Add(self.pattern_view, 1, wx.EXPAND | wx.ALL, 8)
        self.gang_preview_status = self.status_label(preview_box, 'Select a gang')
        preview.Add(self.gang_preview_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        body.Add(preview, 1, wx.EXPAND)
        panel.GetSizer().Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        for control in (self.gang_custom_numbering, self.gang_pattern_rotation,
                        self.gang_connector_rotation):
            control.Bind(wx.EVT_TEXT, self.gang_draft_changed)
        self.gang_pitch_x.bind_changed(self.gang_draft_changed)
        self.gang_pitch_y.bind_changed(self.gang_draft_changed)
        for control in (self.gang_numbering_mode, self.gang_side):
            control.Bind(wx.EVT_CHOICE, self.gang_draft_changed)
        self.mirror.Bind(wx.EVT_CHECKBOX, self.gang_draft_changed)
        self.gang_numbering_mode.Bind(wx.EVT_CHOICE, self.numbering_mode_changed)

    def _resize_gang_rows(self, count):
        current = self.gang_rows.GetNumberRows()
        if current < count:
            self.gang_rows.AppendRows(count - current)
        elif current > count:
            self.gang_rows.DeleteRows(count, current - count)
        for row in range(count):
            self.gang_rows.SetCellValue(row, 0, str(row + 1))
            self.gang_rows.SetReadOnly(row, 0)
            self.gang_rows.SetCellEditor(
                row, 2, DimensionCellEditor(),
            )
            if not self.gang_rows.GetCellValue(row, 2):
                self.gang_rows.SetCellValue(row, 2, '0 mil')
            self.gang_rows.SetCellRenderer(row, 2, DimensionCellRenderer())
        display_index = wx.Display.GetFromWindow(self)
        area = (wx.Display(display_index).GetClientArea()
                if display_index != wx.NOT_FOUND else wx.GetClientDisplayRect())
        row_height = max(1, self.gang_rows.GetDefaultRowSize())
        maximum_visible = max(5, min(12, int(area.height * .32 / row_height)))
        visible_rows = max(5, min(count, maximum_visible))
        height = self.gang_rows.GetColLabelSize() + visible_rows * row_height + 2
        self.gang_rows.SetMinSize((-1, height))
        self.gang_rows.SetMaxSize((-1, height))
        if hasattr(self, 'pages') and len(self.pages) > 5:
            self.pages[5].FitInside()

    def _gang_editor_values(self):
        row_lengths, row_offsets, row_offset_units = [], [], []
        for row in range(self.gang_rows.GetNumberRows()):
            count = self.gang_rows.GetCellValue(row, 1).strip()
            offset = self.gang_rows.GetCellValue(row, 2).strip() or '0'
            if not count:
                raise ValueError(f'Row {row + 1}: enter a position count')
            row_lengths.append(int(count))
            offset_mm, offset_unit, _number_text = parse_dimension(
                offset, 'mil',
            )
            row_offsets.append(offset_mm)
            row_offset_units.append(offset_unit)
        mode = self.gang_numbering_mode.GetSelection()
        traversals = ('row-major', 'serpentine', 'column-major')
        numbering = []
        traversal = traversals[mode] if 0 <= mode < len(traversals) else 'row-major'
        if mode == 3:
            numbering = [value.strip() for value in self.gang_custom_numbering.GetValue().split(',')
                         if value.strip()]
        return {
            'row_lengths': row_lengths,
            'row_offsets': row_offsets,
            'pitch_x': self.gang_pitch_x.value_mm(),
            'pitch_y': self.gang_pitch_y.value_mm(),
            'display_units': {
                'pitch_x': self.gang_pitch_x.selected_unit(),
                'pitch_y': self.gang_pitch_y.selected_unit(),
                'row_offsets': row_offset_units,
                'anchor_x': self.controller.gang(self.current_gang).get(
                    'display_units', {},
                ).get('anchor_x', 'mil'),
                'anchor_y': self.controller.gang(self.current_gang).get(
                    'display_units', {},
                ).get('anchor_y', 'mil'),
            },
            'traversal': traversal,
            'numbering': numbering,
            'rotation': float(self.gang_pattern_rotation.GetValue()),
            'connector_rotation': float(self.gang_connector_rotation.GetValue()),
            'mirror': self.mirror.GetValue(),
            'side': 'B' if self.gang_side.GetSelection() == 1 else 'F',
            'defined': True,
        }

    def _capture_gang_edit(self):
        return {
            'row_lengths': [self.gang_rows.GetCellValue(row, 1)
                            for row in range(self.gang_rows.GetNumberRows())],
            'row_offsets': [self.gang_rows.GetCellValue(row, 2)
                            for row in range(self.gang_rows.GetNumberRows())],
            'pitch_x': self.gang_pitch_x.GetValue(),
            'pitch_x_unit': self.gang_pitch_x.units.GetStringSelection(),
            'pitch_y': self.gang_pitch_y.GetValue(),
            'pitch_y_unit': self.gang_pitch_y.units.GetStringSelection(),
            'numbering_mode': self.gang_numbering_mode.GetSelection(),
            'numbering': self.gang_custom_numbering.GetValue(),
            'rotation': self.gang_pattern_rotation.GetValue(),
            'connector_rotation': self.gang_connector_rotation.GetValue(),
            'side': self.gang_side.GetSelection(),
            'mirror': self.mirror.GetValue(),
        }

    def _restore_gang_edit(self, edit):
        self._resize_gang_rows(len(edit['row_lengths']))
        for row, count in enumerate(edit['row_lengths']):
            self.gang_rows.SetCellValue(row, 1, count)
            self.gang_rows.SetCellValue(row, 2, edit['row_offsets'][row])
        self.gang_pitch_x.ChangeValue(edit['pitch_x'])
        self.gang_pitch_x.units.SetStringSelection(edit['pitch_x_unit'])
        self.gang_pitch_y.ChangeValue(edit['pitch_y'])
        self.gang_pitch_y.units.SetStringSelection(edit['pitch_y_unit'])
        self.gang_numbering_mode.SetSelection(edit['numbering_mode'])
        self.gang_custom_numbering.ChangeValue(edit['numbering'])
        self.gang_custom_numbering.Enable(edit['numbering_mode'] == 3)
        self.gang_pattern_rotation.ChangeValue(edit['rotation'])
        self.gang_connector_rotation.ChangeValue(edit['connector_rotation'])
        self.gang_side.SetSelection(edit['side'])
        self.mirror.SetValue(edit['mirror'])

    def _preview_gang_values(self, values):
        from .core.geometry import GangDefinition
        applied = self.controller.gang(self.current_gang)
        fields = GangDefinition.__dataclass_fields__
        combined = dict(applied)
        combined.update(values)
        combined['gang'] = self.current_gang
        definition = GangDefinition(**{key: value for key, value in combined.items()
                                       if key in fields})
        return definition.positions()

    def _validate_gang_population(self, points):
        expected = {record.connector_pin for record in self.controller.table.records
                    if record.connector == self.current_gang}
        if set(points) != expected:
            raise ValueError(
                f'Numbering must match {len(expected)} netlist positions exactly'
            )

    def _show_gang_definition(self, values, defined, clear_preview=False):
        if clear_preview:
            self.pattern_view.show(self.current_gang, {})
        points = None
        try:
            points = self._preview_gang_values(values)
            self.pattern_view.show(self.current_gang, points)
            self.gang_position_total.SetLabel(f'{len(points)} positions total')
            self._validate_gang_population(points)
        except (TypeError, ValueError) as error:
            if points is None and values.get('numbering'):
                try:
                    fallback = dict(values)
                    fallback['numbering'] = []
                    fallback_points = self._preview_gang_values(fallback)
                    self.pattern_view.show(self.current_gang, fallback_points)
                    self.gang_position_total.SetLabel(
                        f'{len(fallback_points)} positions total'
                    )
                except (TypeError, ValueError):
                    pass
            self.gang_definition_status.SetLabel('Needs input — ' + str(error))
            self._style_status(self.gang_definition_status, 'error')
            self.gang_preview_status.SetLabel('Last renderable geometry')
            self._style_status(self.gang_preview_status, 'warning')
            return False
        if defined:
            self.gang_definition_status.SetLabel(
                f'Defined · matches {len(points)} netlist position'
                f'{"" if len(points) == 1 else "s"}'
            )
            self._style_status(self.gang_definition_status, 'success')
            self.gang_preview_status.SetLabel('Current gang definition')
            self._style_status(self.gang_preview_status, 'success')
        else:
            self.gang_definition_status.SetLabel('Not defined · change a setting to define this gang')
            self._style_status(self.gang_definition_status, 'warning')
            self.gang_preview_status.SetLabel('Preview only · gang not defined')
            self._style_status(self.gang_preview_status, 'warning')
        return True

    def gang_draft_changed(self, event=None):
        if not self._loading_gang and self.current_gang:
            if (event is not None and isinstance(event, grid.GridEvent)
                    and event.GetCol() == 2):
                row = event.GetRow()
                try:
                    _value_mm, unit, number_text = parse_dimension(
                        self.gang_rows.GetCellValue(row, 2) or '0',
                        'mil',
                    )
                except ValueError:
                    pass
                else:
                    self._loading_gang = True
                    try:
                        self.gang_rows.SetCellValue(row, 2, f'{number_text} {unit}')
                    finally:
                        self._loading_gang = False
            values = None
            try:
                values = self._gang_editor_values()
                points = self._preview_gang_values(values)
                self._validate_gang_population(points)
            except (TypeError, ValueError) as error:
                self._gang_invalid_edits[self.current_gang] = self._capture_gang_edit()
                if values is not None:
                    self._show_gang_definition(values, False)
                else:
                    self.gang_definition_status.SetLabel('Needs input — ' + str(error))
                    self._style_status(self.gang_definition_status, 'error')
                    self.gang_preview_status.SetLabel('Last renderable geometry')
                    self._style_status(self.gang_preview_status, 'warning')
            else:
                self._gang_invalid_edits.pop(self.current_gang, None)
                self.controller.set_gang(self.current_gang, values)
                self.controller.config.get('drafts', {}).pop(self.current_gang, None)
                self._show_gang_definition(values, True)
            self.refresh_gang_list()
            self.refresh()
        if event is not None:
            event.Skip()

    def numbering_mode_changed(self, event):
        self.gang_custom_numbering.Enable(self.gang_numbering_mode.GetSelection() == 3)
        self.gang_draft_changed(event)

    def add_gang_row(self, event):
        self._loading_gang = True
        try:
            row = self.gang_rows.GetNumberRows()
            self._resize_gang_rows(row + 1)
            self.gang_rows.SetCellValue(row, 1, '1')
            self.gang_rows.SetCellValue(row, 2, '0 mil')
        finally:
            self._loading_gang = False
        self.gang_draft_changed()

    def remove_gang_row(self, event):
        count = self.gang_rows.GetNumberRows()
        if count <= 1:
            raise ValueError('A gang requires at least one pattern row')
        row = self.gang_rows.GetGridCursorRow()
        if row < 0 or row >= count:
            row = count - 1
        self._loading_gang = True
        try:
            self.gang_rows.DeleteRows(row, 1)
            self._resize_gang_rows(count - 1)
        finally:
            self._loading_gang = False
        self.gang_draft_changed()

    def refresh_gangs(self):
        self.refresh_gang_list()
        names = self.controller.gang_names()
        if names:
            if self.current_gang not in names:
                self.current_gang = names[0]
            self.select_gang(None)
        else:
            self.current_gang = None
            self.gang_editor_title.SetLabel('No RF gangs in the netlist')
            self.pattern_view.show('', {})
            self.gang_definition_status.SetLabel('No RF gangs')
            self._style_status(self.gang_definition_status, 'neutral')
            self.gang_preview_status.SetLabel('No RF gangs')
            self._style_status(self.gang_preview_status, 'neutral')
        if hasattr(self, 'anchor_gang'):
            current = self.anchor_gang.GetStringSelection()
            self.anchor_gang.Set(list(names))
            if names:
                self.anchor_gang.SetStringSelection(current if current in names else names[0])
                self.select_anchor_gang()
        if hasattr(self, 'dc_anchor_connector'):
            dc_names = list(self.controller.dc_connector_names())
            current = self.dc_anchor_connector.GetStringSelection()
            self.dc_anchor_connector.Set(dc_names)
            if dc_names:
                self.dc_anchor_connector.SetStringSelection(current if current in dc_names else dc_names[0])
                self.select_dc_anchor(None)
        if hasattr(self, 'transition_list'):
            self.refresh_rf_transitions()

    def refresh_gang_list(self):
        selected = self.current_gang
        self.gang_list.DeleteAllItems()
        query = self.gang_filter.GetValue().casefold()
        for name in self.controller.gang_names():
            if query and query not in name.casefold():
                continue
            count = sum(record.connector == name for record in self.controller.table.records)
            if name in self._gang_invalid_edits:
                status = 'Invalid'
            else:
                try:
                    gang = self.controller.gang(name)
                    points = self.controller.pattern(name)
                    expected = {record.connector_pin for record in self.controller.table.records
                                if record.connector == name}
                    status = ('Defined' if gang.get('defined') and set(points) == expected
                              else 'Not defined' if not gang.get('defined') else 'Invalid')
                except (TypeError, ValueError):
                    status = 'Invalid'
            index = self.gang_list.InsertItem(self.gang_list.GetItemCount(), name)
            self.gang_list.SetItem(index, 1, str(count))
            self.gang_list.SetItem(index, 2, status)
            if name == selected:
                self.gang_list.Select(index)
        self._resize_gang_list()

    def _resize_gang_list(self):
        count = self.gang_list.GetItemCount()
        dc = wx.ClientDC(self.gang_list)
        fallback_row = dc.GetCharHeight() + 8
        if count:
            item_rect = self.gang_list.GetItemRect(0)
            row_height = max(fallback_row, item_rect.height)
            header_height = max(fallback_row + 4, item_rect.y)
        else:
            row_height = fallback_row
            header_height = fallback_row + 4
        display_index = wx.Display.GetFromWindow(self)
        area = (wx.Display(display_index).GetClientArea()
                if display_index != wx.NOT_FOUND else wx.GetClientDisplayRect())
        maximum_rows = max(1, min(12, int(area.height * .34 / row_height)))
        visible_rows = max(1, min(count, maximum_rows))
        height = header_height + visible_rows * row_height + 4
        self.gang_list.SetMinSize((230, height))
        self.gang_list.SetMaxSize((250, height))
        self.gang_list.GetParent().Layout()
        self.pages[5].FitInside()

    def select_dashboard_gang(self, event):
        name = self.gang_list.GetItemText(event.GetIndex())
        if name != self.current_gang:
            self.current_gang = name
            self.select_gang(None)

    def select_gang(self, event):
        if not self.current_gang:
            return
        applied = self.controller.gang(self.current_gang)
        self._loading_gang = True
        try:
            if self.current_gang in self._gang_invalid_edits:
                self._restore_gang_edit(self._gang_invalid_edits[self.current_gang])
            else:
                rows = list(applied.get('row_lengths') or [1])
                offsets = list(applied.get('row_offsets') or [0] * len(rows))
                if len(offsets) != len(rows):
                    offsets = [0] * len(rows)
                units = applied.get('display_units', {})
                offset_units = list(units.get('row_offsets') or ['mil'] * len(rows))
                if len(offset_units) != len(rows):
                    offset_units = ['mil'] * len(rows)
                self._resize_gang_rows(len(rows))
                for row, (positions, offset, unit) in enumerate(
                        zip(rows, offsets, offset_units)):
                    self.gang_rows.SetCellValue(row, 1, str(positions))
                    self.gang_rows.SetCellValue(
                        row, 2, f'{dimension_text(offset, unit)} {unit}',
                    )
                self.gang_pitch_x.set_mm(applied.get('pitch_x', 2.54),
                                         units.get('pitch_x', 'mil'))
                self.gang_pitch_y.set_mm(applied.get('pitch_y', 2.54),
                                         units.get('pitch_y', 'mil'))
                numbering = applied.get('numbering', [])
                traversal_index = {'row-major': 0, 'serpentine': 1, 'column-major': 2}.get(
                    applied.get('traversal', 'row-major'), 0,
                )
                self.gang_numbering_mode.SetSelection(3 if numbering else traversal_index)
                self.gang_custom_numbering.ChangeValue(', '.join(map(str, numbering)))
                self.gang_custom_numbering.Enable(bool(numbering))
                self.gang_pattern_rotation.ChangeValue(str(applied.get('rotation', 0)))
                self.gang_connector_rotation.ChangeValue(
                    str(applied.get('connector_rotation', 0)),
                )
                self.gang_side.SetSelection(1 if applied.get('side') == 'B' else 0)
                self.mirror.SetValue(bool(applied.get('mirror', False)))
        finally:
            self._loading_gang = False
        self.gang_editor_title.SetLabel(f'{self.current_gang} Gang definition')
        if hasattr(self, 'anchor_gang'):
            self.anchor_gang.SetStringSelection(self.current_gang)
            self.select_anchor_gang()
        if self.current_gang in self._gang_invalid_edits:
            try:
                values = self._gang_editor_values()
            except (TypeError, ValueError) as error:
                self._show_gang_definition(applied, bool(applied.get('defined')), True)
                self.gang_definition_status.SetLabel('Needs input — ' + str(error))
                self._style_status(self.gang_definition_status, 'error')
                self.gang_preview_status.SetLabel('Last renderable geometry')
                self._style_status(self.gang_preview_status, 'warning')
            else:
                self._show_gang_definition(values, False, clear_preview=True)
        else:
            self._show_gang_definition(
                applied, bool(applied.get('defined')), clear_preview=True,
            )

    def copy_gang(self, event):
        if not self.current_gang:
            raise ValueError('Select a gang')
        if self.current_gang in self._gang_invalid_edits:
            raise ValueError('Correct the selected gang definition before copying settings')
        if not self.controller.gang(self.current_gang).get('defined'):
            raise ValueError('Define the selected gang before copying its settings')
        names = [name for name in self.controller.gang_names() if name != self.current_gang]
        with wx.MultiChoiceDialog(
            self, 'Select target gangs. Anchors and library parts remain independent.',
            'Copy gang settings', names,
        ) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                targets = [names[index] for index in dialog.GetSelections()]
                self.controller.copy_gang(self.current_gang, targets)
                self.refresh_gangs()
                self.status.SetLabel(
                    f'Copied {self.current_gang} settings to {len(targets)} gang'
                    f'{"" if len(targets) == 1 else "s"}.'
                )

    def build_rf_transitions(self, panel):
        self.task_box(
            panel, 'RF transitions', 'Optionally assign approved RF transitions',
            'Requirements are derived from netclass, mapped KiCad layer and connector footprint. '
            'Unassigned requirements do not block Review changes or board generation.',
        )
        controls = wx.BoxSizer(wx.HORIZONTAL)
        self.transition_filter = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.transition_filter.SetDescriptiveText('Search requirements')
        self.transition_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_rf_transitions())
        controls.Add(self.transition_filter, 1, wx.RIGHT, 8)
        self.transition_status_filter = wx.Choice(
            panel, choices=['All statuses', 'Ready', 'Not assigned', 'Blocked'],
        )
        self.transition_status_filter.SetSelection(0)
        self.transition_status_filter.Bind(wx.EVT_CHOICE, lambda event: self.refresh_rf_transitions())
        controls.Add(self.transition_status_filter, 0, wx.RIGHT, 8)
        controls.Add(self.button(panel, 'Refresh transition library', self.refresh_transition_catalog), 0, wx.RIGHT, 8)
        self.open_transition_designer = wx.Button(panel, label='Open RF Transition Designer…')
        self.open_transition_designer.Enable(False)
        self.open_transition_designer.SetToolTip('Reserved for the separately installed RF Transition Designer.')
        controls.Add(self.open_transition_designer, 0)
        panel.GetSizer().Add(controls, 0, wx.EXPAND | wx.ALL, 8)

        self.transition_summary = wx.StaticText(panel, label='0 unique requirements · 0 RF locations')
        panel.GetSizer().Add(self.transition_summary, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.transition_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 210))
        for column, (heading, width) in enumerate((
            ('Status', 105), ('Netclass', 110), ('KiCad layer', 110),
            ('Connector footprint', 280), ('Locations', 80), ('Approved transition', 250),
        )):
            self.transition_list.InsertColumn(column, heading, width=width)
        self.transition_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.select_rf_transition)
        panel.GetSizer().Add(self.transition_list, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        detail_box = wx.StaticBox(panel, label='Selected requirement')
        detail = wx.StaticBoxSizer(detail_box, wx.VERTICAL)
        self.transition_detail = wx.StaticText(detail_box, label='Select a requirement')
        self.transition_detail.Wrap(920)
        detail.Add(self.transition_detail, 0, wx.EXPAND | wx.ALL, 8)
        assignment = wx.BoxSizer(wx.HORIZONTAL)
        assignment.Add(wx.StaticText(detail_box, label='Compatible transitions'), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.transition_choice = wx.Choice(detail_box)
        self.transition_choice.Bind(wx.EVT_CHOICE, self.assign_rf_transition)
        assignment.Add(self.transition_choice, 1, wx.RIGHT, 8)
        assignment.Add(self.button(detail_box, 'Browse artifact…', self.browse_rf_transition), 0, wx.RIGHT, 8)
        assignment.Add(self.button(detail_box, 'Clear assignment', self.clear_rf_transition), 0)
        detail.Add(assignment, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(detail, 0, wx.EXPAND | wx.ALL, 8)
        self.transition_page_status = self.status_label(panel, '0 optional transitions assigned')
        self.transition_page_status.SetMinSize((360, -1))
        panel.GetSizer().Add(self.transition_page_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.transition_catalog = {}
        self._transition_choice_paths = []
        self.current_transition_requirement = None

    def _transition_state(self, requirement):
        if not requirement.get('assignment', {}).get('path'):
            return 'Not assigned', 'Optional'
        if (not requirement['layer'] or not requirement['footprint'] or
                len(requirement['part_sha256s']) != 1 or len(requirement['sides']) != 1):
            return 'Blocked', 'Requirement inputs are incomplete or inconsistent'
        try:
            artifact = self.controller.validate_rf_transition(requirement)
            return 'Ready', f'{artifact["artifact_id"]} r{artifact["revision"]}'
        except (OSError, ValueError) as error:
            return 'Blocked', str(error)

    def refresh_rf_transitions(self):
        if not hasattr(self, 'transition_list'):
            return
        requirements = self.controller.rf_transition_requirements()
        query = self.transition_filter.GetValue().casefold()
        wanted = self.transition_status_filter.GetStringSelection()
        self.transition_list.DeleteAllItems()
        visible = []
        ready = 0
        blocked = 0
        locations = sum(len(item['locations']) for item in requirements)
        for requirement in requirements:
            state, detail = self._transition_state(requirement)
            if state == 'Ready':
                ready += 1
            elif state == 'Blocked':
                blocked += 1
            haystack = ' '.join((requirement['netclass'], requirement['layer'],
                                 requirement['footprint'], ' '.join(requirement['locations']))).casefold()
            if query and query not in haystack:
                continue
            if wanted and wanted != 'All statuses' and wanted != state:
                continue
            assignment = requirement.get('assignment', {})
            label = (f'{assignment.get("artifact_id")} r{assignment.get("revision")}'
                     if assignment.get('artifact_id') else detail)
            row = self.transition_list.InsertItem(self.transition_list.GetItemCount(), state)
            for column, value in enumerate((
                requirement['netclass'], requirement['layer'] or 'Unmapped',
                requirement['footprint'] or 'Unassigned', len(requirement['locations']), label,
            ), 1):
                self.transition_list.SetItem(row, column, str(value))
            palette = ui_palette()
            self.transition_list.SetItemBackgroundColour(
                row, {'Ready': palette.success, 'Not assigned': palette.window,
                      'Blocked': palette.error}[state],
            )
            self.transition_list.SetItemTextColour(row, palette.text)
            visible.append(requirement)
        self._visible_transition_requirements = visible
        self.transition_summary.SetLabel(
            f'{len(requirements)} unique requirement{("" if len(requirements) == 1 else "s")} · '
            f'{locations} RF location{("" if locations == 1 else "s")}'
        )
        unassigned = len(requirements) - ready - blocked
        status = f'{ready} assigned · {unassigned} unassigned (optional)'
        if blocked:
            status += f' · {blocked} blocked'
        self.transition_page_status.SetLabel(status)
        self._style_status(
            self.transition_page_status,
            'error' if blocked else 'success',
        )
        self.transition_page_status.GetParent().Layout()
        if self.current_transition_requirement:
            key = self.current_transition_requirement.get('key')
            current = next((item for item in requirements if item['key'] == key), None)
            self.current_transition_requirement = current
            if current:
                self._show_rf_transition_detail(current)

    def select_rf_transition(self, event):
        index = event.GetIndex()
        if 0 <= index < len(self._visible_transition_requirements):
            self.current_transition_requirement = self._visible_transition_requirements[index]
            self._show_rf_transition_detail(self.current_transition_requirement)

    def _show_rf_transition_detail(self, requirement):
        self.transition_detail.SetLabel(
            f'{requirement["netclass"]} · {requirement["layer"] or "Unmapped"} · '
            f'{requirement["footprint"] or "Unassigned"}\n'
            f'Locations: {", ".join(requirement["locations"])}'
        )
        compatible = []
        for path, artifact in sorted(self.transition_catalog.items(), key=lambda item: (
                item[1]['artifact_id'], str(item[1]['revision']), item[0])):
            try:
                self.controller._checked_transition(requirement, path)
            except (OSError, ValueError):
                continue
            compatible.append((path, f'{artifact["artifact_id"]} r{artifact["revision"]}'))
        assigned_path = requirement.get('assignment', {}).get('path', '')
        if assigned_path and assigned_path not in {path for path, _label in compatible}:
            label = requirement['assignment'].get('artifact_id', Path(assigned_path).name)
            revision = requirement['assignment'].get('revision', '')
            compatible.append((assigned_path, f'{label} r{revision}'.rstrip()))
        self._transition_choice_paths = [''] + [path for path, _label in compatible]
        self.transition_choice.Set(['Choose from project transition library'] +
                                   [label for _path, label in compatible])
        selection = (self._transition_choice_paths.index(assigned_path)
                     if assigned_path in self._transition_choice_paths else 0)
        self.transition_choice.SetSelection(selection)

    def refresh_transition_catalog(self, event=None):
        paths = set()
        if self.controller.board_path:
            root = self.controller.board_path.parent
            paths.update(path for path in root.rglob('*.gppo-rf.json')
                         if '.connector-interposer-pinned' not in path.parts)
        paths.update(Path(selection['path']) for selection in self.controller.config['artifacts'].values()
                     if selection.get('path'))
        catalog = {}
        from .artifacts import load_artifact
        errors = 0
        for path in paths:
            try:
                catalog[str(path.resolve())] = load_artifact(path)
            except (OSError, ValueError):
                errors += 1
        self.transition_catalog = catalog
        self.refresh_rf_transitions()
        self.status.SetLabel(
            f'Transition library refreshed: {len(catalog)} valid artifact'
            f'{"" if len(catalog) == 1 else "s"}' +
            (f'; {errors} invalid file{("" if errors == 1 else "s")} ignored.' if errors else '.')
        )

    def assign_rf_transition(self, event):
        if not self.current_transition_requirement:
            raise ValueError('Select an RF transition requirement')
        index = self.transition_choice.GetSelection()
        path = self._transition_choice_paths[index] if index >= 0 else ''
        if not path:
            return
        self.controller.set_rf_transition(self.current_transition_requirement['key'], path)
        self.refresh_rf_transitions()
        self.status.SetLabel('RF transition assignment updated. Board files were not modified.')

    def browse_rf_transition(self, event):
        if not self.current_transition_requirement:
            raise ValueError('Select an RF transition requirement')
        with wx.FileDialog(
            self, 'Select approved RF transition',
            wildcard='RF transitions (*.gppo-rf.json)|*.gppo-rf.json|JSON (*.json)|*.json',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as picker:
            if picker.ShowModal() == wx.ID_OK:
                self.controller.set_rf_transition(
                    self.current_transition_requirement['key'], picker.GetPath(),
                )
                self.refresh_transition_catalog()

    def clear_rf_transition(self, event):
        if not self.current_transition_requirement:
            return
        self.controller.set_rf_transition(self.current_transition_requirement['key'], '')
        self.refresh_rf_transitions()
        self.status.SetLabel('RF transition assignment cleared. Board files were not modified.')

    def build_anchors(self, panel):
        self.task_box(
            panel, 'Connector placement', 'Associate imported connector locations',
            'Use KiCad File → Import → Graphics to import the whole DXF to a user layer first. Place the interposer, then capture one native connector circle or enter a numeric gang anchor.',
        )
        interposer_box = wx.StaticBox(panel, label='Interposer placement')
        interposer = wx.StaticBoxSizer(interposer_box, wx.VERTICAL)
        self.anchor_interposer_ref = wx.StaticText(interposer_box, label='Ref des: —')
        interposer.Add(self.anchor_interposer_ref, 0, wx.ALL, 8)
        placement = wx.FlexGridSizer(cols=6, hgap=8, vgap=6)
        placement.Add(wx.StaticText(interposer_box, label='X'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.interposer_x = DimensionInput(interposer_box, value='0')
        self.interposer_x.bind_changed(self.input_changed)
        placement.Add(self.interposer_x, 1, wx.EXPAND)
        placement.Add(wx.StaticText(interposer_box, label='Y'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.interposer_y = DimensionInput(interposer_box, value='0')
        self.interposer_y.bind_changed(self.input_changed)
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
        self.anchor_gang.Bind(wx.EVT_CHOICE, self.select_anchor_gang)
        panel.GetSizer().Add(self.anchor_gang, 0, wx.EXPAND | wx.ALL, 8)
        self.user_layer = self.field(panel, 'Imported mechanical user layer', 'Dwgs.User')
        self.label_offset_x = self.dimension_field(panel, 'Label offset X from circle', '0')
        self.label_offset_y = self.dimension_field(panel, 'Label offset Y from circle', '0')
        self.anchor_x = self.dimension_field(panel, 'Location 1 X', '0')
        self.anchor_y = self.dimension_field(panel, 'Location 1 Y', '0')
        panel.GetSizer().Add(self.button(panel, 'Use selected circle center', self.capture_circle), 0, wx.ALL, 8)
        panel.GetSizer().Add(self.button(panel, 'Accept anchor', self.accept_anchor), 0, wx.ALL, 8)
        self.anchor_status = self.note(panel, 'No anchor captured.')

        dc_box = wx.StaticBox(panel, label='DC connector pin 1 placement')
        dc = wx.StaticBoxSizer(dc_box, wx.VERTICAL)
        self.dc_anchor_connector = wx.Choice(dc_box)
        self.dc_anchor_connector.Bind(wx.EVT_CHOICE, self.select_dc_anchor)
        dc.Add(self.dc_anchor_connector, 0, wx.EXPAND | wx.ALL, 8)
        dc_fields = wx.FlexGridSizer(cols=4, hgap=8, vgap=6)
        dc_fields.Add(wx.StaticText(dc_box, label='Orientation (degrees)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.dc_anchor_rotation = wx.TextCtrl(dc_box, value='0')
        dc_fields.Add(self.dc_anchor_rotation, 1, wx.EXPAND)
        dc_fields.Add(wx.StaticText(dc_box, label='Board side'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.dc_anchor_side = wx.Choice(dc_box, choices=['Front (F)', 'Back (B)'])
        self.dc_anchor_side.SetSelection(0)
        dc_fields.Add(self.dc_anchor_side, 1, wx.EXPAND)
        dc_fields.AddGrowableCol(1, 1); dc_fields.AddGrowableCol(3, 1)
        dc.Add(dc_fields, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        dc_buttons = wx.BoxSizer(wx.HORIZONTAL)
        dc_buttons.Add(self.button(dc_box, 'Use selected pin 1 circle', self.capture_dc_circle),
                       0, wx.RIGHT, 8)
        dc_buttons.Add(self.button(dc_box, 'Accept DC placement', self.accept_dc_anchor), 0)
        dc.Add(dc_buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.dc_anchor_status = self.status_label(dc_box, 'DC pin 1 placement required')
        dc.Add(self.dc_anchor_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(dc, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        panel.GetSizer().Add(self.button(panel, 'Inspect saved mechanical population', self.inspect_mechanical), 0, wx.ALL, 8)
        self.mechanical_list = wx.ListCtrl(panel, style=wx.LC_REPORT, size=(-1, 140))
        for col, (name, width) in enumerate((('Physical key', 120), ('Center X (mm)', 130), ('Center Y (mm)', 130), ('Source circle UUID', 280), ('Source text UUID', 280))): self.mechanical_list.InsertColumn(col, name, width=width)
        panel.GetSizer().Add(self.mechanical_list, 0, wx.EXPAND | wx.ALL, 8)
        self.hole_path = self.library_field(panel, 'Approved mounting-hole footprint (optional)', 'footprint', allow_mechanical=True)
        self.hole_ref = self.field(panel, 'Mounting-hole reference', 'H1')
        panel.GetSizer().Add(self.button(panel, 'Add mounting hole at this anchor', self.add_hole), 0, wx.ALL, 8)

    def select_anchor_gang(self, event=None):
        gang = self.anchor_gang.GetStringSelection()
        if not gang:
            return
        settings = self.controller.gang(gang)
        anchor = settings.get('anchor', [0, 0])
        units = settings.get('display_units', {})
        self.anchor_x.set_mm(anchor[0], units.get('anchor_x', 'mil'))
        self.anchor_y.set_mm(anchor[1], units.get('anchor_y', 'mil'))
        accepted = bool(settings.get('anchor_accepted'))
        self.anchor_status.SetLabel(
            f'{gang} anchor accepted' if accepted else f'{gang} anchor required'
        )

    def accept_interposer_placement(self, event):
        reference = self._interposer_reference()
        if not reference:
            raise ValueError('The imported netlist must contain exactly one interposer reference')
        selection = self.controller.config['interposers'].get(reference)
        if not selection:
            raise ValueError('Assign the interposer symbol and footprint first')
        pose = [self.interposer_x.value_mm(), self.interposer_y.value_mm(),
                float(self.interposer_rotation.GetValue())]
        if not all(math.isfinite(value) for value in pose):
            raise ValueError('Interposer placement values must be finite numbers')
        selection['pose'] = pose
        selection['pose_units'] = [
            self.interposer_x.selected_unit(), self.interposer_y.selected_unit(),
        ]
        selection['pose_accepted'] = True
        self.controller.changed()
        self.interposer_pose_status.SetLabel(f'{reference} placement accepted')
        self._style_status(self.interposer_pose_status, 'success')

    def capture_circle(self, event):
        if self.pcbnew is None: raise ValueError('Open this workflow from the KiCad PCB Editor')
        selected = list(self.pcbnew.GetCurrentSelection())
        if len(selected) != 1 or not isinstance(selected[0], self.pcbnew.PCB_SHAPE) or selected[0].GetShape() != self.pcbnew.SHAPE_T_CIRCLE:
            raise ValueError('Select exactly one imported native circle in the PCB canvas')
        circle = selected[0]
        if self.pcbnew.GetBoard().GetLayerName(circle.GetLayer()) != self.user_layer.GetValue():
            raise ValueError('Circle must belong to the selected mechanical user layer')
        center = circle.GetCenter()
        self.anchor_x.set_mm(center.x / 1e6)
        self.anchor_y.set_mm(center.y / 1e6)
        self._circle_draft = {'uuid': circle.m_Uuid.AsString(), 'center_nm': [center.x, center.y]}
        self.anchor_status.SetLabel('Draft circle center captured; accept to persist it. Board unchanged.')

    def capture_dc_circle(self, event):
        if self.pcbnew is None:
            raise ValueError('Open this workflow from the KiCad PCB Editor')
        selected = list(self.pcbnew.GetCurrentSelection())
        if (len(selected) != 1 or not isinstance(selected[0], self.pcbnew.PCB_SHAPE) or
                selected[0].GetShape() != self.pcbnew.SHAPE_T_CIRCLE):
            raise ValueError('Select exactly one imported DC pin 1 circle in the PCB canvas')
        circle = selected[0]
        if self.pcbnew.GetBoard().GetLayerName(circle.GetLayer()) != self.user_layer.GetValue():
            raise ValueError('Circle must belong to the selected mechanical user layer')
        center = circle.GetCenter()
        self._dc_circle_draft = {
            'uuid': circle.m_Uuid.AsString(),
            'center_nm': [center.x, center.y],
            'center': [center.x / 1e6, center.y / 1e6],
        }
        self.dc_anchor_status.SetLabel(
            'Draft pin 1 circle captured; set orientation and side, then accept placement.'
        )
        self._style_status(self.dc_anchor_status, 'warning')

    def select_dc_anchor(self, event):
        reference = self.dc_anchor_connector.GetStringSelection()
        if not reference:
            return
        selection = self.controller.dc_connector(reference)
        self.dc_anchor_rotation.ChangeValue(str(selection.get('rotation', 0)))
        self.dc_anchor_side.SetSelection(1 if selection.get('side') == 'B' else 0)
        self.dc_anchor_status.SetLabel(
            f'{reference} pin 1 placement accepted' if selection.get('anchor_accepted')
            else f'{reference} pin 1 placement required'
        )
        self._style_status(
            self.dc_anchor_status,
            'success' if selection.get('anchor_accepted') else 'warning',
        )

    def accept_dc_anchor(self, event):
        reference = self.dc_anchor_connector.GetStringSelection()
        if not reference:
            raise ValueError('Select a DC connector')
        draft = getattr(self, '_dc_circle_draft', None)
        if not draft:
            raise ValueError('Select the DC pin 1 DXF circle and capture its center first')
        if self.pcbnew:
            drawings = {item.m_Uuid.AsString(): item for item in self.pcbnew.GetBoard().GetDrawings()}
            source = drawings.get(draft['uuid'])
            if (source is None or not isinstance(source, self.pcbnew.PCB_SHAPE) or
                    source.GetShape() != self.pcbnew.SHAPE_T_CIRCLE or
                    [source.GetCenter().x, source.GetCenter().y] != draft['center_nm']):
                raise ValueError('DC pin 1 circle changed; capture it again')
        rotation = float(self.dc_anchor_rotation.GetValue())
        if not math.isfinite(rotation):
            raise ValueError('DC connector orientation must be a finite number')
        self.controller.set_dc_connector(reference, {
            'anchor_pad': '1', 'anchor': list(draft['center']),
            'anchor_circle': {'uuid': draft['uuid'], 'center': list(draft['center'])},
            'rotation': rotation,
            'side': 'B' if self.dc_anchor_side.GetSelection() == 1 else 'F',
            'anchor_accepted': True,
        })
        self.controller.config['mechanical']['layer'] = self.user_layer.GetValue()
        self.controller.config['mechanical']['label_offset'] = [
            self.label_offset_x.value_mm(), self.label_offset_y.value_mm(),
        ]
        self.controller.config['mechanical']['label_offset_units'] = [
            self.label_offset_x.selected_unit(), self.label_offset_y.selected_unit(),
        ]
        self.controller.config['mechanical']['accepted'] = True
        self.dc_anchor_status.SetLabel(f'{reference} pin 1 placement accepted')
        self._style_status(self.dc_anchor_status, 'success')
        self._dc_circle_draft = None

    def accept_anchor(self, event):
        gang = self.anchor_gang.GetStringSelection()
        if not gang: raise ValueError('Select a gang')
        if hasattr(self, '_circle_draft') and self.pcbnew:
            drawings = {s.m_Uuid.AsString(): s for s in self.pcbnew.GetBoard().GetDrawings()}
            source = drawings.get(self._circle_draft['uuid'])
            if source is None or [source.GetCenter().x, source.GetCenter().y] != self._circle_draft['center_nm']:
                raise ValueError('Circle changed; capture its center again')
        settings = self.controller.gang(gang)
        display_units = dict(settings.get('display_units', {}))
        display_units['anchor_x'] = self.anchor_x.selected_unit()
        display_units['anchor_y'] = self.anchor_y.selected_unit()
        self.controller.set_gang(gang, {
            'anchor': [self.anchor_x.value_mm(), self.anchor_y.value_mm()],
            'display_units': display_units,
            'anchor_accepted': True,
        })
        self.controller.config['mechanical']['layer'] = self.user_layer.GetValue()
        self.controller.config['mechanical']['label_offset'] = [
            self.label_offset_x.value_mm(), self.label_offset_y.value_mm(),
        ]
        self.controller.config['mechanical']['label_offset_units'] = [
            self.label_offset_x.selected_unit(), self.label_offset_y.selected_unit(),
        ]
        self.controller.config['mechanical']['accepted'] = True
        self.controller.changed()
        self.anchor_status.SetLabel(f'{gang} anchor accepted. Exact mechanical identities are checked during review.')

    def inspect_mechanical(self, event):
        backend = self.controller.backend
        if backend is None or not hasattr(backend, 'call'): raise ValueError('KiCad native backend is unavailable')
        layer = self.user_layer.GetValue()
        offset = [self.label_offset_x.value_mm(), self.label_offset_y.value_mm()]
        def complete(result):
            if self._closing: return
            self.mechanical_list.DeleteAllItems()
            for location in result['locations']:
                row = self.mechanical_list.InsertItem(self.mechanical_list.GetItemCount(), location['key'])
                for col, value in enumerate((*location['center'], location['circle_uuid'], location['text_uuid']), 1): self.mechanical_list.SetItem(row, col, str(value))
            errors = list(result.get('errors', []))
            if self.controller.table:
                from .core.geometry import validate_mechanical_population
                rf_records = tuple(record for record in self.controller.table.records
                                   if self.controller.connector_type(record.connector) == 'RF')
                rf_keys = {record.key for record in rf_records}
                try: validate_mechanical_population(
                    rf_records, [p['key'] for p in result['locations'] if p['key'] in rf_keys],
                )
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
                      'library_id': part.library_id,
                      'pose': [self.anchor_x.value_mm(), self.anchor_y.value_mm(), 0]})
        self.controller.changed()
        self.anchor_status.SetLabel(f'{reference} added as board-only, excluded from BOM and position files.')

    def build_outline(self, panel):
        self.task_box(
            panel, 'Board outline', 'Capture managed board contours',
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
            panel, 'Review changes', 'Validate and compare the desired-state delta',
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
            panel, 'Apply / results', 'Apply to a project copy and inspect results',
            'Apply saves a separate controlled project copy, a reconciliation report and a recovery journal. Keep the source project separate when opening the result.',
        )
        self.results = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        panel.GetSizer().Add(self.results, 1, wx.EXPAND | wx.ALL, 8)
        self.journal = self.field(panel, 'Recovery journal', picker=True)
        panel.GetSizer().Add(self.button(panel, 'Check / recover closed output project', self.recover), 0, wx.ALL, 8)
        self.abort_button = self.button(panel, 'Cancel analysis', self.abort)
        panel.GetSizer().Add(self.abort_button, 0, wx.ALL, 8)

    def compare(self, event):
        if not self.save(None):
            return
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
        self.select_page(PAGES.index('Apply / results'))
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
        if self.controller.config_path and self.controller.config_path.exists():
            if not self._confirm(
                'Saved workflow settings already exist for this project.\n\n'
                'Are you sure you want to overwrite them with the current settings?',
                'Overwrite workflow settings?',
            ):
                self.status.SetLabel('Save cancelled. Existing workflow settings were not changed.')
                return False
        self.controller.save()
        self.status.SetLabel('Workflow settings saved. Board files were not changed.')
        return True

    def _confirm(self, message, title):
        return wx.MessageBox(
            message, title, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self,
        ) == wx.YES

    def delete_workflow_settings(self, event):
        if not self._confirm(
            'This deletes the saved workflow settings for this project. The current settings '
            'will remain in this open dialog until it closes.\n\n'
            'Are you sure you want to delete the saved settings?',
            'Delete workflow settings?',
        ):
            self.status.SetLabel('Delete cancelled. Saved workflow settings were not changed.')
            return False
        if self.controller.delete_saved_settings():
            self.status.SetLabel(
                'Saved workflow settings deleted. Current settings remain open and unsaved.'
            )
        else:
            self.status.SetLabel('No saved workflow settings file exists for this project.')
        return True

    def reset_workflow_settings(self, event):
        if not self._confirm(
            'This clears all current workflow entries and returns the dialog to its initial state. '
            'The saved workflow settings file is not changed unless you save afterward.\n\n'
            'Are you sure you want to reset the current settings?',
            'Reset workflow settings?',
        ):
            self.status.SetLabel('Reset cancelled. Current workflow settings were not changed.')
            return False
        self.controller.reset_current()
        self._reset_view()
        self.status.SetLabel(
            'Current workflow settings reset. Board files and saved workflow settings were not changed.'
        )
        return True

    def _reset_view(self):
        """Make every control reflect a fresh workflow while retaining board context."""
        self._cancel_spreadsheet_timer()
        self._loaded_workbook_path = ''
        self._spreadsheet_columns = {}
        self._assignment_signature = None
        self.current_gang = None
        self._gang_invalid_edits.clear()
        self._review_details = []
        self._circle_draft = None
        self._dc_circle_draft = None

        self._updating_spreadsheet = True
        try:
            self.workbook_path.ChangeValue('')
            self._update_workbook_display()
            self._set_worksheet_choices([], '')
            self.header.SetValue(1)
            self.ignored.ChangeValue('')
            self._set_mapping_columns({})
            self._set_spreadsheet_feedback('')
        finally:
            self._updating_spreadsheet = False

        self.assignment_filter.ChangeValue('')
        self.gang_filter.ChangeValue('')
        self._loading_gang = True
        try:
            self._resize_gang_rows(1)
            self.gang_rows.SetCellValue(0, 1, '1')
            self.gang_rows.SetCellValue(0, 2, '0 mil')
            self.gang_pitch_x.set_mm(2.54, 'mil')
            self.gang_pitch_y.set_mm(2.54, 'mil')
            self.gang_numbering_mode.SetSelection(0)
            self.gang_custom_numbering.ChangeValue('')
            self.gang_custom_numbering.Enable(False)
            self.gang_pattern_rotation.ChangeValue('0')
            self.gang_connector_rotation.ChangeValue('0')
            self.gang_side.SetSelection(0)
            self.mirror.SetValue(False)
        finally:
            self._loading_gang = False
        self.pattern_view.show('', {})
        self.transition_filter.ChangeValue('')
        self.transition_status_filter.SetSelection(0)
        self.transition_catalog = {}
        self.current_transition_requirement = None
        self.transition_list.DeleteAllItems()
        self.transition_choice.Set([])
        self.transition_detail.SetLabel('Select a requirement')

        for control, value in (
            (self.interposer_rotation, '0'), (self.user_layer, 'Dwgs.User'),
            (self.dc_anchor_rotation, '0'), (self.hole_ref, 'H1'),
            (self.output_dir, ''), (self.journal, ''),
        ):
            control.ChangeValue(value)
        for control in (
            self.interposer_x, self.interposer_y, self.label_offset_x,
            self.label_offset_y, self.anchor_x, self.anchor_y,
        ):
            control.set_mm(0, 'mil')
        self.dc_anchor_side.SetSelection(0)
        self.hole_path.set_selection({})
        self.anchor_status.SetLabel('No anchor captured.')
        self.dc_anchor_status.SetLabel('DC pin 1 placement required')
        self._style_status(self.dc_anchor_status, 'warning')
        self.mechanical_list.DeleteAllItems()

        self.outline_enable.SetValue(False)
        self.outline_status.SetLabel('Managed outline disabled. No Edge.Cuts changes requested.')
        self.outline_view.show({})
        self.outline_preview.ChangeValue('')
        self.validation.DeleteAllItems()
        self.diff_list.DeleteAllItems()
        self.review_view.ChangeValue('')
        self.confirm_review.SetValue(False)
        self.results.ChangeValue('')

        self.refresh_layers()
        self.refresh_gangs()
        self.refresh_rf_transitions()
        self.refresh_connector_assignments(True)
        self.select_page(0)
        self.refresh()

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
            pose_units = part.get('pose_units', ['mil', 'mil'])
            self.interposer_x.set_mm(pose[0], pose_units[0])
            self.interposer_y.set_mm(pose[1], pose_units[1])
            self.interposer_rotation.ChangeValue(str(pose[2]))
            self.anchor_interposer_ref.SetLabel('Ref des: ' + ref)
            if part.get('pose_accepted'):
                self.interposer_pose_status.SetLabel(f'{ref} placement accepted')
                self._style_status(self.interposer_pose_status, 'success')
        self.output_dir.ChangeValue(cfg.get('output_dir', ''))
        self.outline_enable.SetValue(cfg['outline'].get('enabled', False))
        self.outline_view.show(cfg['outline'].get('source_preview', {}))
        self.user_layer.ChangeValue(cfg['mechanical'].get('layer', 'Dwgs.User'))
        offset = cfg['mechanical'].get('label_offset', [0, 0])
        offset_units = cfg['mechanical'].get('label_offset_units', ['mil', 'mil'])
        self.label_offset_x.set_mm(offset[0], offset_units[0])
        self.label_offset_y.set_mm(offset[1], offset_units[1])
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
            self._style_status(
                self.columns_status,
                'success' if mapped == len(HEADERS) else 'warning',
            )
            self._style_status(
                self.nets_status,
                'success' if table.included_count else 'warning',
            )
        else:
            self.columns_status.SetLabel(f'0 of {len(HEADERS)} columns assigned')
            self.nets_status.SetLabel('0 nets parsed')
            self._style_status(self.columns_status, 'warning')
            self._style_status(self.nets_status, 'warning')
        self.review_button.Enable(not self.controller.busy)
        self.apply_button.Enable(not self.controller.busy and self.controller.preview is not None and not self.controller.preview.get('conflicts') and self.confirm_review.GetValue())
        self.abort_button.Enable(self.controller.busy and not self._applying)
        self.save_button.Enable(not self.controller.busy and not self._applying)
        self.delete_settings_button.Enable(
            not self.controller.busy and not self._applying
            and bool(self.controller.config_path and self.controller.config_path.exists())
        )
        self.reset_settings_button.Enable(not self.controller.busy and not self._applying)
        self.book.Enable(not self._applying)
        self.step_list.Enable(not self._applying)

    def navigate(self, step):
        self.select_page(self.book.GetSelection() + step)

    def select_page(self, index):
        selection = max(0, min(len(PAGES) - 1, index))
        self.book.SetSelection(selection)
        self.step_list.SetSelection(selection)
        self.step_list.Refresh()

    def close(self, event):
        if self.controller.busy:
            self.status.SetLabel('Wait for the current operation, or cancel analysis before closing.')
            if isinstance(event, wx.CloseEvent): event.Veto()
            return
        self._closing = True
        self._cancel_spreadsheet_timer()
        self.controller.cancel()
        self.Destroy()
