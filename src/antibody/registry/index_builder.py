"""Build a registry `index.json` from `<registry>/packs/<ns>/<name>/` (contracts §7.4)."""

from __future__ import annotations

import json
from pathlib import Path

from antibody.fsutil import atomic_write
from antibody.loaders import load_manifest
from antibody.models import Index, IndexPack, IndexVersion
from antibody.registry.hasher import tree_files, tree_sha256


def build_index(registry_root: Path, generated_at: str) -> Index:
    packs: dict[str, IndexPack] = {}
    for manifest_path in sorted((registry_root / "packs").glob("*/*/pack.toml")):
        pack_dir = manifest_path.parent
        manifest = load_manifest(pack_dir)
        version = IndexVersion(
            sha256=tree_sha256(pack_dir),
            path=pack_dir.relative_to(registry_root).as_posix(),
            rule_count=len(manifest.vaccines),
            files=tuple(tree_files(pack_dir)),
        )
        packs[manifest.id] = IndexPack(
            latest=manifest.pack.version,
            description=manifest.pack.description,
            languages=manifest.pack.languages,
            versions={manifest.pack.version: version},
        )
    return Index(schema_version="0.1", generated_at=generated_at, packs=packs)


def write_index(registry_root: Path, index: Index) -> None:
    text = json.dumps(index.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    atomic_write(registry_root / "index.json", text)
