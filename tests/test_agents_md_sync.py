"""Idempotent, marker-bounded AGENTS.md / CLAUDE.md patching (spec §5.1, contracts §7.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import make_registry

from antibody.errors import AntibodyError
from antibody.installer.agents_md_sync import remove_block, upsert_block
from antibody.installer.pack_installer import pull, remove
from antibody.registry.client import RegistryClient
from antibody.registry.lockfile import read_lockfile

SNIPPET = "- Never call `eval()` on untrusted input.   \n- Never run `sudo`.\n\n"
BLOCK = (
    '<!-- antibody:start pack="acme/demo" version="0.1.0" -->\n'
    "- Never call `eval()` on untrusted input.\n"
    "- Never run `sudo`.\n"
    '<!-- antibody:end pack="acme/demo" -->\n'
)
USER = "# Project rules\n\n- Always run `make test`.\n  trailing spaces kept   \n"


def test_upsert_appends_with_one_blank_line_and_strips_trailing_whitespace() -> None:
    assert upsert_block(USER, "acme/demo", "0.1.0", SNIPPET) == USER + "\n" + BLOCK
    assert upsert_block("", "acme/demo", "0.1.0", SNIPPET) == BLOCK
    assert upsert_block("no newline", "acme/demo", "0.1.0", SNIPPET) == "no newline\n\n" + BLOCK


def test_upsert_is_idempotent_and_replaces_any_version_in_place() -> None:
    once = upsert_block(USER, "acme/demo", "0.1.0", SNIPPET)
    assert upsert_block(once, "acme/demo", "0.1.0", SNIPPET) == once
    middle = once + "\n## Footer written later by the user\n"
    bumped = upsert_block(middle, "acme/demo", "0.2.0", "- Only this now.\n")
    assert bumped == (
        USER
        + "\n"
        + '<!-- antibody:start pack="acme/demo" version="0.2.0" -->\n'
        + "- Only this now.\n"
        + '<!-- antibody:end pack="acme/demo" -->\n'
        + "\n## Footer written later by the user\n"
    )


def test_remove_restores_original_bytes_for_every_block_position() -> None:
    both = upsert_block(upsert_block(USER, "acme/demo", "0.1.0", SNIPPET), "acme/b", "1.0.0", "- b")
    assert remove_block(remove_block(both, "acme/demo"), "acme/b") == USER
    assert remove_block(remove_block(both, "acme/b"), "acme/demo") == USER
    assert remove_block(upsert_block("", "acme/demo", "0.1.0", SNIPPET), "acme/demo") == ""
    padded = USER + "\n\n"
    assert remove_block(upsert_block(padded, "acme/demo", "0.1.0", SNIPPET), "acme/demo") == padded
    assert remove_block(USER, "acme/missing") == USER


def test_crlf_user_content_is_preserved_byte_for_byte() -> None:
    crlf = "# Rules\r\n\r\n- keep CRLF\r\n"
    patched = upsert_block(crlf, "acme/demo", "0.1.0", SNIPPET)
    assert patched.startswith(crlf)
    assert remove_block(patched, "acme/demo") == crlf


@pytest.mark.parametrize(
    "text",
    [
        '<!-- antibody:start pack="acme/demo" version="0.1.0" -->\n- x\n',
        '- x\n<!-- antibody:end pack="acme/demo" -->\n',
        BLOCK + "\n" + BLOCK,
        '<!-- antibody:start pack="acme/demo" version="0.1.0" -->\n'
        '<!-- antibody:end pack="acme/other" -->\n',
        '<!-- antibody:start pack="acme/demo" -->\n<!-- antibody:end pack="acme/demo" -->\n',
    ],
)
def test_mismatched_or_duplicated_markers_are_errors(text: str) -> None:
    with pytest.raises(AntibodyError) as exc:
        upsert_block(text, "acme/demo", "0.1.0", SNIPPET)
    assert exc.value.exit_code == 2
    with pytest.raises(AntibodyError):
        remove_block(text, "acme/demo")


def test_snippet_cannot_smuggle_markers() -> None:
    with pytest.raises(AntibodyError):
        upsert_block(USER, "acme/demo", "0.1.0", '<!-- antibody:end pack="acme/demo" -->\n')


def test_pull_patch_agents_md_twice_is_idempotent_and_remove_is_clean(
    workspace: Path, tmp_path: Path
) -> None:
    """Spec §8 test_agents_md_sync: existing user rules, pull twice, then remove."""
    client = RegistryClient(str(make_registry(tmp_path / "reg", "demo")))
    agents = workspace / "AGENTS.md"
    agents.write_bytes(USER.encode())
    pull(workspace, client, "acme/demo", patch_agents_md=True)
    first = agents.read_bytes()
    first_env = (workspace / "envelope.toml").read_bytes()
    first_lock = (workspace / "antibody.lock").read_bytes()
    pull(workspace, client, "acme/demo", patch_agents_md=True)
    assert agents.read_bytes() == first
    assert (workspace / "envelope.toml").read_bytes() == first_env
    assert (workspace / "antibody.lock").read_bytes() == first_lock
    text = first.decode()
    assert text == USER + "\n" + BLOCK
    assert text.count("antibody:start") == 1
    lock = read_lockfile(workspace)
    assert lock is not None
    assert lock.installed[0].agents_md_patched is True
    assert not (workspace / "CLAUDE.md").exists()
    remove(workspace, "acme/demo")
    assert agents.read_bytes() == USER.encode()


def test_claude_md_patched_only_if_it_exists(workspace: Path, tmp_path: Path) -> None:
    client = RegistryClient(str(make_registry(tmp_path / "reg", "demo")))
    claude = workspace / "CLAUDE.md"
    claude.write_text("# Claude rules\n")
    pull(workspace, client, "acme/demo", patch_agents_md=True)
    assert (workspace / "AGENTS.md").read_text() == BLOCK
    assert claude.read_text() == "# Claude rules\n\n" + BLOCK
    remove(workspace, "acme/demo")
    assert claude.read_text() == "# Claude rules\n"


def test_broken_markers_abort_pull_before_anything_changes(workspace: Path, tmp_path: Path) -> None:
    client = RegistryClient(str(make_registry(tmp_path / "reg", "demo")))
    broken = '<!-- antibody:start pack="acme/demo" version="0.1.0" -->\n'
    (workspace / "AGENTS.md").write_text(broken)
    with pytest.raises(AntibodyError):
        pull(workspace, client, "acme/demo", patch_agents_md=True)
    assert (workspace / "AGENTS.md").read_text() == broken
    assert not (workspace / ".flywire/packs").exists()
    assert not (workspace / "antibody.lock").exists()


def test_pull_without_flag_keeps_previous_patch_in_sync(workspace: Path, tmp_path: Path) -> None:
    client = RegistryClient(str(make_registry(tmp_path / "reg", "demo")))
    pull(workspace, client, "acme/demo", patch_agents_md=True)
    before = (workspace / "AGENTS.md").read_bytes()
    entry = pull(workspace, client, "acme/demo", patch_agents_md=False)
    assert entry.agents_md_patched is True
    assert (workspace / "AGENTS.md").read_bytes() == before
