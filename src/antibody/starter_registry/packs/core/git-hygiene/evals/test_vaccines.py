"""Antigen/antibody micro-evals for core/git-hygiene (run only by `antibody test`)."""

from pathlib import Path

from antibody.ast_engine import Outcome
from antibody.validator import runner

FIXTURES = Path(__file__).parent / "fixtures"


def test_sh_no_force_push_positive_antigen() -> None:
    fixture = FIXTURES / "sh-no-force-push"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_sh_no_force_push_negative_control() -> None:
    fixture = FIXTURES / "sh-no-force-push"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_sh_no_skip_hooks_positive_antigen() -> None:
    fixture = FIXTURES / "sh-no-skip-hooks"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_sh_no_skip_hooks_negative_control() -> None:
    fixture = FIXTURES / "sh-no-skip-hooks"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_sh_no_hard_reset_positive_antigen() -> None:
    fixture = FIXTURES / "sh-no-hard-reset"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_sh_no_hard_reset_negative_control() -> None:
    fixture = FIXTURES / "sh-no-hard-reset"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_sh_no_destructive_rm_positive_antigen() -> None:
    fixture = FIXTURES / "sh-no-destructive-rm"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_sh_no_destructive_rm_negative_control() -> None:
    fixture = FIXTURES / "sh-no-destructive-rm"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_sh_no_pipe_to_shell_positive_antigen() -> None:
    fixture = FIXTURES / "sh-no-pipe-to-shell"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_sh_no_pipe_to_shell_negative_control() -> None:
    fixture = FIXTURES / "sh-no-pipe-to-shell"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW
