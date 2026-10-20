#!/usr/bin/env python3
"""Reject game/private payloads and prove the Python archives are self-contained."""

import argparse
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from verify_distribution import check_name


def verify(directory: Path) -> None:
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as archive:
        if archive.testzip() is not None:
            raise ValueError("Corrupt Python wheel")
        names = archive.namelist()
        for name in names:
            check_name(name)
            if not (name.startswith("oniharness/") or ".dist-info/" in name):
                raise ValueError(f"Unexpected wheel payload: {name}")
        if "oniharness/py.typed" not in names:
            raise ValueError("Missing Python typing marker")
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        if (
            metadata["Name"] != "oniharness"
            or metadata["License-Expression"] != "LicenseRef-AUL-1.0-draft.1"
        ):
            raise ValueError("Incorrect package identity or license")
        for required in ("LICENSE", "LICENSING.md", "UPSTREAM.toml"):
            if not any(name.endswith("/licenses/" + required) for name in names):
                raise ValueError(f"Missing notice: {required}")
    with tarfile.open(sdists[0]) as archive:
        for member in archive.getmembers():
            check_name(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"Unexpected archive member: {member.name}")
        names = archive.getnames()
        for required in (
            "pyproject.toml",
            "python/README.md",
            "python/src/oniharness/__init__.py",
        ):
            if not any(name.endswith("/" + required) for name in names):
                raise ValueError(f"Incomplete source distribution: {required}")
    print(
        "PASS: Python wheel/sdist contain only library sources, examples and license notices"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
