#!/usr/bin/env python3
"""Exercise an archive through KiCad's actual third-party plugin loader."""

import argparse
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import pcbnew


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
        pcbnew.LoadPlugins(
            str(args.builtin_plugins),
            "",
            str(third_party_plugins),
        )

        if package_directory not in pcbnew.KICAD_PLUGINS:
            raise AssertionError(
                "KiCad did not load the installed package:\n"
                + pcbnew.GetWizardsBackTrace()
            )

        module = pcbnew.KICAD_PLUGINS[package_directory]["ModuleName"]
        assert hasattr(module, "BGAInterposerWizard")
        wizard = module.BGAInterposerWizard()
        wizard.BuildFootprint()
        assert not wizard.AnyErrors(), wizard.GetBuildMessages()
        pads = list(wizard.module.Pads())
        assert len(pads) == 337
        assert sum(pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for pad in pads) == 1

        print(f"KiCad discovered {package_directory} from the PCM layout")


if __name__ == "__main__":
    main()
