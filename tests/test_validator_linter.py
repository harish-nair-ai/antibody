"""5-gate dual-assertion linter + runner (spec §5.2, contracts §7.1/§7.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import make_pack

from antibody.ast_engine import Outcome
from antibody.validator import runner
from antibody.validator.linter import GATES, lint_pack


def _failed(pack: Path) -> set[tuple[str, str]]:
    return {(r.gate, r.subject) for r in lint_pack(pack).results if not r.ok}


def test_valid_pack_passes_all_five_gates_for_every_vaccine(tmp_path: Path) -> None:
    report = lint_pack(make_pack(tmp_path / "p"))
    assert report.ok, report.results
    seen = {(r.gate, r.subject) for r in report.results}
    for gate in GATES:
        assert (gate, "py-no-eval") in seen
        assert (gate, "sh-no-sudo") in seen
    latency = [r for r in report.results if r.gate == "latency"]
    assert all("median" in r.detail and "p99" in r.detail for r in latency)


def test_positive_antigen_that_does_not_trigger_fails(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    (pack / "evals/fixtures/py-no-eval/positive_after.py").write_text("def load(s):\n    return 1\n")
    (pack / "evals/fixtures/sh-no-sudo/positive.sh").write_text("ls\n")
    assert _failed(pack) == {
        ("positive_antigen", "py-no-eval"),
        ("positive_antigen", "sh-no-sudo"),
        ("latency", "py-no-eval"),
        ("latency", "sh-no-sudo"),
    }


def test_negative_control_false_positive_fails(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    (pack / "evals/fixtures/py-no-eval/negative_after.py").write_text("x = eval('1')\n")
    (pack / "evals/fixtures/sh-no-sudo/negative.sh").write_text("sudo ls\n")
    failed = _failed(pack)
    assert ("negative_control", "py-no-eval") in failed
    assert ("negative_control", "sh-no-sudo") in failed
    assert ("positive_antigen", "py-no-eval") not in failed


def test_fixture_parse_error_is_a_failure_not_a_skip(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    (pack / "evals/fixtures/py-no-eval/negative_after.py").write_text("def load(:\n")
    assert ("negative_control", "py-no-eval") in _failed(pack)


def test_missing_fixture_fails(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    (pack / "evals/fixtures/py-no-eval/positive_before.py").unlink()
    (pack / "evals/fixtures/sh-no-sudo/negative.sh").unlink()
    failed = _failed(pack)
    assert ("positive_antigen", "py-no-eval") in failed
    assert ("negative_control", "sh-no-sudo") in failed


def test_target_sample_path_outside_rule_scope_fails_positive(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    rule = pack / "rules/py_no_eval.toml"
    rule.write_text(rule.read_text().replace('["**/*.py"]', '["tests/**/*.py"]'))
    assert ("positive_antigen", "py-no-eval") in _failed(pack)


def test_query_that_does_not_compile_fails_ast_gate(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    rule = pack / "rules/py_no_eval.toml"
    rule.write_text(rule.read_text().replace("(call function:", "(kall function:"))
    failed = _failed(pack)
    assert ("ast_compile", "py-no-eval") in failed
    assert not any(gate == "positive_antigen" and s == "py-no-eval" for gate, s in failed)


@pytest.mark.parametrize(
    ("rel", "old", "new", "subject"),
    [
        ("pack.toml", 'version = "0.1.0"', 'version = "one"', "pack"),
        ("pack.toml", "rules/py_no_eval.toml", "../outside.toml", "py-no-eval"),
        ("pack.toml", "evals/fixtures/py-no-eval", "../../etc", "py-no-eval"),
        ("pack.toml", 'target_sample_path = "src/app.py"\n', "", "py-no-eval"),
        ("rules/py_no_eval.toml", 'id = "py-no-eval"', 'id = "py-other"', "py-no-eval"),
        ("rules/sh_no_sudo.toml", "pattern = '^sudo\\b'", "pattern = '(sudo'", "sh-no-sudo"),
    ],
)
def test_schema_gate_failures(tmp_path: Path, rel: str, old: str, new: str, subject: str) -> None:
    pack = make_pack(tmp_path / "p")
    path = pack / rel
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new))
    assert ("schema", subject) in _failed(pack)


def test_rule_file_with_two_rules_and_unreferenced_rule_file_fail_schema(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    sh = pack / "rules/sh_no_sudo.toml"
    sh.write_text(sh.read_text() + sh.read_text().replace("sh-no-sudo", "sh-no-su"))
    assert ("schema", "sh-no-sudo") in _failed(pack)
    pack2 = make_pack(tmp_path / "p2")
    (pack2 / "rules/extra.toml").write_text((pack2 / "rules/sh_no_sudo.toml").read_text())
    assert ("schema", "pack") in _failed(pack2)


def test_shell_vaccine_must_not_declare_target_sample_path(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    manifest = pack / "pack.toml"
    manifest.write_text(
        manifest.read_text().replace(
            'fixture_dir = "evals/fixtures/sh-no-sudo"\n',
            'fixture_dir = "evals/fixtures/sh-no-sudo"\ntarget_sample_path = "x.py"\n',
        )
    )
    assert ("schema", "sh-no-sudo") in _failed(pack)


def test_latency_gate_fails_for_a_slow_evaluation(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    fixtures = pack / "evals/fixtures/py-no-eval"
    body = "".join(f"def f{i}(s):\n    return int(s) + {i}\n" for i in range(30000))
    (fixtures / "positive_before.py").write_text(body)
    (fixtures / "positive_after.py").write_text(body + "x = eval('1')\n")
    report = lint_pack(pack)
    latency = next(r for r in report.results if r.gate == "latency" and r.subject == "py-no-eval")
    assert not latency.ok
    assert not report.ok


def test_runner_public_functions_and_real_pytest_subprocess(tmp_path: Path) -> None:
    pack = make_pack(tmp_path / "p")
    fixtures = pack / "evals/fixtures"
    assert runner.run_positive_antigen(fixtures / "py-no-eval") is Outcome.VIOLATION
    assert runner.run_negative_control(fixtures / "py-no-eval") is Outcome.ALLOW
    assert runner.run_positive_antigen(fixtures / "sh-no-sudo") is Outcome.VIOLATION
    assert runner.run_negative_control(fixtures / "sh-no-sudo") is Outcome.ALLOW
    ok = runner.run_pack_evals(pack)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "4 passed" in ok.stdout
    assert not (pack / ".pytest_cache").exists()
    (fixtures / "sh-no-sudo/negative.sh").write_text("sudo ls\n")
    bad = runner.run_pack_evals(pack)
    assert bad.returncode != 0
    assert "1 failed" in bad.stdout


def test_run_pack_evals_ignores_host_pytest_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--definitely-not-an-option"\n'
    )
    (tmp_path / "conftest.py").write_text("raise RuntimeError('host conftest must not load')\n")
    pack = make_pack(tmp_path / "vendor/p")
    result = runner.run_pack_evals(pack)
    assert result.returncode == 0, result.stdout + result.stderr
