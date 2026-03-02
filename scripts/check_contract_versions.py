#!/usr/bin/env python3
"""Pre-commit hook: verify VERSION == __init__.py == pyproject.toml == jenn-contract.json."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_version_file() -> str:
    return (ROOT / "VERSION").read_text().strip()


def read_init_version() -> str:
    init_path = ROOT / "src" / "jenn_mesh" / "__init__.py"
    for line in init_path.read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"').strip("'")
    raise ValueError("No __version__ found in __init__.py")


def read_pyproject_version() -> str:
    import tomllib

    pyproject = ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def read_contract_version() -> str:
    contract = ROOT / "jenn-contract.json"
    with open(contract) as f:
        data = json.load(f)
    return data["project"]["version"]


def main() -> int:
    versions = {
        "VERSION": read_version_file(),
        "__init__.py": read_init_version(),
        "pyproject.toml": read_pyproject_version(),
        "jenn-contract.json": read_contract_version(),
    }

    unique = set(versions.values())
    if len(unique) == 1:
        print(f"All versions aligned: {unique.pop()}")
        return 0

    print("VERSION MISMATCH detected:")
    for source, version in versions.items():
        print(f"  {source}: {version}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
