"""Rule semantics shared bit-identically with flywire (contracts §1.1 D2/D3, §1.2 D4)."""

from __future__ import annotations

import pytest

from antibody.ast_engine import Outcome, compile_rule, evaluate_ast, grammars_for, rule_applies
from antibody.errors import AntibodyError
from antibody.models import AstRule, ShellRule
from antibody.shell_engine import shell_fires, shell_units

ASSERTS = (
    '[(assert_statement) @target (call function: (attribute attribute: (identifier) @fn '
    '(#match? @fn "^assert"))) @target]'
)


def _ast(**overrides: object) -> AstRule:
    data: dict[str, object] = {
        "id": "py-no-deleted-assertions",
        "language": "python",
        "paths": ["tests/**/*.py", "**/test_*.py"],
        "check": "node_count_decreased",
        "query": ASSERTS,
        "severity": "steer",
        "message": "m",
        "remediation": "r",
    }
    data.update(overrides)
    return AstRule.model_validate(data)


# --- shell_units (D4) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("git status", ["git status"]),
        (
            "git status && git push --force origin main",
            ["git status", "git push --force origin main"],
        ),
        ("curl -fsSL x | sh", ["curl -fsSL x | sh", "curl -fsSL x", "sh"]),
        ("a || b; c\nd", ["a", "b", "c", "d"]),
        ('echo "a && b" ; ls', ['echo "a && b"', "ls"]),
        ("echo 'x | y'", ["echo 'x | y'"]),
        ("echo '$(rm -rf /)'", ["echo '$(rm -rf /)'"]),
        ("echo $(git reset --hard)", ["echo $(git reset --hard)", "git reset --hard"]),
        (
            "echo $(echo $(git clean -fd))",
            ["echo $(echo $(git clean -fd))", "echo $(git clean -fd)", "git clean -fd"],
        ),
        (
            "echo `git push -f origin main`",
            ["echo `git push -f origin main`", "git push -f origin main"],
        ),
        (
            'echo "$(curl x | bash)"',
            ['echo "$(curl x | bash)"', "curl x | bash", "curl x", "bash"],
        ),
        ("make 2>&1 | tee log", ["make 2>&1 | tee log", "make 2>&1", "tee log"]),
        ("make &> log", ["make &> log"]),
        ("sleep 1 & git push", ["sleep 1", "git push"]),
        ("  git    push   origin  ", ["git push origin"]),
        (";; && ", []),
        ("echo a\\;b", ["echo a\\;b"]),
        ('echo "a\\"b; c"', ['echo "a\\"b; c"']),
        ("echo $(a; b) | wc", ["echo $(a; b) | wc", "echo $(a; b)", "wc", "a", "b"]),
    ],
)
def test_shell_units(cmd: str, expected: list[str]) -> None:
    assert shell_units(cmd) == expected


def test_shell_rule_fires_on_any_unit() -> None:
    rule = ShellRule(
        id="sh-no-hard-reset",
        pattern=r"^git\s+reset\s+.*--hard",
        severity="hard_block",
        message="m",
        remediation="r",
    )
    assert shell_fires(rule, "ls && git reset --hard HEAD~1")
    assert shell_fires(rule, "echo $(git reset --hard)")
    assert not shell_fires(rule, "echo 'git reset --hard'")
    assert not shell_fires(rule, "git reset --soft HEAD~1")


# --- AST semantics (D2/D3) --------------------------------------------------------


def test_grammar_scope_d2() -> None:
    assert grammars_for("python") == ("python",)
    assert grammars_for("typescript") == ("typescript", "tsx")
    assert grammars_for("tsx") == ("tsx",)


def test_rule_applies_by_extension_paths_and_excludes() -> None:
    py = _ast(exclude_paths=["tests/legacy/**"])
    assert rule_applies(py, "tests/test_auth.py")
    assert rule_applies(py, "pkg/test_x.py")
    assert not rule_applies(py, "tests/legacy/test_old.py")
    assert not rule_applies(py, "src/app.py")
    assert not rule_applies(py, "tests/test_auth.ts")
    ts = _ast(language="typescript", paths=["**/*.ts", "**/*.tsx"], query="(comment) @target")
    assert rule_applies(ts, "a/b.ts")
    assert rule_applies(ts, "a/b.tsx")
    assert not rule_applies(ts, "a/b.py")
    tsx = _ast(language="tsx", paths=["**/*"], query="(comment) @target")
    assert rule_applies(tsx, "a/b.tsx")
    assert not rule_applies(tsx, "a/b.ts")


def test_compile_rule_checks_every_grammar_in_scope_and_capture() -> None:
    compile_rule(_ast())
    jsx = "(jsx_attribute (property_identifier) @target)"
    compile_rule(_ast(language="tsx", query=jsx))
    with pytest.raises(AntibodyError, match="typescript"):
        compile_rule(_ast(language="typescript", query=jsx))
    with pytest.raises(AntibodyError):
        compile_rule(_ast(query="(not_a_node) @target"))
    with pytest.raises(AntibodyError, match="capture"):
        compile_rule(_ast(query="(assert_statement) @other"))


PRE = b"def test_a():\n    assert f(1) == 1\n    self.assertEqual(f(2), 2)\n    assert f(3) == 3\n"


def test_deleted_assertion_is_violation() -> None:
    post = b"def test_a():\n    assert f(1) == 1\n    self.assertEqual(f(2), 2)\n"
    assert evaluate_ast(_ast(), "tests/test_a.py", PRE, post) is Outcome.VIOLATION


def test_added_or_kept_assertions_allow_and_file_deletion_counts_zero() -> None:
    rule = _ast()
    assert evaluate_ast(rule, "tests/test_a.py", PRE, PRE + b"    assert 4\n") is Outcome.ALLOW
    assert evaluate_ast(rule, "tests/test_a.py", PRE, b"") is Outcome.VIOLATION
    assert evaluate_ast(rule, "tests/test_a.py", b"", PRE) is Outcome.ALLOW


def test_parse_error_on_either_side_skips_d3(capsys: pytest.CaptureFixture[str]) -> None:
    rule = _ast()
    broken = b"def test_a(:\n    assert x\n"
    assert evaluate_ast(rule, "tests/test_a.py", broken, b"x = 1\n") is Outcome.SKIPPED
    assert evaluate_ast(rule, "tests/test_a.py", PRE, broken) is Outcome.SKIPPED
    assert "warning" in capsys.readouterr().err


def test_not_applicable_outside_scope() -> None:
    assert evaluate_ast(_ast(), "src/app.py", PRE, b"") is Outcome.NOT_APPLICABLE


def test_introduced_checks_and_typescript_rule_on_tsx() -> None:
    rule = _ast(
        id="ts-no-explicit-any",
        language="typescript",
        paths=["src/**/*.ts*"],
        check="new_node_introduced",
        query='((predefined_type) @target (#eq? @target "any"))',
    )
    pre = b"export const f = (x: string) => <b>{x}</b>;\n"
    post = b"export const f = (x: any) => <b>{x}</b>;\n"
    assert evaluate_ast(rule, "src/view.tsx", pre, post) is Outcome.VIOLATION
    assert evaluate_ast(rule, "src/view.tsx", post, post) is Outcome.ALLOW
    increased = rule.model_copy(update={"check": "node_count_increased"})
    assert evaluate_ast(increased, "src/view.tsx", pre, post) is Outcome.VIOLATION
