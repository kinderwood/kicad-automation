"""Independent librarian RF authoring plugin; file artifacts are its only handoff."""

__version__ = "0.1.0"

# Pure recipe tooling remains usable outside KiCad. KiCad loads and registers the
# ActionPlugin when pcbnew is present; a broken KiCad import is not hidden.
try:
    import pcbnew
except ModuleNotFoundError as error:
    if error.name != "pcbnew":
        raise
else:
    from .plugin import GPPORFTransitionDesignerPlugin
    import wx

    PLUGIN = GPPORFTransitionDesignerPlugin()
    # Headless pcbnew has no application registration context and may abort in
    # C++; importing recipes must remain safe for CLI consumers and test suites.
    # Discovery precedes the first board/frame; GetBoard() is still None.
    # The native editor app name distinguishes this from standalone wx tests.
    app = wx.GetApp()
    if app is not None and app.GetAppName().casefold() == "pcbnew":
        PLUGIN.register()
