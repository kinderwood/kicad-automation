#!/usr/bin/env python3
"""Exercise an archive through KiCad's actual third-party plugin loader."""

import argparse
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import pcbnew
import wx


def simulate_pcm_install(archive_path, install_root):
    with zipfile.ZipFile(archive_path) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        package_directory = metadata["identifier"].replace(".", "_")
        plugin_root = install_root / "plugins" / package_directory

        for name in archive.namelist():
            path = PurePosixPath(name)
            if len(path.parts) < 2 or path.parts[0] != "plugins":
                continue
            relative = Path(*path.parts[1:])
            target = plugin_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))

    return package_directory, install_root / "plugins"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--builtin-plugins", type=Path, required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temporary:
        package_directory, third_party_plugins = simulate_pcm_install(
            args.archive, Path(temporary)
        )
        # KiCad's real editor supplies a wx application while loading actions.
        # Headless pcbnew does not, and calling the native register method in
        # that state leaves an invalid application handle.  Substitute only the
        # registration boundary so this test proves that the installed module
        # requests registration without pretending to be a complete editor.
        class PackagedApplication:
            def GetAppName(self):
                return "org.kicad.KiCad"

        registered = []
        original_get_app = wx.GetApp
        original_register = pcbnew.ActionPlugin.register
        wx.GetApp = PackagedApplication
        pcbnew.ActionPlugin.register = lambda plugin: registered.append(plugin)
        try:
            pcbnew.LoadPlugins(
                str(args.builtin_plugins),
                "",
                str(third_party_plugins),
            )
        finally:
            wx.GetApp = original_get_app
            pcbnew.ActionPlugin.register = original_register

        if package_directory not in pcbnew.KICAD_PLUGINS:
            raise AssertionError(
                "KiCad did not load the installed package:\n"
                + pcbnew.GetWizardsBackTrace()
            )

        module = pcbnew.KICAD_PLUGINS[package_directory]["ModuleName"]
        if hasattr(module, "BGAInterposerWizard"):
            wizard = module.BGAInterposerWizard()
            wizard.BuildFootprint()
            assert not wizard.AnyErrors(), wizard.GetBuildMessages()
            pads = list(wizard.module.Pads())
            assert len(pads) == 337
            assert sum(pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for pad in pads) == 1
        else:
            # Import through the real loader must construct the action plugin;
            # invoking Run would require the editor's active GUI/board context.
            plugin = getattr(module, "PLUGIN", None)
            assert isinstance(plugin, pcbnew.ActionPlugin), package_directory
            assert plugin in registered, (
                f"{package_directory} was imported but did not register its action"
            )
            assert plugin.GetName() not in ("", "Undefined Action plugin")
            assert plugin.GetDescription()
            assert plugin.GetCategoryName() != "Undefined"
            assert type(plugin).Run is not pcbnew.ActionPlugin.Run

        print(f"KiCad discovered {package_directory} from the PCM layout")


if __name__ == "__main__":
    main()
