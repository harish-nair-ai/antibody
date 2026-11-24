"""Antigen/antibody micro-evals for core/python-safety (run only by `antibody test`)."""

from pathlib import Path

from antibody.ast_engine import Outcome
from antibody.validator import runner

FIXTURES = Path(__file__).parent / "fixtures"


def test_py_no_deleted_assertions_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-deleted-assertions"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_deleted_assertions_negative_control() -> None:
    fixture = FIXTURES / "py-no-deleted-assertions"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_py_no_bare_except_pass_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-bare-except-pass"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_bare_except_pass_negative_control() -> None:
    fixture = FIXTURES / "py-no-bare-except-pass"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_py_no_ssl_verify_false_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-ssl-verify-false"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_ssl_verify_false_negative_control() -> None:
    fixture = FIXTURES / "py-no-ssl-verify-false"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_py_no_shell_true_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-shell-true"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_shell_true_negative_control() -> None:
    fixture = FIXTURES / "py-no-shell-true"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_py_no_lint_suppression_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-lint-suppression"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_lint_suppression_negative_control() -> None:
    fixture = FIXTURES / "py-no-lint-suppression"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_py_no_sql_fstring_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-sql-fstring"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_sql_fstring_negative_control() -> None:
    fixture = FIXTURES / "py-no-sql-fstring"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_py_no_eval_exec_positive_antigen() -> None:
    fixture = FIXTURES / "py-no-eval-exec"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_py_no_eval_exec_negative_control() -> None:
    fixture = FIXTURES / "py-no-eval-exec"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW
