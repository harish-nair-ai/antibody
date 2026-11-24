"""Trace-to-vaccine distiller (deterministic mode)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from antibody.distiller.extractor import Failure
from antibody.distiller.scaffolder import (
    Draft,
    draft_from_command,
    draft_from_template,
    publish,
)
from antibody.distiller.templates import detect
from antibody.errors import DistillError
from antibody.validator.linter import LintReport


@dataclass(frozen=True)
class Distiller:
    failures: Sequence[Failure]

    def draft(self) -> Draft:
        """First failure (in step order) with a distillable signature."""
        for failure in self.failures:
            if failure.command is not None:
                return draft_from_command(failure)
            if failure.path and (template := detect(failure.path, failure.before, failure.after)):
                return draft_from_template(template, failure)
        raise DistillError("no distillable failure signature found")

    def distill(self, out: Path) -> LintReport:
        return publish(self.draft(), out)
