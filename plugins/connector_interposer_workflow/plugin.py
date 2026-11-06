"""KiCad ActionPlugin entry point; importing the plugin never opens a dialog."""
from pathlib import Path
import platform
import sys
import pcbnew
import wx

from .diagnostics import LOG_PATH, reset as reset_diagnostics, trace, trace_exception


class ConnectorToInterposerWorkflowPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = 'Connector to Interposer Workflow'
        self.category = 'PCB automation'
        self.description = 'Select approved parts, validate connectivity and reconcile a staged project copy'
        self.show_toolbar_button = False
        self._dialog = None

    def Run(self):
        if self._dialog is not None:
            try:
                trace('existing_dialog_raise')
                self._dialog.Raise()
                return
            except RuntimeError:
                self._dialog = None
        board = pcbnew.GetBoard()
        filename = board.GetFileName() if board else ''
        from . import __version__
        reset_diagnostics(
            debug_build='windows-footprint-selection',
            plugin_version=__version__,
            platform=platform.platform(),
            python_version=sys.version,
            python_executable=str(sys.executable),
            python_prefix=str(sys.prefix),
            wx_version=wx.version(),
            kicad_version=pcbnew.Version(),
            board_saved=bool(filename and Path(filename).is_file()),
        )
        if not filename or not Path(filename).is_file():
            trace('open_blocked_unsaved_board')
            wx.MessageBox('Save the PCB in its project before opening Workflow.', self.name, wx.OK | wx.ICON_INFORMATION)
            return
        from .controller import WorkflowController, NativeProcessBackend
        from .ui import WorkflowDialog
        parent = next((window for window in wx.GetTopLevelWindows() if 'PCB Editor' in window.GetTitle()), None)
        try:
            controller = WorkflowController(filename, backend=NativeProcessBackend(), dispatch=wx.CallAfter)
            count = board.GetCopperLayerCount()
            copper = [pcbnew.F_Cu] + [getattr(pcbnew, 'In%d_Cu' % i) for i in range(1, count-1)] + [pcbnew.B_Cu]
            controller.set_layers([board.GetLayerName(layer) for layer in copper])
            dialog = WorkflowDialog(parent, controller=controller, pcbnew_module=pcbnew)
        except Exception as error:
            trace_exception('dialog_initialization_failed', error)
            wx.MessageBox(str(error), self.name, wx.OK | wx.ICON_ERROR, parent)
            return
        self._dialog = dialog
        dialog.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
        dialog.Show()
        trace('dialog_shown', log_path=str(LOG_PATH))

    def _destroyed(self, event):
        if event.GetEventObject() is self._dialog:
            trace('dialog_destroyed')
            self._dialog = None
        event.Skip()
