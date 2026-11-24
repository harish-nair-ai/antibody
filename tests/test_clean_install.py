"""`antibody test` must work for an end user's plain (non-dev) install, not just in this repo."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")
# Captured before the autouse fixture repoints XDG_CACHE_HOME, so installs hit the warm cache.
UV_CACHE = (
    subprocess.run([UV, "cache", "dir"], capture_output=True, text=True).stdout.strip()
    if UV
    else ""
)


@pytest.mark.skipif(UV is None, reason="needs uv to build a clean environment")
def test_starter_pack_passes_antibody_test_in_a_clean_install(workspace: Path) -> None:
    assert UV is not None
    venv = workspace.parent / "clean-venv"
    env = {**os.environ, "UV_CACHE_DIR": UV_CACHE}
    env.pop("VIRTUAL_ENV", None)
    subprocess.run([UV, "venv", "-q", str(venv)], check=True, env=env)
    subprocess.run(
        [UV, "pip", "install", "-q", "--python", str(venv), str(PKG)], check=True, env=env
    )
    antibody = str(venv / "bin" / "antibody")
    pulled = subprocess.run(
        [antibody, "pull", "core/python-safety"], capture_output=True, text=True, env=env
    )
    assert pulled.returncode == 0, pulled.stderr
    tested = subprocess.run([antibody, "test"], capture_output=True, text=True, env=env)
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "14 passed" in tested.stdout
