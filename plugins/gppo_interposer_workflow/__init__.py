"""GPPO Interposer Workflow: read-only library consumption and project instances."""
import os

__version__ = '0.1.0'
PLUGIN = None
# Core modules are also usable in a plain Python interpreter and isolated workers.
if not os.environ.get('GPPO_WORKFLOW_WORKER'):
    try:
        import pcbnew
    except ImportError:
        pass
    else:
        from .plugin import GPPOInterposerWorkflowPlugin
        PLUGIN = GPPOInterposerWorkflowPlugin()
        import wx
        # PCB Editor discovers actions before its first board/frame exists.
        # Native pcbnew identifies its wx application independently of that board;
        # plain Python and standalone wx applications must not register actions.
        app = wx.GetApp()
        if app is not None and app.GetAppName().casefold() == 'pcbnew':
            PLUGIN.register()

__all__ = ['PLUGIN', '__version__']
