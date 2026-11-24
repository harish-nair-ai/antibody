"""Known, user-facing errors. Each carries the documented CLI exit code (contracts §0.1)."""

from __future__ import annotations

from pydantic import ValidationError

EXIT_USAGE = 2
EXIT_GATE = 11
EXIT_HASH = 12
EXIT_DISTILL = 14


class AntibodyError(Exception):
    """An actionable error printed as `error: <msg>` with `exit_code` (no traceback)."""

    exit_code = EXIT_USAGE


class GateFailure(AntibodyError):
    exit_code = EXIT_GATE


class IntegrityError(AntibodyError):
    exit_code = EXIT_HASH


class DistillError(AntibodyError):
    exit_code = EXIT_DISTILL


WORDING = (
    ("Field required", "missing required key"),
    ("Input should be", "must be"),
    ("String should", "must"),
    ("List should", "must"),
)


def describe(exc: ValidationError) -> str:
    """`key.path: problem (got value)` per error, without pydantic model names or URLs."""
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(top level)"
        if err["type"] == "extra_forbidden":
            parts.append(f"{loc}: unknown key")
            continue
        msg = err["msg"].removeprefix("Value error, ")
        for pydantic_text, human in WORDING:
            msg = msg.replace(pydantic_text, human)
        got = err.get("input")
        suffix = "" if err["type"] == "missing" or isinstance(got, dict) else f" (got {got!r})"
        parts.append(f"{loc}: {msg}{suffix}")
    return "; ".join(parts)
