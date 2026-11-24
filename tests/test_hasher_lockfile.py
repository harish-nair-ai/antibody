from __future__ import annotations

import hashlib
import os
import tomllib
from pathlib import Path

import pytest

from antibody.errors import AntibodyError
from antibody.models import LockEntry, Lockfile
from antibody.registry.hasher import tree_files, tree_sha256
from antibody.registry.lockfile import read_lockfile, verify_lockfile, write_lockfile


def _expected(files: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in files:
        h.update(rel.encode("utf-8") + b"\x00" + data + b"\x00")
    return h.hexdigest()


def _write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_tree_hash_matches_canonical_algorithm_with_utf8_byte_order(tmp_path: Path) -> None:
    # '-' (0x2d) sorts before '/' (0x2f): os.walk order would visit a/ first.
    _write(tmp_path, "a/b.txt", b"one\n")
    _write(tmp_path, "a-b.txt", b"two\n")
    _write(tmp_path, "B.txt", b"three\n")
    assert tree_files(tmp_path) == ["B.txt", "a-b.txt", "a/b.txt"]
    assert tree_sha256(tmp_path) == _expected(
        [("B.txt", b"three\n"), ("a-b.txt", b"two\n"), ("a/b.txt", b"one\n")]
    )


def test_text_crlf_normalized_but_binary_and_invalid_utf8_hashed_raw(tmp_path: Path) -> None:
    _write(tmp_path, "t.md", b"x\r\ny\r\n")
    _write(tmp_path, "bin.dat", b"\x00\r\n")
    _write(tmp_path, "latin.txt", b"\xff\r\n")
    assert tree_sha256(tmp_path) == _expected(
        [("bin.dat", b"\x00\r\n"), ("latin.txt", b"\xff\r\n"), ("t.md", b"x\ny\n")]
    )
    lf = tmp_path / "lf"
    _write(lf, "t.md", b"x\ny\n")
    crlf = tmp_path / "crlf"
    _write(crlf, "t.md", b"x\r\ny\r\n")
    assert tree_sha256(lf) == tree_sha256(crlf)


def test_ignored_files_do_not_affect_hash(tmp_path: Path) -> None:
    _write(tmp_path, "pack.toml", b"[pack]\n")
    before = tree_sha256(tmp_path)
    _write(tmp_path, "evals/__pycache__/x.cpython-311.pyc", b"\x00junk")
    _write(tmp_path, ".DS_Store", b"\x00")
    _write(tmp_path, "evals/stray.pyc", b"\x00")
    assert tree_sha256(tmp_path) == before
    assert tree_files(tmp_path) == ["pack.toml"]


def test_any_byte_change_changes_hash(tmp_path: Path) -> None:
    _write(tmp_path, "rules/r.toml", b"id = 'a'\n")
    before = tree_sha256(tmp_path)
    _write(tmp_path, "rules/r.toml", b"id = 'b'\n")
    assert tree_sha256(tmp_path) != before


def test_symlink_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "real.txt", b"x")
    os.symlink(tmp_path / "real.txt", tmp_path / "link.txt")
    with pytest.raises(AntibodyError, match="symlink"):
        tree_sha256(tmp_path)


def _entry(pack_id: str, sha: str) -> LockEntry:
    return LockEntry(
        id=pack_id,
        version="0.1.0",
        sha256=sha,
        install_path=f".flywire/packs/{pack_id}",
        agents_md_patched=False,
    )


def test_lockfile_roundtrip_sorted_atomic(tmp_path: Path) -> None:
    lock = Lockfile(
        schema_version="0.1",
        registry_url="bundled://starter_registry",
        installed=(_entry("core/zeta", "a" * 64), _entry("core/alpha", "b" * 64)),
    )
    write_lockfile(tmp_path, lock)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["antibody.lock"]
    raw = tomllib.loads((tmp_path / "antibody.lock").read_text())
    assert raw["schema_version"] == "0.1"
    assert [e["id"] for e in raw["installed"]] == ["core/alpha", "core/zeta"]
    loaded = read_lockfile(tmp_path)
    assert loaded is not None
    assert [e.id for e in loaded.installed] == ["core/alpha", "core/zeta"]
    first = (tmp_path / "antibody.lock").read_bytes()
    write_lockfile(tmp_path, loaded)
    assert (tmp_path / "antibody.lock").read_bytes() == first


def test_read_lockfile_absent_and_invalid(tmp_path: Path) -> None:
    assert read_lockfile(tmp_path) is None
    (tmp_path / "antibody.lock").write_text("schema_version = 3\n")
    with pytest.raises(AntibodyError):
        read_lockfile(tmp_path)
    (tmp_path / "antibody.lock").write_text("not toml ===")
    with pytest.raises(AntibodyError):
        read_lockfile(tmp_path)


def test_verify_lockfile_reports_tamper_missing_and_traversal(tmp_path: Path) -> None:
    pack = tmp_path / ".flywire/packs/core/alpha"
    _write(pack, "rules/r.toml", b"x = 1\n")
    good = _entry("core/alpha", tree_sha256(pack))
    lock = Lockfile(schema_version="0.1", registry_url="bundled://starter_registry", installed=(good,))
    assert verify_lockfile(tmp_path, lock) == []
    _write(pack, "rules/r.toml", b"x = 2\n")
    assert "core/alpha" in verify_lockfile(tmp_path, lock)[0]
    missing = Lockfile(
        schema_version="0.1", registry_url="x", installed=(_entry("core/gone", "c" * 64),)
    )
    assert "not found" in verify_lockfile(tmp_path, missing)[0]
    escape = good.model_copy(update={"install_path": "../outside"})
    evil = Lockfile(schema_version="0.1", registry_url="x", installed=(escape,))
    assert "outside" in verify_lockfile(tmp_path, evil)[0]
