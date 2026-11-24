"""Turn one extracted failure into a complete draft pack, linted before it reaches `--out`.

The pack is written into a sibling staging dir, run through all 5 lint gates, and only
then moved into place with `os.replace`, so a failing draft never leaves files behind.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tomlkit

from antibody.ast_engine import rule_applies
from antibody.distiller.extractor import Failure, fixture_pair
from antibody.distiller.templates import Template, grammar_of, signals
from antibody.errors import AntibodyError
from antibody.models import AstRule, Rule, ShellRule
from antibody.shell_engine import shell_units
from antibody.validator.linter import LintReport, lint_pack

NEGATIVE_COMMENT = {
    "python": "# antibody: negative control (benign comment edit)\n",
    "typescript": "// antibody: negative control (benign comment edit)\n",
    "tsx": "// antibody: negative control (benign comment edit)\n",
}
SHELL_NEGATIVE = "echo antibody-negative-control\n"

EVALS = '''"""Antigen/antibody micro-evals for {pack_id} (run only by `antibody test`)."""

from pathlib import Path

from antibody.ast_engine import Outcome
from antibody.validator import runner

FIXTURE = Path(__file__).parent / "fixtures" / "{rule_id}"


def test_{snake}_positive_antigen() -> None:
    assert runner.run_positive_antigen(FIXTURE) is Outcome.VIOLATION


def test_{snake}_negative_control() -> None:
    assert runner.run_negative_control(FIXTURE) is Outcome.ALLOW
'''


@dataclass(frozen=True)
class Draft:
    rule: Rule
    target_sample_path: str | None
    fixtures: dict[str, str]
    pack_language: str
    category: str
    severity: str
    cwe: str | None
    summary: str
    agents_md: str


def rule_id_for(key: str, failure: Failure) -> str:
    material = "\x00".join(
        (failure.path or "", failure.before, failure.after, failure.command or "")
    )
    return f"distilled-{key}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:8]}"


def ast_fixtures(failure: Failure, before: str, after: str) -> dict[str, str]:
    path = failure.path or ""
    grammar = grammar_of(path) or "python"
    ext = PurePosixPath(path).suffix
    baseline = before if before.endswith("\n") or not before else before + "\n"
    return {
        f"positive_before{ext}": before,
        f"positive_after{ext}": after,
        f"negative_before{ext}": baseline,
        f"negative_after{ext}": baseline + NEGATIVE_COMMENT[grammar],
    }


def draft_from_template(template: Template, failure: Failure) -> Draft:
    path = failure.path or ""
    rule = AstRule(
        id=rule_id_for(template.key, failure),
        language="typescript" if template.language == "typescript" else "python",
        paths=template.paths,
        check="node_count_decreased"
        if template.check == "node_count_decreased"
        else "new_node_introduced",
        query=template.query,
        severity="steer",
        message=template.message,
        remediation=template.remediation,
    )
    if not rule_applies(rule, path):
        rule = rule.model_copy(update={"paths": (*template.paths, path)})
    before, after = fixture_pair(failure, lambda b, a: signals(template, path, b, a))
    return Draft(
        rule=rule,
        target_sample_path=path,
        fixtures=ast_fixtures(failure, before, after),
        pack_language=rule.language,
        category=template.category,
        severity=template.severity,
        cwe=template.cwe,
        summary=template.summary,
        agents_md=template.agents_line + "\n",
    )


def draft_from_command(failure: Failure) -> Draft:
    command = failure.command or ""
    # ponytail: blocks the longest unit of the blocked command verbatim; --llm for generalisation.
    unit = max(shell_units(command), key=len)
    rule = ShellRule(
        id=rule_id_for("shell", failure),
        pattern="^" + re.escape(unit) + r"(?:\s|$)",
        severity="hard_block",
        message=f"Blocked command recurred: {unit}",
        remediation="Do not re-run a command that was already blocked; find a safe alternative.",
    )
    return Draft(
        rule=rule,
        target_sample_path=None,
        fixtures={"positive.sh": command + "\n", "negative.sh": SHELL_NEGATIVE},
        pack_language="shell",
        category="destructive-ops",
        severity="high",
        cwe=None,
        summary="Distilled: blocks a shell command that an agent was already stopped from running.",
        agents_md=f"- Never run `{unit}`.\n",
    )


def _dump_rule(rule: Rule) -> str:
    key = "ast_rules" if isinstance(rule, AstRule) else "shell_rules"
    data: dict[str, Any] = {
        k: list(v) if isinstance(v, tuple) else v for k, v in rule.model_dump().items()
    }
    return tomlkit.dumps({key: [data]})


def _dump_manifest(draft: Draft) -> str:
    rule_id = draft.rule.id
    vaccine: dict[str, Any] = {
        "rule_id": rule_id,
        "rule_file": f"rules/{rule_id.replace('-', '_')}.toml",
        "fixture_dir": f"evals/fixtures/{rule_id}",
    }
    if draft.target_sample_path is not None:
        vaccine["target_sample_path"] = draft.target_sample_path
    vaccine["severity"] = draft.severity
    if draft.cwe:
        vaccine["cwe"] = draft.cwe
    vaccine["summary"] = draft.summary
    pack = {
        "namespace": "community",
        "name": rule_id,
        "version": "0.1.0",
        "description": draft.summary,
        "authors": ["antibody-distill"],
        "license": "MIT",
        "languages": [draft.pack_language],
        "categories": [draft.category],
        "flywire_min_version": "0.1.0",
    }
    return tomlkit.dumps({"pack": pack, "vaccines": [vaccine]})


def write_draft(dest: Path, draft: Draft) -> None:
    rule_id = draft.rule.id
    snake = rule_id.replace("-", "_")
    files = {
        "pack.toml": _dump_manifest(draft),
        f"rules/{snake}.toml": _dump_rule(draft.rule),
        "patches/agents_md.md": draft.agents_md,
        "evals/test_vaccines.py": EVALS.format(
            pack_id=f"community/{rule_id}", rule_id=rule_id, snake=snake
        ),
        **{f"evals/fixtures/{rule_id}/{name}": text for name, text in draft.fixtures.items()},
    }
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def ensure_out_available(out: Path) -> None:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise AntibodyError(f"--out {out} already exists and is not empty")


def publish(draft: Draft, out: Path) -> LintReport:
    """Stage, lint, and atomically move the draft to `out` only if every gate passes."""
    ensure_out_available(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".antibody-distill-") as tmp:
        staged = Path(tmp) / "pack"
        write_draft(staged, draft)
        report = lint_pack(staged)
        if report.ok:
            if out.exists():
                out.rmdir()
            os.replace(staged, out)
    return LintReport(out, report.results)
