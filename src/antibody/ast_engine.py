"""AST rule semantics, bit-identical with flywire (contracts §1.1, D2, D3).

Languages, parsers, compiled queries and pathspecs are cached once per process.
"""

from __future__ import annotations

import sys
import warnings
from enum import StrEnum
from functools import cache
from pathlib import PurePosixPath

import tree_sitter_python
import tree_sitter_typescript
from pathspec import PathSpec
from tree_sitter import Language, Parser, Query, QueryCursor, QueryError

from antibody.errors import GateFailure
from antibody.models import AstRule

EXT_TO_GRAMMAR = {".py": "python", ".ts": "typescript", ".tsx": "tsx"}
_SCOPE: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "typescript": ("typescript", "tsx"),
    "tsx": ("tsx",),
}


class Outcome(StrEnum):
    VIOLATION = "violation"
    ALLOW = "allow"
    SKIPPED = "skipped"  # parse error on either side (D3)
    NOT_APPLICABLE = "not_applicable"


def grammars_for(language: str) -> tuple[str, ...]:
    return _SCOPE[language]


@cache
def language(grammar: str) -> Language:
    if grammar == "python":
        return Language(tree_sitter_python.language())
    if grammar == "typescript":
        return Language(tree_sitter_typescript.language_typescript())
    return Language(tree_sitter_typescript.language_tsx())


@cache
def parser(grammar: str) -> Parser:
    return Parser(language(grammar))


@cache
def compiled_query(grammar: str, source: str) -> Query:
    return Query(language(grammar), source)


@cache
def _pathspec(patterns: tuple[str, ...]) -> PathSpec:
    # Contract §1.1 names "gitwildmatch"; pathspec>=1.0 deprecates the alias (same semantics).
    with warnings.catch_warnings(action="ignore", category=DeprecationWarning):
        return PathSpec.from_lines("gitwildmatch", patterns)


def compile_rule(rule: AstRule) -> None:
    """Compile the query for every grammar in the rule's scope; the capture must exist."""
    for grammar in grammars_for(rule.language):
        try:
            query = compiled_query(grammar, rule.query)
        except QueryError as exc:
            raise GateFailure(f"{rule.id}: query does not compile for {grammar}: {exc}") from exc
        names = {query.capture_name(i) for i in range(query.capture_count)}
        if rule.capture not in names:
            raise GateFailure(f"{rule.id}: capture @{rule.capture} not defined in query")


def rule_applies(rule: AstRule, rel_path: str) -> bool:
    if PurePosixPath(rel_path).suffix not in {
        ext for ext, g in EXT_TO_GRAMMAR.items() if g in grammars_for(rule.language)
    }:
        return False
    if not _pathspec(rule.paths).match_file(rel_path):
        return False
    return not (rule.exclude_paths and _pathspec(rule.exclude_paths).match_file(rel_path))


def count_captures(grammar: str, query: Query, source: bytes, capture: str) -> tuple[int, bool]:
    if not source:
        return 0, False
    tree = parser(grammar).parse(source)
    if tree.root_node.has_error:
        return 0, True
    return len(QueryCursor(query).captures(tree.root_node).get(capture, [])), False


def evaluate_ast(rule: AstRule, rel_path: str, pre: bytes, post: bytes) -> Outcome:
    if not rule_applies(rule, rel_path):
        return Outcome.NOT_APPLICABLE
    grammar = EXT_TO_GRAMMAR[PurePosixPath(rel_path).suffix]
    query = compiled_query(grammar, rule.query)
    pre_count, pre_err = count_captures(grammar, query, pre, rule.capture)
    post_count, post_err = count_captures(grammar, query, post, rule.capture)
    if pre_err or post_err:
        side = "pre" if pre_err else "post"
        print(f"warning: {rule.id}: {side} parse error in {rel_path}; rule skipped", file=sys.stderr)
        return Outcome.SKIPPED
    if rule.check == "node_count_decreased":
        violated = post_count < pre_count
    else:
        violated = post_count > pre_count
    return Outcome.VIOLATION if violated else Outcome.ALLOW
