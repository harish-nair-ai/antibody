"""`antibody.lock` reader, writer, and drift verifier (contracts §7.4)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomlkit
from pydantic import ValidationError

from antibody.errors import AntibodyError
from antibody.fsutil import atomic_write, resolve_inside
from antibody.models import Lockfile
from antibody.registry.hasher import tree_sha256

LOCK_NAME = "antibody.lock"


def read_lockfile(root: Path) -> Lockfile | None:
    path = root / LOCK_NAME
    if not path.exists():
        return None
    try:
        return Lockfile.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError) as exc:
        raise AntibodyError(f"invalid {LOCK_NAME}: {exc}") from exc


def write_lockfile(root: Path, lock: Lockfile) -> None:
    doc = tomlkit.document()
    doc["schema_version"] = lock.schema_version
    doc["registry_url"] = lock.registry_url
    installed = tomlkit.aot()
    for entry in sorted(lock.installed, key=lambda e: e.id):
        table = tomlkit.table()
        for key, value in entry.model_dump().items():
            table[key] = value
        installed.append(table)
    if lock.installed:
        doc["installed"] = installed
    atomic_write(root / LOCK_NAME, tomlkit.dumps(doc))


def verify_lockfile(root: Path, lock: Lockfile) -> list[str]:
    """One human-readable problem per drifted, missing, or unsafe entry; [] when clean."""
    problems: list[str] = []
    for entry in lock.installed:
        try:
            path = resolve_inside(root, entry.install_path)
        except AntibodyError:
            problems.append(f"{entry.id}: install_path {entry.install_path!r} is outside root")
            continue
        if not path.is_dir():
            problems.append(f"{entry.id}: install dir not found: {entry.install_path}")
            continue
        try:
            actual = tree_sha256(path)
        except AntibodyError as exc:
            problems.append(f"{entry.id}: {exc}")
            continue
        if actual != entry.sha256:
            problems.append(f"{entry.id}: sha256 drift (locked {entry.sha256}, found {actual})")
    return problems
