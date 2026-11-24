"""Filesystem primitives: atomic writes, traversal-safe joins, workspace root discovery."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from antibody.errors import AntibodyError


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write via a sibling temp file + fsync + os.replace (contracts §0)."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, prefix=f".{path.name}.") as f:
        tmp = Path(f.name)
        try:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def resolve_inside(base: Path, rel: str) -> Path:
    """Join a relative POSIX path onto `base`, refusing anything that escapes it."""
    if not rel or PurePosixPath(rel).is_absolute() or "\\" in rel:
        raise AntibodyError(f"unsafe path {rel!r}: must be relative POSIX")
    resolved = (base / rel).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise AntibodyError(f"unsafe path {rel!r}: resolves outside {base}")
    return resolved


def find_workspace_root(start: Path) -> Path:
    """First ancestor (inclusive) containing `envelope.toml` or `.git`; else `start`."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / "envelope.toml").exists() or (candidate / ".git").exists():
            return candidate
    return start


def replace_dir(new: Path, dest: Path, scratch: Path) -> None:
    """Move `new` onto `dest`; an existing `dest` is first moved aside into `scratch`."""
    if dest.exists():
        os.replace(dest, scratch / "old")
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(new, dest)
