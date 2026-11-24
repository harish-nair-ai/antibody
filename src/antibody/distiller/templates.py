"""Deterministic failure signatures (spec §6.1 b/c) and the rule each one distills into."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from antibody.ast_engine import EXT_TO_GRAMMAR, compiled_query, count_captures, grammars_for

PY_TESTS = ("tests/**/*.py", "**/*_test.py", "**/test_*.py")
TS_TESTS = ("**/*.test.ts*", "**/*.spec.ts*")
PY_ALL = ("**/*.py",)
TS_ALL = ("**/*.ts", "**/*.tsx")


@dataclass(frozen=True)
class Template:
    key: str
    language: str  # rule language: "python" | "typescript"
    check: str
    query: str
    paths: tuple[str, ...]
    message: str
    remediation: str
    agents_line: str
    category: str
    severity: str
    cwe: str | None
    summary: str


TEMPLATES: tuple[Template, ...] = (
    Template(
        "py-deleted-assertions",
        "python",
        "node_count_decreased",
        "[(assert_statement) @target (call function: (attribute attribute: (identifier) @fn "
        '(#match? @fn "^assert"))) @target]',
        PY_TESTS,
        "Test assertions were removed from a test file.",
        "Restore the deleted assertions and fix the code under test instead.",
        "- Never delete or weaken assertions to make a failing test pass.",
        "test-tampering",
        "critical",
        "CWE-693",
        "Distilled: blocks deleting assertions to fake passing tests.",
    ),
    Template(
        "py-ssl-verify-false",
        "python",
        "new_node_introduced",
        '(keyword_argument name: (identifier) @k (#eq? @k "verify") value: (false)) @target',
        PY_ALL,
        "TLS certificate verification was disabled with `verify=False`.",
        "Keep verification on; pass a CA bundle via `verify=` if needed.",
        "- Never pass `verify=False` to HTTP clients.",
        "security-regression",
        "critical",
        "CWE-295",
        "Distilled: blocks disabling TLS verification.",
    ),
    Template(
        "py-shell-true",
        "python",
        "new_node_introduced",
        '(keyword_argument name: (identifier) @k (#eq? @k "shell") value: (true)) @target',
        PY_ALL,
        "A subprocess call now runs through the shell with `shell=True`.",
        "Pass an argument list without `shell=True`.",
        "- Never call subprocess with `shell=True`; pass an argument list.",
        "security-regression",
        "critical",
        "CWE-78",
        "Distilled: blocks `shell=True` subprocess calls.",
    ),
    Template(
        "py-swallowed-exception",
        "python",
        "new_node_introduced",
        "(except_clause (block (pass_statement)) @target)",
        PY_ALL,
        "A new exception handler silently swallows errors with `pass`.",
        "Handle the specific exception (log, recover, or re-raise) instead of passing.",
        "- Never swallow exceptions with `except ...: pass`.",
        "reliability",
        "high",
        "CWE-390",
        "Distilled: blocks `except ...: pass` handlers.",
    ),
    Template(
        "py-lint-suppression",
        "python",
        "new_node_introduced",
        '((comment) @target (#match? @target "(#\\\\s*noqa|#\\\\s*type:\\\\s*ignore)"))',
        PY_ALL,
        "A new `# noqa` / `# type: ignore` suppression was added.",
        "Fix the underlying lint or type error instead of suppressing it.",
        "- Never add `# noqa` or `# type: ignore`; fix the underlying error.",
        "test-tampering",
        "medium",
        "CWE-710",
        "Distilled: blocks new lint/type suppressions.",
    ),
    Template(
        "py-mock-fallback",
        "python",
        "new_node_introduced",
        '(return_statement [(identifier) (string)] @value (#match? @value "MOCK_")) @target',
        PY_ALL,
        "A hardcoded `MOCK_` value is returned as a fallback.",
        "Return real data or raise; never fake results with MOCK_ fallbacks.",
        "- Never return hardcoded `MOCK_` values to make code appear to work.",
        "test-tampering",
        "high",
        None,
        "Distilled: blocks hardcoded MOCK_ return fallbacks.",
    ),
    Template(
        "ts-deleted-expects",
        "typescript",
        "node_count_decreased",
        '(call_expression function: (identifier) @fn (#eq? @fn "expect")) @target',
        TS_TESTS,
        "`expect()` assertions were removed from a test file.",
        "Restore the deleted expectations and fix the code under test instead.",
        "- Never delete `expect()` calls to make a failing test pass.",
        "test-tampering",
        "critical",
        "CWE-693",
        "Distilled: blocks deleting `expect()` calls.",
    ),
    Template(
        "ts-ts-ignore",
        "typescript",
        "new_node_introduced",
        '((comment) @target (#match? @target "@ts-(ignore|nocheck)"))',
        TS_ALL,
        "A new `@ts-ignore` / `@ts-nocheck` comment was added.",
        "Fix the type error instead of suppressing the compiler.",
        "- Never add `@ts-ignore` or `@ts-nocheck`; fix the types.",
        "test-tampering",
        "medium",
        "CWE-710",
        "Distilled: blocks TypeScript compiler suppressions.",
    ),
)


def grammar_of(path: str) -> str | None:
    return EXT_TO_GRAMMAR.get(PurePosixPath(path).suffix)


def signals(template: Template, path: str, before: str, after: str) -> bool:
    """Whether (before → after) exhibits this template's failure signature."""
    grammar = grammar_of(path)
    if grammar is None or grammar not in grammars_for(template.language):
        return False
    query = compiled_query(grammar, template.query)
    pre, pre_err = count_captures(grammar, query, before.encode(), "target")
    post, post_err = count_captures(grammar, query, after.encode(), "target")
    if pre_err or post_err:
        return False
    return post < pre if template.check == "node_count_decreased" else post > pre


def detect(path: str, before: str, after: str) -> Template | None:
    return next((t for t in TEMPLATES if signals(t, path, before, after)), None)
