# -*- coding: utf-8 -*-
"""Transactional conversation and memory source rewind coordination."""

# The coordinator uses PawGit engine internals to keep one maintenance
# transaction across snapshot resolution, restore, and rollback.
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from .support import PawGitError, RewindResult
from .utils import session_file_path

MEMORY_PATHS = ("MEMORY.md", "memory")


class MemoryRewindCoordinator:
    """Run the Phase 2 memory rewind consistency protocol."""

    def __init__(self, engine, workspace: Any):
        self.engine = engine
        self.workspace = workspace

    async def rewind(
        self,
        *,
        target: str | None,
        session_id: str,
        user_id: str,
        channel: str,
        dry_run: bool,
    ) -> RewindResult:
        if not target:
            raise PawGitError(
                "Usage: /pawgit rewind <target> --include-memory "
                "[--dry-run | --confirm]",
            )
        if self.workspace is None:
            raise PawGitError(
                "Memory rewind requires a live QwenPaw workspace",
            )
        if dry_run:
            async with self.engine.maintenance_lock:
                async with self.engine._lock:
                    entry, blobs = await asyncio.to_thread(
                        self._build_restore_plan,
                        target,
                        session_id,
                        user_id,
                        channel,
                    )
                return self._result(
                    target,
                    entry.commit,
                    blobs,
                    None,
                    dry_run=True,
                )

        async with self.engine.maintenance_lock:
            self.engine.query_gate.clear()
            cron_suspended = False
            try:
                cron_suspended = await self._suspend_cron()
                await self._quiesce_tasks(
                    session_id=session_id,
                    channel=channel,
                )
                return await self._complete_despite_cancellation(
                    self._rewind_locked(
                        target=target,
                        session_id=session_id,
                        user_id=user_id,
                        channel=channel,
                    ),
                )
            finally:
                try:
                    if cron_suspended:
                        await self._restore_cron()
                finally:
                    self.engine.query_gate.set()

    async def _rewind_locked(
        self,
        *,
        target: str,
        session_id: str,
        user_id: str,
        channel: str,
    ) -> RewindResult:
        async with self.engine._lock:
            entry, target_blobs = await asyncio.to_thread(
                self._build_restore_plan,
                target,
                session_id,
                user_id,
                channel,
            )
            pre_ref = await asyncio.to_thread(
                self.engine._make_snapshot_sync,
                "pre-rewind",
                session_id,
                user_id,
                channel,
                None,
                f"Before memory rewind to {target}",
            )

        try:
            await asyncio.to_thread(self._apply_restore_plan, target_blobs)
        except Exception as exc:
            session_rel = next(
                rel for rel in target_blobs if rel.startswith("sessions/")
            )
            await self._rollback(pre_ref, session_rel)
            raise PawGitError(f"Memory rewind failed: {exc}") from exc

        return self._result(
            target,
            entry.commit,
            target_blobs,
            pre_ref,
            dry_run=False,
        )

    def _build_restore_plan(
        self,
        target: str,
        session_id: str,
        user_id: str,
        channel: str,
    ):
        entry = self.engine._resolve_target(
            target,
            session_id,
            user_id,
            channel,
        )
        session_path = session_file_path(
            self.engine.workspace_dir,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )
        session_rel = session_path.relative_to(
            self.engine.workspace_dir,
        ).as_posix()
        blobs = {
            session_rel: self.engine._read_blob(entry.commit, session_rel),
        }
        for rel in self._memory_files(entry.commit):
            blobs[rel] = self.engine._read_blob(entry.commit, rel)
        return entry, blobs

    def _memory_files(self, commit: str) -> tuple[str, ...]:
        output = self.engine._run_git(
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            *MEMORY_PATHS,
        )
        return tuple(line for line in output.splitlines() if line)

    def _apply_restore_plan(self, blobs: dict[str, bytes]) -> None:
        self._remove_path(self.engine.workspace_dir / "MEMORY.md")
        self._remove_path(self.engine.workspace_dir / "memory")
        self.engine._restore_paths(blobs)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

    async def _rollback(self, pre_ref: str, session_rel: str) -> None:
        try:
            blobs = self._build_restore_plan_from_commit(
                pre_ref,
                session_rel,
            )
            await asyncio.to_thread(self._apply_restore_plan, blobs)
        except Exception as exc:
            raise PawGitError(
                "Memory rewind failed and automatic rollback also failed: "
                f"{exc}. Safety ref: {pre_ref}",
            ) from exc

    def _build_restore_plan_from_commit(
        self,
        commit_or_ref: str,
        session_rel: str,
    ) -> dict[str, bytes]:
        commit = self.engine._run_git("rev-parse", commit_or_ref)
        memory_files = self._memory_files(commit)
        return {
            rel: self.engine._read_blob(commit, rel)
            for rel in (session_rel, *memory_files)
            if rel
        }

    async def _quiesce_tasks(
        self,
        *,
        session_id: str,
        channel: str,
    ) -> None:
        tracker = getattr(self.workspace, "task_tracker", None)
        if tracker is None:
            raise PawGitError("Workspace task tracker is not available")
        current_run = await self._current_run_key(
            session_id=session_id,
            channel=channel,
        )
        active = await tracker.list_active_tasks()
        if active and current_run is None:
            raise PawGitError(
                "Cannot safely identify the current rewind command task; "
                "no tasks or files were changed",
            )

        deadline = time.monotonic() + self.engine.memory_quiesce_timeout
        stop_requested: set[str] = set()
        while True:
            remaining = [
                run_key
                for run_key in await tracker.list_active_tasks()
                if run_key != current_run
            ]
            if not remaining:
                return
            for run_key in remaining:
                if run_key not in stop_requested:
                    await tracker.request_stop(run_key)
                    stop_requested.add(run_key)
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
        raise PawGitError(
            "Timed out while waiting for active tasks to stop; "
            "no files were changed",
        )

    async def _current_run_key(
        self,
        *,
        session_id: str,
        channel: str,
    ) -> str | None:
        chat_manager = getattr(self.workspace, "chat_manager", None)
        resolver = getattr(chat_manager, "get_chat_id_by_session", None)
        if not callable(resolver):
            return None
        try:
            return await resolver(session_id, channel)
        except Exception as exc:
            raise PawGitError(
                "Failed to identify the current rewind command task",
            ) from exc

    async def _suspend_cron(self) -> bool:
        cron = getattr(self.workspace, "cron_manager", None)
        if cron is None or not getattr(cron, "_started", False):
            return False
        await cron.stop()
        # AsyncIOScheduler.shutdown is dispatched onto the event loop.
        await asyncio.sleep(0)
        return True

    async def _restore_cron(self) -> None:
        cron = getattr(self.workspace, "cron_manager", None)
        if cron is not None:
            await cron.start()

    @staticmethod
    def _result(
        target: str,
        commit: str,
        blobs: dict[str, bytes],
        pre_ref: str | None,
        *,
        dry_run: bool,
    ) -> RewindResult:
        return RewindResult(
            target=target,
            commit=commit,
            restored_paths=tuple(sorted(blobs)),
            pre_rewind_ref=pre_ref,
            dry_run=dry_run,
            include_memory=True,
        )

    @staticmethod
    async def _complete_despite_cancellation(coroutine):
        """Finish the transaction before propagating cancellation."""
        task = asyncio.create_task(coroutine)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise
