"""Known, user-facing errors. Each carries the documented CLI exit code (contracts §0.1)."""

from __future__ import annotations

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
