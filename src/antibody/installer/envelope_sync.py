"""Idempotently add/remove pack ids in `envelope.packs`, preserving comments (contracts §3)."""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Array, Table
from tomlkit.toml_document import TOMLDocument

from antibody.errors import AntibodyError
from antibody.fsutil import atomic_write

ENVELOPE_NAME = "envelope.toml"


def _load(path: Path) -> tuple[str, TOMLDocument]:
    text = path.read_bytes().decode("utf-8")
    try:
        return text, tomlkit.parse(text)
    except TOMLKitError as exc:
        raise AntibodyError(f"invalid {ENVELOPE_NAME}: {exc}") from exc


def _packs(doc: TOMLDocument, create: bool) -> Array | None:
    envelope = doc.get("envelope")
    if envelope is None:
        if not create:
            return None
        envelope = tomlkit.table()
        doc["envelope"] = envelope
    if not isinstance(envelope, Table):
        raise AntibodyError(f"invalid {ENVELOPE_NAME}: [envelope] must be a table")
    packs = envelope.get("packs")
    if packs is None:
        if not create:
            return None
        envelope["packs"] = tomlkit.array()
        packs = envelope["packs"]
    if not isinstance(packs, Array) or not all(isinstance(p, str) for p in packs):
        raise AntibodyError(f"invalid {ENVELOPE_NAME}: envelope.packs must be a list of strings")
    return packs


def add_pack(root: Path, pack_id: str) -> None:
    path = root / ENVELOPE_NAME
    if not path.exists():
        atomic_write(path, f'[envelope]\nversion = "0.1"\npacks = ["{pack_id}"]\n')
        return
    text, doc = _load(path)
    packs = _packs(doc, create=True)
    assert packs is not None
    if pack_id not in packs:
        packs.append(pack_id)
    if (new := tomlkit.dumps(doc)) != text:
        atomic_write(path, new)


def remove_pack(root: Path, pack_id: str) -> None:
    path = root / ENVELOPE_NAME
    if not path.exists():
        return
    text, doc = _load(path)
    packs = _packs(doc, create=False)
    if packs is None or pack_id not in packs:
        return
    del packs[list(packs).index(pack_id)]
    if (new := tomlkit.dumps(doc)) != text:
        atomic_write(path, new)
