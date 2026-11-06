"""Connector to Interposer Workflow: read-only library consumption and project instances."""
import os

__version__ = '0.4.18'
PLUGIN = None
# Core modules are also usable in a plain Python interpreter and isolated workers.
if not os.environ.get('CONNECTOR_INTERPOSER_WORKFLOW_WORKER'):
    try:
        import pcbnew
    except ImportError:
        pass
    else:
        from .plugin import ConnectorToInterposerWorkflowPlugin
        PLUGIN = ConnectorToInterposerWorkflowPlugin()
        import wx
        # PCB Editor discovers actions before its first board/frame exists.
        # Tested packaged builds can expose different application names, while
        # headless imports have no wx application registration context.
        app = wx.GetApp()
        if app is not None:
            PLUGIN.register()

__all__ = ['PLUGIN', '__version__']
