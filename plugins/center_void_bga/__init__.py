"""KiCad footprint wizard for BGAs with a centered rectangular void."""

from .wizard import CenterVoidBGAWizard


CenterVoidBGAWizard().register()

__all__ = ["CenterVoidBGAWizard"]
