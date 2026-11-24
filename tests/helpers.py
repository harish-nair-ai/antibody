"""Builders for small, real packs on disk (used by several test modules)."""

from __future__ import annotations

from pathlib import Path

PACK_TOML = """\
[pack]
namespace = "acme"
name = "{name}"
version = "{version}"
description = "Test pack."
authors = ["tests"]
license = "MIT"
languages = ["python", "shell"]
categories = ["security-regression"]
flywire_min_version = "0.1.0"

[[vaccines]]
rule_id = "py-no-eval"
rule_file = "rules/py_no_eval.toml"
fixture_dir = "evals/fixtures/py-no-eval"
target_sample_path = "src/app.py"
severity = "high"
summary = "No eval."

[[vaccines]]
rule_id = "sh-no-sudo"
rule_file = "rules/sh_no_sudo.toml"
fixture_dir = "evals/fixtures/sh-no-sudo"
severity = "medium"
summary = "No sudo."
"""

PY_RULE = """\
[[ast_rules]]
id = "py-no-eval"
language = "python"
paths = ["**/*.py"]
check = "new_node_introduced"
query = '''(call function: (identifier) @fn (#eq? @fn "eval")) @target'''
severity = "steer"
message = "eval() introduced."
remediation = "Use ast.literal_eval."
"""

SH_RULE = """\
[[shell_rules]]
id = "sh-no-sudo"
pattern = '^sudo\\b'
severity = "hard_block"
message = "sudo is not allowed."
remediation = "Run without sudo."
"""

EVALS = """\
from pathlib import Path

from antibody.ast_engine import Outcome
from antibody.validator import runner

FIXTURES = Path(__file__).parent / "fixtures"


def test_py_no_eval_positive_antigen() -> None:
    assert runner.run_positive_antigen(FIXTURES / "py-no-eval") is Outcome.VIOLATION


def test_py_no_eval_negative_control() -> None:
    assert runner.run_negative_control(FIXTURES / "py-no-eval") is Outcome.ALLOW


def test_sh_no_sudo_positive_antigen() -> None:
    assert runner.run_positive_antigen(FIXTURES / "sh-no-sudo") is Outcome.VIOLATION


def test_sh_no_sudo_negative_control() -> None:
    assert runner.run_negative_control(FIXTURES / "sh-no-sudo") is Outcome.ALLOW
"""

FILES: dict[str, str] = {
    "rules/py_no_eval.toml": PY_RULE,
    "rules/sh_no_sudo.toml": SH_RULE,
    "patches/agents_md.md": "- Never call `eval()` on untrusted input.   \n- Never run `sudo`.\n",
    "evals/test_vaccines.py": EVALS,
    "evals/fixtures/py-no-eval/positive_before.py": "def load(s):\n    return int(s)\n",
    "evals/fixtures/py-no-eval/positive_after.py": "def load(s):\n    return eval(s)\n",
    "evals/fixtures/py-no-eval/negative_before.py": "def load(s):\n    return int(s)\n",
    "evals/fixtures/py-no-eval/negative_after.py": "def load(text):\n    return int(text)\n",
    "evals/fixtures/sh-no-sudo/positive.sh": "sudo rm -rf build\n",
    "evals/fixtures/sh-no-sudo/negative.sh": "rm -rf build\n",
}


def make_pack(root: Path, name: str = "demo", version: str = "0.1.0") -> Path:
    """A valid 2-vaccine pack (one AST rule, one shell rule) that passes all 5 gates."""
    (root).mkdir(parents=True, exist_ok=True)
    (root / "pack.toml").write_text(PACK_TOML.format(name=name, version=version))
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def make_registry(root: Path, *names: str, version: str = "0.1.0") -> Path:
    """A static registry dir: `packs/acme/<name>/` per name + an index.json from our hasher."""
    from antibody.registry.index_builder import build_index, write_index

    for name in names or ("demo",):
        make_pack(root / "packs" / "acme" / name, name=name, version=version)
    write_index(root, build_index(root, generated_at="2026-11-24T00:00:00Z"))
    return root


def reindex(root: Path) -> None:
    from antibody.registry.index_builder import build_index, write_index

    write_index(root, build_index(root, generated_at="2026-11-24T00:00:00Z"))
