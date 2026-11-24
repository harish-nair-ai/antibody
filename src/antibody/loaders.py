"""Parse pack files into frozen models (parse, don't validate). Failures are gate failures."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from antibody.errors import AntibodyError, GateFailure, describe
from antibody.fsutil import resolve_inside
from antibody.models import PackManifest, Rule, RuleFile, Vaccine

M = TypeVar("M", bound=BaseModel)


def _parse(model: type[M], path: Path) -> M:
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(data)
    except FileNotFoundError as exc:
        raise GateFailure(f"missing file: {path}") from exc
    except ValidationError as exc:
        raise GateFailure(f"invalid {path.name}: {describe(exc)}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise GateFailure(f"invalid {path.name}: {exc}") from exc


def load_manifest(pack_dir: Path) -> PackManifest:
    return _parse(PackManifest, pack_dir / "pack.toml")


def load_rule_file(path: Path) -> RuleFile:
    return _parse(RuleFile, path)


def pack_path(pack_dir: Path, rel: str) -> Path:
    """A pack-relative path that must stay inside the pack (traversal → gate failure)."""
    try:
        return resolve_inside(pack_dir, rel)
    except AntibodyError as exc:
        raise GateFailure(str(exc)) from exc


def load_vaccine_rule(pack_dir: Path, vaccine: Vaccine) -> Rule:
    """The single rule in `vaccine.rule_file`, which must carry id `vaccine.rule_id`."""
    rules = load_rule_file(pack_path(pack_dir, vaccine.rule_file)).all_rules()
    if len(rules) != 1:
        raise GateFailure(f"{vaccine.rule_file}: must contain exactly one rule, found {len(rules)}")
    rule = rules[0]
    if rule.id != vaccine.rule_id:
        raise GateFailure(f"{vaccine.rule_file}: rule id {rule.id!r} != {vaccine.rule_id!r}")
    return rule
