from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

GIT_ENV = {
    "GIT_AUTHOR_NAME": "antibody-tests",
    "GIT_AUTHOR_EMAIL": "tests@antibody.invalid",
    "GIT_COMMITTER_NAME": "antibody-tests",
    "GIT_COMMITTER_EMAIL": "tests@antibody.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real ~/.cache, user git config, registry env, or API keys."""
    home = tmp_path_factory.mktemp("env")
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    gitconfig = home / "gitconfig"
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    for key in (
        "ANTIBODY_REGISTRY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=os.environ.copy()
    )
    return out.stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh git repo used as the antibody workspace root (cwd)."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    monkeypatch.chdir(root)
    yield root
