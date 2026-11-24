"""Deterministic failure extraction from `.tape`, `flight_log.db`, or a unified diff (§6, §7.6).

Every candidate becomes a `Failure`: full before/after text of one file (or one shell
command), already scrubbed of secrets and absolute user paths.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from antibody.ast_engine import parser
from antibody.distiller.templates import grammar_of
from antibody.errors import AntibodyError

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
)
SECRETS = tuple(
    re.compile(p)
    for p in (
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"sk-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"AKIA[0-9A-Z]{16}",
        r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}",
    )
)
USER_PATHS = (
    (re.compile(r"/(?:Users|home)/[^/\s'\"`]+"), "/home/user"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s'\"`]+"), r"C:\\Users\\user"),
)
PATH_KEYS = ("path", "file_path", "filepath", "filename", "target_file")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SLICE_NODES = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "typescript": {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "arrow_function",
        "function_expression",
        "lexical_declaration",
        "expression_statement",
    },
}
SLICE_NODES["tsx"] = SLICE_NODES["typescript"]
CONTEXT_LINES = 3


@dataclass(frozen=True)
class Failure:
    origin: str
    flagged: bool  # STEERed/blocked/errored, or an explicit user-supplied diff
    path: str | None = None
    before: str = ""
    after: str = ""
    old_lines: frozenset[int] = frozenset()
    new_lines: frozenset[int] = frozenset()
    command: str | None = None


# --- scrubbing ---------------------------------------------------------------------------


def scrub(text: str, roots: Sequence[str] = ()) -> str:
    for root in sorted({r for r in roots if len(r) > 1}, key=len, reverse=True):
        text = text.replace(root, "/workspace")
    for secret in SECRETS:
        text = secret.sub("<REDACTED_SECRET>", text)
    for pattern, replacement in USER_PATHS:
        text = pattern.sub(replacement, text)
    return text


def _scrubbed(failure: Failure, roots: Sequence[str]) -> Failure:
    return Failure(
        origin=failure.origin,
        flagged=failure.flagged,
        path=failure.path,
        before=scrub(failure.before, roots),
        after=scrub(failure.after, roots),
        old_lines=failure.old_lines,
        new_lines=failure.new_lines,
        command=scrub(failure.command, roots) if failure.command is not None else None,
    )


# --- unified diffs -----------------------------------------------------------------------


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    lines: tuple[tuple[str, str], ...]  # (" " | "-" | "+", text incl. newline)


@dataclass(frozen=True)
class FileDiff:
    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _diff_path(raw: str) -> str | None:
    name = raw.split("\t", 1)[0].strip()
    if name == "/dev/null":
        return None
    return name[2:] if name.startswith(("a/", "b/")) else name


def _strip_newline(body: list[tuple[str, str]]) -> None:
    """Apply a `\\ No newline at end of file` marker to the preceding hunk line."""
    if body:
        kind, content = body[-1]
        body[-1] = (kind, content.removesuffix("\n"))


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    old: str | None = None
    new: str | None = None
    hunks: list[Hunk] = []
    lines = text.split("\n")

    def flush() -> None:
        if hunks and (old or new):
            files.append(FileDiff(old, new, tuple(hunks)))

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            flush()
            old = new = None
            hunks = []
        elif line.startswith("--- ") and not hunks:
            old = _diff_path(line[4:])
        elif line.startswith("+++ ") and not hunks:
            new = _diff_path(line[4:])
        elif match := _HUNK.match(line):
            old_len = int(match.group(2) or "1")
            new_len = int(match.group(4) or "1")
            body: list[tuple[str, str]] = []
            remaining_old, remaining_new = old_len, new_len
            while (remaining_old > 0 or remaining_new > 0) and i + 1 < len(lines):
                i += 1
                raw = lines[i]
                kind, content = (raw[:1] or " "), raw[1:]
                if kind == "\\":
                    _strip_newline(body)
                    continue
                if kind not in " -+":
                    i -= 1
                    break
                body.append((kind, content + "\n"))
                remaining_old -= kind in " -"
                remaining_new -= kind in " +"
            if i + 1 < len(lines) and lines[i + 1].startswith("\\"):
                _strip_newline(body)
                i += 1
            hunks.append(Hunk(int(match.group(1)), old_len, tuple(body)))
        i += 1
    flush()
    return files


def _walk(
    src: list[str] | None, hunks: Sequence[Hunk]
) -> tuple[str, str, frozenset[int], frozenset[int]]:
    """Apply hunks to `src` lines (or rebuild both sides from hunks alone if `src` is None)."""
    before: list[str] = []
    after: list[str] = []
    old_changed: set[int] = set()
    new_changed: set[int] = set()
    pos = 0
    for hunk in hunks:
        if src is not None:
            start = hunk.old_start if hunk.old_len == 0 else hunk.old_start - 1
            if not pos <= start <= len(src):
                raise ValueError("hunk out of order")
            before += src[pos:start]
            after += src[pos:start]
            pos = start
        for kind, text in hunk.lines:
            if kind in " -":
                if src is not None:
                    if pos >= len(src) or src[pos] != text:
                        raise ValueError("context mismatch")
                    pos += 1
                before.append(text)
                if kind == "-":
                    old_changed.add(len(before))
                    new_changed.add(max(len(after), 1))
            if kind in " +":
                after.append(text)
                if kind == "+":
                    new_changed.add(len(after))
                    old_changed.add(max(len(before), 1))
    if src is not None:
        before += src[pos:]
        after += src[pos:]
    return "".join(before), "".join(after), frozenset(old_changed), frozenset(new_changed)


def _file_failure(origin: str, diff: FileDiff, base: str | None, flagged: bool) -> Failure:
    if diff.old_path is None:
        base = ""
    walked = None
    if base is not None:
        try:
            walked = _walk(base.splitlines(keepends=True), diff.hunks)
        except ValueError:
            walked = None
    before, after, old_lines, new_lines = walked or _walk(None, diff.hunks)
    return Failure(origin, flagged, diff.path, before, after, old_lines, new_lines)


def _line_changes(before: str, after: str) -> tuple[frozenset[int], frozenset[int]]:
    old, new = before.splitlines(), after.splitlines()
    old_changed: set[int] = set()
    new_changed: set[int] = set()
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_changed.update(range(i1 + 1, i2 + 1) or [max(i1, 1)])
        new_changed.update(range(j1 + 1, j2 + 1) or [max(j1, 1)])
    return frozenset(old_changed), frozenset(new_changed)


# --- sources ---------------------------------------------------------------------------------


@contextmanager
def _sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise AntibodyError(f"no such database: {path}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        raise AntibodyError(f"cannot open {path}: {exc}") from exc
    with closing(conn):
        try:
            for pragma in PRAGMAS:
                conn.execute(pragma)
            yield conn
        except sqlite3.DatabaseError as exc:
            raise AntibodyError(f"{path} is not a valid database: {exc}") from exc


def _git_show(repo: Path, rev: str, path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "show", f"{rev}:{path}"],
            capture_output=True,
            check=True,
        )
        return out.stdout.decode("utf-8")
    except (subprocess.CalledProcessError, OSError, UnicodeDecodeError):
        return None


def _command(args: Any) -> str | None:
    if isinstance(args, dict):
        for key in ("command", "cmd"):
            if isinstance(args.get(key), str) and args[key].strip():
                return str(args[key]).strip()
    return None


class _TapeStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step_idx: int
    pre_tree_sha: str
    diff_text: str
    status: str
    arguments_json: str | None


def from_tape(path: Path, step: int | None) -> list[Failure]:
    with _sqlite(path) as conn:
        meta = conn.execute("SELECT repo_root FROM tape_meta LIMIT 1").fetchone()
        if meta is None:
            raise AntibodyError(f"{path}: tape has no tape_meta row")
        query = (
            "SELECT s.step_idx, s.pre_tree_sha, s.diff_text, s.status, t.arguments_json"
            " FROM steps s LEFT JOIN tool_calls t ON t.step_idx = s.step_idx"
        )
        params: tuple[int, ...] = ()
        if step is not None:
            query += " WHERE s.step_idx = ?"
            params = (step,)
        names = ("step_idx", "pre_tree_sha", "diff_text", "status", "arguments_json")
        try:
            rows = [
                _TapeStep.model_validate(dict(zip(names, r, strict=True)))
                for r in conn.execute(query + " ORDER BY s.step_idx, t.id", params)
            ]
        except ValidationError as exc:
            raise AntibodyError(f"{path}: malformed steps row: {exc}") from exc
    if step is not None and not rows:
        raise AntibodyError(f"{path}: no step {step}")
    repo = Path(meta[0])
    failures: list[Failure] = []
    for row in rows:
        origin = f"{path.name} step {row.step_idx}"
        flagged = row.status in ("steered", "error")
        for diff in parse_diff(row.diff_text):
            base = _git_show(repo, row.pre_tree_sha, diff.old_path) if diff.old_path else ""
            failures.append(_file_failure(origin, diff, base, flagged))
        args = json.loads(row.arguments_json) if row.arguments_json else None
        command = _command(args)
        if row.status == "steered" and command and not row.diff_text:
            failures.append(Failure(origin, True, command=command))
    roots = (str(repo), str(repo.resolve()))
    return [_scrubbed(f, roots) for f in failures]


def _relative(root: Path, raw: str) -> str | None:
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        return None
    return resolved.relative_to(root.resolve()).as_posix()


def _event_failure(
    root: Path, origin: str, args: dict[str, Any], target: str | None
) -> Failure | None:
    raw = next((args[k] for k in PATH_KEYS if isinstance(args.get(k), str)), target)
    rel = _relative(root, raw) if raw else None
    if rel is None:
        return None
    file = root / rel
    current = file.read_bytes().decode("utf-8", errors="replace") if file.is_file() else ""
    new_text = args.get("content", args.get("file_text"))
    old_str = args.get("old_str", args.get("old_string"))
    new_str = args.get("new_str", args.get("new_string"))
    patch = args.get("patch", args.get("diff"))
    if isinstance(new_text, str):
        after = new_text
    elif isinstance(old_str, str) and isinstance(new_str, str) and old_str in current:
        count = -1 if args.get("replace_all") else 1
        after = current.replace(old_str, new_str, count)
    elif isinstance(patch, str) and (diffs := parse_diff(patch)):
        return _file_failure(origin, FileDiff(rel, rel, diffs[0].hunks), current, True)
    else:
        return None
    old_lines, new_lines = _line_changes(current, after)
    return Failure(origin, True, rel, current, after, old_lines, new_lines)


def from_flight_db(path: Path, step: int | None) -> list[Failure]:
    with _sqlite(path) as conn:
        session = conn.execute("SELECT workspace_root FROM sessions LIMIT 1").fetchone()
        if (
            step is not None
            and not conn.execute(
                "SELECT 1 FROM tool_events WHERE step_idx = ? LIMIT 1", (step,)
            ).fetchone()
        ):
            raise AntibodyError(f"{path}: no tool event at step {step}")
        query = "SELECT step_idx, target_path, args_json FROM tool_events WHERE verdict != 'ALLOW'"
        params: tuple[int, ...] = ()
        if step is not None:
            query += " AND step_idx = ?"
            params = (step,)
        rows = conn.execute(query + " ORDER BY ts, id", params).fetchall()
    root = Path(session[0]) if session and Path(session[0]).is_dir() else path.parent.parent
    failures: list[Failure] = []
    for step_idx, target, args_json in rows:
        origin = f"{path.name} step {step_idx}"
        args = json.loads(args_json)
        if command := _command(args):
            failures.append(Failure(origin, True, command=command))
        elif isinstance(args, dict) and (found := _event_failure(root, origin, args, target)):
            failures.append(found)
    roots = (str(root), str(root.resolve()))
    return [_scrubbed(f, roots) for f in failures]


def from_diff(path: Path, root: Path) -> list[Failure]:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AntibodyError(f"cannot read diff {path}: {exc}") from exc
    failures = []
    for diff in parse_diff(text):
        base = _git_show(root, "HEAD", diff.old_path) if diff.old_path else ""
        failures.append(_file_failure(path.name, diff, base, True))
    roots = (str(root), str(root.resolve()))
    return [_scrubbed(f, roots) for f in failures]


# --- fixture slicing -----------------------------------------------------------------------


def _slice(text: str, changed: frozenset[int], grammar: str) -> str | None:
    """Smallest enclosing function/class node ±(≤3) lines that parses cleanly, else None."""
    if not text or not changed:
        return None
    tree = parser(grammar).parse(text.encode("utf-8"))
    if tree.root_node.has_error:
        return None
    lines = text.splitlines(keepends=True)
    lo = min(min(changed), len(lines)) - 1
    hi = min(max(changed), len(lines)) - 1
    node = tree.root_node.named_descendant_for_point_range((lo, 0), (hi, 0))
    while node is not None:
        if node.type in SLICE_NODES[grammar]:
            for ctx in range(CONTEXT_LINES, -1, -1):
                start = max(node.start_point.row - ctx, 0)
                end = min(node.end_point.row + ctx, len(lines) - 1)
                piece = "".join(lines[start : end + 1])
                piece = piece if piece.endswith("\n") else piece + "\n"
                if not parser(grammar).parse(piece.encode("utf-8")).root_node.has_error:
                    return piece
        node = node.parent
    return None


def fixture_pair(
    failure: Failure, keeps_signal: Callable[[str, str], bool] | None = None
) -> tuple[str, str]:
    """Sliced (before, after) when both sides slice cleanly (and, if given, the slices still
    exhibit the failure signal); otherwise the whole files."""
    grammar = grammar_of(failure.path or "")
    if grammar is not None:
        before = _slice(failure.before, failure.old_lines, grammar)
        after = _slice(failure.after, failure.new_lines, grammar)
        if (
            before is not None
            and after is not None
            and (keeps_signal is None or keeps_signal(before, after))
        ):
            return before, after
    return failure.before, failure.after
