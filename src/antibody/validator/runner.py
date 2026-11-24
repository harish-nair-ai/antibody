"""Executes positive/negative fixtures and (only on `antibody test`) pack pytest evals.

Public functions used by pack `evals/test_vaccines.py` (contracts §7.2):
`run_positive_antigen(fixture_dir)` and `run_negative_control(fixture_dir)`.
"""

from __future__ import annotations

import math
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal

from antibody.ast_engine import Outcome, evaluate_ast
from antibody.errors import GateFailure
from antibody.loaders import load_manifest, load_vaccine_rule
from antibody.models import AstRule, Rule, Vaccine
from antibody.shell_engine import shell_fires

Kind = Literal["positive", "negative"]
LATENCY_ITERATIONS = 50


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise GateFailure(f"missing fixture: {path}") from exc


def _evaluator(
    rule: Rule, fixture_dir: Path, kind: Kind, target: str | None
) -> Callable[[], Outcome]:
    if isinstance(rule, AstRule):
        if target is None:
            raise GateFailure(f"{rule.id}: AST vaccine needs target_sample_path")
        ext = PurePosixPath(target).suffix
        pre = _read(fixture_dir / f"{kind}_before{ext}")
        post = _read(fixture_dir / f"{kind}_after{ext}")
        ast_rule, rel_path = rule, target
        return lambda: evaluate_ast(ast_rule, rel_path, pre, post)
    shell_rule = rule
    command = _read(fixture_dir / f"{kind}.sh").decode("utf-8").strip()
    return lambda: Outcome.VIOLATION if shell_fires(shell_rule, command) else Outcome.ALLOW


def evaluate_fixture(rule: Rule, fixture_dir: Path, kind: Kind, target: str | None) -> Outcome:
    return _evaluator(rule, fixture_dir, kind, target)()


def measure_latency_ms(rule: Rule, fixture_dir: Path, target: str | None) -> tuple[float, float]:
    """(median, p99) over 50 evaluations of the positive pair (parse pre+post + query)."""
    evaluate = _evaluator(rule, fixture_dir, "positive", target)
    samples: list[float] = []
    for _ in range(LATENCY_ITERATIONS):
        start = time.perf_counter()
        evaluate()
        samples.append((time.perf_counter() - start) * 1000.0)
    ranked = sorted(samples)
    p99 = ranked[math.ceil(0.99 * len(ranked)) - 1]
    return statistics.median(ranked), p99


def _locate(fixture_dir: Path) -> tuple[Vaccine, Rule]:
    """`<pack>/evals/fixtures/<rule_id>` → that vaccine and its rule."""
    pack_dir = fixture_dir.resolve().parents[2]
    manifest = load_manifest(pack_dir)
    for vaccine in manifest.vaccines:
        if vaccine.rule_id == fixture_dir.name:
            return vaccine, load_vaccine_rule(pack_dir, vaccine)
    raise GateFailure(f"no vaccine {fixture_dir.name!r} in {pack_dir / 'pack.toml'}")


def run_positive_antigen(fixture_dir: Path) -> Outcome:
    vaccine, rule = _locate(fixture_dir)
    return evaluate_fixture(rule, fixture_dir, "positive", vaccine.target_sample_path)


def run_negative_control(fixture_dir: Path) -> Outcome:
    vaccine, rule = _locate(fixture_dir)
    return evaluate_fixture(rule, fixture_dir, "negative", vaccine.target_sample_path)


def run_pack_evals(pack_dir: Path) -> subprocess.CompletedProcess[str]:
    """`python -m pytest <pack>/evals -q`, isolated from the host project's pytest config."""
    evals = (pack_dir / "evals").resolve()
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(evals),
        "-q",
        "-c",
        os.devnull,
        "--rootdir",
        str(evals),
        "--confcutdir",
        str(evals),
        "-p",
        "no:cacheprovider",
    ]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(cmd, cwd=evals, env=env, capture_output=True, text=True, check=False)
