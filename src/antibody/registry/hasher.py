"""Canonical SHA-256 tree hash (contracts §7.3, D19)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from antibody.errors import IntegrityError


def _ignored(rel_parts: tuple[str, ...]) -> bool:
    name = rel_parts[-1]
    return "__pycache__" in rel_parts or name == ".DS_Store" or name.endswith(".pyc")


def tree_files(root: Path) -> list[str]:
    """Hashed relative POSIX paths, sorted by UTF-8 bytes. Symlinks are an error."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in (*dirnames, *filenames):
            path = base / name
            if path.is_symlink():
                raise IntegrityError(f"symlink not allowed in pack: {path.relative_to(root)}")
        for name in filenames:
            path = base / name
            rel = path.relative_to(root)
            if _ignored(rel.parts):
                continue
            if not path.is_file():
                raise IntegrityError(f"not a regular file: {rel.as_posix()}")
            found.append(rel.as_posix())
    return sorted(found, key=lambda p: p.encode("utf-8"))


def _normalize(data: bytes) -> bytes:
    if b"\x00" in data:
        return data
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n")


def tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for rel in tree_files(root):
        data = _normalize((root / rel).read_bytes())
        h.update(rel.encode("utf-8") + b"\x00" + data + b"\x00")
    return h.hexdigest()
