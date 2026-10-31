"""KiCad footprint wizard for BGA-style interposers."""

from .wizard import BGAInterposerWizard


BGAInterposerWizard().register()

__all__ = ["BGAInterposerWizard"]
