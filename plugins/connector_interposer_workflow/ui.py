"""Native, modeless wx workflow. Library sources are selected, never authored here."""
from contextlib import contextmanager
import json
import math
import time
from pathlib import Path
import wx
import wx.grid as grid
from . import icons, progress
from .controller import HEADERS, PAGES, WorkflowController, library_id
from .core.library_catalog import KiCadLibraryCatalog
from .core.units import DIMENSION_UNITS, dimension_text, parse_dimension

WORKBOOK_SUFFIXES = ('.xlsx', '.csv', '.txt')
SPREADSHEET_PREVIEW_ROWS = 12


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
        self.muted = _mix_colour(self.text, self.window.Get()[:3], .40)
        self.status = {
            progress.DONE: wx.Colour(123, 216, 138) if self.dark else wx.Colour(46, 125, 50),
            progress.MISSING: wx.Colour(255, 138, 128) if self.dark else wx.Colour(198, 40, 40),
            progress.CAUTION: wx.Colour(255, 192, 77) if self.dark else wx.Colour(199, 119, 0),
            progress.OPTIONAL: self.muted,
        }


def ui_palette():
    return UiPalette()


def _overflow_tooltip(control, text, padding=2, available_width=None):
    """Return the full text only when the control cannot display it."""
    text = str(text or '')
    width = (control.GetClientSize().width if available_width is None
             else available_width)
    available = width - padding
    if not text or available <= 0:
        return ''
    dc = wx.ClientDC(control)
    dc.SetFont(control.GetFont())
    clipped = wx.Control.Ellipsize(text, dc, wx.ELLIPSIZE_END, available)
    return text if clipped != text else ''


def draw_status_icon(dc, status, x, y, palette=None):
    """Draw the 14 px status glyph: check, X, exclamation mark or dash."""
    palette = palette or ui_palette()
    colour = palette.status.get(status, palette.muted)
    dc.SetPen(wx.Pen(colour, 2))
    dc.SetBrush(wx.Brush(colour))
    if status == progress.DONE:
        dc.DrawLines([(x + 2, y + 8), (x + 5, y + 11), (x + 12, y + 3)])
    elif status == progress.MISSING:
        dc.DrawLine(x + 3, y + 3, x + 11, y + 11)
        dc.DrawLine(x + 11, y + 3, x + 3, y + 11)
    elif status == progress.CAUTION:
        dc.DrawLine(x + 7, y + 2, x + 7, y + 8)
        dc.DrawCircle(x + 7, y + 12, 1)
    else:
        dc.DrawLine(x + 3, y + 7, x + 11, y + 7)


class StepList(wx.VListBox):
    """Grouped steps with one status icon each.

    The icon sits in a gutter outside the selection highlight, so it keeps its
    colour on the selected step. Group headings are drawn above the first step
    of each group and are not selectable.
    """
    GUTTER = 28

    def __init__(self, parent, choices, groups=()):
        super().__init__(parent, style=wx.BORDER_SIMPLE | wx.WANTS_CHARS)
        self.choices = tuple(choices)
        self.headings = {self.choices.index(steps[0]): title for title, steps in groups}
        self.statuses = [progress.OPTIONAL] * len(self.choices)
        self.SetItemCount(len(self.choices))
        self.apply_theme()

    def GetCount(self):
        return len(self.choices)

    def set_statuses(self, statuses):
        statuses = list(statuses)
        if statuses != self.statuses:
            self.statuses = statuses
            self.Refresh()

    def item_font(self, index):
        font = wx.Font(self.GetFont())
        font.SetWeight(
            wx.FONTWEIGHT_BOLD if index == self.GetSelection() else wx.FONTWEIGHT_NORMAL
        )
        return font

    def heading_font(self):
        font = wx.Font(self.GetFont())
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        return font

    def row_height(self):
        return wx.ClientDC(self).GetCharHeight() + 8

    def heading_height(self):
        return wx.ClientDC(self).GetCharHeight() + 12

    def best_size(self):
        dc = wx.ClientDC(self)
        dc.SetFont(self.heading_font())
        width = max(dc.GetTextExtent(text)[0] for text in self.choices) + self.GUTTER + 22
        height = sum(self.OnMeasureItem(index) for index in range(len(self.choices))) + 6
        return width, height

    def OnMeasureItem(self, index):
        return self.row_height() + (self.heading_height() if index in self.headings else 0)

    def OnDrawBackground(self, dc, rect, index):
        dc.SetPen(wx.Pen(ui_palette().window))
        dc.SetBrush(wx.Brush(ui_palette().window))
        dc.DrawRectangle(rect)

    def OnDrawItem(self, dc, rect, index):
        palette = ui_palette()
        top = rect.y
        if index in self.headings:
            dc.SetFont(self.heading_font())
            dc.SetTextForeground(palette.text)
            dc.DrawText(self.headings[index], rect.x + 8, top + 7)
            top += self.heading_height()
        height = self.row_height()
        draw_status_icon(dc, self.statuses[index], rect.x + 8, top + (height - 14) // 2, palette)
        selected = index == self.GetSelection()
        if selected:
            dc.SetPen(wx.Pen(palette.highlight))
            dc.SetBrush(wx.Brush(palette.highlight))
            dc.DrawRectangle(rect.x + self.GUTTER, top + 1,
                             rect.width - self.GUTTER - 4, height - 2)
        dc.SetFont(self.item_font(index))
        dc.SetTextForeground(palette.highlight_text if selected else palette.text)
        _width, text_height = dc.GetTextExtent(self.choices[index])
        dc.DrawText(self.choices[index], rect.x + self.GUTTER + 6,
                    top + max(0, (height - text_height) // 2))

    def apply_theme(self):
        palette = ui_palette()
        self.SetBackgroundColour(palette.window)
        self.SetForegroundColour(palette.text)
        self.Refresh()


class ChecklistView(wx.Panel):
    """The About-this-step checklist: every concern always listed, in place."""

    def __init__(self, parent, on_select=None, background='info'):
        super().__init__(parent)
        self.lines = ()
        self.on_select = on_select
        self.background = background
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.paint)
        self.Bind(wx.EVT_LEFT_UP, self.clicked)

    def row_height(self):
        return self.GetCharHeight() + 8

    def set_lines(self, lines):
        lines = tuple(lines)
        if lines != self.lines:
            self.lines = lines
            self.SetMinSize((self.GetMinSize().width,
                             self.row_height() * max(1, len(lines))))
            self.Refresh()

    def clicked(self, event):
        index = event.GetY() // self.row_height()
        if self.on_select and 0 <= index < len(self.lines):
            self.on_select(index, self.lines[index])
        event.Skip()

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        palette = ui_palette()
        dc.SetBackground(wx.Brush(getattr(palette, self.background)))
        dc.Clear()
        dc.SetFont(self.GetFont())
        width, height = self.GetClientSize().width, self.row_height()
        for index, line in enumerate(self.lines):
            top = index * height
            draw_status_icon(dc, line.status, 0, top + (height - 14) // 2, palette)
            _w, text_height = dc.GetTextExtent(line.label)
            dc.SetTextForeground(palette.text)
            dc.DrawText(line.label, 20, top + (height - text_height) // 2)
            if line.count:
                dc.SetTextForeground(palette.muted)
                dc.DrawText(line.count, width - dc.GetTextExtent(line.count)[0],
                            top + (height - text_height) // 2)


class Spotlight:
    """Point at a control: a blue outline fades in, holds, and fades out.

    The page scrolls smoothly to the control first when it is out of view.
    Nothing is selected and nothing moves. The outline is four thin strips in
    the margin around the control: real windows, so every platform paints
    them, and they are removed afterwards.
    """
    TICK, FADE_IN, HOLD, FADE_OUT, SCROLL_STEPS = 40, 300, 3000, 600, 8

    def __init__(self, owner):
        self.target, self.elapsed, self.scroll, self.strips = None, 0, [], []
        self.timer = wx.Timer(owner)
        owner.Bind(wx.EVT_TIMER, self._tick, self.timer)

    @classmethod
    def strength(cls, elapsed):
        """Outline opacity, 0 to 1, at ``elapsed`` milliseconds."""
        if elapsed < cls.FADE_IN:
            return max(0.0, elapsed / cls.FADE_IN)
        if elapsed <= cls.FADE_IN + cls.HOLD:
            return 1.0
        return max(0.0, 1 - (elapsed - cls.FADE_IN - cls.HOLD) / cls.FADE_OUT)

    def show(self, target):
        self.stop()
        self.target, self.elapsed = target, 0
        self.scroll = self._scroll_plan(target)
        self.timer.Start(self.TICK)

    def stop(self):
        self.timer.Stop()
        self.target = None
        for strip in self.strips:
            if strip:
                strip.Destroy()
        self.strips = []

    def _place(self):
        """Create or move the four strips to sit just outside the control."""
        rect = self.target.GetRect()
        rect.Inflate(4, 4)
        edges = ((rect.x, rect.y, rect.width, 2), (rect.x, rect.Bottom - 1, rect.width, 2),
                 (rect.x, rect.y, 2, rect.height), (rect.Right - 1, rect.y, 2, rect.height))
        if not self.strips:
            parent = self.target.GetParent()
            self.strips = [wx.Window(parent, size=(2, 2)) for _edge in edges]
        for strip, (x, y, width, height) in zip(self.strips, edges):
            strip.SetSize(x, y, width, height)

    def _scroll_plan(self, target):
        """Scroll positions that bring ``target`` into view in a few even steps."""
        page = target.GetParent()
        while page is not None and not isinstance(page, wx.ScrolledWindow):
            page = page.GetParent()
        if page is None:
            return []
        self.page = page
        top = page.ScreenToClient(target.ClientToScreen((0, 0))).y
        height = page.GetClientSize().height
        if 0 <= top and top + target.GetSize().height <= height:
            return []
        unit = max(1, page.GetScrollPixelsPerUnit()[1])
        x, start = page.GetViewStart()
        end = max(0, start + (top - 20) // unit)
        return [(x, round(start + (end - start) * step / self.SCROLL_STEPS))
                for step in range(1, self.SCROLL_STEPS + 1)]

    def _tick(self, event):
        if not self.target:
            self.stop()
            return
        if self.scroll:
            self.page.Scroll(*self.scroll.pop(0))
            return
        self.elapsed += self.TICK
        if self.elapsed >= self.FADE_IN + self.HOLD + self.FADE_OUT:
            self.stop()
            return
        parent = self.target.GetParent()
        colour = _mix_colour(parent.GetBackgroundColour(), ui_palette().accent.Get()[:3],
                             self.strength(self.elapsed))
        self._place()
        for strip in self.strips:
            strip.SetBackgroundColour(colour)
            strip.Refresh()


class MessageLog:
    """The dialog's message sink: every status line, kept for copying."""

    def __init__(self, view):
        self.view = view
        self.last = ''

    def SetLabel(self, text):
        self.last = str(text)
        self.view.AppendText(f'{time.strftime("%H:%M:%S")}  {self.last}\n')

    def GetLabel(self):
        return self.last

    def log(self, text):
        """Record detail for the log only; the last status line stays as it is."""
        self.view.AppendText(str(text) + '\n')


class CircleNotSaved(ValueError):
    """A picked circle that the saved board's drawing does not hold."""


class StatusLabel(wx.StaticText):
    """Status text that is never silently clipped.

    A free-standing label grows to fit its text. A label that fills a box of
    fixed width keeps that width and offers the complete text on hover.
    """
    MINIMUM_WIDTH = 190
    fit = True

    def SetLabel(self, label):
        super().SetLabel(label)
        width = self.GetTextExtent(label).width + 12
        if self.fit:
            self.SetMinSize((max(self.MINIMUM_WIDTH, width), -1))
        else:
            self.SetToolTip(label if width > self.GetSize().width else '')
        parent = self.GetParent()
        if parent is not None:
            parent.Layout()


class DimensionInput(wx.Panel):
    """A numeric linear-dimension input with an independent unit selector."""
    def __init__(self, parent, value='0', unit='mil', compact=False):
        super().__init__(parent)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        self.text = wx.TextCtrl(self, value=str(value), style=wx.TE_PROCESS_ENTER)
        self.units = wx.Choice(self, choices=list(DIMENSION_UNITS))
        self.units.SetStringSelection(unit)
        self.units.SetMinSize((70, -1))
        control_height = max(
            28, self.text.GetBestSize().height, self.units.GetBestSize().height,
        )
        self.text.SetMinSize((-1, control_height))
        self.units.SetMinSize((70, control_height))
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
        # Dimension fields are value-entry controls. Commit them on Enter,
        # focus departure or unit selection instead of rebuilding downstream
        # views after every partially typed character.
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
        if not self.text.GetValue().strip():
            return self.units.GetStringSelection()
        _value_mm, unit, _number_text = parse_dimension(
            self.text.GetValue(), self.units.GetStringSelection(),
        )
        return unit

    def set_mm(self, value_mm, unit=None):
        unit = unit or self.units.GetStringSelection() or 'mm'
        self.units.SetStringSelection(unit)
        self.text.ChangeValue('' if value_mm is None else dimension_text(value_mm, unit))

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
        self.table = None
        self.start = self.pending = ''

    def Create(self, parent, control_id, event_handler):
        # GridCellEditor.SetControl requires wx.Control, not a generic wx.Window.
        # Passing wx.Panel raises inside the SWIG callback and can leave wx.Grid
        # unwinding through a half-created editor, which crashes the host process.
        self.panel = wx.Control(parent, control_id, style=wx.BORDER_NONE)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        self.text = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER | wx.BORDER_NONE)
        self.units = wx.Choice(self.panel, choices=list(DIMENSION_UNITS))
        control_height = max(
            28, self.text.GetBestSize().height, self.units.GetBestSize().height,
        )
        self.text.SetMinSize((-1, control_height))
        self.units.SetMinSize((68, control_height))
        layout.Add(self.text, 1, wx.RIGHT, 2)
        layout.Add(self.units, 0)
        self.panel.SetSizer(layout)
        self.SetControl(self.panel)
        self.panel.PushEventHandler(event_handler)
        self.text.Bind(wx.EVT_TEXT_ENTER, self._commit_enter)
        self.units.Bind(wx.EVT_CHOICE, self._commit_choice)

    def Destroy(self):
        if self.panel is not None:
            self.panel.PopEventHandler()
        super().Destroy()

    def SetSize(self, rect):
        self.panel.SetSize(rect)
        self.panel.Layout()

    def BeginEdit(self, row, col, table):
        self.table = table
        self.start = table.GetCellValue(row, col) or '0 mil'
        try:
            _value_mm, unit, number_text = parse_dimension(self.start, 'mil')
        except ValueError:
            unit, number_text = 'mil', self.start
        self.text.ChangeValue(number_text)
        self.units.SetStringSelection(unit)
        self.text.SetFocus()
        self.text.SetSelection(-1, -1)

    def _commit_enter(self, event):
        self._finish_edit()

    def _commit_choice(self, event):
        wx.CallAfter(self._finish_edit)

    def _finish_edit(self):
        if self.table is None:
            return
        try:
            self.table.SaveEditControlValue()
            self.table.DisableCellEditControl()
        except RuntimeError:
            # The grid may already be ending the edit because focus moved.
            return
        self.table.SetFocus()

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
    """Draw a table entry cell: white, blue outline, chevron.

    The cell's attribute background still records its state (assigned, missing
    or conflicting) for the workflow, but it is never painted as a fill. A
    missing or conflicting entry gets a red outline and the red X in a slot
    that is always reserved, so the text does not shift when it appears.
    """
    SLOT = 18

    def __init__(self, arrow=True):
        super().__init__()
        self.arrow = arrow

    def Clone(self):
        return AssignmentRenderer(self.arrow)

    @staticmethod
    def needs_attention(colour, palette=None):
        palette = palette or ui_palette()
        return colour.Get()[:3] in (palette.warning.Get()[:3], palette.error.Get()[:3])

    def Draw(self, table, attr, dc, rect, row, column, selected):
        palette = ui_palette()
        value = table.GetCellValue(row, column)
        dc.SetFont(attr.GetFont())
        if not self.arrow:
            background = table.GetLabelBackgroundColour()
            dc.SetPen(wx.Pen(background))
            dc.SetBrush(wx.Brush(background))
            dc.DrawRectangle(rect)
            dc.SetTextForeground(table.GetLabelTextColour())
            _, text_height = dc.GetTextExtent(value)
            dc.DrawText(value, rect.x + 6, rect.y + max(1, (rect.height - text_height) // 2))
            return
        problem = self.needs_attention(attr.GetBackgroundColour(), palette)
        dc.SetPen(wx.Pen(palette.window))
        dc.SetBrush(wx.Brush(palette.window))
        dc.DrawRectangle(rect)
        dc.SetPen(wx.Pen(palette.status[progress.MISSING] if problem else palette.highlight, 2))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawRectangle(rect.x + 1, rect.y + 1, rect.width - 2, rect.height - 2)
        if problem:
            draw_status_icon(dc, progress.MISSING, rect.x + 3,
                             rect.y + (rect.height - 14) // 2, palette)
        text = value or 'Select…'
        dc.SetTextForeground(palette.text if value else palette.muted)
        clipped = wx.Control.Ellipsize(text, dc, wx.ELLIPSIZE_END,
                                       max(0, rect.width - self.SLOT - 20))
        _, text_height = dc.GetTextExtent(clipped)
        dc.DrawText(clipped, rect.x + self.SLOT + 2,
                    rect.y + max(1, (rect.height - text_height) // 2))
        center_x = rect.GetRight() - 10
        center_y = rect.y + rect.height // 2
        dc.SetPen(wx.Pen(palette.text))
        dc.SetBrush(wx.Brush(palette.text))
        dc.DrawPolygon((wx.Point(center_x - 4, center_y - 2),
                        wx.Point(center_x + 4, center_y - 2),
                        wx.Point(center_x, center_y + 3)))


class TableTextCell(wx.Panel):
    """Vertically aligned read-only cell for a composed native table."""
    def __init__(self, parent, label='', bold=False, background=None):
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self.text = wx.StaticText(self, label=label, style=wx.ST_ELLIPSIZE_END)
        if bold:
            font = self.text.GetFont()
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            self.text.SetFont(font)
        layout = wx.BoxSizer(wx.HORIZONTAL)
        layout.Add(self.text, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 7)
        self.SetSizer(layout)
        self.set_colours(background or ui_palette().window, ui_palette().text)

    def SetLabel(self, label):
        self.text.SetLabel(label)

    def GetLabel(self):
        return self.text.GetLabel()

    def set_colours(self, background, foreground):
        self.SetBackgroundColour(background)
        self.SetForegroundColour(foreground)
        self.text.SetBackgroundColour(background)
        self.text.SetForegroundColour(foreground)
        self.Refresh()


class TableDividerCell(wx.Panel):
    """One segment of a contiguous divider across a composed table."""
    def __init__(self, parent):
        super().__init__(parent)
        self.SetMinSize((-1, 9))
        self.SetMaxSize((-1, 9))
        self.Bind(wx.EVT_PAINT, self._paint)

    def _paint(self, event):
        palette = ui_palette()
        dc = wx.PaintDC(self)
        dc.SetBackground(wx.Brush(palette.window))
        dc.Clear()
        dc.SetPen(wx.Pen(palette.frame))
        dc.DrawLine(0, 4, self.GetClientSize().width, 4)


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
        super().__init__(parent)
        # An unspecified width in the constructor becomes a large native best
        # width on GTK and can force the containing scrolled page wider than
        # its viewport. Keep only a useful minimum and let the sizer expand it.
        self.SetMinSize((250, 300))
        self.name, self.points = '', {}
        self.orientation = 0
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.paint)

    def DoGetBestSize(self):
        return wx.Size(250, 300)
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
        screen_points, radius = self.screen_layout(*self.GetClientSize())
        label_font = wx.Font(dc.GetFont())
        label_font.SetPointSize(self.label_point_size(
            radius, len(screen_points), label_font.GetPointSize(),
        ))
        dc.SetFont(label_font)
        # One placement for the whole gang: inside every circle, or beside every one.
        inside = all(self.label_fits(*dc.GetTextExtent(str(pin)), radius)
                     for pin in screen_points)
        # Connector orientation: 0 degrees points up, positive turns counter-clockwise.
        angle = math.radians(float(self.orientation or 0))
        tick = (-math.sin(angle), -math.cos(angle))
        for pin, (px, py) in screen_points.items():
            colour = anchor if str(pin) == '1' else geometry
            dc.SetPen(wx.Pen(colour, 2))
            # An explicit background brush stays hollow on Windows/GTK themes
            # where the stock transparent brush can inherit a light fill.
            dc.SetBrush(wx.Brush(background))
            dc.DrawCircle(px, py, radius)
            dc.DrawLine(round(px + tick[0] * radius), round(py + tick[1] * radius),
                        round(px + tick[0] * (radius + 6)), round(py + tick[1] * (radius + 6)))
            dc.SetTextForeground(foreground)
            label = str(pin)
            label_width, label_height = dc.GetTextExtent(label)
            if inside:
                dc.DrawText(label, px - label_width // 2, py - label_height // 2)
            else:
                # Beside the circle, away from the orientation tick.
                dc.DrawText(label,
                            round(px - tick[0] * (radius + 4) - (label_width if tick[0] > .5 else 0 if tick[0] < -.5 else label_width / 2)),
                            round(py - tick[1] * (radius + 4) - (label_height if tick[1] > .5 else 0 if tick[1] < -.5 else label_height / 2)))

    @staticmethod
    def label_fits(label_width, label_height, radius):
        """A number is drawn inside its circle only when it clears the outline."""
        return math.hypot(label_width, label_height) <= 2 * radius - 3

    @staticmethod
    def label_point_size(radius, count, default_size):
        """Keep multi-digit labels distinct when dense gangs shrink their markers."""
        if count > 12 or radius <= 6:
            return max(8, min(default_size, radius + 2))
        return default_size

    def screen_layout(self, width, height):
        """Center the gang centroid and fit every location into the live preview."""
        if not self.points:
            return {}, 0
        values = tuple(self.points.values())
        center_x = sum(point[0] for point in values) / len(values)
        center_y = sum(point[1] for point in values) / len(values)
        relative = {
            pin: (point[0] - center_x, point[1] - center_y)
            for pin, point in self.points.items()
        }
        canvas_center = (width / 2, 40 + max(20, height - 56) / 2)
        half_width = max(10, width / 2 - 40)
        half_height = max(10, (height - 56) / 2 - 30)
        extent_x = max(abs(point[0]) for point in relative.values())
        extent_y = max(abs(point[1]) for point in relative.values())
        scales = []
        if extent_x > 1e-12:
            scales.append(half_width / extent_x)
        if extent_y > 1e-12:
            scales.append(half_height / extent_y)
        scale = max(.01, min(scales)) if scales else 1
        laid_out = {
            pin: (round(canvas_center[0] + point[0] * scale),
                  round(canvas_center[1] + point[1] * scale))
            for pin, point in relative.items()
        }
        screen_values = tuple(laid_out.values())
        distances = [
            math.hypot(first[0] - second[0], first[1] - second[1])
            for index, first in enumerate(screen_values)
            for second in screen_values[index + 1:]
        ]
        minimum_distance = min(distances) if distances else 36
        radius = max(5, min(12, round(minimum_distance * .34)))
        return laid_out, radius


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


class EntryCell(wx.Panel):
    """A table entry cell composed of real controls.

    White surface, blue outline, chevron. When the entry is required and not
    set, the outline turns red and the red X appears in a slot that is always
    reserved. A disabled cell has a neutral outline. ``bulk`` cells apply a
    value to every row and are never themselves missing.
    """
    PLACEHOLDER = 'Select…'
    BULK_PLACEHOLDER = 'Set for all…'

    def _build_entry(self):
        self.bulk = False
        self.needs_attention = False
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.slot = wx.Panel(self, size=(16, 16))
        self.slot.Bind(wx.EVT_PAINT, self._paint_slot)
        row.Add(self.slot, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        self.display = wx.StaticText(self, label=self.PLACEHOLDER, style=wx.ST_ELLIPSIZE_END)
        row.Add(self.display, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 3)
        self.chevron = wx.StaticText(self, label='▾')
        row.Add(self.chevron, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 8)
        self.SetSizer(row)
        self.Bind(wx.EVT_PAINT, self._paint_outline)
        self.Bind(wx.EVT_SIZE, self._resized)

    def placeholder(self):
        return self.BULK_PLACEHOLDER if self.bulk else self.PLACEHOLDER

    def set_bulk(self):
        self.bulk = True
        self._style()

    def _outline(self, palette):
        if not self.IsEnabled():
            return palette.muted
        return palette.status[progress.MISSING] if self.needs_attention else palette.highlight

    def _paint_outline(self, event):
        dc = wx.PaintDC(self)
        width, height = self.GetClientSize()
        dc.SetPen(wx.Pen(self._outline(ui_palette()), 2))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawRectangle(1, 1, width - 1, height - 1)

    def _paint_slot(self, event):
        dc = wx.PaintDC(self.slot)
        if self.needs_attention and self.IsEnabled():
            draw_status_icon(dc, progress.MISSING, 1, 1)

    def _style_entry(self, assigned):
        palette = ui_palette()
        self.needs_attention = not assigned and not self.bulk
        self.SetBackgroundColour(palette.window)
        self.SetForegroundColour(palette.text)
        for control in (self.slot, self.display, self.chevron):
            control.SetBackgroundColour(palette.window)
        self.display.SetForegroundColour(palette.text if assigned else palette.muted)
        self.chevron.SetForegroundColour(palette.text)
        self.Refresh()

    def _resized(self, event):
        event.Skip()
        self.Refresh()
        wx.CallAfter(self._refresh_overflow_tooltip)

    def _refresh_overflow_tooltip(self):
        try:
            text = self.display.GetLabel()
            available = max(
                self.display.GetClientSize().width,
                self.GetClientSize().width - self.chevron.GetSize().width - 44,
            )
            tooltip = _overflow_tooltip(
                self.display, text, available_width=available,
            )
            for control in (self, self.display, self.chevron):
                control.SetToolTip(tooltip)
        except RuntimeError:
            # Dynamic connector rows can be rebuilt before a queued size event.
            return


class SymbolAssignmentCtrl(EntryCell):
    """Table entry cell that opens the native loaded-library symbol picker."""
    def __init__(self, parent, owner, title, require_two_pin, on_change):
        super().__init__(parent, style=wx.BORDER_NONE | wx.WANTS_CHARS)
        self.owner, self.title = owner, title
        self.require_two_pin, self.on_change = require_two_pin, on_change
        self.selection = {}
        self.valid = False
        self._build_entry()
        for control in (self, self.slot, self.display, self.chevron):
            control.Bind(wx.EVT_LEFT_DOWN, self._clicked)
        self.Bind(wx.EVT_KEY_DOWN, self._key_down)
        self.set_selection({})

    def _clicked(self, event):
        if self.IsEnabled():
            self.SetFocus()
            self.owner.guard(self.choose)(event)

    def _key_down(self, event):
        if event.GetKeyCode() in (wx.WXK_SPACE, wx.WXK_RETURN,
                                  getattr(wx, 'WXK_NUMPAD_ENTER', wx.WXK_RETURN)):
            self.owner.guard(self.choose)(event)
            return
        event.Skip()

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

    def set_selection(self, selection, valid=None):
        self.selection = dict(selection or {})
        self.display.SetLabel(self.selection.get('library_id', self.placeholder()))
        self.valid = bool(self.selection.get('library_id')) if valid is None else bool(valid)
        self._style()
        self._refresh_overflow_tooltip()

    def set_valid(self, valid):
        self.valid = bool(valid)
        self._style()

    def _style(self):
        if not self.selection.get('library_id'):
            self.display.SetLabel(self.placeholder())
        self._style_entry(bool(self.selection.get('library_id')) and self.valid)


class FootprintAssignmentCtrl(EntryCell):
    """Filtered footprint assignment rendered as a one-click table entry cell."""
    def __init__(self, parent, on_change):
        super().__init__(parent, style=wx.BORDER_NONE | wx.WANTS_CHARS)
        self.on_change, self.options = on_change, []
        self.selected = {}
        self.valid = False
        self._build_entry()
        for control in (self, self.slot, self.display, self.chevron):
            control.Bind(wx.EVT_LEFT_DOWN, self._clicked)
        self.Bind(wx.EVT_KEY_DOWN, self._key_down)
        self.set_options((), {})

    def Enable(self, enable=True):
        result = super().Enable(enable)
        self.Refresh()
        return result

    def set_options(self, options, selected, valid=None):
        self.options = [dict(option) for option in options]
        selected = dict(selected or {})
        allowed_ids = {option.get('library_id') for option in self.options}
        if (selected.get('library_id') and selected.get('path') and
                not any(option.get('library_id') == selected['library_id'] for option in self.options)):
            self.options.append(selected)
            self.options.sort(key=lambda option: option['library_id'])
        selected_id = selected.get('library_id', '')
        self.selected = next(
            (dict(option) for option in self.options
             if option.get('library_id') == selected_id),
            {},
        )
        self.display.SetLabel(self.selected.get('library_id', self.placeholder()))
        inferred_valid = bool(self.selected) and selected_id in allowed_ids
        self.valid = inferred_valid if valid is None else bool(valid and self.selected)
        self._style()
        self._refresh_overflow_tooltip()

    def set_valid(self, valid):
        self.valid = bool(valid and self.selected)
        self._style()

    def selection(self):
        return dict(self.selected)

    def select_index(self, index):
        """Select a menu option by zero-based index for tests and adapters."""
        if index < 0 or index >= len(self.options):
            return
        self._select(self.options[index])

    def _clicked(self, event):
        if self.IsEnabled():
            self.SetFocus()
            self._show_menu()

    def _key_down(self, event):
        if event.GetKeyCode() in (
                wx.WXK_SPACE, wx.WXK_RETURN,
                getattr(wx, 'WXK_NUMPAD_ENTER', wx.WXK_RETURN),
            ):
            self._show_menu()
            return
        event.Skip()

    def _show_menu(self):
        if not self.IsEnabled() or not self.options:
            return
        menu = wx.Menu()
        selected_id = self.selected.get('library_id', '')
        choices = {}
        clear_item = menu.AppendRadioItem(wx.ID_ANY, 'Clear assignment')
        clear_item.Check(not selected_id)
        choices[clear_item.GetId()] = {}
        for option in self.options:
            # Keep an explicit empty state first. MSW suppresses wxEVT_MENU
            # when an already-checked radio item is clicked, and otherwise a
            # one-option radio group checks its only footprint automatically.
            item = menu.AppendRadioItem(wx.ID_ANY, option['library_id'])
            item.Check(option['library_id'] == selected_id)
            choices[item.GetId()] = dict(option)
        owner = wx.GetTopLevelParent(self)
        position = wx.Point(0, self.GetClientSize().height)
        try:
            if hasattr(owner, '_popup_assignment_menu'):
                chosen = owner._popup_assignment_menu(self, menu, position)
            else:
                chosen = self.GetPopupMenuSelectionFromUser(menu, position)
        finally:
            menu.Destroy()
        if chosen in choices:
            self._select(choices[chosen])

    def _select(self, selection):
        self.set_options(self.options, selection)
        self.on_change(self.selection())

    def _style(self):
        if not self.selected:
            self.display.SetLabel(self.placeholder())
        self._style_entry(bool(self.selected) and self.valid)


class WorkflowDialog(wx.Dialog):
    def __init__(self, parent=None, controller=None, pcbnew_module=None):
        super().__init__(parent, title='Connector to Interposer Workflow', size=(1320, 840),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.pcbnew = pcbnew_module
        self.controller = controller or WorkflowController(dispatch=wx.CallAfter)
        self.controller.dispatch = wx.CallAfter
        self.current_gang = None
        self.current_anchor_gang = None
        self.current_dc_anchor = None
        self._loading_gang = False
        self._pick = None
        self._pick_timer = None
        self._shown_in_editor = []
        self._inspected_revision = self._inspected_layer = ''
        self._inspected_circles = ()
        self._refresh_hold = 0
        self._refresh_pending = False
        self._loading_placement = False
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
        self._status_labels = []
        self._persistent_headings = []
        self.readout_unit = 'mil'
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self._build_toolbar(), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        body = wx.BoxSizer(wx.HORIZONTAL)
        navigation = wx.Panel(self)
        navigation_sizer = wx.BoxSizer(wx.VERTICAL)
        self.step_list = StepList(navigation, PAGES, progress.GROUPS)
        step_width, step_height = self.step_list.best_size()
        step_width = max(236, step_width)
        self.step_list.SetMinSize((step_width, step_height))
        self.step_list.SetMaxSize((step_width, step_height))
        self.step_list.SetSelection(0)
        self.step_list.Bind(wx.EVT_LISTBOX, self.select_step)
        navigation_sizer.Add(self.step_list, 0)
        self.about_box = wx.Panel(navigation, style=wx.BORDER_SIMPLE)
        about_sizer = wx.BoxSizer(wx.VERTICAL)
        self.about_title = wx.StaticText(self.about_box, label=PAGES[0])
        about_font = self.about_title.GetFont()
        about_font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.about_title.SetFont(about_font)
        about_sizer.Add(self.about_title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.about_text = wx.StaticText(self.about_box, label=progress.ABOUT[PAGES[0]])
        about_sizer.Add(self.about_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.checklist = ChecklistView(self.about_box, on_select=self._checklist_selected)
        self.spotlight = Spotlight(self)
        about_sizer.Add(self.checklist, 0, wx.EXPAND | wx.ALL, 10)
        self.about_box.SetSizer(about_sizer)
        self.about_box.SetMinSize((step_width, -1))
        self.about_box.SetMaxSize((step_width, -1))
        navigation_sizer.Add(self.about_box, 1, wx.EXPAND | wx.TOP, 10)
        navigation.SetSizer(navigation_sizer)
        body.Add(navigation, 0, wx.EXPAND | wx.ALL, 10)

        self.book = wx.Simplebook(self, style=wx.BORDER_SIMPLE)
        self.page_hosts = []
        self.pages = []
        for title in PAGES:
            host = wx.Panel(self.book)
            host.SetSizer(wx.BoxSizer(wx.VERTICAL))
            panel = wx.ScrolledWindow(host)
            panel.SetScrollRate(8, 8)
            panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
            panel._workflow_page_host = host
            host.GetSizer().Add(panel, 1, wx.EXPAND)
            self.book.AddPage(host, title)
            self.page_hosts.append(host)
            self.pages.append(panel)
        self.build_spreadsheet(self.pages[PAGES.index('Netlist source')])
        self.build_netclass_types(self.pages[PAGES.index('Netclass type')])
        self.build_layer_assignment(self.pages[PAGES.index('Layer assignment')])
        self.build_routing_permissions(self.pages[PAGES.index('Routing permissions')])
        self.build_connector_assignments(self.pages[PAGES.index('Connector assignments')])
        self.build_gangs(self.pages[PAGES.index('Define RF gangs')])
        self.build_rf_transitions(self.pages[PAGES.index('RF transitions')])
        self.build_anchors(self.pages[PAGES.index('Mechanical placement')])
        self.build_microvias(self.pages[PAGES.index('Interposer microvias')])
        self.build_outline(self.pages[PAGES.index('Board outline')])
        self.build_review(self.pages[PAGES.index('Review changes')])
        self.build_results(self.pages[PAGES.index('Apply / results')])
        self.book.Bind(wx.EVT_BOOKCTRL_PAGE_CHANGED, self.page_changed)
        body.Add(self.book, 1, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 10)
        outer.Add(body, 1, wx.EXPAND)

        self.messages.SetMinSize((-1, self.MESSAGES_HEIGHT))
        self.messages.Hide()
        outer.Add(self.messages, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.SetSizer(outer)
        display_index = wx.Display.GetFromPoint(wx.GetMousePosition())
        if display_index == wx.NOT_FOUND:
            display_index = 0
        display_area = wx.Display(display_index).GetClientArea()
        width = min(self.DEFAULT_SIZE[0], int(display_area.width * .95))
        height = min(self.DEFAULT_SIZE[1], int(display_area.height * .92))
        self.SetMinSize((min(980, width), min(640, height)))
        self.SetSize((width, height))
        self.CentreOnScreen()
        self.Bind(wx.EVT_CLOSE, self.close)
        self.Bind(wx.EVT_SYS_COLOUR_CHANGED, self.system_colours_changed)
        self.apply_theme()
        self.restore()
        self.Layout()
        for page in self.pages:
            page.FitInside()
        self.refresh()
        if self.controller.board_path:
            self.refresh_transition_catalog()
            self.status.last = 'Ready'
        self._saved_snapshot = self._settings_snapshot()

    DEFAULT_SIZE = (1280, 800)
    MESSAGES_HEIGHT = 170

    def _build_toolbar(self):
        """Window-level actions, with KiCad's own icons and tooltips."""
        toolbar = wx.Panel(self)
        self.toolbar = toolbar
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.messages = wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
        )
        self.status = MessageLog(self.messages)
        self.status.last = 'Ready'
        bitmaps = icons.load(('left', 'right', 'save', 'trash', 'undo', 'info'),
                             dark=ui_palette().dark)

        def tool(name, tip, handler):
            control = wx.BitmapButton(toolbar, bitmap=bitmaps[name], style=wx.BORDER_NONE)
            control.SetToolTip(tip)
            control.Bind(wx.EVT_BUTTON, self.guard(handler))
            sizer.Add(control, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 2)
            return control

        def separator():
            line = wx.StaticLine(toolbar, style=wx.LI_VERTICAL, size=(-1, 24))
            sizer.Add(line, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 8)

        self.previous_button = tool('left', 'Previous step', lambda event: self.navigate(-1))
        self.next_button = tool('right', 'Next step', lambda event: self.navigate(1))
        separator()
        self.save_button = tool(
            'save', 'Save workflow settings without applying board changes', self.save,
        )
        self.delete_settings_button = tool(
            'trash', 'Delete the saved workflow settings', self.delete_workflow_settings,
        )
        self.reset_settings_button = tool(
            'undo', 'Reset the current workflow settings', self.reset_workflow_settings,
        )
        separator()
        self.unit_buttons = {}
        for unit in ('mil', 'mm'):
            control = wx.ToggleButton(toolbar, label=unit, style=wx.BU_EXACTFIT)
            control.SetToolTip(f'Show read-only values in {unit}')
            control.SetValue(unit == self.readout_unit)
            control.Bind(wx.EVT_TOGGLEBUTTON,
                         self.guard(lambda event, value=unit: self.set_readout_unit(value)))
            sizer.Add(control, 0, wx.ALIGN_CENTER_VERTICAL)
            self.unit_buttons[unit] = control
        separator()
        self.messages_button = wx.BitmapToggleButton(
            toolbar, wx.ID_ANY, bitmaps['info'], style=wx.BORDER_NONE,
        )
        self.messages_button.SetToolTip('Show messages')
        self.messages_button.Bind(wx.EVT_TOGGLEBUTTON, self.toggle_messages)
        sizer.Add(self.messages_button, 0, wx.ALIGN_CENTER_VERTICAL)
        toolbar.SetSizer(sizer)
        return toolbar

    def set_readout_unit(self, unit):
        """Switch the unit of read-only values; entry fields keep their own."""
        self.readout_unit = unit
        for name, control in self.unit_buttons.items():
            control.SetValue(name == unit)
        self.refresh_placement()

    def toggle_messages(self, event=None):
        """Dock the message log under the window; step content does not move."""
        show = self.messages_button.GetValue()
        if show == self.messages.IsShown():
            return
        self.messages.Show(show)
        width, height = self.GetSize()
        delta = self.MESSAGES_HEIGHT + 10
        self.SetSize((width, height + delta if show else height - delta))
        self.Layout()

    def _settings_snapshot(self):
        return json.dumps(self.controller.config, sort_keys=True, default=str)

    def _refresh_guidance(self):
        """Step icons at a glance; description and checklist for the open step."""
        statuses, current = [], PAGES[self.book.GetSelection()]
        for step in PAGES:
            lines = progress.checklist(self.controller, step)
            statuses.append(progress.overall(lines))
            if step == current:
                self.checklist.set_lines(lines)
        self.step_list.set_statuses(statuses)
        if self.about_title.GetLabel() != current:
            self.about_title.SetLabel(current)
        width = max(120, self.about_box.GetClientSize().width - 22)
        if getattr(self, '_about_wrapped', None) != (current, width):
            self._about_wrapped = (current, width)
            # Wrap here: GTK's own wrapping reports a stale best height.
            from wx.lib.wordwrap import wordwrap
            dc = wx.ClientDC(self.about_text)
            dc.SetFont(self.about_text.GetFont())
            text = '\n'.join(line.rstrip() for line in
                             wordwrap(progress.ABOUT[current], width, dc).split('\n'))
            self.about_text.SetLabel(text)
            self.about_text.SetMinSize((-1, dc.GetMultiLineTextExtent(text)[1] + 4))
        self.about_box.Layout()

    def select_step(self, event):
        self._disarm_pick()
        self.book.SetSelection(event.GetSelection())
        self.step_list.Refresh()
        self._refresh_guidance()
        self._step_shown()

    def _checklist_target(self, step, index):
        """The control a checklist line is about; its tab is brought forward."""
        def tab(notebook, page, control):
            notebook.SetSelection(page)
            return control
        targets = {
            'Netlist source': (self.workbook_browse, self.mapper, self.mapper),
            'Netclass type': (self.netclass_type_grid,),
            'Layer assignment': (self.layer_map,),
            'Routing permissions': (self.rules,),
            'Connector assignments': tuple(
                (lambda page=page: tab(self.assignment_notebook, page,
                                       self.assignment_notebook.GetPage(page)))
                for page in range(3)),
            'RF transitions': (self.transition_list,) * 3,
            'Define RF gangs': (self.gang_list,),
            'Mechanical placement': (
                self.user_layer,
                lambda: tab(self.placement_notebook, 0, self.interposer_list),
                lambda: tab(self.placement_notebook, 1, self.anchor_gang_list),
                lambda: tab(self.placement_notebook, 2, self.dc_anchor_list),
                lambda: tab(self.placement_notebook, 3, self.hole_list)),
            'Interposer microvias': (self.microvia_enable,),
            'Board outline': (self.outline_list,) * 2,
            'Review changes': (self.review_button,) + (self.problem_list,) * 3,
            'Apply / results': (lambda: tab(self.results_notebook, 0, self.apply_button),) * 2,
        }.get(step, ())
        if not 0 <= index < len(targets):
            return None
        target = targets[index]
        return target() if callable(target) else target

    def _checklist_selected(self, index, line):
        """Clicking a checklist line points at its control; nothing else changes."""
        target = self._checklist_target(PAGES[self.book.GetSelection()], index)
        if target is not None:
            self.spotlight.show(target)

    def _step_shown(self):
        """Work a step does by itself when it opens."""
        self.spotlight.stop()
        if PAGES[self.book.GetSelection()] == 'Mechanical placement':
            self._placement_shown()
        else:
            self._highlight_proposal()

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
            with self._coalesced_refresh():
                try:
                    callback(event)
                except Exception as error:
                    self.status.SetLabel(str(error))
                    wx.MessageBox(str(error), 'Workflow requires attention', wx.OK | wx.ICON_ERROR, self)
                self.refresh()
        return run

    @contextmanager
    def _coalesced_refresh(self):
        """Run at most one dialog refresh for a whole user action.

        Repopulating a list selects its current row, and that selection event
        is itself a guarded handler. Without coalescing, one edit refreshed the
        entire dialog once per nested handler.
        """
        self._refresh_hold += 1
        try:
            yield
        finally:
            self._refresh_hold -= 1
            if not self._refresh_hold and self._refresh_pending:
                self.refresh()

    def _bind_commit(self, control, handler):
        """Commit typed text on Enter or focus departure, not per keystroke."""
        edited = [False]

        def mark(event):
            edited[0] = True
            event.Skip()

        def commit(event):
            if edited[0] and not self._closing:
                edited[0] = False
                handler()
            if event.GetEventType() != wx.wxEVT_TEXT_ENTER:
                event.Skip()

        control.Bind(wx.EVT_TEXT, mark)
        control.Bind(wx.EVT_TEXT_ENTER, commit)
        control.Bind(wx.EVT_KILL_FOCUS, commit)

    def _style_grid(self, view):
        palette = ui_palette()
        view.SetDefaultCellBackgroundColour(palette.window)
        view.SetDefaultCellTextColour(palette.text)
        view.SetLabelBackgroundColour(palette.heading)
        view.SetLabelTextColour(palette.panel_text)
        view.SetSelectionBackground(palette.highlight)
        view.SetSelectionForeground(palette.highlight_text)
        self._enable_grid_overflow_tooltips(view)

    def _enable_grid_overflow_tooltips(self, view):
        if getattr(view, '_workflow_overflow_tooltips', False):
            return
        view._workflow_overflow_tooltips = True
        window = view.GetGridWindow()
        window.Bind(
            wx.EVT_MOTION,
            lambda event, target=view: self._grid_tooltip_motion(event, target),
        )
        window.Bind(wx.EVT_LEAVE_WINDOW, self._grid_tooltip_leave)

    @staticmethod
    def _grid_overflow_text(view, row, column):
        if not (0 <= row < view.GetNumberRows()
                and 0 <= column < view.GetNumberCols()):
            return ''
        renderer = view.GetCellRenderer(row, column)
        try:
            if not isinstance(renderer, (AssignmentRenderer, DimensionCellRenderer)):
                return ''
            value = view.GetCellValue(row, column).strip()
            if not value:
                return ''
            dc = wx.ClientDC(view.GetGridWindow())
            dc.SetFont(view.GetCellFont(row, column))
            width = view.CellToRect(row, column).width
            if isinstance(renderer, AssignmentRenderer):
                available = max(0, width - (28 if renderer.arrow else 12))
                clipped = wx.Control.Ellipsize(
                    value, dc, wx.ELLIPSIZE_END, available,
                )
                return value if clipped != value else ''
            try:
                _value_mm, unit, number_text = parse_dimension(value, 'mil')
                display = f'{number_text} {unit}'
            except ValueError:
                display = value
                unit = ''
                number_text = value
            number_width = dc.GetTextExtent(number_text)[0]
            unit_width = dc.GetTextExtent(unit + ' ▾')[0] if unit else 0
            return display if number_width + unit_width + 24 > width else ''
        finally:
            renderer.DecRef()

    def _grid_tooltip_motion(self, event, view):
        x, y = view.CalcUnscrolledPosition(event.GetX(), event.GetY())
        row, column = view.YToRow(y), view.XToCol(x)
        tooltip = self._grid_overflow_text(view, row, column)
        window = view.GetGridWindow()
        if window.GetToolTipText() != tooltip:
            window.SetToolTip(tooltip)
        event.Skip()

    @staticmethod
    def _grid_tooltip_leave(event):
        event.GetEventObject().SetToolTip('')
        event.Skip()

    @staticmethod
    def _fit_grid_contents(view, columns):
        """Size a compact grid from its headers, values and bounded column roles."""
        dc = wx.ClientDC(view)
        for column, (values, minimum, maximum, *extra) in enumerate(columns):
            dc.SetFont(view.GetLabelFont())
            measured = dc.GetTextExtent(view.GetColLabelValue(column))[0]
            dc.SetFont(view.GetDefaultCellFont())
            measured = max(
                [measured] + [dc.GetTextExtent(str(value))[0] for value in values],
            )
            view.SetColSize(column, max(minimum, min(maximum, measured + 28 + sum(extra))))
        width = sum(view.GetColSize(column) for column in range(view.GetNumberCols()))
        width += view.GetNumberCols() + 2
        height = view.GetColLabelSize() + sum(
            view.GetRowSize(row) for row in range(view.GetNumberRows())
        ) + 2
        view.SetMinSize((width, height))
        view.SetMaxSize((width, height))

    def _show_grid_choice_menu(self, view, row, column, choices, on_choice):
        """Open a synchronous native menu for a rendered table assignment cell."""
        if self._closing or row < 0 or column < 0:
            return
        view.SetGridCursor(row, column)
        view.MakeCellVisible(row, column)
        current = view.GetCellValue(row, column).strip()
        menu = wx.Menu()
        values = {}
        for choice in ('', *tuple(choices)):
            item = menu.AppendRadioItem(
                wx.ID_ANY, choice if choice else 'Clear assignment',
            )
            item.Check(choice == current)
            values[item.GetId()] = choice
        rectangle = view.CellToRect(row, column)
        x, y = view.CalcScrolledPosition(
            rectangle.x, rectangle.y + rectangle.height,
        )
        try:
            chosen = self._popup_assignment_menu(
                view.GetGridWindow(), menu, wx.Point(x, y),
            )
        finally:
            menu.Destroy()
        if chosen in values:
            on_choice(values[chosen])

    @staticmethod
    def _grid_choice_key(event, view, editable_column, show):
        row, column = view.GetGridCursorRow(), view.GetGridCursorCol()
        if (column == editable_column
                and event.GetKeyCode() in (
                    wx.WXK_SPACE, wx.WXK_RETURN,
                    getattr(wx, 'WXK_NUMPAD_ENTER', wx.WXK_RETURN),
                )):
            show(row)
            return
        event.Skip()

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

    @staticmethod
    def _fix_table_cell_width(control, width, height):
        control.SetMinSize((width, height))
        control.SetMaxSize((width, height))

    @staticmethod
    def _style_table_data_cell(control):
        palette = ui_palette()
        # A readout that shares a row with entry cells is flat grey.
        if isinstance(control, TableTextCell):
            control.set_colours(palette.panel, palette.text)
        else:
            control.SetBackgroundColour(palette.panel)
            control.SetForegroundColour(palette.text)

    @staticmethod
    def _assignment_table(parent, columns):
        """Create a bordered table surface with a semantic window-colour inset."""
        palette = ui_palette()
        surface = wx.Panel(parent, style=wx.BORDER_SIMPLE)
        surface.SetBackgroundColour(palette.window)
        inset = wx.BoxSizer(wx.VERTICAL)
        table = wx.Panel(surface)
        table.SetBackgroundColour(palette.window)
        cells = wx.FlexGridSizer(cols=columns, hgap=0, vgap=0)
        table.SetSizer(cells)
        inset.Add(table, 0, wx.ALL, 4)
        surface.SetSizer(inset)
        return surface, table, cells

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
        self.about_box.SetBackgroundColour(palette.info)
        for control in (self.about_title, self.about_text):
            control.SetBackgroundColour(palette.info)
            control.SetForegroundColour(palette.text)
        self.checklist.Refresh()
        for control in self._persistent_headings:
            self._style_heading(control)
        self._style_table_data_cell(self.interposer_ref_display)
        for table_surface in (
                self.interposer_assignment_surface, self.interposer_assignment_table,
                self.gang_assignment_rows, self.gang_assignment_table,
                self.dc_assignment_rows, self.dc_assignment_table,
            ):
            table_surface.SetBackgroundColour(palette.window)
        for control in self._status_labels:
            self._style_status(control, getattr(control, '_workflow_status_state', 'neutral'))
        for view in (self.mapper, self.netclass_type_grid, self.layer_map, self.rules,
                     self.gang_rows):
            self._style_grid(view)
        for view in (self.netclass_type_grid, self.layer_map):
            view.SetDefaultCellBackgroundColour(palette.panel)
        self._style_mapper_theme()
        if hasattr(self, 'spreadsheet_feedback'):
            self._set_spreadsheet_feedback(
                self.spreadsheet_feedback.GetLabel().strip(),
                getattr(self, '_spreadsheet_feedback_error', False),
            )
        self.refresh_layers()
        self.refresh_gangs()
        self.refresh_rf_transitions()
        self.refresh_mounting_holes()
        self.refresh_connector_assignments(True)
        self.refresh_microvias()
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
        """Step guidance is shown in the About-this-step box, not on the page."""
        return None

    def status_label(self, parent, label, fit=True):
        control = StatusLabel(parent, label='', style=wx.BORDER_SIMPLE)
        control.fit = fit
        if not fit:
            control.SetMinSize((StatusLabel.MINIMUM_WIDTH, -1))
        font = control.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        control.SetFont(font)
        control.SetLabel(label)
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
        self.mapper.SetColSize(0, 0)
        self.mapper.SetColSize(1, 82)
        for column, name in enumerate(HEADERS, 2):
            self.mapper.SetColSize(column, 125 if name in ('Interposer Pin', 'Connector Pin') else 112)
            self.mapper.SetReadOnly(0, column)
        self.mapper.SetRowSize(0, 34)
        for row_index in range(1, SPREADSHEET_PREVIEW_ROWS + 1):
            self.mapper.SetRowSize(row_index, 28)
        table_height = (self.mapper.GetColLabelSize() + self.mapper.GetRowSize(0)
                        + sum(self.mapper.GetRowSize(row) for row in range(1, SPREADSHEET_PREVIEW_ROWS + 1))
                        + 6)
        table_width = sum(
            self.mapper.GetColSize(column) for column in range(self.mapper.GetNumberCols())
        ) + self.mapper.GetNumberCols() + 2
        self.mapper.SetMinSize((table_width, table_height))
        self.mapper.SetMaxSize((table_width, table_height))
        self.mapper.SetCellValue(0, 1, 'Column')
        self.mapper.SetReadOnly(0, 0)
        self.mapper.SetReadOnly(0, 1)
        palette = ui_palette()
        self.mapper.SetCellBackgroundColour(0, 1, palette.assigned)
        self.mapper.SetCellTextColour(0, 1, palette.text)
        self.mapper.SetCellRenderer(0, 1, AssignmentRenderer(False))
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
        self.columns_status.Hide()
        self.nets_status.Hide()
        self.view_button = self.button(mapping_box, 'Open Full Table…', self.view_data)
        row.Add(self.view_button, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self.preview_count = wx.StaticText(mapping_box, label='Showing 0 of 0 nets')
        self.preview_count.SetForegroundColour(ui_palette().muted)
        row.Add(self.preview_count, 0, wx.ALIGN_CENTER_VERTICAL)
        mapping.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(mapping, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.workbook_path.Bind(wx.EVT_TEXT, self.workbook_changed)
        self.sheet.Bind(wx.EVT_CHOICE, self.worksheet_changed)
        self.header.Bind(wx.EVT_SPINCTRL, self.header_changed)
        self.header.Bind(wx.EVT_TEXT, self.header_changed)
        self.ignored.Bind(wx.EVT_TEXT, self.excluded_rows_changed)
        self.mapper.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.assignment_clicked)
        self.mapper.Bind(wx.EVT_KEY_DOWN, self.assignment_key_down)
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

    def assignment_clicked(self, event):
        if event.GetRow() == 0 and event.GetCol() >= 2:
            self._show_assignment_menu(event.GetCol())
            return
        event.Skip()

    def assignment_key_down(self, event):
        row, column = self.mapper.GetGridCursorRow(), self.mapper.GetGridCursorCol()
        if (event.GetKeyCode() in (
                wx.WXK_SPACE, wx.WXK_RETURN,
                getattr(wx, 'WXK_NUMPAD_ENTER', wx.WXK_RETURN),
            )
                and row == 0 and column >= 2):
            self._show_assignment_menu(column)
            return
        event.Skip()

    def _show_assignment_menu(self, column):
        """Open a one-click native menu without leaving a wx.Grid editor active."""
        if self._closing or column < 2 or column >= len(HEADERS) + 2:
            return
        self._show_grid_choice_menu(
            self.mapper, 0, column, self._spreadsheet_columns,
            lambda value: self._set_mapping_choice(column, value),
        )

    @staticmethod
    def _popup_assignment_menu(window, menu, position):
        # Return the selected ID directly instead of depending on platform-
        # specific wx.EVT_MENU delivery from a temporary popup menu.
        return window.GetPopupMenuSelectionFromUser(menu, position)

    def _set_mapping_choice(self, column, value):
        if self._closing:
            return
        self.mapper.SetCellValue(0, column, value)
        if column == 2:
            self._autofill_consecutive_mapping()
        self._style_assignment_row()
        self._schedule_spreadsheet_parse('mapping', 0)

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
        by_column = {column: label for label, column in self._spreadsheet_columns.items()}
        was_updating = self._updating_spreadsheet
        self._updating_spreadsheet = True
        try:
            for field_offset, header in enumerate(HEADERS):
                grid_column = field_offset + 2
                source_column = mapped.get(header)
                self.mapper.SetCellValue(0, grid_column, by_column.get(source_column, ''))
            self._render_mapping_preview(table, mapped)
            self._style_assignment_row()
            self._fit_grid_contents(self.mapper, (
                ((), 0, 0),
                ([self.mapper.GetCellValue(row, 1)
                  for row in range(1, self.mapper.GetNumberRows())], 72, 96),
                *tuple(
                    ([self.mapper.GetCellValue(row, column)
                      for row in range(self.mapper.GetNumberRows())], 100, 170, 10)
                    for column in range(2, self.mapper.GetNumberCols())
                ),
            ))
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
        self.netclass_auto_assign = self.button(box, 'Auto Assign', self.auto_assign_netclass_types)
        self.netclass_auto_assign.SetToolTip(
            'Fill blank types from exact J# / J### reference conventions and the exact DC netclass; preserve manual selections.'
        )
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
        self.netclass_type_grid.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.netclass_type_clicked)
        self.netclass_type_grid.Bind(
            wx.EVT_KEY_DOWN,
            lambda event: self._grid_choice_key(
                event, self.netclass_type_grid, 3, self._show_netclass_type_menu,
            ),
        )
        sizer.Add(self.netclass_type_grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        sizer.Add(self.netclass_auto_assign, 0, wx.LEFT | wx.TOP, 8)
        self.netclass_type_status = self.status_label(box, '0 of 0 assigned')
        sizer.Add(self.netclass_type_status, 0, wx.ALL, 8)
        self.netclass_type_status.Hide()
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
            view.SetReadOnly(row, 3)
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
        self.pages[PAGES.index('Netclass type')].FitInside()

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
            self._show_netclass_type_menu(event.GetRow())
            return
        event.Skip()

    def _show_netclass_type_menu(self, row):
        if 0 <= row < self.netclass_type_grid.GetNumberRows():
            self._show_grid_choice_menu(
                self.netclass_type_grid, row, 3, ('RF', 'DC'),
                lambda value: self._set_netclass_type_choice(row, value),
            )

    def _set_netclass_type_choice(self, row, kind):
        if row < 0 or row >= self.netclass_type_grid.GetNumberRows():
            return
        name = self.netclass_type_grid.GetCellValue(row, 0)
        self.netclass_type_grid.SetCellValue(row, 3, kind)
        if kind:
            self.controller.set_netclass_type(name, kind)
        else:
            self.controller.config['netclass_types'].pop(name, None)
            self.controller.changed()
        self.refresh_netclass_types()
        self.refresh_gangs()
        self.refresh_connector_assignments(True)
        self.status.SetLabel(
            f'{name} classified as {kind}. Board files were not modified.' if kind else
            f'{name} is unassigned. Board files were not modified.'
        )

    def netclass_type_changed(self, event):
        """Compatibility entry point for callers that commit a value directly."""
        if event.GetCol() != 3:
            event.Skip(); return
        self._set_netclass_type_choice(
            event.GetRow(), self.netclass_type_grid.GetCellValue(event.GetRow(), 3),
        )
        event.Skip()

    def build_layer_assignment(self, panel):
        self.task_box(
            panel, 'Layer assignment', 'Assign each netlist layer to the board stackup',
            'Map layer names from the imported netlist to enabled KiCad copper layers. Auto assign fills only unambiguous numeric matches and preserves manual selections. Changes take effect immediately and are used by validation and RF artifact checks.',
        )
        layer_box = wx.StaticBox(panel, label='Layer mappings')
        layer_sizer = wx.StaticBoxSizer(layer_box, wx.VERTICAL)
        self.layer_auto_assign = self.button(layer_box, 'Auto Assign', self.auto_assign_layers)
        self.layer_auto_assign.SetToolTip(
            'Fill blank mappings with unambiguous layer-number matches; preserve manual selections.'
        )

        self.layer_map = grid.Grid(layer_box)
        self.layer_map.CreateGrid(0, 2)
        self.layer_map.SetRowLabelSize(0)
        self.layer_map.SetColLabelValue(0, 'Netlist layer')
        self.layer_map.SetColLabelValue(1, 'KiCad layer')
        self.layer_map.SetColSize(0, 140)
        self.layer_map.SetColSize(1, 140)
        self.layer_map.SetMinSize((-1, self.layer_map.GetColLabelSize() + 2))
        self.layer_map.SetMaxSize((-1, self.layer_map.GetColLabelSize() + 2))
        layer_sizer.Add(self.layer_map, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        layer_sizer.Add(self.layer_auto_assign, 0, wx.LEFT | wx.TOP, 8)
        status_row = wx.BoxSizer(wx.HORIZONTAL)
        self.layer_mapping_status = self.status_label(layer_box, '0 of 0 assigned')
        status_row.Add(self.layer_mapping_status, 0, wx.ALIGN_CENTER_VERTICAL)
        self.layer_mapping_status.Hide()
        status_row.AddStretchSpacer()
        layer_sizer.Add(status_row, 0, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(layer_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.layer_map.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.layer_assignment_clicked)
        self.layer_map.Bind(
            wx.EVT_KEY_DOWN,
            lambda event: self._grid_choice_key(
                event, self.layer_map, 1, self._show_layer_assignment_menu,
            ),
        )

    def build_routing_permissions(self, panel):
        self.task_box(
            panel, 'Routing permissions', 'Review the current routing validation policy',
            'This release evaluates permissions by netclass and copper layer. Disallowed combinations block workflow validation only; a future per-net design will add search, filters and previewed KiCad rule updates.',
        )
        self.rules = grid.Grid(panel)
        self.rules.CreateGrid(0, 1)
        self.rules.Bind(grid.EVT_GRID_CELL_CHANGED, self.routing_permission_changed)
        panel.GetSizer().Add(self.rules, 0, wx.ALL, 8)
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
                self.layer_map.SetReadOnly(row, 1)
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
            self.pages[PAGES.index('Layer assignment')].FitInside()

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
            self.new_rules.SetLabel(
                f'{len(self.controller.new_rules)} new combinations require review.'
                if self.controller.new_rules else ''
            )
            size = (
                self.rules.GetRowLabelSize() + 2 + sum(
                    self.rules.GetColSize(col) for col in range(self.rules.GetNumberCols())),
                self.rules.GetColLabelSize() + 2 + sum(
                    self.rules.GetRowSize(row) for row in range(self.rules.GetNumberRows())),
            )
            self.rules.SetMinSize(size)
            self.rules.SetMaxSize(size)
            self.pages[PAGES.index('Routing permissions')].Layout()
            self.pages[PAGES.index('Routing permissions')].FitInside()
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
            self._show_layer_assignment_menu(event.GetRow())
            return
        event.Skip()

    def _show_layer_assignment_menu(self, row):
        if 0 <= row < self.layer_map.GetNumberRows():
            self._show_grid_choice_menu(
                self.layer_map, row, 1, self.controller.copper_layers,
                lambda value: self._set_layer_assignment_choice(row, value),
            )

    def _set_layer_assignment_choice(self, row, kicad_layer):
        if row < 0 or row >= self.layer_map.GetNumberRows():
            return
        netlist_layer = self.layer_map.GetCellValue(row, 0)
        self.layer_map.SetCellValue(row, 1, kicad_layer)
        if kicad_layer:
            self.controller.set_layer_mapping(netlist_layer, kicad_layer)
        else:
            self.controller.config['layer_mapping'].pop(netlist_layer, None)
            self.controller.changed()
        names = [self.layer_map.GetCellValue(index, 0)
                 for index in range(self.layer_map.GetNumberRows())]
        assigned = sum(bool(self.controller.config['layer_mapping'].get(name)) for name in names)
        self.layer_mapping_status.SetLabel(f'{assigned} of {len(names)} assigned')
        self._style_status(
            self.layer_mapping_status,
            'success' if names and assigned == len(names) else 'warning',
        )
        self._style_layer_assignment_cell(row)
        self.status.SetLabel(
            f'{netlist_layer} now maps to {kicad_layer}. The current workflow changed; '
            'board files were not modified.' if kicad_layer else
            f'{netlist_layer} is unassigned. The current workflow changed; board files were not modified.'
        )
        self.refresh()

    def layer_assignment_changed(self, event):
        """Compatibility entry point for callers that commit a value directly."""
        if self._updating_layers or event.GetCol() != 1:
            event.Skip()
            return
        self._set_layer_assignment_choice(
            event.GetRow(), self.layer_map.GetCellValue(event.GetRow(), 1),
        )
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
        self.assignment_notebook = wx.Notebook(panel)
        interposer_box = wx.Panel(self.assignment_notebook)
        interposer = wx.BoxSizer(wx.VERTICAL)
        interposer_box.SetSizer(interposer)
        (self.interposer_assignment_surface,
         self.interposer_assignment_table,
         self.interposer_assignment_grid) = self._assignment_table(interposer_box, 5)
        for label, width in (
                ('Ref des', 100), ('Pins used', 85), ('Symbol pins', 90),
                ('Symbol', 280), ('Footprint', 300)):
            heading = wx.StaticText(
                self.interposer_assignment_table, label=label, style=wx.BORDER_SIMPLE,
            )
            font = heading.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD); heading.SetFont(font)
            self._style_heading(heading)
            self._persistent_headings.append(heading)
            self._fix_table_cell_width(heading, width, 34)
            self.interposer_assignment_grid.Add(heading, 0, wx.EXPAND | wx.ALL, 0)
        self.interposer_ref_display = TableTextCell(
            self.interposer_assignment_table, label='—',
        )
        self._fix_table_cell_width(self.interposer_ref_display, 100, 38)
        self._style_table_data_cell(self.interposer_ref_display)
        self.interposer_assignment_grid.Add(self.interposer_ref_display, 0, wx.EXPAND)
        self.interposer_pins_used_display = TableTextCell(
            self.interposer_assignment_table, label='—',
        )
        self._fix_table_cell_width(self.interposer_pins_used_display, 85, 38)
        self._style_table_data_cell(self.interposer_pins_used_display)
        self.interposer_assignment_grid.Add(self.interposer_pins_used_display, 0, wx.EXPAND)
        self.interposer_symbol_pins_display = TableTextCell(
            self.interposer_assignment_table, label='—',
        )
        self._fix_table_cell_width(self.interposer_symbol_pins_display, 90, 38)
        self._style_table_data_cell(self.interposer_symbol_pins_display)
        self.interposer_assignment_grid.Add(self.interposer_symbol_pins_display, 0, wx.EXPAND)
        self.interposer_symbol_assignment = SymbolAssignmentCtrl(
            self.interposer_assignment_table, self, 'Choose interposer symbol', False,
            lambda selection, symbol: self.assign_interposer_symbol(selection, symbol),
        )
        self._fix_table_cell_width(self.interposer_symbol_assignment, 280, 38)
        self.interposer_assignment_grid.Add(self.interposer_symbol_assignment, 0, wx.EXPAND)
        self.interposer_footprint_assignment = FootprintAssignmentCtrl(
            self.interposer_assignment_table, self.assign_interposer_footprint,
        )
        self._fix_table_cell_width(self.interposer_footprint_assignment, 300, 38)
        self.interposer_assignment_grid.Add(self.interposer_footprint_assignment, 0, wx.EXPAND)
        interposer.Add(self.interposer_assignment_surface, 0, wx.ALL, 8)
        self.interposer_assignment_status = self.status_label(interposer_box, '0 of 0 interposers assigned')
        interposer.Add(self.interposer_assignment_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.interposer_assignment_status.Hide()
        self.assignment_notebook.AddPage(interposer_box, 'Interposer')

        gangs_box = wx.Panel(self.assignment_notebook)
        gangs = wx.BoxSizer(wx.VERTICAL)
        gangs_box.SetSizer(gangs)
        self.assignment_filter = wx.SearchCtrl(gangs_box, style=wx.TE_PROCESS_ENTER)
        self.assignment_filter.SetDescriptiveText('Search')
        self.assignment_filter.SetMinSize((330, -1))
        self.assignment_filter.SetMaxSize((330, -1))
        self.assignment_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_connector_assignments(True))
        gangs.Add(self.assignment_filter, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        (self.gang_assignment_rows,
         self.gang_assignment_table,
         _assignment_grid) = self._assignment_table(gangs_box, 4)
        gangs.Add(self.gang_assignment_rows, 0, wx.ALL, 8)
        self.gang_assignment_status = self.status_label(gangs_box, '0 of 0 gangs assigned')
        gangs.Add(self.gang_assignment_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.gang_assignment_status.Hide()
        self.assignment_notebook.AddPage(gangs_box, 'RF gangs')
        self.gang_assignment_controls = {}
        self.gang_assignment_all_controls = {}

        dc_box = wx.Panel(self.assignment_notebook)
        dc = wx.BoxSizer(wx.VERTICAL)
        dc_box.SetSizer(dc)
        self.dc_assignment_filter = wx.SearchCtrl(dc_box, style=wx.TE_PROCESS_ENTER)
        self.dc_assignment_filter.SetDescriptiveText('Search')
        self.dc_assignment_filter.SetMinSize((330, -1))
        self.dc_assignment_filter.SetMaxSize((330, -1))
        self.dc_assignment_filter.Bind(
            wx.EVT_TEXT, lambda event: self.refresh_connector_assignments(True),
        )
        dc.Add(self.dc_assignment_filter, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        (self.dc_assignment_rows,
         self.dc_assignment_table,
         _dc_grid) = self._assignment_table(dc_box, 5)
        dc.Add(self.dc_assignment_rows, 0, wx.ALL, 8)
        self.dc_assignment_status = self.status_label(dc_box, '0 of 0 DC connectors assigned')
        dc.Add(self.dc_assignment_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.dc_assignment_status.Hide()
        self.assignment_notebook.AddPage(dc_box, 'DC connectors')
        panel.GetSizer().Add(self.assignment_notebook, 1, wx.EXPAND | wx.ALL, 8)
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

    def _interposer_used_pins(self, reference):
        if not self.controller.table or not reference:
            return set()
        return {
            str(record.interposer_pin) for record in self.controller.table.records
            if record.interposer == reference
        }

    def _connector_used_pins(self, reference):
        if not self.controller.table or not reference:
            return set()
        return {
            str(record.connector_pin) for record in self.controller.table.records
            if record.connector == reference
        }

    @staticmethod
    def _symbol_pins(symbol):
        return {str(pin[0]) for pin in symbol.pin_numbers} if symbol else set()

    @staticmethod
    def _pin_count_text(count):
        return f'{count} pin' + ('' if count == 1 else 's')

    def _symbol_assignment_issue(self, kind, symbol, used_pins=()):
        if symbol is None:
            return 'Choose a symbol.'
        available = self._symbol_pins(symbol)
        if kind == 'rf':
            if len(available) != 2:
                return (f'Symbol has {self._pin_count_text(len(available))}; '
                        'an RF connector symbol requires exactly 2 pins.')
            return ''
        used = {str(pin) for pin in used_pins}
        if kind == 'dc' and symbol.unit_count != 1:
            return 'Choose a single-unit DC connector symbol.'
        if len(available) < len(used):
            return (f'Symbol has {self._pin_count_text(len(available))}; '
                    f'{self._pin_count_text(len(used))} are used.')
        missing = sorted(used - available)
        if missing:
            return f'Symbol is missing used pins {", ".join(missing)}.'
        return ''

    def _set_symbol_assignment_state(self, symbol_control, footprint_control,
                                     kind, symbol, used_pins, selected_footprint,
                                     symbol_pins_display=None):
        issue = self._symbol_assignment_issue(kind, symbol, used_pins)
        symbol_control.set_valid(not issue)
        if symbol_pins_display is not None:
            symbol_pins_display.SetLabel(str(len(self._symbol_pins(symbol))) if symbol else '—')
        options = self._allowed_footprint_specs(symbol) if not issue else ()
        footprint_control.set_options(options, selected_footprint, valid=not issue)
        footprint_control.Enable(bool(symbol and not issue))
        return issue, options

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
        issue, options = self._set_symbol_assignment_state(
            self.interposer_symbol_assignment, self.interposer_footprint_assignment,
            'interposer', symbol, self._interposer_used_pins(reference), {},
            self.interposer_symbol_pins_display,
        )
        if issue:
            self.status.SetLabel(f'{reference}: {issue}')
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self.controller.changed()
        self._refresh_assignment_status()

    def assign_interposer_footprint(self, selection):
        reference = self._interposer_reference()
        if not reference:
            return
        current = self.controller.config['interposers'].setdefault(reference, {})
        if not selection:
            for key in ('path', 'library_id', 'sha256', 'approved', 'footprint_mpn'):
                current.pop(key, None)
            self.controller.changed()
            self._refresh_assignment_status()
            return
        from .core.libraries import read_footprint
        approved = read_footprint(selection['path'], selection['library_id'], selection.get('sha256'))
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
            if controls.get('symbol_pins') is not None:
                controls['symbol_pins'].SetLabel('—')

    def _set_gang_symbol(self, gang, selection, symbol, options):
        self.controller.set_gang(gang, {
            'symbol': dict(selection), 'part_id': '', 'library_id': '',
            'part_sha256': '', 'footprint_mpn': '',
            'value': symbol.value, 'symbol_mpn': symbol.mpn,
        })
        controls = self.gang_assignment_controls.get(gang)
        if controls:
            controls['symbol'].set_selection(selection)
            issue = self._symbol_assignment_issue('rf', symbol)
            controls['symbol'].set_valid(not issue)
            controls['footprint'].set_options(options if not issue else (), {}, valid=not issue)
            controls['footprint'].Enable(not issue)

    def assign_gang_symbol(self, gang, selection, symbol):
        self._clear_bulk_assignment('rf')
        issue = self._symbol_assignment_issue('rf', symbol)
        options = self._allowed_footprint_specs(symbol) if not issue else ()
        self._set_gang_symbol(gang, selection, symbol, options)
        if issue:
            self.status.SetLabel(f'{gang}: {issue}')
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_all_gang_symbols(self, selection, symbol):
        names = self.controller.gang_names()
        issue = self._symbol_assignment_issue('rf', symbol)
        options = self._allowed_footprint_specs(symbol) if not issue else ()
        self._bulk_assignments['rf'] = {
            'names': names, 'symbol': dict(selection), 'footprint': {},
        }
        for gang in names:
            self._set_gang_symbol(gang, selection, symbol, options)
        controls = self.gang_assignment_all_controls
        controls['symbol'].set_selection(selection)
        controls['symbol'].set_valid(not issue)
        controls['footprint'].set_options(options, {}, valid=not issue)
        controls['footprint'].Enable(not issue)
        if issue:
            self.status.SetLabel(issue)
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_gang_footprint(self, gang, selection):
        self._clear_bulk_assignment('rf')
        if not selection:
            self.controller.set_gang(gang, {
                'part_id': '', 'library_id': '', 'part_sha256': '',
                'footprint_mpn': '',
            })
            self._refresh_assignment_status()
            return
        self.controller.set_gang(gang, {
            'part_id': selection['path'], 'library_id': selection['library_id'],
            'part_sha256': selection.get('sha256', ''),
        })
        self._refresh_assignment_status()

    def assign_all_gang_footprints(self, selection):
        names = self.controller.gang_names()
        state = self._bulk_assignments['rf']
        state['names'] = names
        state['footprint'] = dict(selection or {})
        for gang in names:
            values = ({
                'part_id': selection['path'], 'library_id': selection['library_id'],
                'part_sha256': selection.get('sha256', ''),
            } if selection else {
                'part_id': '', 'library_id': '', 'part_sha256': '',
                'footprint_mpn': '',
            })
            self.controller.set_gang(gang, values)
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
            issue = self._symbol_assignment_issue(
                'dc', symbol, self._connector_used_pins(reference),
            )
            controls['symbol'].set_valid(not issue)
            controls['symbol_pins'].SetLabel(str(len(self._symbol_pins(symbol))))
            controls['footprint'].set_options(options if not issue else (), {}, valid=not issue)
            controls['footprint'].Enable(not issue)

    def assign_dc_symbol(self, reference, selection, symbol):
        self._clear_bulk_assignment('dc')
        issue = self._symbol_assignment_issue(
            'dc', symbol, self._connector_used_pins(reference),
        )
        options = self._allowed_footprint_specs(symbol) if not issue else ()
        self._set_dc_symbol(reference, selection, symbol, options)
        if issue:
            self.status.SetLabel(f'{reference}: {issue}')
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_all_dc_symbols(self, selection, symbol):
        names = self.controller.dc_connector_names()
        issues = {
            reference: self._symbol_assignment_issue(
                'dc', symbol, self._connector_used_pins(reference),
            )
            for reference in names
        }
        valid_for_all = not any(issues.values())
        options = self._allowed_footprint_specs(symbol) if symbol.unit_count == 1 else ()
        self._bulk_assignments['dc'] = {
            'names': names, 'symbol': dict(selection), 'footprint': {},
        }
        for reference in names:
            self._set_dc_symbol(reference, selection, symbol, options)
        controls = self.dc_assignment_all_controls
        controls['symbol'].set_selection(selection)
        controls['symbol'].set_valid(valid_for_all)
        controls['symbol_pins'].SetLabel(str(len(self._symbol_pins(symbol))))
        controls['footprint'].set_options(options if valid_for_all else (), {}, valid=valid_for_all)
        controls['footprint'].Enable(valid_for_all)
        if not valid_for_all:
            first = next(reference for reference in names if issues[reference])
            self.status.SetLabel(f'{first}: {issues[first]}')
        elif not symbol.footprint_filters:
            self.status.SetLabel(f'{symbol.library_id} has no footprint filters; ask the librarian to add them.')
        elif not options:
            self.status.SetLabel(f'No loaded footprint matches {symbol.library_id} and its electrical pins.')
        self._refresh_assignment_status()

    def assign_dc_footprint(self, reference, selection):
        self._clear_bulk_assignment('dc')
        if not selection:
            self.controller.set_dc_connector(reference, {
                'path': '', 'library_id': '', 'sha256': '', 'footprint_mpn': '',
            })
            self._refresh_assignment_status()
            return
        from .core.libraries import read_footprint
        approved = read_footprint(selection['path'], selection['library_id'],
                                  selection.get('sha256'))
        self.controller.set_dc_connector(reference, {
            'path': approved.path, 'library_id': approved.library_id,
            'sha256': approved.sha256, 'footprint_mpn': approved.mpn,
        })
        self._refresh_assignment_status()

    def assign_all_dc_footprints(self, selection):
        approved = None
        if selection:
            from .core.libraries import read_footprint
            approved = read_footprint(selection['path'], selection['library_id'],
                                      selection.get('sha256'))
        names = self.controller.dc_connector_names()
        state = self._bulk_assignments['dc']
        state['names'] = names
        state['footprint'] = dict(selection or {})
        for reference in names:
            values = ({
                'path': approved.path, 'library_id': approved.library_id,
                'sha256': approved.sha256, 'footprint_mpn': approved.mpn,
            } if approved else {
                'path': '', 'library_id': '', 'sha256': '', 'footprint_mpn': '',
            })
            self.controller.set_dc_connector(reference, values)
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
        label = TableTextCell(
            parent, label='All', bold=True, background=ui_palette().heading,
        )
        quantity = TableTextCell(
            parent, label='—', bold=True, background=ui_palette().heading,
        )
        leading = [(label, 100), (quantity, 80 if kind == 'rf' else 85)]
        symbol_pins = None
        if kind == 'dc':
            symbol_pins = TableTextCell(
                parent, label='—', bold=True, background=ui_palette().heading,
            )
            leading.append((symbol_pins, 90))
        for control, width in leading:
            self._fix_table_cell_width(control, width, 38)
            control.set_colours(ui_palette().heading, ui_palette().text)
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
        symbol_control.set_bulk()
        footprint_control.set_bulk()
        symbol_spec = state.get('symbol', {})
        symbol_control.set_selection(symbol_spec)
        symbol = self._configured_symbol(symbol_spec, kind == 'rf')
        used_sets = (
            [self._connector_used_pins(name) for name in names]
            if kind == 'dc' else [set()]
        )
        issues = [self._symbol_assignment_issue(kind, symbol, used) for used in used_sets]
        symbol_valid = bool(symbol) and not any(issues)
        symbol_control.set_valid(symbol_valid)
        if symbol_pins is not None:
            symbol_pins.SetLabel(str(len(self._symbol_pins(symbol))) if symbol else '—')
        footprint_control.set_options(
            self._allowed_footprint_specs(symbol) if symbol_valid else (),
            state.get('footprint', {}), valid=symbol_valid,
        )
        footprint_control.Enable(symbol_valid)
        self._fix_table_cell_width(symbol_control, 300 if kind == 'rf' else 280, 38)
        self._fix_table_cell_width(footprint_control, 320 if kind == 'rf' else 300, 38)
        for control in [item[0] for item in leading] + [symbol_control, footprint_control]:
            sizer.Add(
                control, 0, wx.EXPAND,
            )
        setattr(self, controls_attribute, {
            'symbol': symbol_control, 'footprint': footprint_control,
            'symbol_pins': symbol_pins,
        })
        for _column in range(4 if kind == 'rf' else 5):
            sizer.Add(TableDividerCell(parent), 0, wx.EXPAND)

    def refresh_connector_assignments(self, force=False):
        reference = self._interposer_reference()
        names = self.controller.gang_names()
        dc_names = self.controller.dc_connector_names()
        query = self.assignment_filter.GetValue().casefold() if hasattr(self, 'assignment_filter') else ''
        dc_query = self.dc_assignment_filter.GetValue().casefold() \
            if hasattr(self, 'dc_assignment_filter') else ''
        signature = (reference, names, dc_names, query, dc_query)
        if not force and signature == self._assignment_signature:
            self._refresh_assignment_status()
            return
        self._assignment_signature = signature
        self.interposer_ref_display.SetLabel(reference or '—')
        interposer_used = self._interposer_used_pins(reference)
        self.interposer_pins_used_display.SetLabel(
            str(len(interposer_used)) if reference else '—',
        )
        interposer = self.controller.config['interposers'].get(reference, {}) if reference else {}
        self.refresh_interposer_placement()
        symbol_spec = interposer.get('symbol', {})
        self.interposer_symbol_assignment.set_selection(symbol_spec)
        symbol = self._configured_symbol(symbol_spec, False)
        self._set_symbol_assignment_state(
            self.interposer_symbol_assignment, self.interposer_footprint_assignment,
            'interposer', symbol, interposer_used, interposer,
            self.interposer_symbol_pins_display,
        )
        self.interposer_symbol_assignment.Enable(bool(reference))

        sizer = self.gang_assignment_table.GetSizer()
        sizer.Clear(True)
        self.gang_assignment_controls = {}
        self.gang_assignment_rows.SetBackgroundColour(ui_palette().window)
        self.gang_assignment_table.SetBackgroundColour(ui_palette().window)
        for label, width in (('Gang', 100), ('Positions', 80), ('Symbol', 300), ('Footprint', 320)):
            heading = wx.StaticText(self.gang_assignment_table, label=label, style=wx.BORDER_SIMPLE)
            font = heading.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD); heading.SetFont(font)
            self._style_heading(heading); self._fix_table_cell_width(heading, width, 34)
            sizer.Add(heading, 0, wx.EXPAND)
        self._add_all_assignment_row(
            self.gang_assignment_table, sizer, 'rf', names,
        )
        for name in names:
            if query and query not in name.casefold():
                continue
            settings = self.controller.gang(name)
            count = sum(record.connector == name for record in self.controller.table.records)
            gang_label = TableTextCell(self.gang_assignment_table, label=name)
            self._fix_table_cell_width(gang_label, 100, 38)
            self._style_table_data_cell(gang_label)
            positions = TableTextCell(self.gang_assignment_table, label=str(count))
            self._fix_table_cell_width(positions, 80, 38)
            self._style_table_data_cell(positions)
            symbol_control = SymbolAssignmentCtrl(
                self.gang_assignment_table, self, 'Choose connector symbol', True,
                lambda selection, symbol, gang=name: self.assign_gang_symbol(gang, selection, symbol),
            )
            footprint_control = FootprintAssignmentCtrl(
                self.gang_assignment_table,
                lambda selection, gang=name: self.assign_gang_footprint(gang, selection),
            )
            selected_symbol = settings.get('symbol', {})
            symbol_control.set_selection(selected_symbol)
            symbol = self._configured_symbol(selected_symbol, True)
            issue = self._symbol_assignment_issue('rf', symbol)
            symbol_control.set_valid(not issue)
            footprint_control.set_options(self._allowed_footprint_specs(symbol) if not issue else (), {
                'path': settings.get('part_id', ''), 'library_id': settings.get('library_id', ''),
                'sha256': settings.get('part_sha256', ''),
            }, valid=not issue)
            footprint_control.Enable(bool(symbol and not issue))
            self._fix_table_cell_width(symbol_control, 300, 38)
            self._fix_table_cell_width(footprint_control, 320, 38)
            for control in (gang_label, positions, symbol_control, footprint_control):
                sizer.Add(control, 0, wx.EXPAND)
            self.gang_assignment_controls[name] = {
                'symbol': symbol_control, 'footprint': footprint_control,
            }
        self.gang_assignment_table.Layout()
        self.gang_assignment_rows.Layout()

        dc_sizer = self.dc_assignment_table.GetSizer()
        dc_sizer.Clear(True)
        self.dc_assignment_controls = {}
        self.dc_assignment_rows.SetBackgroundColour(ui_palette().window)
        self.dc_assignment_table.SetBackgroundColour(ui_palette().window)
        for label, width in (
                ('Ref des', 100), ('Pins used', 85), ('Symbol pins', 90),
                ('Symbol', 280), ('Footprint', 300)):
            heading = wx.StaticText(self.dc_assignment_table, label=label, style=wx.BORDER_SIMPLE)
            font = heading.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD); heading.SetFont(font)
            self._style_heading(heading); self._fix_table_cell_width(heading, width, 34)
            dc_sizer.Add(heading, 0, wx.EXPAND)
        self._add_all_assignment_row(
            self.dc_assignment_table, dc_sizer, 'dc', dc_names,
        )
        for name in dc_names:
            if dc_query and dc_query not in name.casefold():
                continue
            selection = self.controller.dc_connector(name)
            used_pins = self._connector_used_pins(name)
            reference_label = TableTextCell(self.dc_assignment_table, label=name)
            self._fix_table_cell_width(reference_label, 100, 38)
            self._style_table_data_cell(reference_label)
            pins = TableTextCell(self.dc_assignment_table, label=str(len(used_pins)))
            self._fix_table_cell_width(pins, 85, 38)
            self._style_table_data_cell(pins)
            symbol_pins = TableTextCell(self.dc_assignment_table, label='—')
            self._fix_table_cell_width(symbol_pins, 90, 38)
            self._style_table_data_cell(symbol_pins)
            symbol_control = SymbolAssignmentCtrl(
                self.dc_assignment_table, self, 'Choose DC connector symbol', False,
                lambda spec, symbol, connector=name: self.assign_dc_symbol(connector, spec, symbol),
            )
            footprint_control = FootprintAssignmentCtrl(
                self.dc_assignment_table,
                lambda spec, connector=name: self.assign_dc_footprint(connector, spec),
            )
            symbol_spec = selection.get('symbol', {})
            symbol_control.set_selection(symbol_spec)
            symbol = self._configured_symbol(symbol_spec, False)
            self._set_symbol_assignment_state(
                symbol_control, footprint_control, 'dc', symbol, used_pins,
                selection, symbol_pins,
            )
            self._fix_table_cell_width(symbol_control, 280, 38)
            self._fix_table_cell_width(footprint_control, 300, 38)
            for control in (reference_label, pins, symbol_pins, symbol_control, footprint_control):
                dc_sizer.Add(control, 0, wx.EXPAND)
            self.dc_assignment_controls[name] = {
                'symbol': symbol_control, 'footprint': footprint_control,
                'symbol_pins': symbol_pins,
            }
        self.dc_assignment_table.Layout()
        self.dc_assignment_rows.Layout()
        self.pages[PAGES.index('Connector assignments')].FitInside()
        self._refresh_assignment_status()

    def _refresh_assignment_status(self):
        reference = self._interposer_reference()
        interposer = self.controller.config['interposers'].get(reference, {}) if reference else {}
        interposer_symbol = self._configured_symbol(interposer.get('symbol', {}), False)
        interposer_valid = not self._symbol_assignment_issue(
            'interposer', interposer_symbol, self._interposer_used_pins(reference),
        )
        assigned_interposer = int(bool(interposer_valid and interposer.get('library_id')))
        interposer_total = int(bool(reference))
        self.interposer_assignment_status.SetLabel(
            f'{assigned_interposer} of {interposer_total} interposers assigned',
        )
        names = self.controller.gang_names()
        assigned_gangs = 0
        for name in names:
            selection = self.controller.gang(name)
            symbol = self._configured_symbol(selection.get('symbol', {}), True)
            assigned_gangs += bool(
                not self._symbol_assignment_issue('rf', symbol) and selection.get('library_id')
            )
        self.gang_assignment_status.SetLabel(f'{assigned_gangs} of {len(names)} gangs assigned')
        dc_names = self.controller.dc_connector_names()
        assigned_dc = 0
        for name in dc_names:
            selection = self.controller.dc_connector(name)
            symbol = self._configured_symbol(selection.get('symbol', {}), False)
            assigned_dc += bool(
                not self._symbol_assignment_issue(
                    'dc', symbol, self._connector_used_pins(name),
                ) and selection.get('library_id')
            )
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
        selector_box.SetMinSize((225, -1))
        selector_box.SetMaxSize((240, -1))
        self.gang_list = wx.ListCtrl(
            selector_box, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
        )
        self.gang_list.SetMinSize((205, 60))
        self.gang_list.SetMaxSize((220, 60))
        for column, (heading, width) in enumerate((
            ('Gang', 55), ('Positions', 62), ('Status', 80),
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
        editor_box.SetMaxSize((380, -1))
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
        self.gang_rows.SetColSize(2, 172)
        self.gang_rows.SetDefaultRowSize(36, True)
        self.gang_rows.SetReadOnly(0, 0)
        self.gang_rows.SetMinSize((314, 220))
        self.gang_rows.Bind(grid.EVT_GRID_CELL_LEFT_CLICK, self.gang_cell_clicked)
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
        self.gang_custom_numbering = wx.TextCtrl(placement_page, style=wx.TE_PROCESS_ENTER)
        self.gang_custom_numbering.SetToolTip('Comma-separated position indices in traversal order.')
        placement.Add(self.gang_custom_numbering, 1, wx.EXPAND)
        placement.Add(wx.StaticText(placement_page, label='Pattern rotation (degrees)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_pattern_rotation = wx.TextCtrl(
            placement_page, value='0', style=wx.TE_PROCESS_ENTER,
        )
        placement.Add(self.gang_pattern_rotation, 1, wx.EXPAND)
        placement.Add(wx.StaticText(placement_page, label='Connector orientation (degrees)'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.gang_connector_rotation = wx.TextCtrl(
            placement_page, value='0', style=wx.TE_PROCESS_ENTER,
        )
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
        self.gang_definition_status = self.status_label(editor_box, 'Select a gang', fit=False)
        editor.Add(self.gang_definition_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        body.Add(editor, 0, wx.EXPAND | wx.RIGHT, 8)

        preview_box = wx.StaticBox(panel, label='Gang preview')
        preview_box.SetMinSize((250, -1))
        preview = wx.StaticBoxSizer(preview_box, wx.VERTICAL)
        self.pattern_view = PatternPreview(preview_box)
        preview.Add(self.pattern_view, 1, wx.EXPAND | wx.ALL | wx.FIXED_MINSIZE, 8)
        self.gang_preview_status = self.status_label(preview_box, 'Select a gang', fit=False)
        preview.Add(self.gang_preview_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        body.Add(preview, 1, wx.EXPAND)
        panel.GetSizer().Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        for control in (self.gang_custom_numbering, self.gang_pattern_rotation,
                        self.gang_connector_rotation):
            self._bind_commit(control, self.gang_draft_changed)
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
            self.pages[PAGES.index('Define RF gangs')].FitInside()

    def gang_cell_clicked(self, event):
        """Activate the composite dimension editor on the first click."""
        if event.GetCol() == 2:
            row, column = event.GetRow(), event.GetCol()
            self.gang_rows.SetGridCursor(row, column)
            wx.CallAfter(self._begin_gang_dimension_edit, row, column)
        event.Skip()

    def _begin_gang_dimension_edit(self, row, column):
        if self._closing or row >= self.gang_rows.GetNumberRows():
            return
        self.gang_rows.SetGridCursor(row, column)
        self.gang_rows.EnableCellEditControl()

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
        if not row_lengths:
            raise ValueError('Add a row and enter its positions')
        # A pitch is only needed when there is something to space.
        pitch_x = pitch_y = None
        if self.gang_pitch_x.GetValue().strip():
            pitch_x = self.gang_pitch_x.value_mm()
        elif max(row_lengths) > 1:
            raise ValueError('Enter the position pitch')
        if self.gang_pitch_y.GetValue().strip():
            pitch_y = self.gang_pitch_y.value_mm()
        elif len(row_lengths) > 1:
            raise ValueError('Enter the row pitch')
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
            'pitch_x': pitch_x,
            'pitch_y': pitch_y,
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
        combined = dict(self.controller.gang(self.current_gang))
        combined.update(values)
        combined['gang'] = self.current_gang
        return self.controller.gang_definition(combined).positions()

    def _validate_gang_population(self, points):
        expected = {record.connector_pin for record in self.controller.table.records
                    if record.connector == self.current_gang}
        if set(points) != expected:
            raise ValueError(
                f'Numbering must match {len(expected)} netlist positions exactly'
            )

    def _show_gang_definition(self, values, defined, clear_preview=False):
        self.pattern_view.orientation = values.get('connector_rotation', 0)
        if clear_preview:
            self.pattern_view.show(self.current_gang, {})
        if not defined and not values.get('row_lengths'):
            # A gang nobody has described yet: nothing to draw and nothing wrong.
            self.gang_position_total.SetLabel('0 positions total')
            self.gang_definition_status.SetLabel('Not defined')
            self._style_status(self.gang_definition_status, 'warning')
            self.gang_preview_status.SetLabel('Not defined')
            self._style_status(self.gang_preview_status, 'warning')
            return False
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
        with self._coalesced_refresh():
            self._gang_draft_changed(event)
        if event is not None:
            event.Skip()

    def _gang_draft_changed(self, event=None):
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

    def numbering_mode_changed(self, event):
        self.gang_custom_numbering.Enable(self.gang_numbering_mode.GetSelection() == 3)
        self.gang_draft_changed(event)

    def add_gang_row(self, event):
        self._loading_gang = True
        try:
            row = self.gang_rows.GetNumberRows()
            self._resize_gang_rows(row + 1)
            self.gang_rows.SetCellValue(row, 1, '')
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
        if hasattr(self, 'anchor_gang_list'):
            self.refresh_anchor_gangs()
        if hasattr(self, 'dc_anchor_list'):
            self.refresh_dc_anchors()
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
                if not self.controller.gang(name).get('defined'):
                    status = 'Not defined'
                else:
                    status = ('Defined' if progress.gang_defined(self.controller, name)
                              else 'Invalid')
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
        self.pages[PAGES.index('Define RF gangs')].FitInside()

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
                rows = list(applied.get('row_lengths') or [])
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
                self.gang_pitch_x.set_mm(applied.get('pitch_x'),
                                         units.get('pitch_x', 'mil'))
                self.gang_pitch_y.set_mm(applied.get('pitch_y'),
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
        if hasattr(self, 'anchor_gang_list'):
            self.current_anchor_gang = self.current_gang
            self.refresh_anchor_gangs()
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
        self.transition_filter.SetDescriptiveText('Search')
        self.transition_filter.Bind(wx.EVT_TEXT, lambda event: self.refresh_rf_transitions())
        controls.Add(self.transition_filter, 1, wx.RIGHT, 8)
        self.transition_status_filter = wx.Choice(
            panel, choices=['All statuses', 'Ready', 'Not assigned', 'Blocked'],
        )
        self.transition_status_filter.SetSelection(0)
        self.transition_status_filter.Bind(wx.EVT_CHOICE, lambda event: self.refresh_rf_transitions())
        controls.Add(self.transition_status_filter, 0, wx.RIGHT, 8)
        search_project = self.button(panel, 'Search Project', self.refresh_transition_catalog)
        search_project.SetToolTip(
            'Look again for RF transition files in the project folder.'
        )
        controls.Add(search_project, 0, wx.RIGHT, 12)
        self.transition_found = wx.StaticText(
            panel, label='Found 0 transitions in the project folder',
        )
        self.transition_found.SetForegroundColour(ui_palette().muted)
        controls.Add(self.transition_found, 0, wx.ALIGN_CENTER_VERTICAL)
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
        assignment.Add(self.button(detail_box, 'Browse…', self.browse_rf_transition), 0, wx.RIGHT, 8)
        assignment.Add(self.button(detail_box, 'Clear', self.clear_rf_transition), 0)
        detail.Add(assignment, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.GetSizer().Add(detail, 0, wx.EXPAND | wx.ALL, 8)
        self.transition_page_status = self.status_label(panel, '0 optional transitions assigned')
        self.transition_page_status.SetMinSize((360, -1))
        panel.GetSizer().Add(self.transition_page_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.transition_page_status.Hide()
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
        self.transition_found.SetLabel(
            f'Found {len(catalog)} transition{"" if len(catalog) == 1 else "s"} '
            'in the project folder'
        )
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

    PLACEMENT_KINDS = ('interposer', 'gang', 'dc')
    PLACEMENT_FEATURE = {'interposer': 'origin', 'gang': 'location 1', 'dc': 'pin 1'}

    def _drawing_layer_names(self):
        """User layers of the open board by their KiCad names, else KiCad's defaults."""
        names = []
        try:
            board = self.pcbnew.GetBoard()
            for attribute in ('Dwgs_User', 'Cmts_User', 'Eco1_User', 'Eco2_User',
                              *(f'User_{number}' for number in range(1, 46))):
                layer = getattr(self.pcbnew, attribute, None)
                if layer is not None and board.IsLayerEnabled(layer):
                    names.append(str(board.GetLayerName(layer)))
        except (AttributeError, TypeError, RuntimeError):
            names = []
        return names or ['User.Drawings', 'User.Comments', 'User.Eco1', 'User.Eco2']

    def build_anchors(self, panel):
        sizer = panel.GetSizer()
        layer_row = wx.BoxSizer(wx.HORIZONTAL)
        layer_row.Add(wx.StaticText(panel, label='Drawing layer:'), 0,
                      wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.user_layer = wx.ComboBox(
            panel, value='User.Drawings', choices=self._drawing_layer_names(),
            style=wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER,
        )
        self.user_layer.SetMinSize((200, -1))
        layer_row.Add(self.user_layer, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        # Short readout beside the layer: circle count, or why it cannot be read.
        self.drawing_status = wx.StaticText(panel, label='')
        layer_row.Add(self.drawing_status, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(layer_row, 0, wx.ALL, 8)
        self.placement_notebook = wx.Notebook(panel)
        sizer.Add(self.placement_notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.placement_buttons, self.proposal_text, self._proposals = {}, {}, {}
        self._proposal_uuids = frozenset()
        self.current_hole = None

        page, self.interposer_list = self._placement_tab(
            'interposer', 'Interposer', ('Ref des', 'Origin X', 'Origin Y'), 62)
        self.interposer_side, self.interposer_rotation = self._side_rotation_fields(page)
        page, self.anchor_gang_list = self._placement_tab(
            'gang', 'RF gangs', ('Gang', 'Location 1 X', 'Location 1 Y'), 230)
        note = wx.StaticText(page, label='Side and rotation are set in Define RF gangs.')
        note.SetForegroundColour(ui_palette().muted)
        page.GetSizer().Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        page, self.dc_anchor_list = self._placement_tab(
            'dc', 'DC connectors', ('Connector', 'Pin 1 X', 'Pin 1 Y'), 150)
        self.dc_anchor_side, self.dc_anchor_rotation = self._side_rotation_fields(page)

        holes_page = wx.Panel(self.placement_notebook)
        holes_page.SetSizer(wx.BoxSizer(wx.VERTICAL))
        self.hole_list = wx.ListCtrl(
            holes_page, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES)
        for column, (heading, width) in enumerate(
            (('Reference', 100), ('Footprint', 280), ('X', 150), ('Y', 150), ('Status', 110)),
        ):
            self.hole_list.InsertColumn(column, heading, width=width)
        self.hole_list.SetMinSize((802, 160))
        self.hole_list.SetMaxSize((-1, 160))
        holes_page.GetSizer().Add(self.hole_list, 0, wx.ALL, 8)
        hole_actions = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (('Add', self.add_hole), ('Remove', self.remove_hole),
                               ('Pick in Editor', self.pick_hole),
                               ('Show in Editor', self.show_hole)):
            hole_actions.Add(self.button(holes_page, label, handler), 0, wx.RIGHT, 8)
        holes_page.GetSizer().Add(hole_actions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        hole_fields = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        hole_fields.AddGrowableCol(1, 1)
        hole_fields.Add(wx.StaticText(holes_page, label='Reference:'), 0,
                        wx.ALIGN_CENTER_VERTICAL)
        self.hole_ref = wx.TextCtrl(holes_page, value='H1')
        self.hole_ref.SetMinSize((120, -1))
        hole_fields.Add(self.hole_ref, 0)
        hole_fields.Add(wx.StaticText(holes_page, label='Footprint:'), 0,
                        wx.ALIGN_CENTER_VERTICAL)
        self.hole_path = LibrarySelectionCtrl(
            holes_page, self, 'Choose mounting-hole footprint', 'footprint',
            allow_mechanical=True, on_change=self.library_changed,
        )
        self.hole_path.SetMinSize((520, -1))
        self.hole_path.SetMaxSize((560, -1))
        hole_fields.Add(self.hole_path, 1, wx.EXPAND)
        holes_page.GetSizer().Add(hole_fields, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.placement_notebook.AddPage(holes_page, 'Mounting holes')

        advanced_page = wx.Panel(self.placement_notebook)
        advanced_page.SetSizer(wx.BoxSizer(wx.VERTICAL))
        tolerance_row = wx.BoxSizer(wx.HORIZONTAL)
        tolerance_row.Add(wx.StaticText(advanced_page, label='Centre tolerance:'), 0,
                          wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.feature_tolerance = DimensionInput(advanced_page, value='0.3', compact=True)
        tolerance_row.Add(self.feature_tolerance, 0)
        advanced_page.GetSizer().Add(tolerance_row, 0, wx.ALL, 8)
        tolerance_help = wx.StaticText(
            advanced_page,
            label=('How far a drawing circle may be from its calculated position '
                   'and still count as a match.'),
        )
        tolerance_help.SetForegroundColour(ui_palette().muted)
        advanced_page.GetSizer().Add(tolerance_help, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.placement_notebook.AddPage(advanced_page, 'Advanced')

        self.user_layer.Bind(wx.EVT_COMBOBOX, self._mechanical_source_changed)
        self.user_layer.Bind(wx.EVT_TEXT_ENTER, self._mechanical_source_changed)
        self.feature_tolerance.bind_changed(self._mechanical_source_changed)
        self.interposer_rotation.Bind(wx.EVT_TEXT_ENTER, self._interposer_placement_changed)
        self.interposer_rotation.Bind(wx.EVT_KILL_FOCUS, self._interposer_placement_changed)
        self.interposer_side.Bind(wx.EVT_CHOICE, self._interposer_placement_changed)
        self.anchor_gang_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.select_anchor_gang)
        self.dc_anchor_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.select_dc_anchor)
        self.hole_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.select_hole)
        self.dc_anchor_rotation.Bind(wx.EVT_TEXT_ENTER, self._dc_placement_changed)
        self.dc_anchor_rotation.Bind(wx.EVT_KILL_FOCUS, self._dc_placement_changed)
        self.dc_anchor_side.Bind(wx.EVT_CHOICE, self._dc_placement_changed)
        self.placement_notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._placement_tab_changed)

    def _placement_tab(self, kind, title, headings, height):
        """One part tab: a read-only table, the three placement buttons, the proposal."""
        page = wx.Panel(self.placement_notebook)
        page.SetSizer(wx.BoxSizer(wx.VERTICAL))
        view = wx.ListCtrl(
            page, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES)
        for column, (heading, width) in enumerate(zip(
                (*headings, 'Side', 'Rotation', 'Status'), (100, 170, 170, 70, 80, 110))):
            view.InsertColumn(column, heading, width=width)
        view.SetMinSize((712, height))
        view.SetMaxSize((-1, height))
        page.GetSizer().Add(view, 0, wx.ALL, 8)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        buttons = {}
        for key, label, handler in (('pick', 'Pick in Editor', self.pick_placement),
                                    ('show', 'Show in Editor', self.show_placement),
                                    ('propose', 'Use Proposed Match', self.use_proposal)):
            buttons[key] = self.button(
                page, label, lambda event, handler=handler: handler(kind))
            actions.Add(buttons[key], 0, wx.RIGHT, 8)
        buttons['propose'].Disable()
        page.GetSizer().Add(actions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.placement_buttons[kind] = buttons
        # Always present, so the fields below never move.
        self.proposal_text[kind] = wx.StaticText(page, label='')
        self.proposal_text[kind].SetMinSize((-1, self.GetCharHeight() + 2))
        page.GetSizer().Add(self.proposal_text[kind], 0,
                            wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.placement_notebook.AddPage(page, title)
        return page, view

    @staticmethod
    def _side_rotation_fields(page):
        fields = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        fields.Add(wx.StaticText(page, label='Side:'), 0, wx.ALIGN_CENTER_VERTICAL)
        side = wx.Choice(page, choices=['Front', 'Back'])
        side.SetSelection(0)
        side.SetMinSize((170, -1))
        fields.Add(side, 0)
        fields.Add(wx.StaticText(page, label='Rotation:'), 0, wx.ALIGN_CENTER_VERTICAL)
        rotation_row = wx.BoxSizer(wx.HORIZONTAL)
        rotation = wx.TextCtrl(page, value='0', style=wx.TE_PROCESS_ENTER)
        rotation.SetMinSize((90, -1))
        rotation_row.Add(rotation, 0, wx.RIGHT, 6)
        rotation_row.Add(wx.StaticText(page, label='°'), 0, wx.ALIGN_CENTER_VERTICAL)
        fields.Add(rotation_row, 0)
        page.GetSizer().Add(fields, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        return side, rotation

    @staticmethod
    def _coordinate_text(values, units=None):
        if not values or len(values) != 2:
            return '—'
        units = units or ('mil', 'mil')
        return ', '.join(
            f'{dimension_text(float(value), unit)} {unit}'
            for value, unit in zip(values, units)
        )

    def _fit_list_columns(self, view, columns):
        """Widen value columns and their list so full-precision values are never clipped."""
        for column in columns:
            view.SetColumnWidth(column, wx.LIST_AUTOSIZE_USEHEADER)
            heading = view.GetColumnWidth(column)
            view.SetColumnWidth(column, wx.LIST_AUTOSIZE)
            view.SetColumnWidth(column, max(heading, view.GetColumnWidth(column)) + 8)
        width = sum(view.GetColumnWidth(column) for column in range(view.GetColumnCount())) + 12
        minimum, maximum = view.GetMinSize(), view.GetMaxSize()
        if width > minimum.width:
            view.SetMinSize((width, minimum.height))
            view.SetMaxSize((width, maximum.height))
            page = view.GetParent()
            while page is not None and page is not self:
                page.Layout()
                page = page.GetParent()

    @staticmethod
    def _skip_event(event):
        if event is not None:
            event.Skip()

    def _store_mechanical_source(self):
        """Record the drawing layer and tolerance; only a real change counts as an edit."""
        layer = self.user_layer.GetValue().strip()
        if not layer:
            raise ValueError('Choose the layer that holds the imported drawing')
        from .core.geometry import feature_tolerance
        tolerance = feature_tolerance(self.feature_tolerance.value_mm())
        unit = self.feature_tolerance.selected_unit()
        mechanical = self.controller.config['mechanical']
        changed = False
        if mechanical.get('layer') != layer:
            mechanical.update(layer=layer, inspected_revision='', inspected_geometry='')
            self._inspected_revision = ''
            self._inspected_layer = ''
            self._inspected_circles = ()
            changed = True
        if (mechanical.get('feature_tolerance_mm') != tolerance or
                mechanical.get('feature_tolerance_unit') != unit):
            mechanical['feature_tolerance_mm'] = tolerance
            mechanical['feature_tolerance_unit'] = unit
            changed = True
        if changed:
            self.controller.changed()

    def _drawing_note(self, text, error=False):
        palette = ui_palette()
        self.drawing_status.SetLabel(text)
        self.drawing_status.SetForegroundColour(
            palette.status['missing'] if error else palette.muted)
        self.drawing_status.GetParent().Layout()

    def _mechanical_source_changed(self, event=None):
        if self._loading_placement:
            self._skip_event(event)
            return
        try:
            self._store_mechanical_source()
        except ValueError as error:
            self._drawing_note(str(error), True)
        else:
            if not self._read_drawing():
                self.refresh_placement()
            self.refresh()
        self._skip_event(event)

    def _read_drawing(self, then=None):
        """Read the drawing layer of the saved board. It runs by itself; there is no button."""
        backend = self.controller.backend
        if (backend is None or not hasattr(backend, 'call') or
                not self.controller.board_path or self.controller.busy or self._closing):
            return False
        try:
            self._store_mechanical_source()
        except ValueError as error:
            self._drawing_note(str(error), True)
            return False
        layer = self.user_layer.GetValue().strip()

        def complete(result):
            if self._closing:
                return
            self._drawing_read(layer, result)
            self.refresh()
            if then is not None:
                self.guard(lambda _event: then())(None)

        def failed(error):
            if self._closing:
                return
            self._inspected_revision = ''
            self._inspected_layer = ''
            self._inspected_circles = ()
            self._drawing_note(str(error), True)
            self.refresh_placement()
            self.refresh()

        self._drawing_note('Reading…')
        self.controller.run_async(
            lambda: backend.call(
                'inspect', board_path=str(self.controller.board_path), source_layer=layer,
            ),
            complete, failed, pinned=False,
        )
        self.refresh()
        return True

    def _drawing_read(self, layer, result):
        """Take a fresh drawing inventory and carry unchanged placements over to it."""
        from . import placement
        mechanical = self.controller.config['mechanical']
        circles = tuple(result['circles'])
        self._inspected_revision = result['revision']
        self._inspected_layer = layer
        self._inspected_circles = circles
        known = (mechanical.get('inspected_revision') == result['revision'] and
                 mechanical.get('accepted'))
        mechanical['inspected_revision'] = result['revision']
        mechanical['inspected_geometry'] = result['geometry_revision']
        mechanical['accepted'] = True
        carried = placement.reassociate(self.controller, circles, result['revision'])
        if carried or not known:
            self.controller.changed()
        noun = 'circle' if len(circles) == 1 else 'circles'
        self._drawing_note(f'{len(circles)} {noun}')
        self.refresh_placement()

    def _placement_shown(self):
        """Entering the step reads the drawing when it is not in memory yet."""
        self.refresh_placement()
        if (not self._inspected_revision or
                self._inspected_layer != self.user_layer.GetValue().strip()):
            self._read_drawing()

    def _selected_mechanical_circle(self, description):
        if self.pcbnew is None:
            raise ValueError('Open this workflow from the KiCad PCB Editor')
        layer = self.user_layer.GetValue().strip()
        selected = list(self.pcbnew.GetCurrentSelection())
        if (len(selected) != 1 or not isinstance(selected[0], self.pcbnew.PCB_SHAPE)
                or selected[0].GetShape() != self.pcbnew.SHAPE_T_CIRCLE):
            raise ValueError(f'Select one {description} circle on {layer} in the PCB Editor')
        circle = selected[0]
        found = self.pcbnew.GetBoard().GetLayerName(circle.GetLayer())
        if found != layer:
            raise ValueError(f'That circle is on {found}; the drawing layer is {layer}')
        center = circle.GetCenter()
        return circle, [center.x / 1e6, center.y / 1e6]

    def _circle_record(self, circle, center):
        """Resolve a live circle to the same circle of the saved, read drawing."""
        revision = self.controller.config['mechanical'].get('inspected_revision', '')
        layer = self.user_layer.GetValue().strip()
        not_saved = CircleNotSaved(
            'This circle is not in the saved board. Save the board, then pick again.')
        if (not revision or getattr(self, '_inspected_revision', '') != revision or
                getattr(self, '_inspected_layer', '') != layer):
            raise not_saved
        from .core.geometry import feature_tolerance
        tolerance = feature_tolerance(
            self.controller.config['mechanical']['feature_tolerance_mm'],
        )
        radius = circle.GetRadius() / 1e6
        matches = [
            candidate for candidate in self._inspected_circles
            if math.dist(candidate['center'], center) <= tolerance and
            abs(float(candidate['radius']) - radius) <= tolerance
        ]
        live_uuid = circle.m_Uuid.AsString()
        selected = next(
            (candidate for candidate in matches if candidate.get('uuid') == live_uuid),
            None,
        )
        if selected is None and len(matches) == 1:
            selected = matches[0]
        if selected is None and matches:
            raise ValueError(
                'Several saved circles share this position, so the pick does not '
                'resolve uniquely; delete the duplicates in the drawing'
            )
        if selected is None:
            raise not_saved
        return {
            'uuid': selected['uuid'], 'center': list(selected['center']),
            'revision': revision,
        }

    def _rotation_value(self, control, stored):
        """A typed rotation, or the stored one put back when the text is not a number."""
        try:
            rotation = float(control.GetValue())
            if not math.isfinite(rotation):
                raise ValueError
        except ValueError:
            control.ChangeValue(f'{float(stored):g}')
            self.status.SetLabel('Rotation must be a number of degrees.')
            return float(stored)
        return rotation

    def _interposer_placement_changed(self, event=None, source_circle=None):
        if self._loading_placement:
            self._skip_event(event)
            return
        reference = self._interposer_reference()
        selection = self.controller.config['interposers'].get(reference) if reference else None
        if not reference or selection is None:
            self._skip_event(event)
            return
        stored = (selection.get('pose') or [0, 0, 0])[2]
        rotation = self._rotation_value(self.interposer_rotation, stored)
        side = 'B' if self.interposer_side.GetSelection() == 1 else 'F'
        circle = source_circle or selection.get('pose_circle', {})
        center = list(circle.get('center', ()))
        placed = len(center) == 2 and all(math.isfinite(value) for value in center)
        pose = [center[0], center[1], rotation] if placed else [0, 0, rotation]
        if (source_circle is None and selection.get('pose') == pose and
                selection.get('side') == side):
            self._skip_event(event)
            return
        selection['pose'] = pose
        selection['side'] = side
        if placed:
            selection['pose_circle'] = circle
            selection['pose_accepted'] = True
        self.controller.changed()
        self.refresh_interposer_placement()
        self.refresh()
        self._skip_event(event)

    @staticmethod
    def _microvia_size_label(diameter, drill):
        diameter_mil, drill_mil = diameter / .0254, drill / .0254
        def number(value):
            return str(int(round(value))) if abs(value - round(value)) < 1e-6 else f'{value:.3f}'.rstrip('0').rstrip('.')
        return (f'{number(diameter_mil)} mil pad / {number(drill_mil)} mil drill '
                f'({diameter:g} / {drill:g} mm)')

    def build_microvias(self, panel):
        self.task_box(
            panel, 'Interposer microvias', 'Optionally generate uniform stacked laser microvias',
            'Generate coincident microvias at every interposer pad. Ground pins stack through the board; signal pins stack to their routing layer, skip one span, then continue as GND where layers remain.',
        )
        self.microvia_enable = wx.CheckBox(
            panel, label='Generate interposer microvia stacks',
        )
        self.microvia_enable.Bind(wx.EVT_CHECKBOX, self.guard(self.toggle_microvias))
        panel.GetSizer().Add(self.microvia_enable, 0, wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label='Predefined microvia size'), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.microvia_size = wx.Choice(panel)
        self.microvia_size.Bind(wx.EVT_CHOICE, self.guard(self.choose_microvia_size))
        row.Add(self.microvia_size, 0)
        panel.GetSizer().Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.microvia_summary = wx.StaticText(panel, label='0 ground pins · 0 signal pins · 0 generated microvias')
        panel.GetSizer().Add(self.microvia_summary, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.microvia_status = self.status_label(panel, 'Assign an interposer footprint first')
        panel.GetSizer().Add(self.microvia_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.microvia_status.Hide()
        self._microvia_choice_pairs = []

    def toggle_microvias(self, event):
        self.controller.set_microvias_enabled(self.microvia_enable.GetValue())
        self.refresh_microvias()

    def choose_microvia_size(self, event):
        index = self.microvia_size.GetSelection()
        if not 0 <= index < len(self._microvia_choice_pairs):
            return
        self.controller.set_microvia_size(*self._microvia_choice_pairs[index])
        self.refresh_microvias()

    def refresh_microvias(self):
        if not hasattr(self, 'microvia_enable'):
            return
        available = self.controller.microvia_available()
        enabled = bool(self.controller.config.get('microvias', {}).get('enabled'))
        self.microvia_enable.SetValue(enabled)
        self.microvia_enable.Enable(available and not self.controller.busy)
        presets = self.controller.microvia_presets() if available else ()
        eligible = [item for item in presets if item.eligible]
        self._microvia_choice_pairs = [(item.diameter, item.drill) for item in eligible]
        self.microvia_size.Set([
            self._microvia_size_label(item.diameter, item.drill) for item in eligible
        ])
        selected = self.controller.config.get('microvias', {}).get('size', {})
        pair = (selected.get('diameter'), selected.get('drill'))
        self.microvia_size.SetSelection(
            self._microvia_choice_pairs.index(pair)
            if pair in self._microvia_choice_pairs else wx.NOT_FOUND
        )
        self.microvia_size.Enable(available and enabled and bool(eligible) and not self.controller.busy)
        ground, signal, count = self.controller.microvia_counts()
        self.microvia_summary.SetLabel(
            f'{ground} ground pin{("" if ground == 1 else "s")} · '
            f'{signal} signal pin{("" if signal == 1 else "s")} · '
            f'{count} generated microvia{("" if count == 1 else "s")}'
        )
        if not available:
            message, state = 'Assign an interposer footprint first', 'warning'
        elif not enabled:
            message, state = 'Off — no interposer microvias requested', 'neutral'
        elif not eligible:
            rejected = next((item.reason for item in presets if item.reason), '')
            message = 'No Board Setup predefined size satisfies the board and interposer constraints'
            if rejected:
                message += ': ' + rejected
            state = 'error'
        elif pair not in self._microvia_choice_pairs:
            message, state = 'Needs input — select a predefined microvia size', 'warning'
        else:
            message, state = 'Ready — selected Board Setup size maintains minimum copper spacing', 'success'
        self.microvia_status.SetLabel(message)
        self._style_status(self.microvia_status, state)
        self.microvia_status.GetParent().Layout()

    # --- Placement tables ---------------------------------------------------

    def _placement_name(self, kind):
        if kind == 'interposer':
            return self._interposer_reference()
        return self.current_anchor_gang if kind == 'gang' else self.current_dc_anchor

    def _length_text(self, value):
        unit = self.readout_unit
        return f'{dimension_text(float(value), unit)} {unit}'

    def _placement_row(self, kind, name):
        from . import placement
        selection = placement.part_selection(self.controller, kind, name)
        position = placement.anchor(self.controller, kind, name)
        if kind == 'interposer':
            rotation = (selection.get('pose') or [0, 0, 0])[2]
        elif kind == 'gang':
            rotation = selection.get('connector_rotation', 0)
        else:
            rotation = selection.get('rotation', 0)
        if position is None:
            status = 'Not placed'
        elif placement.stale(self.controller,
                             placement.circle_record(self.controller, kind, name)):
            status = 'Pick again'
        else:
            status = 'Placed'
        return (
            name,
            self._length_text(position[0]) if position else '—',
            self._length_text(position[1]) if position else '—',
            'Back' if selection.get('side') == 'B' else 'Front',
            f'{float(rotation):g}°', status,
        )

    def _fill_placement_list(self, view, kind, names, current):
        self._loading_placement = True
        try:
            view.DeleteAllItems()
            for name in names:
                values = self._placement_row(kind, name)
                index = view.InsertItem(view.GetItemCount(), values[0])
                for column, value in enumerate(values[1:], 1):
                    view.SetItem(index, column, value)
                if index % 2:
                    view.SetItemBackgroundColour(index, ui_palette().alternate)
                if name == current:
                    view.Select(index)
            self._fit_list_columns(view, (1, 2))
            for column in (1, 2):
                # Never narrower than the empty table, so placing a part moves nothing.
                view.SetColumnWidth(column, max(170, view.GetColumnWidth(column)))
        finally:
            self._loading_placement = False

    def refresh_placement(self):
        if not hasattr(self, 'interposer_list'):
            return
        self.refresh_interposer_placement()
        self.refresh_anchor_gangs()
        self.refresh_dc_anchors()
        self.refresh_mounting_holes()

    def refresh_interposer_placement(self):
        if not hasattr(self, 'interposer_list'):
            return
        reference = self._interposer_reference()
        self._fill_placement_list(
            self.interposer_list, 'interposer', [reference] if reference else [], reference)
        selection = self.controller.config['interposers'].get(reference, {}) if reference else {}
        self._loading_placement = True
        try:
            self.interposer_rotation.ChangeValue(
                f'{float((selection.get("pose") or [0, 0, 0])[2]):g}')
            self.interposer_side.SetSelection(1 if selection.get('side') == 'B' else 0)
        finally:
            self._loading_placement = False
        self._update_proposal('interposer')

    def refresh_anchor_gangs(self):
        if not hasattr(self, 'anchor_gang_list'):
            return
        names = list(self.controller.gang_names())
        if self.current_anchor_gang not in names:
            self.current_anchor_gang = names[0] if names else None
        self._fill_placement_list(
            self.anchor_gang_list, 'gang', names, self.current_anchor_gang)
        self._update_proposal('gang')

    def select_anchor_gang(self, event=None):
        if self._loading_placement:
            self._skip_event(event)
            return
        if event is not None and hasattr(event, 'GetIndex') and event.GetIndex() >= 0:
            self.current_anchor_gang = self.anchor_gang_list.GetItemText(event.GetIndex())
        self._update_proposal('gang')
        self._skip_event(event)

    def _rf_anchor_changed(self, event=None, source_circle=None):
        if self._loading_placement or not self.current_anchor_gang:
            self._skip_event(event)
            return
        circle = source_circle or {}
        anchor = list(circle.get('center', ()))
        if len(anchor) != 2 or not all(math.isfinite(value) for value in anchor):
            raise ValueError('Pick the location 1 circle')
        self._store_mechanical_source()
        self.controller.config['mechanical']['accepted'] = True
        self.controller.set_gang(self.current_anchor_gang, {
            'anchor': anchor, 'anchor_circle': circle, 'anchor_accepted': True,
        })
        self.refresh_anchor_gangs()
        self._skip_event(event)

    def refresh_dc_anchors(self):
        if not hasattr(self, 'dc_anchor_list'):
            return
        names = list(self.controller.dc_connector_names())
        if self.current_dc_anchor not in names:
            self.current_dc_anchor = names[0] if names else None
        self._fill_placement_list(self.dc_anchor_list, 'dc', names, self.current_dc_anchor)
        self.select_dc_anchor()

    def select_dc_anchor(self, event=None):
        if self._loading_placement:
            self._skip_event(event)
            return
        if event is not None and hasattr(event, 'GetIndex') and event.GetIndex() >= 0:
            self.current_dc_anchor = self.dc_anchor_list.GetItemText(event.GetIndex())
        reference = self.current_dc_anchor
        selection = self.controller.dc_connector(reference) if reference else {}
        self._loading_placement = True
        try:
            self.dc_anchor_rotation.ChangeValue(f'{float(selection.get("rotation", 0)):g}')
            self.dc_anchor_side.SetSelection(1 if selection.get('side') == 'B' else 0)
        finally:
            self._loading_placement = False
        self._update_proposal('dc')
        self._skip_event(event)

    def _dc_placement_changed(self, event=None, source_circle=None):
        reference = self.current_dc_anchor
        if self._loading_placement or not reference:
            self._skip_event(event)
            return
        selection = self.controller.dc_connector(reference)
        values = {
            'rotation': self._rotation_value(
                self.dc_anchor_rotation, selection.get('rotation', 0)),
            'side': 'B' if self.dc_anchor_side.GetSelection() == 1 else 'F',
        }
        if source_circle is not None:
            self._store_mechanical_source()
            self.controller.config['mechanical']['accepted'] = True
            values.update({
                'anchor_pad': '1', 'anchor': list(source_circle['center']),
                'anchor_circle': source_circle, 'anchor_accepted': True,
            })
        elif all(selection.get(key) == value for key, value in values.items()):
            self._skip_event(event)
            return
        self.controller.set_dc_connector(reference, values)
        self.refresh_dc_anchors()
        self.refresh()
        self._skip_event(event)

    def _placement_tab_changed(self, event):
        self._disarm_pick()
        self._highlight_proposal()
        event.Skip()

    # --- Proposed match -----------------------------------------------------

    def _update_proposal(self, kind):
        """Find where the selected, unplaced part fits the drawing circles."""
        from . import placement
        name = self._placement_name(kind)
        found, covered, text = (), (), ''
        if (name and self._inspected_circles and
                placement.anchor(self.controller, kind, name) is None):
            try:
                found, index, relative = placement.proposals(
                    self.controller, kind, name, self._inspected_circles)
            except (OSError, TypeError, ValueError) as error:
                text = str(error)
            else:
                if len(found) == 1:
                    x, y = found[0]['center']
                    covered = index.covered((x + dx, y + dy) for dx, dy in relative)
                    text = (f'Proposed match for {name}: {self._length_text(x)}, '
                            f'{self._length_text(y)}. It is highlighted in the PCB Editor.')
                elif found:
                    text = (f'{name} fits {len(found)} places in the drawing. '
                            'Pick one in the editor.')
                else:
                    text = f'No place in the drawing fits {name}. Pick its circle in the editor.'
        self._proposals[kind] = (name, tuple(found), frozenset(covered))
        self.proposal_text[kind].SetLabel(text)
        self.placement_buttons[kind]['propose'].Enable(len(found) == 1)
        self._highlight_proposal()

    def _highlight_proposal(self):
        """Brighten the proposed circles of the visible tab; clear them otherwise."""
        wanted = frozenset()
        tab = self.placement_notebook.GetSelection()
        if (self.book.GetSelection() == PAGES.index('Mechanical placement') and
                0 <= tab < len(self.PLACEMENT_KINDS)):
            _name, found, covered = self._proposals.get(
                self.PLACEMENT_KINDS[tab], ('', (), frozenset()))
            if len(found) == 1:
                wanted = covered
        if wanted == self._proposal_uuids:
            return
        self._proposal_uuids = wanted
        try:
            self._brighten(wanted)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def use_proposal(self, kind):
        name, found, _covered = self._proposals.get(kind, ('', (), frozenset()))
        if not name or len(found) != 1:
            raise ValueError('There is no single proposed match for this row')
        self._place(kind, name, {
            'uuid': found[0]['uuid'], 'center': list(found[0]['center']),
            'revision': self._inspected_revision,
        })

    # --- Pick and show ------------------------------------------------------

    def _place(self, kind, name, record):
        self._disarm_pick()
        if kind == 'interposer':
            self._store_mechanical_source()
            self.controller.config['mechanical']['accepted'] = True
            self._interposer_placement_changed(source_circle=record)
        elif kind == 'gang':
            self.current_anchor_gang = name
            self._rf_anchor_changed(source_circle=record)
        else:
            self.current_dc_anchor = name
            self._dc_placement_changed(source_circle=record)
        self.status.SetLabel(f'{name} placed.')

    def _picked_record(self, description, retry):
        """The saved circle for the editor selection; re-reads the board once if needed."""
        circle, center = self._selected_mechanical_circle(description)
        try:
            return self._circle_record(circle, center)
        except CircleNotSaved:
            # The board may have been saved since the drawing was read.
            if retry is None or not self._read_drawing(retry):
                raise
            return None

    def pick_placement(self, kind):
        name = self._placement_name(kind)
        if not name:
            raise ValueError('There is nothing to place on this tab')
        feature = self.PLACEMENT_FEATURE[kind]

        def accept(retry=True):
            record = self._picked_record(
                f'{name} {feature}', (lambda: accept(False)) if retry else None)
            if record is not None:
                self._place(kind, name, record)

        self._arm_pick(f'the {feature} circle of {name}', accept)

    def show_placement(self, kind):
        from . import placement
        name = self._placement_name(kind)
        if not name:
            raise ValueError('There is nothing to show on this tab')
        record = placement.circle_record(self.controller, kind, name)
        if placement.anchor(self.controller, kind, name) is None or not record.get('uuid'):
            _name, found, covered = self._proposals.get(kind, ('', (), frozenset()))
            if len(found) != 1:
                raise ValueError(f'{name} is not placed yet')
            self._show_in_editor(covered, first=found[0]['uuid'])
            return
        uuids = {record['uuid']}
        if self._inspected_circles:
            uuids |= placement.occupied(
                self.controller,
                placement.circle_index(self.controller, self._inspected_circles),
                kind, name,
            )
        self._show_in_editor(uuids, first=record['uuid'])

    # --- Mounting holes -----------------------------------------------------

    def _hole(self, reference):
        return next((hole for hole in self.controller.config['mounting_holes']
                     if hole.get('reference') == reference), None)

    def select_hole(self, event=None):
        if not self._loading_placement and event is not None and event.GetIndex() >= 0:
            self.current_hole = self.hole_list.GetItemText(event.GetIndex())
        self._skip_event(event)

    def add_hole(self, event):
        from .core.libraries import read_footprint
        path = self.hole_path.GetValue()
        selection = self.hole_path.GetSelectionSpec()
        reference = self.hole_ref.GetValue().strip()
        if not reference:
            raise ValueError('Enter a reference for the mounting hole')
        if self._hole(reference) is not None:
            raise ValueError(f'{reference} already exists')
        if not path or not selection.get('library_id'):
            raise ValueError('Choose a mounting-hole footprint')
        part = read_footprint(path, selection['library_id'], selection.get('sha256'),
                              allow_mechanical=True)
        self.controller.config['mounting_holes'].append({
            'reference': reference, 'path': str(Path(path).resolve()),
            'sha256': part.sha256, 'library_id': part.library_id,
        })
        self.controller.changed()
        self.current_hole = reference
        stem = reference.rstrip('0123456789')
        number = int(reference[len(stem):] or 0)
        while self._hole(f'{stem}{number}') is not None:
            number += 1
        self.hole_ref.ChangeValue(f'{stem}{number}')
        self.refresh_mounting_holes()

    def refresh_mounting_holes(self):
        if not hasattr(self, 'hole_list'):
            return
        from . import placement
        holes = self.controller.config.get('mounting_holes', [])
        references = [str(hole.get('reference', '')) for hole in holes]
        if self.current_hole not in references:
            self.current_hole = references[0] if references else None
        self._loading_placement = True
        try:
            self.hole_list.DeleteAllItems()
            for hole in holes:
                pose = hole.get('pose') or ()
                record = hole.get('pose_circle') or {}
                index = self.hole_list.InsertItem(
                    self.hole_list.GetItemCount(), str(hole.get('reference', '')))
                self.hole_list.SetItem(index, 1, str(hole.get('library_id', '')))
                self.hole_list.SetItem(
                    index, 2, self._length_text(pose[0]) if len(pose) >= 2 else '—')
                self.hole_list.SetItem(
                    index, 3, self._length_text(pose[1]) if len(pose) >= 2 else '—')
                self.hole_list.SetItem(
                    index, 4,
                    'Not placed' if len(pose) < 2
                    else 'Pick again' if placement.stale(self.controller, record)
                    else 'Placed')
                if index % 2:
                    self.hole_list.SetItemBackgroundColour(index, ui_palette().alternate)
                if hole.get('reference') == self.current_hole:
                    self.hole_list.Select(index)
            if holes:
                self._fit_list_columns(self.hole_list, (1, 2, 3))
        finally:
            self._loading_placement = False

    def _selected_hole(self):
        hole = self._hole(self.current_hole) if self.current_hole else None
        if hole is None:
            raise ValueError('Select a mounting hole in the table first')
        return hole

    def remove_hole(self, event):
        reference = self._selected_hole()['reference']
        self.controller.config['mounting_holes'] = [
            hole for hole in self.controller.config['mounting_holes']
            if hole.get('reference') != reference
        ]
        self.controller.changed()
        self.refresh_mounting_holes()

    def pick_hole(self, event):
        reference = self._selected_hole()['reference']

        def accept(retry=True):
            record = self._picked_record(
                f'{reference} mounting-hole', (lambda: accept(False)) if retry else None)
            hole = self._hole(reference)
            if record is None or hole is None:
                return
            self._disarm_pick()
            self._store_mechanical_source()
            self.controller.config['mechanical']['accepted'] = True
            hole['pose'] = [*record['center'], 0]
            hole['pose_circle'] = record
            self.controller.changed()
            self.refresh_mounting_holes()
            self.status.SetLabel(f'{reference} placed.')

        self._arm_pick(f'the circle of {reference}', accept)

    def show_hole(self, event):
        hole = self._selected_hole()
        uuid = (hole.get('pose_circle') or {}).get('uuid')
        if not uuid:
            raise ValueError(f'{hole["reference"]} is not placed yet')
        self._show_in_editor({uuid})

    OUTLINE_ROLES = (('outer', 'Outer boundary'), ('cutout', 'Centre cutout'))

    def build_outline(self, panel):
        body = wx.BoxSizer(wx.HORIZONTAL)
        settings = wx.BoxSizer(wx.VERTICAL)
        self.outline_enable = wx.CheckBox(panel, label='Create the board outline')
        settings.Add(self.outline_enable, 0, wx.BOTTOM, 10)
        self.outline_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for column, (heading, width) in enumerate(
                (('Contour', 150), ('Lines and arcs', 120), ('Status', 110))):
            self.outline_list.InsertColumn(column, heading, width=width)
        for _role, label in self.OUTLINE_ROLES:
            self.outline_list.InsertItem(self.outline_list.GetItemCount(), label)
        self.outline_list.SetMinSize((384, 96))
        self.outline_list.SetMaxSize((384, 96))
        self.outline_list.Select(0)
        settings.Add(self.outline_list, 0, wx.BOTTOM, 10)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        actions.Add(self.button(panel, 'Pick in Editor', self.pick_outline), 0, wx.RIGHT, 8)
        actions.Add(self.button(panel, 'Show in Editor', self.show_outline), 0, wx.RIGHT, 8)
        actions.Add(self.button(panel, 'Clear', self.clear_outline), 0)
        settings.Add(actions, 0)
        self.outline_status = wx.StaticText(panel, label='')
        self.outline_status.Hide()
        body.Add(settings, 0, wx.ALL, 8)
        preview = wx.BoxSizer(wx.VERTICAL)
        preview.Add(wx.StaticText(panel, label='Preview'), 0, wx.BOTTOM, 6)
        self.outline_view = OutlinePreview(panel)
        preview.Add(self.outline_view, 1, wx.EXPAND)
        body.Add(preview, 1, wx.EXPAND | wx.ALL, 8)
        panel.GetSizer().Add(body, 1, wx.EXPAND)
        self.outline_enable.Bind(wx.EVT_CHECKBOX, self.guard(self.toggle_outline))

    def _outline_role(self):
        index = self.outline_list.GetFirstSelected()
        return self.OUTLINE_ROLES[max(0, index)][0]

    def refresh_outline(self):
        """Show each contour's size and whether it closes."""
        from .core.outline import validate_outline
        outline = self.controller.config.get('outline', {})
        contours = outline.get('source_preview', {})
        for row, (role, _label) in enumerate(self.OUTLINE_ROLES):
            count = len(outline.get(role + '_uuids', ()))
            status = 'None'
            if count:
                try:
                    if role == 'outer':
                        validate_outline(contours.get('outer', []), [])
                    else:
                        validate_outline(contours.get('outer', []), [contours.get('cutout', [])])
                    status = 'Closed'
                except (TypeError, ValueError, KeyError):
                    status = 'Problem'
            self.outline_list.SetItem(row, 1, str(count))
            self.outline_list.SetItem(row, 2, status)
        self.outline_view.show(contours)

    def pick_outline(self, event):
        role = self._outline_role()
        self._arm_pick('the ' + dict(self.OUTLINE_ROLES)[role].lower(),
                       lambda: self.capture_outline(role))

    def show_outline(self, event):
        role = self._outline_role()
        self._show_in_editor(self.controller.config.get('outline', {}).get(role + '_uuids', ()))

    def clear_outline(self, event):
        role = self._outline_role()
        outline = self.controller.config['outline']
        outline.pop(role + '_uuids', None)
        outline.get('source_preview', {}).pop(role, None)
        self.controller.changed()
        self.refresh_outline()

    # ---- PCB Editor interaction shared by Board outline and Mechanical placement ----

    def _selection_ids(self):
        return tuple(sorted(item.m_Uuid.AsString()
                            for item in self.pcbnew.GetCurrentSelection()))

    def _arm_pick(self, description, accept):
        """Take the next selection made in the PCB Editor; there is no second click here."""
        if not self.pcbnew or not hasattr(self.pcbnew, 'GetCurrentSelection'):
            raise ValueError('Open this workflow from the KiCad PCB Editor')
        self._disarm_pick()
        self._pick = {'accept': accept, 'before': self._selection_ids()}
        self._pick_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._poll_pick, self._pick_timer)
        self._pick_timer.Start(200)
        self.status.SetLabel(f'Waiting for {description} in the PCB Editor.')

    def _poll_pick(self, event):
        if self._closing or not self._pick:
            return
        current = self._selection_ids()
        if not current:
            # Deselecting first lets the same item be picked again.
            self._pick['before'] = ()
        elif current != self._pick['before']:
            accept = self._pick['accept']
            self._disarm_pick()
            self.guard(lambda _event: accept())(None)

    def _disarm_pick(self):
        if self._pick_timer is not None:
            self._pick_timer.Stop()
            self._pick_timer = None
        self._pick = None

    def _brighten(self, uuids):
        """Brighten exactly these saved drawing items in the PCB Editor."""
        if not self.pcbnew or not hasattr(self.pcbnew, 'GetBoard'):
            raise ValueError('Open this workflow from the KiCad PCB Editor')
        for item in self._shown_in_editor:
            try:
                item.ClearBrightened()
            except (RuntimeError, AttributeError):
                pass
        wanted = set(uuids)
        drawings = list(self.pcbnew.GetBoard().GetDrawings()) if wanted else []
        found = [item for item in drawings if item.m_Uuid.AsString() in wanted]
        missing = wanted - {item.m_Uuid.AsString() for item in found}
        if missing:
            found.extend(self._live_circles(drawings, missing))
        for item in found:
            item.SetBrightened()
        self._shown_in_editor = found
        self.pcbnew.Refresh()
        return found

    def _live_circles(self, drawings, uuids):
        """Live circles standing where the saved circles ``uuids`` are.

        A graphic imported in this editor session can carry another UUID in
        memory than in the saved file, so position on the drawing layer decides.
        """
        centres = [circle['center'] for circle in self._inspected_circles
                   if circle['uuid'] in uuids]
        if not centres:
            return []
        tolerance = self.controller.config['mechanical']['feature_tolerance_mm']
        layer = self.user_layer.GetValue().strip()
        board = self.pcbnew.GetBoard()
        found = []
        for item in drawings:
            try:
                if (not isinstance(item, self.pcbnew.PCB_SHAPE) or
                        item.GetShape() != self.pcbnew.SHAPE_T_CIRCLE or
                        board.GetLayerName(item.GetLayer()) != layer):
                    continue
                centre = item.GetCenter()
            except (AttributeError, TypeError):
                continue
            position = (centre.x / 1e6, centre.y / 1e6)
            if any(math.dist(position, saved) <= tolerance for saved in centres):
                found.append(item)
        return found

    def _show_in_editor(self, uuids, first=None):
        """Highlight the given saved drawing items in the PCB Editor and move to one."""
        if not set(uuids):
            raise ValueError('Nothing is set for this row yet')
        found = self._brighten(uuids)
        self._proposal_uuids = None
        if not found:
            raise ValueError('These drawing items are no longer on the board')
        if hasattr(self.pcbnew, 'FocusOnItem'):
            self.pcbnew.FocusOnItem(next(
                (item for item in found if item.m_Uuid.AsString() == first), found[0]))

    def _show_outline_state(self):
        self.outline_status.SetLabel(
            'Enabled: capture sources and validate during comparison.'
            if self.outline_enable.GetValue()
            else 'Managed outline disabled. No Edge.Cuts changes requested.'
        )
        self.refresh_outline()

    def toggle_outline(self, event):
        self.controller.config['outline']['enabled'] = self.outline_enable.GetValue()
        self.controller.changed()
        self._show_outline_state()

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
        self.controller.changed()
        self.refresh_outline()
        self.status.SetLabel(json.dumps(self.controller.config['outline'], sort_keys=True))

    def _heading(self, parent, text):
        label = wx.StaticText(parent, label=text)
        font = label.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        label.SetFont(font)
        return label

    def _report_list(self, parent, columns, height, single=True):
        style = wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES
        view = wx.ListCtrl(parent, style=style | (wx.LC_SINGLE_SEL if single else 0))
        for column, (heading, width) in enumerate(columns):
            view.InsertColumn(column, heading, width=width)
        view.SetMinSize((-1, height))
        return view

    @staticmethod
    def _fill_report_list(view, rows):
        view.DeleteAllItems()
        alternate = ui_palette().alternate
        for values in rows:
            index = view.InsertItem(view.GetItemCount(), str(values[0]))
            for column, value in enumerate(values[1:], 1):
                view.SetItem(index, column, str(value))
            if index % 2:
                view.SetItemBackgroundColour(index, alternate)

    def build_review(self, panel):
        sizer = panel.GetSizer()
        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.review_button = self.button(panel, 'Review Changes', self.review_changes)
        actions.Add(self.review_button, 0, wx.RIGHT, 12)
        self.review_status = wx.StaticText(panel, label='Not reviewed yet.')
        self.review_status.SetForegroundColour(ui_palette().muted)
        actions.Add(self.review_status, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(actions, 0, wx.ALL, 8)

        sizer.Add(self._heading(panel, 'Problems'), 0, wx.LEFT | wx.RIGHT, 8)
        self.problem_list = self._report_list(
            panel, (('Level', 100), ('Step', 200), ('Problem', 690)), 180)
        sizer.Add(self.problem_list, 0, wx.EXPAND | wx.ALL, 8)
        self.goto_step_button = self.button(panel, 'Go to Step', self.goto_problem_step)
        self.goto_step_button.Disable()
        sizer.Add(self.goto_step_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sizer.Add(self._heading(panel, 'Changes Apply will make'), 0, wx.LEFT | wx.RIGHT, 8)
        changes = wx.BoxSizer(wx.HORIZONTAL)
        summary = wx.BoxSizer(wx.VERTICAL)
        hint = wx.StaticText(panel, label='Select a line to list its items')
        hint.SetForegroundColour(ui_palette().muted)
        summary.Add(hint, 0, wx.BOTTOM, 6)
        self.change_summary = self._report_list(panel, (('Change', 220), ('Count', 76)), 150)
        self.change_summary.SetMinSize((302, 150))
        summary.Add(self.change_summary, 1, wx.EXPAND)
        changes.Add(summary, 0, wx.EXPAND | wx.RIGHT, 16)
        detail = wx.BoxSizer(wx.VERTICAL)
        self.change_detail_title = self._heading(panel, '')
        detail.Add(self.change_detail_title, 0, wx.BOTTOM, 6)
        self.change_detail = self._report_list(
            panel, (('Item', 130), ('Before', 220), ('After', 330)), 150)
        detail.Add(self.change_detail, 1, wx.EXPAND)
        changes.Add(detail, 1, wx.EXPAND)
        sizer.Add(changes, 1, wx.EXPAND | wx.ALL, 8)

        self._problems, self._change_groups = [], []
        self._review_failure = ''
        self._reviewed = ''
        self._reviewing = False
        self.problem_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._problem_selected)
        self.problem_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._problem_selected)
        self.change_summary.Bind(wx.EVT_LIST_ITEM_SELECTED, self.show_change)

    def build_results(self, panel):
        from . import review
        self.results_notebook = wx.Notebook(panel)
        panel.GetSizer().Add(self.results_notebook, 1, wx.EXPAND | wx.ALL, 8)

        page = wx.Panel(self.results_notebook)
        page.SetSizer(wx.BoxSizer(wx.VERTICAL))
        ready = wx.BoxSizer(wx.HORIZONTAL)
        self.apply_lead = wx.StaticText(page, label='Nothing to apply yet:')
        ready.Add(self.apply_lead, 0, wx.RIGHT, 6)
        self.apply_headline = self._heading(page, 'review the changes first')
        ready.Add(self.apply_headline, 1)
        page.GetSizer().Add(ready, 0, wx.EXPAND | wx.ALL, 8)
        self.apply_button = self.button(page, 'Apply to Board…', self.apply)
        page.GetSizer().Add(self.apply_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        page.GetSizer().Add(self._heading(page, 'Progress'), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.apply_gauge = wx.Gauge(page, range=len(review.STAGES), size=(520, 16))
        page.GetSizer().Add(self.apply_gauge, 0, wx.ALL, 8)
        self.stage_view = ChecklistView(page, background='window')
        self.stage_view.SetMinSize((520, -1))
        page.GetSizer().Add(self.stage_view, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        page.GetSizer().Add(self._heading(page, 'Result'), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        results = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        results.AddGrowableCol(1, 1)
        self.result_fields = {}
        for key, label in (('board', 'Board:'), ('report', 'Report:'),
                           ('journal', 'Recovery journal:')):
            results.Add(wx.StaticText(page, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            self.result_fields[key] = wx.StaticText(
                page, label='—', style=wx.ST_ELLIPSIZE_MIDDLE)
            results.Add(self.result_fields[key], 1, wx.EXPAND)
        page.GetSizer().Add(results, 0, wx.EXPAND | wx.ALL, 8)
        self.results_notebook.AddPage(page, 'Apply')

        recovery = wx.Panel(self.results_notebook)
        recovery.SetSizer(wx.BoxSizer(wx.VERTICAL))
        recovery.GetSizer().Add(wx.StaticText(
            recovery,
            label=('Use this only if Apply was interrupted. It puts the project files back '
                   'as they were before that Apply.')), 0, wx.ALL, 8)
        self.journal = self.field(recovery, 'Recovery journal:', picker=True, invalidate=False)
        recovery.GetSizer().Add(
            self.button(recovery, 'Recover Project…', self.recover), 0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        note = wx.StaticText(recovery, label='Close this project in every KiCad editor first.')
        note.SetForegroundColour(ui_palette().muted)
        recovery.GetSizer().Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.results_notebook.AddPage(recovery, 'Recovery')
        self._set_stages(-1)

    # --- Review changes -------------------------------------------------------

    def _ask(self, message, yes, title=None):
        """A yes/cancel question with the action named on its button."""
        with wx.MessageDialog(
            self, message, title or self.GetTitle(),
            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION,
        ) as dialog:
            dialog.SetYesNoLabels(yes, 'Cancel')
            return dialog.ShowModal() == wx.ID_YES

    def review_changes(self, event):
        board = self.pcbnew.GetBoard() if self.pcbnew is not None else None
        if board is not None and hasattr(self.pcbnew, 'SaveBoard'):
            # Review reads the saved board, so saving is the drafter's decision.
            if not self._ask('Review reads the saved board.\n\nSave the PCB now?',
                             'Save and Review'):
                return
            if not self.pcbnew.SaveBoard(str(self.controller.board_path), board, True):
                raise ValueError('KiCad could not save the PCB')
        if not self.save(None):
            return
        self._review_failure = ''
        issues = self.controller.validate()
        self.render_review(None)
        blocking = sum(issue.severity == 'error' for issue in issues)
        self._reviewed = 'blocked' if blocking else 'done'
        if blocking:
            noun = 'problem blocks' if blocking == 1 else 'problems block'
            raise ValueError(f'{blocking} {noun} the review. Fix what is listed under '
                             'Problems, then review again.')

        def complete(preview):
            if self._closing:
                return
            self.render_review(preview)
            self.status.SetLabel('Review finished.')
            self.refresh()

        self._reviewing = True
        self.status.SetLabel('Review: reading the saved board and libraries.')
        self.controller.run_async(self.controller.review, complete, self.failed)
        self.refresh()

    def render_review(self, preview):
        """Fill Problems and the change summary from the last review."""
        from . import review
        self._reviewing = False
        self._problems = review.problems(
            self.controller.issues, preview, self._review_failure)
        self._fill_report_list(
            self.problem_list,
            [(row.level, row.step, row.message) for row in self._problems])
        self.goto_step_button.Disable()
        self._change_groups = review.summary(preview, self._length_text)
        self._fill_report_list(
            self.change_summary,
            [(label, len(changes)) for label, changes in self._change_groups])
        if self._change_groups:
            self.change_summary.Select(0)
        else:
            self.show_change(None)
        if preview is not None:
            # The complete record is for the message log, not for these tables.
            self.status.log(json.dumps(
                {name: preview.get(name) for name in ('status', 'changes', 'conflicts',
                                                      'warnings')},
                indent=2, default=str))

    def _problem_selected(self, event):
        index = self.problem_list.GetFirstSelected()
        self.goto_step_button.Enable(
            0 <= index < len(self._problems) and self._problems[index].step in PAGES and
            self._problems[index].step != 'Review changes')
        event.Skip()

    def goto_problem_step(self, event):
        index = self.problem_list.GetFirstSelected()
        if not 0 <= index < len(self._problems):
            raise ValueError('Select a problem first')
        self.select_page(PAGES.index(self._problems[index].step))

    def show_change(self, event):
        """The selected summary line lists its items on the right."""
        index = self.change_summary.GetFirstSelected()
        if not 0 <= index < len(self._change_groups):
            self.change_detail_title.SetLabel('')
            self.change_detail.DeleteAllItems()
            return
        label, changes = self._change_groups[index]
        noun = 'item' if len(changes) == 1 else 'items'
        self.change_detail_title.SetLabel(f'{label} — {len(changes)} {noun}')
        self._fill_report_list(
            self.change_detail,
            [(change.item, change.before, change.after) for change in changes])
        # Full text in every column; the list scrolls sideways when it must.
        for column, minimum in ((0, 130), (1, 120), (2, 330)):
            self.change_detail.SetColumnWidth(column, wx.LIST_AUTOSIZE)
            self.change_detail.SetColumnWidth(
                column, max(minimum, self.change_detail.GetColumnWidth(column) + 8))

    # --- Apply ----------------------------------------------------------------

    def _set_stages(self, active, failed=False):
        """Show the named stages: done before ``active``, pending after it."""
        from . import review
        lines = []
        for index, name in enumerate(review.STAGES):
            if index < active:
                lines.append(progress.Line(progress.DONE, name))
            elif index == active:
                lines.append(progress.Line(
                    progress.MISSING if failed else progress.CAUTION, name,
                    'stopped' if failed else 'running'))
            else:
                lines.append(progress.Line(progress.OPTIONAL, name))
        self._stage = active
        self.stage_view.set_lines(lines)
        self.apply_gauge.SetValue(max(0, min(active, len(review.STAGES))))
        self.stage_view.Update()
        self.apply_gauge.Update()

    def apply(self, event):
        from . import review
        if self.controller.preview is None:
            raise ValueError('Review the changes first')
        if self.pcbnew is None or self.pcbnew.GetBoard() is None:
            raise ValueError('The reviewed PCB is no longer open')
        if not self._ask(
                'Apply these changes to the open board?\n\n' +
                review.headline(self._change_groups).capitalize() + '.\n\n'
                'The board and the managed schematic sheets are saved. Keep the '
                'Schematic Editor closed while this runs.', 'Apply'):
            return
        for field in self.result_fields.values():
            field.SetLabel('—')
        self._applying = True
        self._set_stages(0)
        self.status.SetLabel('Apply: staging and verifying the changes.')
        self.controller.run_async(self.controller.apply, self.publish_prepared, self.failed)
        self.refresh()

    def publish_prepared(self, prepared):
        if self._closing:
            return
        # Staging and verification both happened in the background step.
        self._set_stages(2)
        try:
            result = self.controller.publish(
                prepared, self.pcbnew, progress=lambda: self._set_stages(3))
        except Exception as error:
            self.failed(error)
            return
        self.applied(result)

    def _show_result(self, result):
        result = result if isinstance(result, dict) else {}
        unchanged = result.get('status') == 'NOOP'
        values = {
            'board': ('No changes were needed' if unchanged
                      else f'{result.get("board_path", "—")} (updated and saved)'),
            'report': result.get('report_path') or '—',
            'journal': result.get('journal') or '—',
        }
        for key, value in values.items():
            self.result_fields[key].SetLabel(str(value))
            self.result_fields[key].SetToolTip(str(value))
        self.status.log(json.dumps(
            {name: value for name, value in result.items() if name != 'registry'},
            indent=2, default=str))

    def applied(self, result):
        from . import review
        self._applying = False
        if self._closing:
            return
        if self.pcbnew is not None and hasattr(self.pcbnew, 'Refresh'):
            self.pcbnew.Refresh()
        self._set_stages(len(review.STAGES))
        self._show_result(result)
        self.select_page(PAGES.index('Apply / results'))
        self.status.SetLabel('Apply finished. The board and the managed schematic sheets '
                             'are updated and saved.')
        if isinstance(result, dict) and result.get('journal'):
            self.journal.ChangeValue(str(result['journal']))
        self.refresh()

    def recovered(self, result):
        self._applying = False
        if self._closing:
            return
        self._show_result({})
        self.status.log(json.dumps(result, indent=2, default=str))
        self.select_page(PAGES.index('Apply / results'))
        self.status.SetLabel('Recovery finished. The project files are as they were '
                             'before that Apply.')
        wx.MessageBox(self.status.GetLabel(), self.GetTitle(), wx.OK | wx.ICON_INFORMATION, self)
        self.refresh()

    def failed(self, error):
        applying = self._applying
        self._applying = False
        self._reviewing = False
        if self._closing:
            return
        message = str(error)
        self.status.SetLabel(message)
        if applying:
            if self._stage >= 0:
                self._set_stages(self._stage, failed=True)
        else:
            self._review_failure = message
            self.render_review(None)
        self.refresh()
        wx.MessageBox(message,
                      'Review stopped' if not applying
                      else 'Apply stopped' if self._stage >= 0 else 'Recovery stopped',
                      wx.OK | wx.ICON_ERROR, self)

    def recover(self, event):
        from .recovery import recover
        if not self.journal.GetValue().strip():
            raise ValueError('Choose the recovery journal of the interrupted Apply')
        if self._ask('Close this project in the PCB Editor, the Schematic Editor and every '
                     'other KiCad editor first.\n\nRecover the project from this journal now?',
                     'Recover', 'Recover project'):
            self._applying = True
            self._set_stages(-1)
            self.controller.run_async(
                lambda: recover(self.journal.GetValue(), closed_project=True),
                self.recovered, self.failed)
            self.refresh()

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
        self._saved_snapshot = self._settings_snapshot()
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
            self._saved_snapshot = None
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
        self.current_anchor_gang = None
        self.current_dc_anchor = None
        self._gang_invalid_edits.clear()

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
            self.gang_pitch_x.set_mm(None, 'mil')
            self.gang_pitch_y.set_mm(None, 'mil')
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

        self._loading_placement = True
        try:
            for control, value in (
                (self.interposer_rotation, '0'), (self.user_layer, 'User.Drawings'),
                (self.dc_anchor_rotation, '0'), (self.hole_ref, 'H1'),
                (self.journal, ''),
            ):
                control.ChangeValue(value)
            from .core.geometry import DEFAULT_FEATURE_TOLERANCE_MM
            self.feature_tolerance.set_mm(DEFAULT_FEATURE_TOLERANCE_MM, 'mil')
            self.dc_anchor_side.SetSelection(0)
            self.interposer_side.SetSelection(0)
        finally:
            self._loading_placement = False
        self.microvia_enable.SetValue(False)
        self.microvia_size.Set([])
        self._microvia_choice_pairs = []
        self.hole_path.set_selection({})
        self.current_hole = None
        self._drawing_note('')
        self._inspected_revision = self._inspected_layer = ''
        self._inspected_circles = ()
        for kind in self.PLACEMENT_KINDS:
            self._proposals[kind] = ('', (), frozenset())
            self.proposal_text[kind].SetLabel('')
        for view in (self.interposer_list, self.anchor_gang_list, self.dc_anchor_list):
            view.DeleteAllItems()
        self.hole_list.DeleteAllItems()

        self.outline_enable.SetValue(False)
        self.outline_status.SetLabel('Managed outline disabled. No Edge.Cuts changes requested.')
        self.outline_view.show({})
        self.refresh_outline()
        self._review_failure = ''
        self._reviewed = ''
        self.render_review(None)
        self._set_stages(-1)
        for field in self.result_fields.values():
            field.SetLabel('—')

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
        self.outline_enable.SetValue(cfg['outline'].get('enabled', False))
        self._show_outline_state()
        self.outline_view.show(cfg['outline'].get('source_preview', {}))
        self._loading_placement = True
        self.user_layer.ChangeValue(cfg['mechanical']['layer'])
        self.feature_tolerance.set_mm(
            cfg['mechanical']['feature_tolerance_mm'],
            cfg['mechanical']['feature_tolerance_unit'],
        )
        self._loading_placement = False
        self.refresh_layers()
        self.refresh_gangs()
        self.refresh_mounting_holes()
        self.refresh_connector_assignments(True)
        self.refresh_microvias()

    def refresh(self):
        if self._refresh_hold:
            self._refresh_pending = True
            return
        self._refresh_hold += 1
        try:
            self._refresh()
        finally:
            self._refresh_hold -= 1
            self._refresh_pending = False

    def _refresh(self):
        self.refresh_connector_assignments()
        self.refresh_microvias()
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
            self.preview_count.SetLabel(
                f'Showing {min(SPREADSHEET_PREVIEW_ROWS, table.included_count)} '
                f'of {table.included_count} {noun}'
            )
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
            self.preview_count.SetLabel('Showing 0 of 0 nets')
            self._style_status(self.columns_status, 'warning')
            self._style_status(self.nets_status, 'warning')
        self.review_button.Enable(not self.controller.busy)
        preview = self.controller.preview
        self.apply_button.Enable(
            not self.controller.busy and preview is not None and not preview.get('conflicts'))
        from . import review
        if getattr(self, '_reviewing', False):
            review_status = 'Reading the saved board and libraries…'
        elif preview is not None:
            review_status = 'Review is up to date with your settings.'
        elif self._review_failure:
            review_status = 'The review stopped. See Problems.'
        elif self._reviewed == 'blocked':
            review_status = 'Blocked. Fix the problems below, then review again.'
        elif self._reviewed:
            review_status = 'Settings changed since the last review.'
        else:
            review_status = 'Not reviewed yet.'
        if self.review_status.GetLabel() != review_status:
            self.review_status.SetLabel(review_status)
        ready = preview is not None and not preview.get('conflicts')
        lead = 'Ready to apply:' if ready else 'Nothing to apply yet:'
        headline = (review.headline(self._change_groups) if ready
                    else 'resolve the conflicts first' if preview is not None
                    else 'review the changes first')
        if (self.apply_lead.GetLabel(), self.apply_headline.GetLabel()) != (lead, headline):
            self.apply_lead.SetLabel(lead)
            self.apply_headline.SetLabel(headline)
            self.apply_lead.GetParent().Layout()
        self.save_button.Enable(not self.controller.busy and not self._applying)
        self.delete_settings_button.Enable(
            not self.controller.busy and not self._applying
            and bool(self.controller.config_path and self.controller.config_path.exists())
        )
        self.reset_settings_button.Enable(not self.controller.busy and not self._applying)
        self.book.Enable(not self._applying)
        self.step_list.Enable(not self._applying)
        self._refresh_guidance()

    def navigate(self, step):
        self.select_page(self.book.GetSelection() + step)

    def select_page(self, index):
        selection = max(0, min(len(PAGES) - 1, index))
        self.book.SetSelection(selection)
        self.step_list.SetSelection(selection)
        self.step_list.Refresh()
        self._refresh_guidance()
        self._step_shown()

    def close(self, event):
        if self.controller.busy:
            self.status.SetLabel('Wait for the current operation to finish before closing.')
            if isinstance(event, wx.CloseEvent): event.Veto()
            return
        if self._settings_snapshot() != self._saved_snapshot:
            with wx.MessageDialog(
                self, 'Save workflow settings before closing?', self.GetTitle(),
                wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT | wx.ICON_QUESTION,
            ) as dialog:
                dialog.SetYesNoCancelLabels('Save', "Don't Save", 'Cancel')
                answer = dialog.ShowModal()
            if answer == wx.ID_CANCEL:
                if isinstance(event, wx.CloseEvent): event.Veto()
                return
            if answer == wx.ID_YES:
                self.controller.save()
        self._closing = True
        self._disarm_pick()
        self.spotlight.stop()
        for item in self._shown_in_editor:
            try:
                item.ClearBrightened()
            except (RuntimeError, AttributeError):
                pass
        self._cancel_spreadsheet_timer()
        self.controller.cancel()
        self.Destroy()
