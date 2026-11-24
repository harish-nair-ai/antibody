"""Antigen/antibody micro-evals for core/typescript-safety (run only by `antibody test`)."""

from pathlib import Path

from antibody.ast_engine import Outcome
from antibody.validator import runner

FIXTURES = Path(__file__).parent / "fixtures"


def test_ts_no_deleted_expects_positive_antigen() -> None:
    fixture = FIXTURES / "ts-no-deleted-expects"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_ts_no_deleted_expects_negative_control() -> None:
    fixture = FIXTURES / "ts-no-deleted-expects"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_ts_no_skipped_tests_positive_antigen() -> None:
    fixture = FIXTURES / "ts-no-skipped-tests"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_ts_no_skipped_tests_negative_control() -> None:
    fixture = FIXTURES / "ts-no-skipped-tests"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_ts_no_ts_ignore_positive_antigen() -> None:
    fixture = FIXTURES / "ts-no-ts-ignore"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_ts_no_ts_ignore_negative_control() -> None:
    fixture = FIXTURES / "ts-no-ts-ignore"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_ts_no_explicit_any_positive_antigen() -> None:
    fixture = FIXTURES / "ts-no-explicit-any"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_ts_no_explicit_any_negative_control() -> None:
    fixture = FIXTURES / "ts-no-explicit-any"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_ts_no_dangerously_set_html_positive_antigen() -> None:
    fixture = FIXTURES / "ts-no-dangerously-set-html"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_ts_no_dangerously_set_html_negative_control() -> None:
    fixture = FIXTURES / "ts-no-dangerously-set-html"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW


def test_ts_no_empty_catch_positive_antigen() -> None:
    fixture = FIXTURES / "ts-no-empty-catch"
    assert runner.run_positive_antigen(fixture) is Outcome.VIOLATION


def test_ts_no_empty_catch_negative_control() -> None:
    fixture = FIXTURES / "ts-no-empty-catch"
    assert runner.run_negative_control(fixture) is Outcome.ALLOW
