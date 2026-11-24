"""Typer CLI: search, pull, remove, sync, verify, test, lint, distill (spec §8)."""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, ParamSpec, TypeVar

import typer

from antibody.distiller import Distiller, extractor
from antibody.errors import AntibodyError, DistillError, GateFailure, IntegrityError
from antibody.fsutil import find_workspace_root, resolve_inside
from antibody.installer import pack_installer
from antibody.registry.client import RegistryClient, registry_url
from antibody.registry.lockfile import read_lockfile, verify_lockfile
from antibody.validator.linter import LintReport, lint_pack
from antibody.validator.runner import run_pack_evals

app = typer.Typer(pretty_exceptions_enable=False, no_args_is_help=True, add_completion=False)
P = ParamSpec("P")
R = TypeVar("R")
RegistryOpt = Annotated[str | None, typer.Option("--registry", help="Registry URL or path.")]


def _guard(fn: Callable[P, R]) -> Callable[P, R]:
    """Known errors → `error: <msg>` + exit code; anything else → `error: internal:` exit 1."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise
        except AntibodyError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(exc.exit_code) from None
        except Exception as exc:
            typer.echo(f"error: internal: {type(exc).__name__}: {exc}", err=True)
            raise typer.Exit(1) from None

    return wrapper


def _root() -> Path:
    return find_workspace_root(Path.cwd())


def _version(value: bool) -> None:
    if value:
        typer.echo("antibody 0.1.0")
        raise typer.Exit(0)


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    """Vaccine registry for coding agents."""


def _print_report(report: LintReport) -> None:
    for r in report.results:
        status = "PASS" if r.ok else "FAIL"
        typer.echo(f"{status} {r.gate} {r.subject}" + (f"  {r.detail}" if r.detail else ""))


@app.command()
@_guard
def search(
    query: Annotated[str | None, typer.Argument()] = None,
    lang: Annotated[str | None, typer.Option("--lang")] = None,
    registry: RegistryOpt = None,
) -> None:
    """List matching packs from the registry."""
    for pack_id, pack in RegistryClient(registry_url(registry)).search(query, lang):
        rules = pack.versions[pack.latest].rule_count
        langs = ",".join(pack.languages)
        typer.echo(f"{pack_id}  {pack.latest}  {rules} rules  [{langs}]  {pack.description}")


@app.command()
@_guard
def pull(
    ref: str,
    patch_agents_md: Annotated[bool, typer.Option("--patch-agents-md")] = False,
    registry: RegistryOpt = None,
) -> None:
    """Fetch, verify, and install a pack (<ns/name>[@version])."""
    client = RegistryClient(registry_url(registry))
    entry = pack_installer.pull(_root(), client, ref, patch_agents_md)
    typer.echo(f"installed {entry.id}@{entry.version} -> {entry.install_path}")
    if entry.agents_md_patched:
        typer.echo("patched AGENTS.md")


@app.command()
@_guard
def remove(pack_id: str) -> None:
    """Remove an installed pack, its lock entry, envelope entry, and AGENTS.md block."""
    pack_installer.remove(_root(), pack_id)
    typer.echo(f"removed {pack_id}")


@app.command()
@_guard
def sync(registry: RegistryOpt = None) -> None:
    """Install exactly the versions pinned in antibody.lock."""
    root = _root()
    lock = read_lockfile(root)
    if lock is None:
        raise AntibodyError("no antibody.lock found; run `antibody pull <pack>` first")
    url = registry or os.environ.get("ANTIBODY_REGISTRY") or lock.registry_url
    changed = pack_installer.sync(root, RegistryClient(url))
    typer.echo(f"synced {len(lock.installed)} pack(s); reinstalled {len(changed)}")


@app.command()
@_guard
def verify() -> None:
    """Check every installed pack against its antibody.lock SHA-256."""
    root = _root()
    lock = read_lockfile(root)
    if lock is None:
        raise AntibodyError("no antibody.lock found")
    problems = verify_lockfile(root, lock)
    if problems:
        for problem in problems:
            typer.echo(f"tamper: {problem}", err=True)
        raise IntegrityError(f"{len(problems)} pack(s) drifted from antibody.lock")
    typer.echo(f"ok: {len(lock.installed)} pack(s) match antibody.lock")


@app.command()
@_guard
def lint(pack_dir: Path) -> None:
    """Run the 5 validation gates on a pack directory."""
    if not pack_dir.is_dir():
        raise AntibodyError(f"no such pack directory: {pack_dir}")
    report = lint_pack(pack_dir)
    _print_report(report)
    if not report.ok:
        raise GateFailure(f"{pack_dir}: lint gate failure")


@app.command(name="test")
@_guard
def test_cmd(pack_dir: Annotated[Path | None, typer.Argument()] = None) -> None:
    """Run the 5 gates plus pack pytest evals on a pack dir, or on every installed pack."""
    if pack_dir is not None:
        packs = [(str(pack_dir), pack_dir)]
    else:
        root = _root()
        lock = read_lockfile(root)
        if lock is None or not lock.installed:
            raise AntibodyError("no installed packs; run `antibody pull <pack>` first")
        packs = [(e.id, resolve_inside(root, e.install_path)) for e in lock.installed]
    failures: list[str] = []
    for name, directory in packs:
        if not directory.is_dir():
            raise AntibodyError(f"no such pack directory: {directory}")
        typer.echo(f"== {name}")
        report = lint_pack(directory)
        _print_report(report)
        failures += [
            f"{name}: gate {r.gate} {r.subject} failed" + (f" ({r.detail})" if r.detail else "")
            for r in report.results
            if not r.ok
        ]
        if list((directory / "evals").glob("test_*.py")):
            result = run_pack_evals(directory)
            typer.echo(result.stdout, nl=False)
            if result.returncode not in (0, 5):  # 5 = no tests collected
                failures.append(f"{name}: evals failed: {_eval_failure(result)}")
    if failures:
        raise GateFailure("; ".join(failures))


def _eval_failure(result: subprocess.CompletedProcess[str]) -> str:
    """The failing test ids, else the last line pytest (or python) wrote to stderr."""
    failed = [ln.removeprefix("FAILED ") for ln in result.stdout.splitlines()
              if ln.startswith(("FAILED ", "ERROR "))]  # fmt: skip
    if failed:
        return ", ".join(failed)
    lines = [ln for ln in (result.stderr + result.stdout).splitlines() if ln.strip()]
    return lines[-1] if lines else f"pytest exited {result.returncode}"


@app.command()
@_guard
def distill(
    out: Annotated[Path, typer.Option("--out")],
    from_tape: Annotated[list[Path] | None, typer.Option("--from-tape")] = None,
    from_flight_db: Annotated[Path | None, typer.Option("--from-flight-db")] = None,
    from_diff: Annotated[Path | None, typer.Option("--from-diff")] = None,
    step: Annotated[int | None, typer.Option("--step")] = None,
) -> None:
    """Distill a failure from a tape, flight log, or diff into a lint-verified draft pack."""
    sources = [bool(from_tape), from_flight_db is not None, from_diff is not None]
    if sum(sources) != 1:
        raise AntibodyError("give exactly one of --from-tape, --from-flight-db, --from-diff")
    for path in [*(from_tape or []), from_flight_db, from_diff]:
        if path is not None and not path.is_file():
            raise AntibodyError(f"no such file: {path}")
    if step is not None and from_diff is not None:
        raise AntibodyError("--step applies only to --from-tape / --from-flight-db")
    from antibody.distiller.scaffolder import ensure_out_available

    ensure_out_available(out)
    if from_tape:
        failures = [f for t in from_tape for f in extractor.from_tape(t, step)]
    elif from_flight_db is not None:
        failures = extractor.from_flight_db(from_flight_db, step)
    else:
        assert from_diff is not None
        failures = extractor.from_diff(from_diff, _root())
    report = Distiller(failures).distill(out)
    _print_report(report)
    if not report.ok:
        raise DistillError("distilled pack failed lint gates; nothing written")
    typer.echo(f"draft pack {out} passes all 5 gates")
