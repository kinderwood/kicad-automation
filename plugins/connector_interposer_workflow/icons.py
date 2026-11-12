"""KiCad's own toolbar icons, read from the installation that is running.

KiCad ships every icon, in light and dark variants, in one archive under its
stock data directory. Reading it keeps the Workflow toolbar identical to the
user's KiCad without redistributing the artwork. No scripting call returns
that directory, so it is found relative to the running interpreter and the
``pcbnew`` module; wx stock art is the fallback.
"""
import io
import sys
import tarfile
from pathlib import Path

import wx

_ARCHIVES = (
    Path('share/kicad/resources/images.tar.gz'),
    Path('SharedSupport/resources/images.tar.gz'),
    Path('resources/images.tar.gz'),
)
_STOCK = {
    'left': wx.ART_GO_BACK, 'right': wx.ART_GO_FORWARD, 'save': wx.ART_FILE_SAVE,
    'trash': wx.ART_DELETE, 'undo': wx.ART_UNDO, 'info': wx.ART_INFORMATION,
}
_cache = {}


def archive_path():
    """Return KiCad's image archive, or None when it cannot be located."""
    origins = [sys.executable, getattr(sys.modules.get('pcbnew'), '__file__', None)]
    for origin in origins:
        if not origin:
            continue
        for root in list(Path(origin).resolve().parents)[:8]:
            for relative in _ARCHIVES:
                candidate = root / relative
                if candidate.is_file():
                    return candidate
    return None


def load(names, size=24, dark=False):
    """Return ``{name: wx.Bitmap}`` for KiCad icon names such as ``save``."""
    key = (tuple(names), size, dark)
    if key in _cache:
        return _cache[key]
    result = {}
    path = archive_path()
    if path is not None:
        try:
            with tarfile.open(path) as archive:
                for name in names:
                    for variant in ((f'{name}_dark',) if dark else ()) + (name,):
                        try:
                            member = archive.extractfile(f'./{variant}_{size}.png')
                        except KeyError:
                            member = None
                        if member is not None:
                            image = wx.Image(io.BytesIO(member.read()), wx.BITMAP_TYPE_PNG)
                            if image.IsOk():
                                result[name] = wx.Bitmap(image)
                                break
        except (OSError, tarfile.TarError):
            result = {}
    for name in names:
        if name not in result:
            result[name] = wx.ArtProvider.GetBitmap(
                _STOCK.get(name, wx.ART_MISSING_IMAGE), wx.ART_TOOLBAR, (size, size),
            )
    _cache[key] = result
    return result
