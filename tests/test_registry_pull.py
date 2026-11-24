"""Registry client, atomic installer, envelope.toml sync (spec §5.1, contracts §3, §7.4)."""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import threading
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import git
from helpers import make_registry, reindex
from typer.testing import CliRunner

from antibody.cli import app
from antibody.errors import AntibodyError, GateFailure, IntegrityError
from antibody.installer import envelope_sync
from antibody.installer.pack_installer import parse_pack_ref, pull, remove, sync
from antibody.registry.client import BUNDLED_URL, RegistryClient, cache_root
from antibody.registry.hasher import tree_sha256
from antibody.registry.lockfile import read_lockfile, verify_lockfile


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def http_registry(tmp_path: Path) -> Iterator[tuple[str, Path, http.server.ThreadingHTTPServer]]:
    served = make_registry(tmp_path / "served", "demo")
    handler = functools.partial(_QuietHandler, directory=str(served))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", served, server
    finally:
        server.shutdown()
        server.server_close()


def _installed(root: Path, pack_id: str = "acme/demo") -> Path:
    return root / ".flywire" / "packs" / pack_id


def _tmp_is_clean(root: Path) -> bool:
    tmp = root / ".flywire" / ".tmp"
    return not tmp.exists() or not any(tmp.iterdir())


# --- references & client ----------------------------------------------------------


def test_parse_pack_ref() -> None:
    assert parse_pack_ref("core/python-safety") == ("core/python-safety", None)
    assert parse_pack_ref("core/python-safety@0.1.0") == ("core/python-safety", "0.1.0")
    for bad in ("python-safety", "Core/x", "a/b/c", "a/b@1.0", "../x", "a/b@"):
        with pytest.raises(AntibodyError) as exc:
            parse_pack_ref(bad)
        assert exc.value.exit_code == 2


def test_cache_root_honours_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_root() == tmp_path / "xdg" / "antibody"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert cache_root() == Path.home() / ".cache" / "antibody"


def test_bundled_registry_resolves_and_searches() -> None:
    client = RegistryClient(BUNDLED_URL)
    version, entry = client.resolve("core/python-safety", None)
    assert version == "0.1.0"
    assert entry.rule_count == 7
    assert [pid for pid, _ in client.search("hooks", None)] == ["core/git-hygiene"]
    assert [pid for pid, _ in client.search(None, "tsx")] == ["core/typescript-safety"]
    assert len(client.search(None, None)) == 3
    with pytest.raises(AntibodyError) as exc:
        client.resolve("core/nope", None)
    assert exc.value.exit_code == 2
    with pytest.raises(AntibodyError):
        client.resolve("core/python-safety", "9.9.9")
    with pytest.raises(AntibodyError):
        RegistryClient("bundled://other").index()


def test_local_dir_and_file_url_registries(tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    for url in (str(reg), reg.as_uri()):
        client = RegistryClient(url)
        _, entry = client.resolve("acme/demo", None)
        dest = tmp_path / f"out-{len(url)}"
        client.fetch(entry, dest)
        assert tree_sha256(dest) == entry.sha256
    with pytest.raises(AntibodyError):
        RegistryClient(str(tmp_path / "missing")).index()


def test_index_entry_path_traversal_is_refused(tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    index = json.loads((reg / "index.json").read_text())
    index["packs"]["acme/demo"]["versions"]["0.1.0"]["path"] = "../../etc"
    (reg / "index.json").write_text(json.dumps(index))
    client = RegistryClient(str(reg))
    _, entry = client.resolve("acme/demo", None)
    with pytest.raises(AntibodyError, match="unsafe path"):
        client.fetch(entry, tmp_path / "out")


# --- pull ---------------------------------------------------------------------------


def test_pull_from_local_registry_installs_locks_and_creates_envelope(
    workspace: Path, tmp_path: Path
) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    entry = pull(workspace, RegistryClient(str(reg)), "acme/demo", patch_agents_md=False)
    installed = _installed(workspace)
    assert (installed / "pack.toml").is_file()
    assert tree_sha256(installed) == entry.sha256
    assert (workspace / "envelope.toml").read_text() == (
        '[envelope]\nversion = "0.1"\npacks = ["acme/demo"]\n'
    )
    lock = read_lockfile(workspace)
    assert lock is not None
    assert lock.registry_url == str(reg)
    assert [(e.id, e.install_path, e.agents_md_patched) for e in lock.installed] == [
        ("acme/demo", ".flywire/packs/acme/demo", False)
    ]
    assert verify_lockfile(workspace, lock) == []
    assert _tmp_is_clean(workspace)
    assert not (workspace / "AGENTS.md").exists()


def test_pull_hash_mismatch_installs_nothing(workspace: Path, tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    rule = reg / "packs/acme/demo/rules/py_no_eval.toml"
    rule.write_bytes(rule.read_bytes().replace(b"eval()", b"evil()"))
    with pytest.raises(IntegrityError):
        pull(workspace, RegistryClient(str(reg)), "acme/demo", patch_agents_md=True)
    assert not _installed(workspace).exists()
    assert not (workspace / "envelope.toml").exists()
    assert not (workspace / "antibody.lock").exists()
    assert not (workspace / "AGENTS.md").exists()
    assert _tmp_is_clean(workspace)


def test_pull_query_compile_failure_installs_nothing(workspace: Path, tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    rule = reg / "packs/acme/demo/rules/py_no_eval.toml"
    rule.write_text(rule.read_text().replace("(call function:", "(kall function:"))
    reindex(reg)
    with pytest.raises(GateFailure):
        pull(workspace, RegistryClient(str(reg)), "acme/demo", patch_agents_md=False)
    assert not _installed(workspace).exists()
    assert _tmp_is_clean(workspace)


def test_pull_symlink_in_registry_pack_is_refused(workspace: Path, tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    os.symlink("/etc/passwd", reg / "packs/acme/demo/patches/evil.md")
    with pytest.raises(IntegrityError):
        pull(workspace, RegistryClient(str(reg)), "acme/demo", patch_agents_md=False)
    assert not _installed(workspace).exists()


def test_pull_never_executes_pack_python(workspace: Path, tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    canary = tmp_path / "executed"
    evil = f"open({str(canary)!r}, 'w').write('pwned')\n"
    (reg / "packs/acme/demo/evals/conftest.py").write_text(evil)
    (reg / "packs/acme/demo/evals/test_vaccines.py").write_text(evil)
    reindex(reg)
    pull(workspace, RegistryClient(str(reg)), "acme/demo", patch_agents_md=True)
    assert (_installed(workspace) / "evals/conftest.py").exists()
    assert not canary.exists()


def test_repull_new_version_replaces_dir_atomically(workspace: Path, tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    pull(workspace, RegistryClient(str(reg)), "acme/demo", patch_agents_md=False)
    shutil.rmtree(reg / "packs")
    make_registry(reg, "demo", version="0.2.0")
    (reg / "packs/acme/demo/patches/agents_md.md").unlink()
    reindex(reg)
    entry = pull(workspace, RegistryClient(str(reg)), "acme/demo@0.2.0", patch_agents_md=False)
    assert entry.version == "0.2.0"
    assert not (_installed(workspace) / "patches/agents_md.md").exists()
    assert tree_sha256(_installed(workspace)) == entry.sha256
    assert sorted(p.name for p in (workspace / ".flywire/packs/acme").iterdir()) == ["demo"]
    assert _tmp_is_clean(workspace)


def test_pull_from_http_registry_with_offline_cache_fallback(
    workspace: Path, http_registry: tuple[str, Path, http.server.ThreadingHTTPServer]
) -> None:
    url, _, server = http_registry
    entry = pull(workspace, RegistryClient(url), "acme/demo", patch_agents_md=False)
    assert tree_sha256(_installed(workspace)) == entry.sha256
    assert (cache_root() / "registries").is_dir()
    server.shutdown()
    server.server_close()
    shutil.rmtree(workspace / ".flywire")
    again = pull(workspace, RegistryClient(url), "acme/demo", patch_agents_md=False)
    assert again.sha256 == entry.sha256
    assert tree_sha256(_installed(workspace)) == entry.sha256


def test_http_registry_unreachable_without_cache_is_actionable(workspace: Path) -> None:
    with pytest.raises(AntibodyError, match="no cached copy"):
        RegistryClient("http://127.0.0.1:9/").index()


def test_http_files_with_traversal_are_refused(
    workspace: Path, http_registry: tuple[str, Path, http.server.ThreadingHTTPServer]
) -> None:
    url, served, _ = http_registry
    index = json.loads((served / "index.json").read_text())
    index["packs"]["acme/demo"]["versions"]["0.1.0"]["files"].append("../../../outside.txt")
    (served / "index.json").write_text(json.dumps(index))
    with pytest.raises(AntibodyError, match="unsafe path"):
        pull(workspace, RegistryClient(url), "acme/demo", patch_agents_md=False)
    assert not _installed(workspace).exists()


def test_pull_from_git_file_registry_with_offline_cache_fallback(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = make_registry(tmp_path / "gitreg", "demo")
    git(src, "init", "-q")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "registry")
    url = f"git+{src.as_uri()}"
    entry = pull(workspace, RegistryClient(url), "acme/demo", patch_agents_md=False)
    assert tree_sha256(_installed(workspace)) == entry.sha256
    shutil.rmtree(src)
    shutil.rmtree(workspace / ".flywire")
    capsys.readouterr()
    again = pull(workspace, RegistryClient(url), "acme/demo", patch_agents_md=False)
    assert "warning: using cached registry" in capsys.readouterr().err
    assert again.sha256 == entry.sha256
    assert not list((cache_root() / "registries").glob(".clone-*"))


# --- envelope.toml ------------------------------------------------------------------

ENVELOPE = """\
# Team envelope: keep these comments!
[envelope]
version = "0.1"   # pinned schema
mode = "steer"
packs = [
    "core/git-hygiene",  # shell safety
]

[scope]
max_files_modified = 15  # review limit
"""


def test_envelope_add_and_remove_preserve_comments_and_are_idempotent(workspace: Path) -> None:
    path = workspace / "envelope.toml"
    path.write_text(ENVELOPE)
    envelope_sync.add_pack(workspace, "core/python-safety")
    once = path.read_text()
    envelope_sync.add_pack(workspace, "core/python-safety")
    assert path.read_text() == once
    for comment in ("# Team envelope", "# pinned schema", "# shell safety", "# review limit"):
        assert comment in once
    parsed = tomllib.loads(once)
    assert parsed["envelope"]["packs"] == ["core/git-hygiene", "core/python-safety"]
    assert parsed["scope"]["max_files_modified"] == 15
    envelope_sync.remove_pack(workspace, "core/python-safety")
    after = path.read_text()
    assert tomllib.loads(after)["envelope"]["packs"] == ["core/git-hygiene"]
    for comment in ("# Team envelope", "# pinned schema", "# shell safety", "# review limit"):
        assert comment in after
    envelope_sync.remove_pack(workspace, "core/not-there")
    assert path.read_text() == after


def test_envelope_without_packs_key_or_table(workspace: Path) -> None:
    path = workspace / "envelope.toml"
    path.write_text('# c\n[envelope]\nmode = "audit"\n')
    envelope_sync.add_pack(workspace, "core/x")
    assert tomllib.loads(path.read_text())["envelope"] == {"mode": "audit", "packs": ["core/x"]}
    path.write_text("[scope]\nmax_files_modified = 3\n")
    envelope_sync.add_pack(workspace, "core/x")
    assert tomllib.loads(path.read_text())["envelope"]["packs"] == ["core/x"]


def test_envelope_invalid_is_an_actionable_error(workspace: Path) -> None:
    path = workspace / "envelope.toml"
    path.write_text("[envelope\n")
    with pytest.raises(AntibodyError):
        envelope_sync.add_pack(workspace, "core/x")
    path.write_text('[envelope]\npacks = "core/x"\n')
    with pytest.raises(AntibodyError):
        envelope_sync.add_pack(workspace, "core/y")
    assert path.read_text() == '[envelope]\npacks = "core/x"\n'


# --- remove & sync ------------------------------------------------------------------


def test_remove_cleans_dir_lock_and_envelope_only(workspace: Path, tmp_path: Path) -> None:
    reg = make_registry(tmp_path / "reg", "demo", "other")
    client = RegistryClient(str(reg))
    (workspace / "envelope.toml").write_text(ENVELOPE)
    pull(workspace, client, "acme/demo", patch_agents_md=False)
    pull(workspace, client, "acme/other", patch_agents_md=False)
    remove(workspace, "acme/demo")
    assert not _installed(workspace).exists()
    assert _installed(workspace, "acme/other").is_dir()
    lock = read_lockfile(workspace)
    assert lock is not None
    assert [e.id for e in lock.installed] == ["acme/other"]
    packs = tomllib.loads((workspace / "envelope.toml").read_text())["envelope"]["packs"]
    assert packs == ["core/git-hygiene", "acme/other"]
    with pytest.raises(AntibodyError) as exc:
        remove(workspace, "acme/demo")
    assert exc.value.exit_code == 2


def test_sync_restores_pinned_versions_and_detects_registry_drift(
    workspace: Path, tmp_path: Path
) -> None:
    reg = make_registry(tmp_path / "reg", "demo")
    client = RegistryClient(str(reg))
    pull(workspace, client, "acme/demo", patch_agents_md=True)
    agents = (workspace / "AGENTS.md").read_bytes()
    shutil.rmtree(workspace / ".flywire")
    assert sync(workspace, client) == ["acme/demo"]
    lock = read_lockfile(workspace)
    assert lock is not None
    assert verify_lockfile(workspace, lock) == []
    assert (workspace / "AGENTS.md").read_bytes() == agents
    assert sync(workspace, client) == []
    (_installed(workspace) / "pack.toml").write_text("tampered")
    assert sync(workspace, client) == ["acme/demo"]
    assert verify_lockfile(workspace, lock) == []
    rule = reg / "packs/acme/demo/rules/sh_no_sudo.toml"
    rule.write_text(rule.read_text().replace("sudo is", "sudo was"))
    reindex(reg)
    shutil.rmtree(workspace / ".flywire")
    with pytest.raises(IntegrityError):
        sync(workspace, RegistryClient(str(reg)))
    assert not _installed(workspace).exists()


def test_cli_pull_local_registry_offline_fallback_envelope_and_clean_remove(
    workspace: Path,
    http_registry: tuple[str, Path, http.server.ThreadingHTTPServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §8 test_registry_pull, end to end through the CLI."""
    url, served, server = http_registry
    runner = CliRunner()
    (workspace / "envelope.toml").write_text(ENVELOPE)
    local = runner.invoke(app, ["pull", "acme/demo", "--registry", str(served)])
    assert local.exit_code == 0, local.stderr
    assert "acme/demo@0.1.0" in local.stdout
    envelope = (workspace / "envelope.toml").read_text()
    assert "# shell safety" in envelope and "# review limit" in envelope
    assert tomllib.loads(envelope)["envelope"]["packs"] == ["core/git-hygiene", "acme/demo"]
    assert runner.invoke(app, ["remove", "acme/demo"]).exit_code == 0
    assert not _installed(workspace).exists()
    assert tomllib.loads((workspace / "envelope.toml").read_text())["envelope"]["packs"] == [
        "core/git-hygiene"
    ]
    lock = read_lockfile(workspace)
    assert lock is not None and lock.installed == ()

    monkeypatch.setenv("ANTIBODY_REGISTRY", url)
    online = runner.invoke(app, ["pull", "acme/demo"])
    assert online.exit_code == 0, online.stderr
    server.shutdown()
    server.server_close()
    shutil.rmtree(workspace / ".flywire")
    offline = runner.invoke(app, ["pull", "acme/demo"])
    assert offline.exit_code == 0, offline.stderr
    assert "warning: using cached" in offline.stderr
    assert runner.invoke(app, ["verify"]).exit_code == 0
    missing = runner.invoke(app, ["remove", "acme/nope"])
    assert missing.exit_code == 2
    assert missing.stderr.startswith("error: ")


def test_cli_pull_exit_codes(workspace: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["pull", "core/does-not-exist"]).exit_code == 2
    assert runner.invoke(app, ["pull", "not-a-ref"]).exit_code == 2
    reg = make_registry(tmp_path / "reg", "demo")
    rule = reg / "packs/acme/demo/rules/py_no_eval.toml"
    rule.write_bytes(rule.read_bytes() + b"\n")
    mismatch = runner.invoke(app, ["pull", "acme/demo", "--registry", str(reg)])
    assert mismatch.exit_code == 12
    assert "sha256 mismatch" in mismatch.stderr
    rule.write_text(rule.read_text().replace("(call function:", "(kall function:"))
    reindex(reg)
    broken = runner.invoke(app, ["pull", "acme/demo", "--registry", str(reg)])
    assert broken.exit_code == 11
    assert not _installed(workspace).exists()


def test_cli_sync_restores_and_reports_drift(workspace: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    reg = make_registry(tmp_path / "reg", "demo")
    assert runner.invoke(app, ["pull", "acme/demo", "--registry", str(reg)]).exit_code == 0
    shutil.rmtree(workspace / ".flywire")
    restored = runner.invoke(app, ["sync"])
    assert restored.exit_code == 0, restored.stderr
    assert runner.invoke(app, ["verify"]).exit_code == 0
    rule = reg / "packs/acme/demo/rules/sh_no_sudo.toml"
    rule.write_text(rule.read_text().replace("sudo is", "sudo was"))
    reindex(reg)
    shutil.rmtree(workspace / ".flywire")
    assert runner.invoke(app, ["sync"]).exit_code == 12


def test_sync_without_lockfile_is_usage_error(workspace: Path) -> None:
    with pytest.raises(AntibodyError) as exc:
        sync(workspace, RegistryClient(BUNDLED_URL))
    assert exc.value.exit_code == 2
