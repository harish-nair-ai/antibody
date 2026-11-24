"""Trace-to-vaccine distiller (spec §6, contracts §6, §7.6) against real tapes, git, and HTTP."""

from __future__ import annotations

import difflib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import git
from tapes import write_flight_db, write_tape
from typer.testing import CliRunner

from antibody.cli import app
from antibody.registry.hasher import tree_sha256

SECRET = "sk-ant-abcdefghijklmnopqrstuvwxyz0123"


def _auth_test(repo_root: str) -> str:
    return f'''from app.auth import login

FIXTURES = "{repo_root}/fixtures"


def test_login_ok():
    result = login("alice", "secret")
    assert result.ok
    assert result.user == "alice"


def test_login_rejects_bad_password():
    token = "{SECRET}"
    home = "/Users/alice/projects/app/users.json"
    result = login("alice", "wrong", token=token, users=home)
    assert result.ok is False
    assert result.error == "invalid credentials"
    assert result.attempts == 1
'''


def _commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD^{tree}")


@dataclass
class Scenario:
    repo: Path
    tape: Path
    pre: str
    post: str
    diff: str


def _steered_deletion(tmp_path: Path, name: str = "session.tape") -> Scenario:
    """A real repo + tape whose step 1 is a STEERed (D12) assertion-deleting write."""
    repo = tmp_path / "agent-repo"
    (repo / "tests").mkdir(parents=True)
    git(repo, "init", "-q")
    original = _auth_test(str(repo))
    (repo / "tests/test_auth.py").write_text(original)
    (repo / "README.md").write_text("demo\n")
    pre = _commit_all(repo, "base")
    mutated = original.replace('    assert result.error == "invalid credentials"\n', "")
    (repo / "tests/test_auth.py").write_text(mutated)
    post = _commit_all(repo, "agent proposal")
    diff = git(repo, "diff-tree", "-p", pre, post) + "\n"
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    tape = write_tape(
        tmp_path / name,
        str(repo),
        git(repo, "rev-parse", "HEAD"),
        [
            {"kind": "llm_call", "pre": pre, "post": pre},
            {
                "status": "steered",
                "pre": pre,
                "post": pre,
                "diff": diff,
                "tool": (
                    "write_file",
                    {"path": "tests/test_auth.py", "content": "..."},
                    "[FLYWIRE: py-no-deleted-assertions] Test assertions were removed.\n"
                    "Action required: Restore the deleted assertions.",
                ),
            },
        ],
    )
    return Scenario(repo, tape, pre, post, diff)


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def _read_tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*") if p.is_file()}


# --- spec §8 acceptance ---------------------------------------------------------------


def test_distill_from_tape_with_deleted_assertion_produces_lint_clean_pack(
    workspace: Path, tmp_path: Path
) -> None:
    scenario = _steered_deletion(tmp_path)
    out = tmp_path / "draft"
    result = _invoke("distill", "--from-tape", str(scenario.tape), "--out", str(out))
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "passes all 5 gates" in result.stdout
    lint = _invoke("lint", str(out))
    assert lint.exit_code == 0, lint.stdout
    evals = _invoke("test", str(out))
    assert evals.exit_code == 0, evals.stdout + evals.stderr
    assert "2 passed" in evals.stdout

    manifest = tomllib.loads((out / "pack.toml").read_text())
    vaccine = manifest["vaccines"][0]
    assert vaccine["target_sample_path"] == "tests/test_auth.py"
    rule_file = tomllib.loads((out / vaccine["rule_file"]).read_text())
    rule = rule_file["ast_rules"][0]
    assert rule["id"] == vaccine["rule_id"]
    assert rule["check"] == "node_count_decreased"
    assert rule["language"] == "python"
    assert (out / "patches/agents_md.md").read_text().startswith("- ")

    files = _read_tree(out)
    fixtures = {k: v for k, v in files.items() if "/fixtures/" in k}
    assert len(fixtures) == 4
    before = files[f"{vaccine['fixture_dir']}/positive_before.py"]
    after = files[f"{vaccine['fixture_dir']}/positive_after.py"]
    assert "def test_login_rejects_bad_password" in before
    assert "def test_login_ok" not in before, "fixture must be the enclosing-function slice"
    assert before.count("assert ") == after.count("assert ") + 1
    blob = "".join(files.values())
    assert SECRET not in blob and "<REDACTED_SECRET>" in before
    assert str(scenario.repo) not in blob
    assert "/Users/alice" not in blob


def test_distill_is_deterministic(workspace: Path, tmp_path: Path) -> None:
    scenario = _steered_deletion(tmp_path)
    for name in ("a", "b"):
        result = _invoke(
            "distill", "--from-tape", str(scenario.tape), "--out", str(tmp_path / name)
        )
        assert result.exit_code == 0, result.stdout + result.stderr
    assert tree_sha256(tmp_path / "a") == tree_sha256(tmp_path / "b")


def test_distill_reconstructs_from_hunks_when_repo_is_gone(workspace: Path, tmp_path: Path) -> None:
    before = "def test_x():\n    assert a\n    assert b\n    assert c\n"
    after = "def test_x():\n    assert a\n    assert c\n"
    diff = _diff("tests/test_x.py", before, after)
    tape = write_tape(
        tmp_path / "t.tape",
        str(tmp_path / "vanished"),
        "0" * 40,
        [{"status": "ok", "pre": "1" * 40, "post": "2" * 40, "diff": diff}],
    )
    out = tmp_path / "draft"
    result = _invoke("distill", "--from-tape", str(tape), "--out", str(out))
    assert result.exit_code == 0, result.stdout + result.stderr
    fixture = out / "evals/fixtures"
    (rule_dir,) = fixture.iterdir()
    assert (rule_dir / "positive_before.py").read_text() == before
    assert (rule_dir / "positive_after.py").read_text() == after


def test_step_selection_and_errors(workspace: Path, tmp_path: Path) -> None:
    scenario = _steered_deletion(tmp_path)
    tape = str(scenario.tape)
    selected = _invoke(
        "distill", "--from-tape", tape, "--out", str(tmp_path / "s1b"), "--step", "1"
    )
    assert selected.exit_code == 0, selected.stdout + selected.stderr
    nothing = _invoke("distill", "--from-tape", tape, "--out", str(tmp_path / "s0"), "--step", "0")
    assert nothing.exit_code == 14
    assert not (tmp_path / "s0").exists()
    invalid = _invoke("distill", "--from-tape", tape, "--out", str(tmp_path / "s9"), "--step", "9")
    assert invalid.exit_code == 2


def test_tape_without_failures_exits_14(workspace: Path, tmp_path: Path) -> None:
    diff = _diff("src/a.py", "x = 1\n", "x = 2\n")
    tape = write_tape(
        tmp_path / "clean.tape",
        str(tmp_path),
        "0" * 40,
        [{"status": "ok", "pre": "1" * 40, "post": "2" * 40, "diff": diff}],
    )
    result = _invoke("distill", "--from-tape", str(tape), "--out", str(tmp_path / "out"))
    assert result.exit_code == 14
    assert result.stderr.startswith("error: ")
    assert not (tmp_path / "out").exists()


def test_bad_arguments_exit_2(workspace: Path, tmp_path: Path) -> None:
    scenario = _steered_deletion(tmp_path)
    out = tmp_path / "busy"
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    busy = _invoke("distill", "--from-tape", str(scenario.tape), "--out", str(out))
    assert busy.exit_code == 2
    assert (out / "keep.txt").read_text() == "mine"
    assert _invoke("distill", "--out", str(tmp_path / "x")).exit_code == 2
    both = _invoke(
        "distill",
        "--from-tape",
        str(scenario.tape),
        "--from-diff",
        str(scenario.tape),
        "--out",
        str(tmp_path / "x"),
    )
    assert both.exit_code == 2
    missing = _invoke("distill", "--from-tape", str(tmp_path / "nope.tape"), "--out", "x")
    assert missing.exit_code == 2
    assert not (tmp_path / "nope.tape").exists()


# --- signatures (c) via --from-diff ------------------------------------------------------


def _diff(path: str, before: str, after: str) -> str:
    body = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return f"diff --git a/{path} b/{path}\n" + "".join(body)


SIGNATURES = [
    (
        "src/http.py",
        "def get(u):\n    return requests.get(u)\n",
        "def get(u):\n    return requests.get(u, verify=False)\n",
        "verify",
    ),
    (
        "src/run.py",
        "def run(c):\n    return subprocess.run(c)\n",
        "def run(c):\n    return subprocess.run(c, shell=True)\n",
        "shell",
    ),
    (
        "src/lint.py",
        "def f(x):\n    return x\n",
        "def f(x):  # noqa: ANN001\n    return x\n",
        "noqa",
    ),
    (
        "src/cfg.py",
        "def load():\n    try:\n        return read()\n    except OSError:\n        return {}\n",
        "def load():\n    try:\n        return read()\n    except Exception:\n        pass\n",
        "pass_statement",
    ),
    (
        "src/api.py",
        "def user():\n    return fetch_user()\n",
        "def user():\n    return MOCK_USER\n",
        "MOCK_",
    ),
    (
        "src/port.ts",
        "export function p(v: string): number {\n  return Number(v);\n}\n",
        "export function p(v: string): number {\n  // @ts-ignore\n  return v * 1;\n}\n",
        "@ts-",
    ),
    (
        "src/math.test.ts",
        'it("adds", () => {\n  expect(add(1, 2)).toBe(3);\n  expect(add(2, 2)).toBe(4);\n});\n',
        'it("adds", () => {\n  expect(add(1, 2)).toBe(3);\n});\n',
        "expect",
    ),
]


@pytest.mark.parametrize(("path", "before", "after", "needle"), SIGNATURES)
def test_from_diff_detects_anti_patterns(
    workspace: Path, tmp_path: Path, path: str, before: str, after: str, needle: str
) -> None:
    patch = tmp_path / "change.patch"
    patch.write_text(_diff(path, before, after))
    out = tmp_path / "draft"
    result = _invoke("distill", "--from-diff", str(patch), "--out", str(out))
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _invoke("lint", str(out)).exit_code == 0
    (rule_path,) = (out / "rules").iterdir()
    assert needle in rule_path.read_text()
    manifest = tomllib.loads((out / "pack.toml").read_text())
    assert manifest["vaccines"][0]["target_sample_path"] == path


def test_from_diff_uses_git_head_content_for_full_file(workspace: Path, tmp_path: Path) -> None:
    lines = [f"def f{i}():\n    return {i}\n\n\n" for i in range(12)]
    original = "import subprocess\n\n\n" + "".join(lines)
    (workspace / "src").mkdir()
    (workspace / "src/big.py").write_text(original)
    _commit_all(workspace, "base")
    mutated = original.replace(
        "def f6():\n    return 6\n", "def f6():\n    return subprocess.run('x', shell=True)\n"
    )
    patch = tmp_path / "p.patch"
    patch.write_text(_diff("src/big.py", original, mutated))
    out = tmp_path / "draft"
    result = _invoke("distill", "--from-diff", str(patch), "--out", str(out))
    assert result.exit_code == 0, result.stdout + result.stderr
    (rule_dir,) = (out / "evals/fixtures").iterdir()
    sliced = (rule_dir / "positive_after.py").read_text()
    assert "def f6" in sliced and "def f0" not in sliced


# --- --from-flight-db ------------------------------------------------------------------------


def test_from_flight_db_file_write_and_shell_events(workspace: Path, tmp_path: Path) -> None:
    (workspace / "src").mkdir()
    (workspace / "src/deploy.py").write_text(
        "import subprocess\n\n\ndef deploy():\n    subprocess.run(['make', 'deploy'])\n"
    )
    db = write_flight_db(
        workspace / ".flywire/flight_log.db",
        str(workspace),
        [
            {"tool": "read_file", "verdict": "ALLOW", "args": {"path": "src/deploy.py"}},
            {
                "tool": "str_replace_editor",
                "verdict": "STEER",
                "rule_id": "py-no-shell-true",
                "target_path": "src/deploy.py",
                "args": {
                    "path": "src/deploy.py",
                    "old_str": "subprocess.run(['make', 'deploy'])",
                    "new_str": "subprocess.run('make deploy', shell=True)",
                },
            },
            {
                "tool": "bash",
                "op_class": "GIT_MUTATE",
                "verdict": "HARD_BLOCK",
                "rule_id": "sh-no-force-push",
                "args": {"command": "cd app && git push --force origin main"},
            },
        ],
    )
    out = tmp_path / "ast"
    result = _invoke("distill", "--from-flight-db", str(db), "--out", str(out))
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "shell" in next((out / "rules").iterdir()).read_text()

    shell_out = tmp_path / "shell"
    shell = _invoke("distill", "--from-flight-db", str(db), "--out", str(shell_out), "--step", "2")
    assert shell.exit_code == 0, shell.stdout + shell.stderr
    (fixture,) = (shell_out / "evals/fixtures").iterdir()
    assert (fixture / "positive.sh").read_text() == "cd app && git push --force origin main\n"
    rule = tomllib.loads(next((shell_out / "rules").iterdir()).read_text())
    assert "shell_rules" in rule
    assert _invoke("test", str(shell_out)).exit_code == 0
    allowed = _invoke(
        "distill", "--from-flight-db", str(db), "--out", str(tmp_path / "r"), "--step", "0"
    )
    assert allowed.exit_code == 14
