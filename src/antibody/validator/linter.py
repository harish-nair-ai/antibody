"""5-gate dual-assertion linter (spec §5.2, contracts §7.1).

Gates: schema → ast_compile → positive_antigen → negative_control → latency.
A vaccine that fails a gate is not evaluated by later gates (they would be meaningless).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from antibody.ast_engine import Outcome, compile_rule, rule_applies
from antibody.errors import AntibodyError, GateFailure
from antibody.fsutil import resolve_inside
from antibody.loaders import load_manifest, load_vaccine_rule, pack_path
from antibody.models import AstRule, PackManifest, Rule, Vaccine
from antibody.validator.runner import Kind, evaluate_fixture, measure_latency_ms

GATES = ("schema", "ast_compile", "positive_antigen", "negative_control", "latency")
MEDIAN_BUDGET_MS = 5.0
P99_BUDGET_MS = 15.0


@dataclass(frozen=True)
class GateResult:
    gate: str
    subject: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class LintReport:
    pack_dir: Path
    results: tuple[GateResult, ...]

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)


def _check_vaccine_schema(pack_dir: Path, vaccine: Vaccine) -> tuple[Rule, Path]:
    rule = load_vaccine_rule(pack_dir, vaccine)
    fixture_dir = pack_path(pack_dir, vaccine.fixture_dir)
    target = vaccine.target_sample_path
    if isinstance(rule, AstRule):
        if target is None:
            raise GateFailure("AST vaccine requires target_sample_path")
        try:
            resolve_inside(Path("/workspace"), target)
        except AntibodyError as exc:
            raise GateFailure(f"target_sample_path: {exc}") from exc
    elif target is not None:
        raise GateFailure("shell vaccine must omit target_sample_path")
    return rule, fixture_dir


def _check_rule_files(pack_dir: Path, manifest: PackManifest) -> GateResult:
    """Every rules/*.toml is referenced by exactly one vaccine; rule ids are unique."""
    on_disk = {p.relative_to(pack_dir).as_posix() for p in (pack_dir / "rules").glob("*.toml")}
    referenced = [v.rule_file for v in manifest.vaccines]
    problems = [f"{f} not referenced by any vaccine" for f in sorted(on_disk - set(referenced))]
    problems += [
        f"{f} referenced more than once" for f in sorted(set(referenced)) if referenced.count(f) > 1
    ]
    ids = [v.rule_id for v in manifest.vaccines]
    problems += [f"duplicate rule_id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
    return GateResult("schema", "pack", not problems, "; ".join(problems))


def _fixture_gate(kind: Kind, rule: Rule, fixture_dir: Path, vaccine: Vaccine) -> GateResult:
    gate = "positive_antigen" if kind == "positive" else "negative_control"
    expected = Outcome.VIOLATION if kind == "positive" else Outcome.ALLOW
    target = vaccine.target_sample_path
    if isinstance(rule, AstRule) and target is not None and not rule_applies(rule, target):
        return GateResult(gate, rule.id, False, f"rule does not apply to {target}")
    try:
        outcome = evaluate_fixture(rule, fixture_dir, kind, target)
    except (GateFailure, UnicodeDecodeError) as exc:
        return GateResult(gate, rule.id, False, str(exc))
    return GateResult(gate, rule.id, outcome is expected, f"expected {expected}, got {outcome}")


def _latency_gate(rule: Rule, fixture_dir: Path, vaccine: Vaccine) -> GateResult:
    median, p99 = measure_latency_ms(rule, fixture_dir, vaccine.target_sample_path)
    ok = median < MEDIAN_BUDGET_MS and p99 < P99_BUDGET_MS
    detail = f"median {median:.3f} ms, p99 {p99:.3f} ms"
    return GateResult("latency", rule.id, ok, detail)


def _lint_vaccine(pack_dir: Path, vaccine: Vaccine) -> list[GateResult]:
    subject = vaccine.rule_id
    try:
        rule, fixture_dir = _check_vaccine_schema(pack_dir, vaccine)
    except GateFailure as exc:
        return [GateResult("schema", subject, False, str(exc))]
    results = [GateResult("schema", subject, True)]
    try:
        if isinstance(rule, AstRule):
            compile_rule(rule)
    except GateFailure as exc:
        return [*results, GateResult("ast_compile", subject, False, str(exc))]
    results.append(GateResult("ast_compile", subject, True))
    fixtures = [
        _fixture_gate("positive", rule, fixture_dir, vaccine),
        _fixture_gate("negative", rule, fixture_dir, vaccine),
    ]
    results += fixtures
    if all(r.ok for r in fixtures):
        results.append(_latency_gate(rule, fixture_dir, vaccine))
    else:
        results.append(GateResult("latency", subject, False, "not measured: fixture gates failed"))
    return results


def lint_pack(pack_dir: Path) -> LintReport:
    try:
        manifest = load_manifest(pack_dir)
    except GateFailure as exc:
        return LintReport(pack_dir, (GateResult("schema", "pack", False, str(exc)),))
    results = [_check_rule_files(pack_dir, manifest)]
    for vaccine in manifest.vaccines:
        results += _lint_vaccine(pack_dir, vaccine)
    return LintReport(pack_dir, tuple(results))
