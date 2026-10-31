#!/usr/bin/env python3
"""Check generated PCM hashes, URLs, and archive layout."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory

    repository = json.loads((directory / "repository.json").read_text())
    packages = json.loads((directory / "packages.json").read_text())
    package = packages["packages"][0]
    version = package["versions"][0]
    plugin_zip = directory / version["download_url"].rsplit("/", 1)[1]

    assert repository["packages"]["sha256"] == digest(directory / "packages.json")
    assert repository["resources"]["sha256"] == digest(directory / "resources.zip")
    assert version["download_sha256"] == digest(plugin_zip)
    assert version["download_size"] == plugin_zip.stat().st_size

    with zipfile.ZipFile(plugin_zip) as archive:
        names = set(archive.namelist())
        assert "metadata.json" in names
        assert "plugins/center_void_bga/__init__.py" in names
        assert "plugins/center_void_bga/wizard.py" in names
        assert "resources/icon.png" in names
        assert version["install_size"] == sum(i.file_size for i in archive.infolist())

    print("PCM repository checks passed")


if __name__ == "__main__":
    main()
