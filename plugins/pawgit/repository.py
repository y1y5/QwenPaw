# -*- coding: utf-8 -*-
"""Low-level shadow Git repository operations."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .support import EXCLUDE_PATTERNS, PawGitError


class ShadowGitRepository:
    """Own the shadow repository, index, process environment, and file I/O."""

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir).expanduser().resolve()
        self.pawgit_dir = self.workspace_dir / ".pawgit"
        self.git_dir = self.pawgit_dir / "shadow.git"
        self.index_file = self.pawgit_dir / "index"
        self.config_file = self.pawgit_dir / "config.toml"
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
                "[gc]\n"
                "gc_keep_count = 30\n"
                "gc_keep_days = 14\n"
                "pre_rewind_retention_days = 7\n"
                "[auto]\n"
                "debounce_seconds = 1.5\n",
                encoding="utf-8",
            )

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
