#!/usr/bin/env python3
"""Build a self-hostable KiCad PCM repository from plugin manifests."""

import argparse
import hashlib
import json
import struct
import time
import zipfile
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = ROOT / "plugins"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zip_size(path):
    with zipfile.ZipFile(path) as archive:
        return sum(item.file_size for item in archive.infolist())


def png_chunk(kind, data):
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def make_default_icon(size=64):
    """Create a dependency-free fallback icon for plugins without icon.png."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            background = (24, 33, 48, 255)
            grid_x = (x - 8) % 7
            grid_y = (y - 8) % 7
            in_grid = 5 <= x <= 58 and 5 <= y <= 58
            in_opening = 22 <= x <= 41 and 22 <= y <= 41
            ball = in_grid and not in_opening and grid_x <= 3 and grid_y <= 3
            row.extend((246, 189, 66, 255) if ball else background)
        rows.append(bytes(row))
    raw = b"".join(rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )


def discover_plugins():
    manifests = sorted(PLUGINS_DIR.glob("*/metadata.json"))
    if not manifests:
        raise RuntimeError(f"no plugin manifests found under {PLUGINS_DIR}")
    return [(path.parent, json.loads(path.read_text())) for path in manifests]


def icon_bytes(plugin_dir):
    icon = plugin_dir / "icon.png"
    return icon.read_bytes() if icon.exists() else make_default_icon()


def build_plugin(plugin_dir, metadata, base_url, output_dir):
    version = metadata["versions"][0]["version"]
    archive_name = f"{plugin_dir.name.replace('_', '-')}-{version}.zip"
    plugin_zip = output_dir / archive_name

    with zipfile.ZipFile(plugin_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(plugin_dir / "metadata.json", "metadata.json")
        archive.write(ROOT / "LICENSE", Path("plugins") / plugin_dir.name / "LICENSE")
        for source in sorted(plugin_dir.rglob("*")):
            if not source.is_file() or source.name == "metadata.json":
                continue
            if "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
                continue
            relative = source.relative_to(plugin_dir)
            archive.write(source, Path("plugins") / plugin_dir.name / relative)
        archive.writestr("resources/icon.png", icon_bytes(plugin_dir))

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
    return package, plugin_zip


def build(base_url, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    discovered = discover_plugins()
    packages = []
    plugin_archives = []

    for plugin_dir, metadata in discovered:
        package, plugin_zip = build_plugin(plugin_dir, metadata, base_url, output_dir)
        packages.append(package)
        plugin_archives.append(plugin_zip)

    schema = packages[0]["$schema"]
    packages_path = output_dir / "packages.json"
    packages_path.write_text(
        json.dumps({"$schema": schema, "packages": packages}, indent=2) + "\n"
    )

    resources_path = output_dir / "resources.zip"
    with zipfile.ZipFile(resources_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for plugin_dir, metadata in discovered:
            archive.writestr(
                f"{metadata['identifier']}/icon.png", icon_bytes(plugin_dir)
            )

    now = int(time.time())
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
    repository = {
        "$schema": schema,
        "name": "KiCad Automation Repository",
        "maintainer": {
            "name": "kinderwood",
            "contact": {"web": "https://github.com/kinderwood/kicad-automation"},
        },
        "packages": {
            "url": f"{base_url}/packages.json",
            "sha256": sha256(packages_path),
            "update_time_utc": timestamp,
            "update_timestamp": now,
        },
        "resources": {
            "url": f"{base_url}/resources.zip",
            "sha256": sha256(resources_path),
            "update_time_utc": timestamp,
            "update_timestamp": now,
        },
    }
    (output_dir / "repository.json").write_text(json.dumps(repository, indent=2) + "\n")
    (output_dir / ".nojekyll").write_text("")
    return plugin_archives


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        required=True,
        help="Public URL of the directory that will contain repository.json",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for plugin_zip in build(args.base_url.rstrip("/"), args.output_dir):
        print(plugin_zip)


if __name__ == "__main__":
    main()
