"""Fenced, idempotent upsert/remove of pack blocks in AGENTS.md / CLAUDE.md (contracts §7.5).

Text is handled as exact bytes (decoded UTF-8, no newline translation) so every byte
outside the markers survives untouched, including CRLF line endings.
"""

from __future__ import annotations

import re
from pathlib import Path

from antibody.errors import AntibodyError
from antibody.fsutil import atomic_write

AGENTS_MD = "AGENTS.md"
CLAUDE_MD = "CLAUDE.md"
_START = re.compile(r'<!-- antibody:start pack="([^"]+)" version="([^"]+)" -->')
_END = re.compile(r'<!-- antibody:end pack="([^"]+)" -->')
_MARKER_PREFIX = "<!-- antibody:"


def _content(line: str) -> str:
    return line.rstrip("\r\n")


def _blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """pack id → (start line, end line), validating that markers pair up exactly once."""
    blocks: dict[str, tuple[int, int]] = {}
    open_pack: tuple[str, int] | None = None
    for i, raw in enumerate(lines):
        line = _content(raw)
        if start := _START.fullmatch(line):
            if open_pack is not None:
                raise AntibodyError(f"nested antibody marker at line {i + 1}")
            open_pack = (start.group(1), i)
        elif end := _END.fullmatch(line):
            if open_pack is None or open_pack[0] != end.group(1):
                raise AntibodyError(f"unmatched antibody:end marker at line {i + 1}")
            if open_pack[0] in blocks:
                raise AntibodyError(f"duplicate antibody block for {open_pack[0]}")
            blocks[open_pack[0]] = (open_pack[1], i)
            open_pack = None
        elif line.lstrip().startswith(_MARKER_PREFIX):
            raise AntibodyError(f"malformed antibody marker at line {i + 1}: {line.strip()}")
    if open_pack is not None:
        raise AntibodyError(f"unterminated antibody block for {open_pack[0]}")
    return blocks


def render_block(pack_id: str, version: str, snippet: str) -> list[str]:
    body = [line.rstrip() for line in snippet.splitlines()]
    while body and not body[-1]:
        body.pop()
    while body and not body[0]:
        body.pop(0)
    if any(line.lstrip().startswith(_MARKER_PREFIX) for line in body):
        raise AntibodyError(f"{pack_id}: patches/agents_md.md must not contain antibody markers")
    return [
        f'<!-- antibody:start pack="{pack_id}" version="{version}" -->\n',
        *(f"{line}\n" for line in body),
        f'<!-- antibody:end pack="{pack_id}" -->\n',
    ]


def upsert_block(text: str, pack_id: str, version: str, snippet: str) -> str:
    lines = text.splitlines(keepends=True)
    blocks = _blocks(lines)
    block = render_block(pack_id, version, snippet)
    if pack_id in blocks:
        start, end = blocks[pack_id]
        return "".join([*lines[:start], *block, *lines[end + 1 :]])
    if not text:
        return "".join(block)
    prefix = text if text.endswith("\n") else text + "\n"
    return prefix + "\n" + "".join(block)


def remove_block(text: str, pack_id: str) -> str:
    """Delete the block plus the one blank separator line that `upsert_block` added."""
    lines = text.splitlines(keepends=True)
    blocks = _blocks(lines)
    if pack_id not in blocks:
        return text
    start, end = blocks[pack_id]
    if start > 0 and not _content(lines[start - 1]):
        start -= 1
    return "".join([*lines[:start], *lines[end + 1 :]])


def _targets(root: Path) -> list[Path]:
    claude = root / CLAUDE_MD
    return [root / AGENTS_MD, *([claude] if claude.exists() else [])]


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def plan_upsert(root: Path, pack_id: str, version: str, snippet: str) -> dict[Path, str]:
    """New contents per target file; raises before anything is written."""
    return {p: upsert_block(_read(p), pack_id, version, snippet) for p in _targets(root)}


def plan_remove(root: Path, pack_id: str) -> dict[Path, str]:
    return {p: remove_block(_read(p), pack_id) for p in _targets(root) if p.exists()}


def apply_plan(plan: dict[Path, str]) -> None:
    for path, text in plan.items():
        if not path.exists() or _read(path) != text:
            atomic_write(path, text.encode("utf-8"))
