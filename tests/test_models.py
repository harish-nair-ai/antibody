from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from antibody.models import AstRule, Index, Lockfile, PackManifest, RuleFile, ShellRule

PACK_TOML = """
[pack]
namespace = "core"
name = "python-safety"
version = "0.1.0"
description = "Blocks test weakening."
authors = ["flywire-maintainers"]
license = "MIT"
languages = ["python"]
categories = ["test-tampering"]
flywire_min_version = "0.1.0"

[[vaccines]]
rule_id = "py-no-deleted-assertions"
rule_file = "rules/py_no_deleted_assertions.toml"
fixture_dir = "evals/fixtures/py-no-deleted-assertions"
target_sample_path = "tests/test_sample.py"
severity = "critical"
cwe = "CWE-693"
summary = "Prevents agents from deleting assertions to fake passing tests."
"""

RULE_TOML = r"""
[[ast_rules]]
id = "py-no-deleted-assertions"
language = "python"
paths = ["tests/**/*.py"]
check = "node_count_decreased"
query = '''(assert_statement) @target'''
severity = "steer"
message = "Test assertions were removed from a test file."
remediation = "Restore the deleted assertions."

[[shell_rules]]
id = "sh-no-hard-reset"
pattern = '^git\s+reset\s+.*--hard'
severity = "hard_block"
message = "Destructive git reset detected."
remediation = "Use git stash."
"""


def test_pack_manifest_parses_spec_example() -> None:
    manifest = PackManifest.model_validate(tomllib.loads(PACK_TOML))
    assert manifest.id == "core/python-safety"
    assert manifest.vaccines[0].rule_id == "py-no-deleted-assertions"
    assert manifest.vaccines[0].target_sample_path == "tests/test_sample.py"


def test_pack_manifest_is_frozen_and_forbids_extra_keys() -> None:
    data = tomllib.loads(PACK_TOML)
    manifest = PackManifest.model_validate(data)
    with pytest.raises(ValidationError):
        manifest.pack.name = "other"  # type: ignore[misc]
    data["pack"]["homepage"] = "x"
    with pytest.raises(ValidationError):
        PackManifest.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("namespace", "Core"),
        ("name", "-bad"),
        ("version", "0.1"),
        ("languages", ["cobol"]),
    ],
)
def test_pack_manifest_rejects_bad_fields(field: str, value: object) -> None:
    data = tomllib.loads(PACK_TOML)
    data["pack"][field] = value
    with pytest.raises(ValidationError):
        PackManifest.model_validate(data)


def test_vaccine_rejects_bad_severity_and_rule_id() -> None:
    for key, value in (("severity", "fatal"), ("rule_id", "Bad_Id")):
        data = tomllib.loads(PACK_TOML)
        data["vaccines"][0][key] = value
        with pytest.raises(ValidationError):
            PackManifest.model_validate(data)


def test_rule_file_parses_contract_example() -> None:
    rules = RuleFile.model_validate(tomllib.loads(RULE_TOML))
    ast = rules.ast_rules[0]
    assert isinstance(ast, AstRule)
    assert ast.capture == "target"
    assert ast.exclude_paths == ()
    assert isinstance(rules.shell_rules[0], ShellRule)
    assert rules.all_rules()[1].id == "sh-no-hard-reset"


def test_rule_file_rejects_empty_paths_bad_check_bad_regex_unknown_keys() -> None:
    base = tomllib.loads(RULE_TOML)
    cases = [
        ("ast_rules", "paths", []),
        ("ast_rules", "check", "node_count_changed"),
        ("ast_rules", "language", "javascript"),
        ("shell_rules", "pattern", "(unclosed"),
        ("shell_rules", "severity", "block"),
    ]
    for table, key, value in cases:
        data = tomllib.loads(RULE_TOML)
        data[table][0][key] = value
        with pytest.raises(ValidationError):
            RuleFile.model_validate(data)
    base["extra"] = 1
    with pytest.raises(ValidationError):
        RuleFile.model_validate(base)


def test_index_and_lockfile_models() -> None:
    index = Index.model_validate(
        {
            "schema_version": "0.1",
            "generated_at": "2026-11-24T00:00:00Z",
            "packs": {
                "core/python-safety": {
                    "latest": "0.1.0",
                    "description": "d",
                    "languages": ["python"],
                    "versions": {
                        "0.1.0": {
                            "sha256": "a" * 64,
                            "path": "packs/core/python-safety",
                            "rule_count": 7,
                            "files": ["pack.toml"],
                        }
                    },
                }
            },
        }
    )
    assert index.packs["core/python-safety"].versions["0.1.0"].rule_count == 7
    with pytest.raises(ValidationError):
        Index.model_validate({"schema_version": "0.2", "generated_at": "x", "packs": {}})
    lock = Lockfile.model_validate(
        {
            "schema_version": "0.1",
            "registry_url": "bundled://starter_registry",
            "installed": [
                {
                    "id": "core/python-safety",
                    "version": "0.1.0",
                    "sha256": "b" * 64,
                    "install_path": ".flywire/packs/core/python-safety",
                    "agents_md_patched": True,
                }
            ],
        }
    )
    assert lock.installed[0].agents_md_patched is True
    with pytest.raises(ValidationError):
        Lockfile.model_validate(
            {
                "schema_version": "0.1",
                "registry_url": "x",
                "installed": [
                    {
                        "id": "nope",
                        "version": "0.1.0",
                        "sha256": "short",
                        "install_path": "p",
                        "agents_md_patched": False,
                    }
                ],
            }
        )
