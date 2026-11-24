"""Real SQLite `.tape` (§6) and `flight_log.db` (§5) fixtures built from the contract DDL."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
)

TAPE_DDL = """
CREATE TABLE IF NOT EXISTS tape_meta (
    tape_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL DEFAULT '0.1',
    created_at REAL NOT NULL,
    command_argv TEXT NOT NULL,
    repo_root TEXT NOT NULL,
    base_commit_sha TEXT NOT NULL,
    parent_tape_id TEXT,
    fork_step_idx INTEGER,
    chaos_seed INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS steps (
    step_idx INTEGER PRIMARY KEY,
    ts_start REAL NOT NULL,
    ts_end REAL NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('llm_call','tool_call','chaos_fault')),
    pre_tree_sha TEXT NOT NULL,
    post_tree_sha TEXT NOT NULL,
    diff_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error','steered','injected_fault'))
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_idx INTEGER NOT NULL REFERENCES steps(step_idx),
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    raw_sse_chunks TEXT,
    http_status INTEGER NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    latency_ms REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_idx INTEGER NOT NULL REFERENCES steps(step_idx),
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_text TEXT NOT NULL,
    is_error INTEGER NOT NULL DEFAULT 0,
    exit_code INTEGER
);
CREATE TABLE IF NOT EXISTS chaos_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_idx INTEGER NOT NULL REFERENCES steps(step_idx),
    preset TEXT NOT NULL,
    seed INTEGER NOT NULL,
    target TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_step ON llm_calls(step_idx);
CREATE INDEX IF NOT EXISTS idx_tool_calls_step ON tool_calls(step_idx);
CREATE INDEX IF NOT EXISTS idx_chaos_events_step ON chaos_events(step_idx);
"""

FLIGHT_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    workspace_root TEXT NOT NULL,
    mode TEXT NOT NULL,
    last_code_edit_ts REAL DEFAULT 0.0,
    last_test_pass_ts REAL DEFAULT 0.0,
    last_test_pass_count INTEGER DEFAULT 0,
    consecutive_hard_blocks INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    step_idx INTEGER NOT NULL,
    ts REAL NOT NULL,
    tool_name TEXT NOT NULL,
    op_class TEXT NOT NULL,
    target_path TEXT,
    state_hash TEXT,
    verdict TEXT NOT NULL,
    rule_id TEXT,
    reason TEXT,
    exit_code INTEGER,
    error_hash TEXT,
    latency_ms REAL NOT NULL,
    args_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session_step ON tool_events(session_id, step_idx);
CREATE INDEX IF NOT EXISTS idx_events_session_path ON tool_events(session_id, target_path);
"""


def _open(path: Path, ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for pragma in PRAGMAS:
        conn.execute(pragma)
    conn.executescript(ddl)
    return conn


def _close(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def write_tape(path: Path, repo_root: str, base_commit: str, steps: list[dict[str, Any]]) -> Path:
    """Each step: kind, status, pre, post, diff, and optional tool=(name, args, result)."""
    conn = _open(path, TAPE_DDL)
    conn.execute(
        "INSERT INTO tape_meta (tape_id, created_at, command_argv, repo_root, base_commit_sha,"
        " metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("a" * 32, 1.0, json.dumps(["agent"]), repo_root, base_commit, '{"producer":"agent-rr"}'),
    )
    for idx, step in enumerate(steps):
        conn.execute(
            "INSERT INTO steps (step_idx, ts_start, ts_end, kind, pre_tree_sha, post_tree_sha,"
            " diff_text, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                idx,
                float(idx),
                float(idx) + 0.5,
                step.get("kind", "tool_call"),
                step["pre"],
                step["post"],
                step.get("diff", ""),
                step.get("status", "ok"),
            ),
        )
        if "tool" in step:
            name, args, result = step["tool"]
            conn.execute(
                "INSERT INTO tool_calls (step_idx, tool_name, arguments_json, result_text,"
                " is_error) VALUES (?, ?, ?, ?, ?)",
                (idx, name, json.dumps(args), result, int(step.get("status") == "error")),
            )
    _close(conn)
    return path


def write_flight_db(path: Path, workspace_root: str, events: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open(path, FLIGHT_DDL)
    conn.execute(
        "INSERT INTO sessions (session_id, started_at, workspace_root, mode) VALUES (?, ?, ?, ?)",
        ("s" * 32, 1.0, workspace_root, "steer"),
    )
    for idx, ev in enumerate(events):
        conn.execute(
            "INSERT INTO tool_events (session_id, step_idx, ts, tool_name, op_class, target_path,"
            " verdict, rule_id, reason, latency_ms, args_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "s" * 32,
                idx,
                float(idx),
                ev["tool"],
                ev.get("op_class", "FILE_WRITE"),
                ev.get("target_path"),
                ev["verdict"],
                ev.get("rule_id"),
                ev.get("reason"),
                1.0,
                json.dumps(ev["args"]),
            ),
        )
    _close(conn)
    return path
