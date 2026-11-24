"""CLI surface: --version/--help, search, lint/test exit codes, no tracebacks (contracts §0)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from helpers import make_pack
from typer.testing import CliRunner

from antibody.cli import app

COMMANDS = ["search", "pull", "remove", "sync", "verify", "test", "lint", "distill"]
ANTIBODY = Path(sys.executable).parent / "antibody"


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == "antibody 0.1.0\n"


@pytest.mark.parametrize("command", [None, *COMMANDS])
def test_help_on_every_command(command: str | None) -> None:
    args = [command, "--help"] if command else ["--help"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_search_bundled_registry(workspace: Path) -> None:
    runner = CliRunner()
    everything = runner.invoke(app, ["search"])
    assert everything.exit_code == 0
    lines = everything.stdout.splitlines()
    assert [line.split()[0] for line in lines] == [
        "core/git-hygiene",
        "core/python-safety",
        "core/typescript-safety",
    ]
    assert "0.1.0" in lines[1] and "7 rules" in lines[1]
    ts = runner.invoke(app, ["search", "--lang", "tsx"])
    assert ts.stdout.split()[0] == "core/typescript-safety"
    none = runner.invoke(app, ["search", "zzz-no-match"])
    assert none.exit_code == 0
    assert none.stdout == ""


def test_lint_exit_codes(tmp_path: Path) -> None:
    runner = CliRunner()
    pack = make_pack(tmp_path / "p")
    ok = runner.invoke(app, ["lint", str(pack)])
    assert ok.exit_code == 0, ok.stdout
    assert "PASS" in ok.stdout and "FAIL" not in ok.stdout
    (pack / "evals/fixtures/sh-no-sudo/positive.sh").write_text("ls\n")
    bad = runner.invoke(app, ["lint", str(pack)])
    assert bad.exit_code == 11
    assert "FAIL positive_antigen sh-no-sudo" in bad.stdout
    missing = runner.invoke(app, ["lint", str(tmp_path / "nope")])
    assert missing.exit_code == 2


def test_test_command_runs_gates_and_real_pytest_evals(workspace: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    pack = make_pack(tmp_path / "p")
    ok = runner.invoke(app, ["test", str(pack)])
    assert ok.exit_code == 0, ok.stdout + ok.stderr
    assert "4 passed" in ok.stdout
    (pack / "evals/test_vaccines.py").write_text(
        (pack / "evals/test_vaccines.py").read_text() + "\n\ndef test_extra() -> None:\n"
        "    assert False\n"
    )
    failing = runner.invoke(app, ["test", str(pack)])
    assert failing.exit_code == 11
    assert "1 failed" in failing.stdout
    assert failing.stderr.startswith(f"error: {pack}: evals failed: ")
    assert "test_extra" in failing.stderr
    (pack / "evals/fixtures/sh-no-sudo/positive.sh").write_text("ls\n")
    gate = runner.invoke(app, ["test", str(pack)])
    assert f"{pack}: gate positive_antigen sh-no-sudo failed" in gate.stderr


def test_test_command_on_installed_packs(workspace: Path) -> None:
    runner = CliRunner()
    nothing = runner.invoke(app, ["test"])
    assert nothing.exit_code == 2
    assert runner.invoke(app, ["pull", "core/git-hygiene"]).exit_code == 0
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "core/git-hygiene" in result.stdout
    assert runner.invoke(app, ["verify"]).exit_code == 0


def test_verify_without_lockfile(workspace: Path) -> None:
    result = CliRunner().invoke(app, ["verify"])
    assert result.exit_code == 2
    assert result.stderr.startswith("error: ")


def test_workspace_root_is_discovered_from_subdirectory(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = workspace / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert CliRunner().invoke(app, ["pull", "core/git-hygiene"]).exit_code == 0
    assert (workspace / "antibody.lock").is_file()
    assert (workspace / ".flywire/packs/core/git-hygiene/pack.toml").is_file()
    assert not (sub / "antibody.lock").exists()


def test_real_console_script_errors_have_no_traceback(workspace: Path) -> None:
    version = subprocess.run([ANTIBODY, "--version"], capture_output=True, text=True, check=False)
    assert (version.returncode, version.stdout) == (0, "antibody 0.1.0\n")
    missing = subprocess.run(
        [ANTIBODY, "pull", "core/nope"], capture_output=True, text=True, check=False
    )
    assert missing.returncode == 2
    assert missing.stderr.startswith("error: ")
    assert "Traceback" not in missing.stderr
    (workspace / "antibody.lock").write_text("garbage = [")
    corrupt = subprocess.run([ANTIBODY, "verify"], capture_output=True, text=True, check=False)
    assert corrupt.returncode == 2
    assert "Traceback" not in corrupt.stderr


def test_unexpected_exception_is_reported_as_internal_error(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import antibody.cli as cli

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "read_lockfile", boom)
    result = CliRunner().invoke(app, ["verify"])
    assert result.exit_code == 1
    assert result.stderr == "error: internal: RuntimeError: disk on fire\n"
