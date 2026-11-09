"""KiCad ActionPlugin entry point; importing the plugin never opens a dialog."""
from pathlib import Path
import pcbnew
import wx


class ConnectorToInterposerWorkflowPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = 'Connector to Interposer Workflow'
        self.category = 'PCB automation'
        self.description = 'Select approved parts, validate connectivity and reconcile the open project'
        self.show_toolbar_button = False
        self._dialog = None

    def Run(self):
        if self._dialog is not None:
            try:
                self._dialog.Raise()
                return
            except RuntimeError:
                self._dialog = None
        board = pcbnew.GetBoard()
        filename = board.GetFileName() if board else ''
        if not filename or not Path(filename).is_file():
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
            settings = board.GetDesignSettings()
            controller.set_board_microvia_rules(
                [(pcbnew.ToMM(item.m_Diameter), pcbnew.ToMM(item.m_Drill))
                 for item in board.GetViasDimensionsList()],
                pcbnew.ToMM(settings.m_MicroViasMinSize),
                pcbnew.ToMM(settings.m_MicroViasMinDrill),
                pcbnew.ToMM(settings.m_MinClearance),
            )
            dialog = WorkflowDialog(parent, controller=controller, pcbnew_module=pcbnew)
        except Exception as error:
            wx.MessageBox(str(error), self.name, wx.OK | wx.ICON_ERROR, parent)
            return
        self._dialog = dialog
        dialog.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
        dialog.Show()

    def _destroyed(self, event):
        if event.GetEventObject() is self._dialog:
            self._dialog = None
        event.Skip()
