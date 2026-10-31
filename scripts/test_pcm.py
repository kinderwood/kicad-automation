#!/usr/bin/env python3
"""Check generated PCM hashes, URLs, resources, and plugin archives."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_as(instance, definition, schema):
    scoped_schema = {
        "$schema": schema["$schema"],
        "$ref": f"#/definitions/{definition}",
        "definitions": schema["definitions"],
    }
    jsonschema.Draft7Validator(scoped_schema).validate(instance)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory

    repository = json.loads((directory / "repository.json").read_text())
    catalogue = json.loads((directory / "packages.json").read_text())
    schema = json.loads((ROOT / "schemas/pcm.v1.schema.json").read_text())
    jsonschema.Draft7Validator.check_schema(schema)
    validate_as(repository, "Repository", schema)
    validate_as(catalogue, "PackageArray", schema)
    packages = catalogue["packages"]
    assert packages, "catalogue contains no packages"
    assert len({package["identifier"] for package in packages}) == len(packages)

    assert repository["packages"]["sha256"] == digest(directory / "packages.json")
    assert repository["resources"]["sha256"] == digest(directory / "resources.zip")

    with zipfile.ZipFile(directory / "resources.zip") as resources:
        resource_names = set(resources.namelist())
        for package in packages:
            assert f"{package['identifier']}/icon.png" in resource_names

    for package in packages:
        validate_as(package, "Package", schema)
        version = package["versions"][0]
        plugin_zip = directory / version["download_url"].rsplit("/", 1)[1]
        assert version["download_sha256"] == digest(plugin_zip)
        assert version["download_size"] == plugin_zip.stat().st_size

        with zipfile.ZipFile(plugin_zip) as archive:
            names = set(archive.namelist())
            assert "metadata.json" in names
            assert "resources/icon.png" in names
            assert "plugins/__init__.py" in names
            assert "plugins/LICENSE" in names
            assert version["install_size"] == sum(
                item.file_size for item in archive.infolist()
            )
            installed_metadata = json.loads(archive.read("metadata.json"))
            assert installed_metadata["identifier"] == package["identifier"]
            validate_as(installed_metadata, "Package", schema)

    print(f"PCM repository checks passed for {len(packages)} package(s)")


if __name__ == "__main__":
    main()
