"""Frozen pydantic v2 models for every external file format (contracts §1, §7)."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SLUG = r"^[a-z0-9][a-z0-9-]*$"
SEMVER = r"^\d+\.\d+\.\d+$"
SHA256 = r"^[0-9a-f]{64}$"
PACK_ID = r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$"

Slug = Annotated[str, Field(pattern=SLUG)]
Semver = Annotated[str, Field(pattern=SEMVER)]
Sha256 = Annotated[str, Field(pattern=SHA256)]
PackId = Annotated[str, Field(pattern=PACK_ID)]
RuleLanguage = Literal["python", "typescript", "tsx"]
Severity = Literal["steer", "hard_block"]


def _valid_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc
    return pattern


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AstRule(_Frozen):
    id: Slug
    language: RuleLanguage
    paths: tuple[str, ...] = Field(min_length=1)
    exclude_paths: tuple[str, ...] = ()
    check: Literal["node_count_decreased", "new_node_introduced", "node_count_increased"]
    query: str = Field(min_length=1)
    capture: str = "target"
    severity: Severity
    message: str
    remediation: str


class ShellRule(_Frozen):
    id: Slug
    pattern: Annotated[str, AfterValidator(_valid_regex)]
    severity: Severity
    message: str
    remediation: str


Rule = AstRule | ShellRule


class RuleFile(_Frozen):
    ast_rules: tuple[AstRule, ...] = ()
    shell_rules: tuple[ShellRule, ...] = ()

    def all_rules(self) -> tuple[Rule, ...]:
        return (*self.ast_rules, *self.shell_rules)


class PackMeta(_Frozen):
    namespace: Slug
    name: Slug
    version: Semver
    description: str
    authors: tuple[str, ...]
    license: str
    languages: tuple[Literal["python", "typescript", "tsx", "shell"], ...]
    categories: tuple[str, ...]
    flywire_min_version: Semver


class Vaccine(_Frozen):
    rule_id: Slug
    rule_file: str
    fixture_dir: str
    target_sample_path: str | None = None
    severity: Literal["critical", "high", "medium", "low"]
    cwe: str | None = None
    summary: str


class PackManifest(_Frozen):
    pack: PackMeta
    vaccines: tuple[Vaccine, ...] = Field(min_length=1)

    @property
    def id(self) -> str:
        return f"{self.pack.namespace}/{self.pack.name}"


class IndexVersion(_Frozen):
    sha256: Sha256
    path: str
    rule_count: int = Field(ge=0)
    files: tuple[str, ...]


class IndexPack(_Frozen):
    latest: Semver
    description: str
    languages: tuple[str, ...]
    versions: dict[Semver, IndexVersion]


class Index(_Frozen):
    schema_version: Literal["0.1"]
    generated_at: str
    packs: dict[PackId, IndexPack]


class LockEntry(_Frozen):
    id: PackId
    version: Semver
    sha256: Sha256
    install_path: str
    agents_md_patched: bool


class Lockfile(_Frozen):
    schema_version: Literal["0.1"]
    registry_url: str
    installed: tuple[LockEntry, ...] = ()
