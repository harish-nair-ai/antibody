"""Atomic pull / remove / sync of packs into `.flywire/packs/` (spec §5.1, contracts §7.4).

Pulling only copies files, hashes them, and parses TOML/compiles queries: pack Python
code is never imported or executed.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from antibody.ast_engine import compile_rule
from antibody.errors import AntibodyError, IntegrityError
from antibody.fsutil import replace_dir, resolve_inside
from antibody.installer import agents_md_sync, envelope_sync
from antibody.loaders import load_manifest, load_rule_file
from antibody.models import PACK_ID, SEMVER, IndexVersion, LockEntry, Lockfile
from antibody.registry.client import RegistryClient
from antibody.registry.hasher import tree_sha256
from antibody.registry.lockfile import read_lockfile, write_lockfile

PACKS_DIR = ".flywire/packs"
_REF = re.compile(rf"(?P<id>{PACK_ID[1:-1]})(?:@(?P<version>{SEMVER[1:-1]}))?")


def parse_pack_ref(ref: str) -> tuple[str, str | None]:
    match = _REF.fullmatch(ref)
    if match is None:
        raise AntibodyError(f"invalid pack reference {ref!r}; expected <namespace>/<name>[@X.Y.Z]")
    return match.group("id"), match.group("version")


def install_path(pack_id: str) -> str:
    return f"{PACKS_DIR}/{pack_id}"


@contextmanager
def _staging(root: Path) -> Iterator[Path]:
    """A private dir under `.flywire/.tmp/`, always removed (and .tmp if left empty)."""
    base = root / ".flywire" / ".tmp"
    base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(dir=base))
    try:
        yield scratch
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        with suppress(OSError):
            base.rmdir()


def _stage(
    client: RegistryClient, pack_id: str, version: str, entry: IndexVersion, scratch: Path
) -> Path:
    """Fetch into `scratch/pack`, verify the canonical hash, manifest identity, and queries."""
    staged = scratch / "pack"
    client.fetch(entry, staged)
    actual = tree_sha256(staged)
    if actual != entry.sha256:
        raise IntegrityError(
            f"{pack_id}@{version}: sha256 mismatch (index {entry.sha256}, got {actual})"
        )
    manifest = load_manifest(staged)
    if (manifest.id, manifest.pack.version) != (pack_id, version):
        found = f"{manifest.id}@{manifest.pack.version}"
        raise IntegrityError(f"registry entry {pack_id}@{version} contains {found}")
    for rule_path in sorted((staged / "rules").glob("*.toml")):
        for rule in load_rule_file(rule_path).ast_rules:
            compile_rule(rule)
    return staged


def _install(
    root: Path,
    client: RegistryClient,
    pack_id: str,
    version: str,
    entry: IndexVersion,
    patch_agents_md: bool,
) -> bool:
    """Stage + verify, plan AGENTS.md, then commit. Returns whether AGENTS.md got patched."""
    with _staging(root) as scratch:
        staged = _stage(client, pack_id, version, entry, scratch)
        snippet_path = staged / "patches" / "agents_md.md"
        plan: dict[Path, str] = {}
        patched = patch_agents_md and snippet_path.is_file()
        if patched:
            snippet = snippet_path.read_bytes().decode("utf-8")
            plan = agents_md_sync.plan_upsert(root, pack_id, version, snippet)
        replace_dir(staged, root / install_path(pack_id), scratch)
    envelope_sync.add_pack(root, pack_id)
    agents_md_sync.apply_plan(plan)
    return patched


def _lock(root: Path, url: str) -> Lockfile:
    return read_lockfile(root) or Lockfile(schema_version="0.1", registry_url=url)


def pull(root: Path, client: RegistryClient, ref: str, patch_agents_md: bool) -> LockEntry:
    pack_id, requested = parse_pack_ref(ref)
    version, entry = client.resolve(pack_id, requested)
    lock = _lock(root, client.url)
    previous = next((e for e in lock.installed if e.id == pack_id), None)
    keep_patch = previous is not None and previous.agents_md_patched
    patched = _install(root, client, pack_id, version, entry, patch_agents_md or keep_patch)
    new = LockEntry(
        id=pack_id,
        version=version,
        sha256=entry.sha256,
        install_path=install_path(pack_id),
        agents_md_patched=patched,
    )
    others = tuple(e for e in lock.installed if e.id != pack_id)
    write_lockfile(
        root, Lockfile(schema_version="0.1", registry_url=client.url, installed=(*others, new))
    )
    return new


def remove(root: Path, pack_id: str) -> None:
    lock = read_lockfile(root)
    entry = next((e for e in lock.installed if e.id == pack_id), None) if lock else None
    if lock is None or entry is None:
        raise AntibodyError(f"pack {pack_id!r} is not installed")
    plan = agents_md_sync.plan_remove(root, pack_id)
    target = resolve_inside(root, entry.install_path)
    if target.exists():
        shutil.rmtree(target)
    envelope_sync.remove_pack(root, pack_id)
    agents_md_sync.apply_plan(plan)
    others = tuple(e for e in lock.installed if e.id != pack_id)
    write_lockfile(root, lock.model_copy(update={"installed": others}))


def sync(root: Path, client: RegistryClient) -> list[str]:
    """Install exactly the pinned versions; returns ids that were (re)installed."""
    lock = read_lockfile(root)
    if lock is None:
        raise AntibodyError("no antibody.lock found; run `antibody pull <pack>` first")
    changed: list[str] = []
    for entry in lock.installed:
        target = resolve_inside(root, install_path(entry.id))
        if target.is_dir() and tree_sha256(target) == entry.sha256:
            continue
        _, indexed = client.resolve(entry.id, entry.version)
        if indexed.sha256 != entry.sha256:
            raise IntegrityError(
                f"{entry.id}@{entry.version}: registry sha256 {indexed.sha256} "
                f"drifted from antibody.lock {entry.sha256}"
            )
        _install(root, client, entry.id, entry.version, indexed, entry.agents_md_patched)
        changed.append(entry.id)
    return changed
