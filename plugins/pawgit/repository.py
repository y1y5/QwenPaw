# -*- coding: utf-8 -*-
"""Low-level shadow Git repository operations."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, TypeVar, cast

from .support import EXCLUDE_PATTERNS, PawGitError

ConfigValue = TypeVar("ConfigValue", int, float)

DEFAULT_GC_KEEP_COUNT = 20
DEFAULT_GC_KEEP_DAYS = 7
DEFAULT_PRE_REWIND_RETENTION_DAYS = 7
DEFAULT_AUTO_DEBOUNCE_SECONDS = 1.5
DEFAULT_TIMELINE_LIMIT = 20
DEFAULT_TIMELINE_MAX_LIMIT = 200
DEFAULT_QUERY_PREVIEW_CHARS = 120
DEFAULT_MEMORY_QUIESCE_TIMEOUT = 30.0

DEFAULT_CONFIG = f"""\
[gc]
gc_keep_count = {DEFAULT_GC_KEEP_COUNT}
gc_keep_days = {DEFAULT_GC_KEEP_DAYS}
pre_rewind_retention_days = {DEFAULT_PRE_REWIND_RETENTION_DAYS}

[auto]
debounce_seconds = {DEFAULT_AUTO_DEBOUNCE_SECONDS}

[timeline]
default_limit = {DEFAULT_TIMELINE_LIMIT}
max_limit = {DEFAULT_TIMELINE_MAX_LIMIT}

[display]
query_preview_chars = {DEFAULT_QUERY_PREVIEW_CHARS}

[safety]
include_memory_quiesce_timeout = {DEFAULT_MEMORY_QUIESCE_TIMEOUT}
"""


class ShadowGitRepository:
    """Own the shadow repository, index, process environment, and file I/O."""

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir).expanduser().resolve()
        self.pawgit_dir = self.workspace_dir / ".pawgit"
        self.git_dir = self.pawgit_dir / "shadow.git"
        self.index_file = self.pawgit_dir / "index"
        self.config_file = self.pawgit_dir / "config.toml"
        self.config: dict[str, Any] = {}
        self._config_mtime_ns: int | None = None
        self._lock = asyncio.Lock()
        self._ensure_repo()

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_DIR": str(self.git_dir),
                "GIT_WORK_TREE": str(self.workspace_dir),
                "GIT_INDEX_FILE": str(self.index_file),
                "GIT_AUTHOR_NAME": "PawGit",
                "GIT_AUTHOR_EMAIL": "pawgit@localhost",
                "GIT_COMMITTER_NAME": "PawGit",
                "GIT_COMMITTER_EMAIL": "pawgit@localhost",
            },
        )
        return env

    def _run_git(self, *args: str, input_text: str | None = None) -> str:
        if shutil.which("git") is None:
            raise PawGitError("git executable was not found on PATH")
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.workspace_dir),
            env=self._git_env(),
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise PawGitError(f"git {' '.join(args)} failed: {detail}")
        return proc.stdout.strip()

    def _ensure_repo(self) -> None:
        self.pawgit_dir.mkdir(parents=True, exist_ok=True)
        if not self.git_dir.exists():
            subprocess.run(
                ["git", "init", "--bare", str(self.git_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
        info_dir = self.git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_path = info_dir / "exclude"
        existing = (
            exclude_path.read_text(encoding="utf-8").splitlines()
            if exclude_path.exists()
            else []
        )
        merged = list(existing)
        for pattern in EXCLUDE_PATTERNS:
            if pattern not in merged:
                merged.append(pattern)
        exclude_path.write_text("\n".join(merged) + "\n", encoding="utf-8")
        if not self.config_file.exists():
            self.config_file.write_text(
                DEFAULT_CONFIG,
                encoding="utf-8",
            )
        self.reload_config(force=True)

    def _load_config(self) -> dict[str, Any]:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        try:
            with self.config_file.open("rb") as config_stream:
                data = tomllib.load(config_stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PawGitError(
                f"Failed to load PawGit config {self.config_file}: {exc}",
            ) from exc
        if not isinstance(data, dict):
            raise PawGitError("PawGit config root must be a TOML table")
        return data

    def reload_config(self, *, force: bool = False) -> dict[str, Any]:
        """Reload config when its modification time changes."""
        try:
            mtime_ns = self.config_file.stat().st_mtime_ns
        except OSError as exc:
            raise PawGitError(
                f"Failed to stat PawGit config {self.config_file}: {exc}",
            ) from exc
        if force or mtime_ns != self._config_mtime_ns:
            self.config = self._load_config()
            self._config_mtime_ns = mtime_ns
        return self.config

    def config_number(
        self,
        section: str,
        key: str,
        default: ConfigValue,
        *,
        minimum: ConfigValue,
        maximum: ConfigValue,
    ) -> ConfigValue:
        """Read and validate one numeric config value."""
        config = self.reload_config()
        table = config.get(section, {})
        if not isinstance(table, dict):
            raise PawGitError(f"Config section [{section}] must be a table")
        value = table.get(key, default)
        expected_type = type(default)
        valid_type = isinstance(value, expected_type)
        if expected_type is float:
            valid_type = isinstance(value, (int, float))
        if isinstance(value, bool) or not valid_type:
            raise PawGitError(
                f"Config {section}.{key} must be " f"{expected_type.__name__}",
            )
        if expected_type is float:
            value = float(value)
        value = cast(ConfigValue, value)
        if value < minimum or value > maximum:
            raise PawGitError(
                f"Config {section}.{key} must be between "
                f"{minimum} and {maximum}",
            )
        return value

    def _ref_exists(self, ref: str) -> bool:
        proc = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=str(self.workspace_dir),
            env=self._git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return proc.returncode == 0

    def _read_blob(self, commit: str, rel: str) -> bytes:
        object_name = f"{commit}:{rel}"
        proc = subprocess.run(
            ["git", "cat-file", "blob", object_name],
            cwd=str(self.workspace_dir),
            env=self._git_env(),
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = proc.stderr.decode(errors="replace").strip()
            raise PawGitError(
                f"Snapshot {commit[:12]} does not contain session file {rel}"
                + (f": {detail}" if detail else ""),
            )
        return proc.stdout

    def _restore_paths(self, blobs: dict[str, bytes]) -> None:
        for rel, content in blobs.items():
            target = self.workspace_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent,
                    prefix=f".{target.name}.pawgit-",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    temp_path = Path(temp_file.name)
                os.replace(temp_path, target)
            except OSError as exc:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
                raise PawGitError(
                    f"Failed to restore session file {rel}: {exc}",
                ) from exc
