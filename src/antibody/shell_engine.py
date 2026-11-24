"""Shell rule semantics, bit-identical with flywire (contracts §1.2, D4)."""

from __future__ import annotations

import re
from functools import cache

from antibody.models import ShellRule


def _skip_backtick(cmd: str, i: int) -> int:
    """`i` is just past an opening backtick; return the index of the closing one (or len)."""
    while i < len(cmd):
        if cmd[i] == "\\":
            i += 2
            continue
        if cmd[i] == "`":
            return i
        i += 1
    return len(cmd)


def _skip_subshell(cmd: str, i: int) -> int:
    """`i` is just past `$(`; return the index of the matching `)` (or len). Nesting-aware."""
    depth, in_single, in_double = 1, False, False
    while i < len(cmd):
        c = cmd[i]
        if in_single:
            in_single = c != "'"
        elif c == "\\":
            i += 1
        elif cmd.startswith("$(", i):
            i = _skip_subshell(cmd, i + 2)
        elif c == "`":
            i = _skip_backtick(cmd, i + 1)
        elif c == "'" and not in_double:
            in_single = True
        elif c == '"':
            in_double = not in_double
        elif not in_double and c == "(":
            depth += 1
        elif not in_double and c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(cmd)


def _norm(text: str) -> str:
    return " ".join(text.split())


def shell_units(cmd: str) -> list[str]:
    pipelines: list[list[str]] = []
    nested: list[str] = []
    commands: list[str] = []
    buf: list[str] = []

    def end_command() -> None:
        commands.append("".join(buf))
        buf.clear()

    def end_pipeline() -> None:
        end_command()
        pipelines.append(commands.copy())
        commands.clear()

    i, in_single, in_double = 0, False, False
    while i < len(cmd):
        c = cmd[i]
        if in_single:
            buf.append(c)
            in_single = c != "'"
            i += 1
        elif c == "\\":
            buf.append(cmd[i : i + 2])
            i += 2
        elif cmd.startswith("$(", i):
            end = _skip_subshell(cmd, i + 2)
            nested.append(cmd[i + 2 : end])
            buf.append(cmd[i : end + 1])
            i = end + 1
        elif c == "`":
            end = _skip_backtick(cmd, i + 1)
            nested.append(cmd[i + 1 : end])
            buf.append(cmd[i : end + 1])
            i = end + 1
        elif c == '"' or in_double or c == "'":
            if c == '"':
                in_double = not in_double
            elif not in_double:
                in_single = True
            buf.append(c)
            i += 1
        elif cmd.startswith(("&&", "||"), i):
            end_pipeline()
            i += 2
        elif c in ";\n" or (c == "&" and cmd[i - 1 : i] != ">" and cmd[i + 1 : i + 2] != ">"):
            end_pipeline()
            i += 1
        elif c == "|":
            end_command()
            i += 1
        else:
            buf.append(c)
            i += 1
    end_pipeline()

    units: list[str] = []
    for cmds in pipelines:
        pipeline = _norm("|".join(cmds))
        if pipeline:
            units.append(pipeline)
        if len(cmds) > 1:
            units.extend(u for u in map(_norm, cmds) if u)
    for body in nested:
        units.extend(shell_units(body))
    return units


@cache
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def shell_fires(rule: ShellRule, cmd: str) -> bool:
    regex = _regex(rule.pattern)
    return any(regex.search(unit) for unit in shell_units(cmd))
