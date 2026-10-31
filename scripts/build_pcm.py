#!/usr/bin/env python3
"""Build a self-hostable KiCad PCM repository and plugin archive."""

import argparse
import hashlib
import json
import struct
import time
import zipfile
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "org.kicad.center-void-bga-wizard"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zip_size(path):
    with zipfile.ZipFile(path) as archive:
        return sum(item.file_size for item in archive.infolist())


def png_chunk(kind, data):
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def make_icon(size=64):
    """Create a small dependency-free PNG depicting a BGA with a center void."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            background = (24, 33, 48, 255)
            grid_x = (x - 8) % 7
            grid_y = (y - 8) % 7
            in_grid = 5 <= x <= 58 and 5 <= y <= 58
            in_void = 22 <= x <= 41 and 22 <= y <= 41
            ball = in_grid and not in_void and grid_x <= 3 and grid_y <= 3
            color = (246, 189, 66, 255) if ball else background
            row.extend(color)
        rows.append(bytes(row))
    raw = b"".join(rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )


def add_file(archive, source, destination):
    archive.write(source, destination)


def build(base_url, output_dir):
    metadata = json.loads((ROOT / "metadata.json").read_text())
    version = metadata["versions"][0]["version"]
    output_dir.mkdir(parents=True, exist_ok=True)

    plugin_zip = output_dir / f"center-void-bga-wizard-{version}.zip"
    with zipfile.ZipFile(plugin_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        add_file(archive, ROOT / "metadata.json", "metadata.json")
        add_file(archive, ROOT / "README.md", "plugins/center_void_bga/README.md")
        add_file(
            archive,
            ROOT / "plugins/center_void_bga/__init__.py",
            "plugins/center_void_bga/__init__.py",
        )
        add_file(
            archive,
            ROOT / "plugins/center_void_bga/wizard.py",
            "plugins/center_void_bga/wizard.py",
        )
        archive.writestr("resources/icon.png", make_icon())

    package = dict(metadata)
    package["versions"] = [
        {
            **metadata["versions"][0],
            "download_url": f"{base_url}/{plugin_zip.name}",
            "download_sha256": sha256(plugin_zip),
            "download_size": plugin_zip.stat().st_size,
            "install_size": zip_size(plugin_zip),
        }
    ]
    packages = {"$schema": metadata["$schema"], "packages": [package]}
    packages_path = output_dir / "packages.json"
    packages_path.write_text(json.dumps(packages, indent=2) + "\n")

    resources_path = output_dir / "resources.zip"
    with zipfile.ZipFile(resources_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{IDENTIFIER}/icon.png", make_icon())

    now = int(time.time())
    repository = {
        "$schema": metadata["$schema"],
        "name": "Center Void BGA Wizard Repository",
        "maintainer": metadata["maintainer"],
        "packages": {
            "url": f"{base_url}/packages.json",
            "sha256": sha256(packages_path),
            "update_time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "update_timestamp": now,
        },
        "resources": {
            "url": f"{base_url}/resources.zip",
            "sha256": sha256(resources_path),
            "update_time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "update_timestamp": now,
        },
    }
    (output_dir / "repository.json").write_text(json.dumps(repository, indent=2) + "\n")
    (output_dir / ".nojekyll").write_text("")
    return plugin_zip


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        required=True,
        help="Public URL of the directory that will contain repository.json",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    plugin_zip = build(args.base_url.rstrip("/"), args.output_dir)
    print(plugin_zip)


if __name__ == "__main__":
    main()
