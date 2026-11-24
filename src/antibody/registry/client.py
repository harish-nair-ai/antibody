"""Static registry client: bundled://, local dir, file://, http(s):// static files, git+ URLs.

Every source is reduced to a local directory holding `index.json` + pack dirs; remote
sources are mirrored into `${XDG_CACHE_HOME:-~/.cache}/antibody/registries/<key>/` and
fall back to that mirror when the network is unavailable (contracts §7.4).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx
from pydantic import ValidationError

from antibody.errors import AntibodyError, describe
from antibody.fsutil import atomic_write, replace_dir, resolve_inside
from antibody.models import Index, IndexPack, IndexVersion

BUNDLED_URL = "bundled://starter_registry"
HTTP_TIMEOUT_S = 10.0
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "antibody"


def registry_url(explicit: str | None) -> str:
    return explicit or os.environ.get("ANTIBODY_REGISTRY") or BUNDLED_URL


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


class RegistryClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._index: Index | None = None
        self._root: Path | None = None

    # -- source resolution -------------------------------------------------------

    @property
    def _is_http(self) -> bool:
        return self.url.startswith(("https://", "http://"))

    def _mirror(self) -> Path:
        key = hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]
        return cache_root() / "registries" / key

    def root(self) -> Path:
        """Local directory containing `index.json` (synced from the network if remote)."""
        if self._root is None:
            self._root = self._resolve_root()
        return self._root

    def _resolve_root(self) -> Path:
        if self.url.startswith("bundled://"):
            if self.url != BUNDLED_URL:
                raise AntibodyError(f"unknown bundled registry {self.url!r}; use {BUNDLED_URL}")
            return Path(str(files("antibody") / "starter_registry"))
        if self.url.startswith("git+"):
            return self._sync_git()
        if self._is_http:
            return self._mirror()
        if self.url.startswith("file://"):
            return Path(unquote(urlparse(self.url).path))
        return Path(self.url).resolve()

    def _sync_git(self) -> Path:
        mirror = self._mirror()
        mirror.parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(dir=mirror.parent, prefix=".clone-"))
        try:
            cmd = [
                "git",
                "clone",
                "--depth",
                "1",
                "--quiet",
                "--",
                self.url[4:],
                str(scratch / "repo"),
            ]
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
            replace_dir(scratch / "repo", mirror, scratch)
        except (subprocess.CalledProcessError, OSError) as exc:
            if not (mirror / "index.json").is_file():
                detail = getattr(exc, "stderr", "") or str(exc)
                raise AntibodyError(
                    f"cannot clone registry {self.url}: {detail.strip()}; no cached copy"
                ) from exc
            _warn(f"using cached registry for {self.url} (clone failed)")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return mirror

    def _http_get(self, rel: str) -> bytes:
        url = self.url.rstrip("/") + "/" + quote(rel)
        response = httpx.get(url, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
        return response.content

    # -- index -----------------------------------------------------------------------

    def index(self) -> Index:
        if self._index is None:
            self._index = self._load_index()
        return self._index

    def _load_index(self) -> Index:
        root = self.root()
        if self._is_http:
            try:
                data = self._http_get("index.json")
                Index.model_validate(json.loads(data))
                atomic_write(root / "index.json", data)
            except (httpx.HTTPError, OSError) as exc:
                if not (root / "index.json").is_file():
                    raise AntibodyError(
                        f"cannot reach registry {self.url} ({exc}); no cached copy"
                    ) from exc
                _warn(f"using cached registry for {self.url} ({exc})")
            except ValidationError as exc:
                raise AntibodyError(f"invalid index.json at {self.url}: {describe(exc)}") from exc
            except ValueError as exc:
                raise AntibodyError(f"invalid index.json at {self.url}: {exc}") from exc
        path = root / "index.json"
        try:
            return Index.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise AntibodyError(f"registry {self.url} has no index.json") from exc
        except ValidationError as exc:
            raise AntibodyError(f"invalid index.json in {self.url}: {describe(exc)}") from exc
        except ValueError as exc:
            raise AntibodyError(f"invalid index.json in {self.url}: {exc}") from exc

    def resolve(self, pack_id: str, version: str | None) -> tuple[str, IndexVersion]:
        pack = self.index().packs.get(pack_id)
        if pack is None:
            raise AntibodyError(f"pack {pack_id!r} not found in registry {self.url}")
        chosen = version or pack.latest
        entry = pack.versions.get(chosen)
        if entry is None:
            known = ", ".join(sorted(pack.versions))
            raise AntibodyError(f"{pack_id}@{chosen} not found (available: {known})")
        return chosen, entry

    def search(self, query: str | None, lang: str | None) -> list[tuple[str, IndexPack]]:
        needle = (query or "").lower()
        return [
            (pack_id, pack)
            for pack_id, pack in sorted(self.index().packs.items())
            if (needle in pack_id.lower() or needle in pack.description.lower())
            and (lang is None or lang in pack.languages)
        ]

    # -- pack files --------------------------------------------------------------------

    def fetch(self, entry: IndexVersion, dest: Path) -> None:
        """Copy the pack dir for `entry` to `dest` (never executes anything from it)."""
        root = self.root()
        source = resolve_inside(root, entry.path)
        if self._is_http:
            self._download_pack(entry, source)
        if not source.is_dir():
            raise AntibodyError(f"pack directory {entry.path} missing in registry {self.url}")
        shutil.copytree(source, dest, symlinks=True, ignore=_COPY_IGNORE)

    def _download_pack(self, entry: IndexVersion, mirror_dir: Path) -> None:
        mirror_dir.parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(dir=mirror_dir.parent, prefix=".download-"))
        try:
            staged = scratch / "pack"
            staged.mkdir()
            for rel in entry.files:
                target = resolve_inside(staged, rel)
                data = self._http_get(f"{entry.path}/{rel}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            replace_dir(staged, mirror_dir, scratch)
        except (httpx.HTTPError, OSError) as exc:
            if not mirror_dir.is_dir():
                raise AntibodyError(
                    f"cannot download {entry.path} from {self.url} ({exc}); no cached copy"
                ) from exc
            _warn(f"using cached pack {entry.path} from {self.url} ({exc})")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
