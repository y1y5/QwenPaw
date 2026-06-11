# -*- coding: utf-8 -*-
"""PawGit shadow-git engine for Phase 1."""

# Internal sync methods mirror their async entry points for asyncio.to_thread.
# pylint: disable=too-many-arguments

from __future__ import annotations

import asyncio
import time

from . import support as _support
from .repository import (
    DEFAULT_AUTO_DEBOUNCE_SECONDS,
    DEFAULT_GC_KEEP_COUNT,
    DEFAULT_GC_KEEP_DAYS,
    DEFAULT_PRE_REWIND_RETENTION_DAYS,
    DEFAULT_QUERY_PREVIEW_CHARS,
    DEFAULT_TIMELINE_LIMIT,
    DEFAULT_TIMELINE_MAX_LIMIT,
    ShadowGitRepository,
)
from .support import (
    SNAPSHOT_EXCLUDE_PATHSPECS,
    GcResult,
    PawGitError,
    RewindResult,
    TimelineEntry,
    encode_metadata,
    exclude_pattern_to_pathspec,
    latest_user_query,
    message_text,
    query_from_commit_message,
    ref_kind,
    ref_session_key,
    render_gc as render_gc_result,
    render_rewind as render_rewind_result,
    render_timeline as render_timeline_entries,
)
from .utils import sanitize_ref_component, session_file_path, session_key

# Public compatibility exports retained for existing integrations and tests.
EXCLUDE_PATTERNS = _support.EXCLUDE_PATTERNS

# Backward-compatible alias used by existing tests and integrations.
_exclude_pattern_to_pathspec = exclude_pattern_to_pathspec


class PawGitEngine(ShadowGitRepository):
    """Manage a workspace's shadow git repository."""

    @property
    def auto_debounce_seconds(self) -> float:
        return self.config_number(
            "auto",
            "debounce_seconds",
            DEFAULT_AUTO_DEBOUNCE_SECONDS,
            minimum=0.0,
            maximum=300.0,
        )

    @property
    def timeline_default_limit(self) -> int:
        return self.config_number(
            "timeline",
            "default_limit",
            DEFAULT_TIMELINE_LIMIT,
            minimum=1,
            maximum=self.timeline_max_limit,
        )

    @property
    def timeline_max_limit(self) -> int:
        return self.config_number(
            "timeline",
            "max_limit",
            DEFAULT_TIMELINE_MAX_LIMIT,
            minimum=1,
            maximum=10_000,
        )

    @property
    def query_preview_chars(self) -> int:
        return self.config_number(
            "display",
            "query_preview_chars",
            DEFAULT_QUERY_PREVIEW_CHARS,
            minimum=20,
            maximum=10_000,
        )

    @property
    def gc_keep_count(self) -> int:
        return self.config_number(
            "gc",
            "gc_keep_count",
            DEFAULT_GC_KEEP_COUNT,
            minimum=0,
            maximum=1_000_000,
        )

    @property
    def gc_keep_days(self) -> int:
        return self.config_number(
            "gc",
            "gc_keep_days",
            DEFAULT_GC_KEEP_DAYS,
            minimum=0,
            maximum=36_500,
        )

    @property
    def pre_rewind_retention_days(self) -> int:
        return self.config_number(
            "gc",
            "pre_rewind_retention_days",
            DEFAULT_PRE_REWIND_RETENTION_DAYS,
            minimum=0,
            maximum=36_500,
        )

    async def snapshot(
        self,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        message: str,
    ) -> str:
        """Create a permanent snapshot and return its display name."""
        ref = await self.make_snapshot(
            kind="snap",
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            name=message or None,
            message=message,
        )
        return ref.rsplit("/", 1)[-1]

    async def make_snapshot(
        self,
        *,
        kind: str,
        session_id: str,
        user_id: str,
        channel: str,
        name: str | None = None,
        message: str = "",
    ) -> str:
        """Create a parentless full-workspace snapshot and return its ref."""
        if kind not in {"auto", "snap", "pre-rewind"}:
            raise ValueError(f"Unsupported snapshot kind: {kind}")
        async with self._lock:
            return await asyncio.to_thread(
                self._make_snapshot_sync,
                kind,
                session_id,
                user_id,
                channel,
                name,
                message,
            )

    def _make_snapshot_sync(
        self,
        kind: str,
        session_id: str,
        user_id: str,
        channel: str,
        name: str | None,
        message: str,
    ) -> str:
        key = session_key(
            channel=channel,
            user_id=user_id,
            session_id=session_id,
        )
        now_ms = int(time.time() * 1000)
        if kind == "auto":
            ref = f"refs/auto/{key}/{now_ms}"
            subject = f"auto {key} {now_ms}"
        elif kind == "snap":
            label = sanitize_ref_component(
                name or message or f"snapshot-{now_ms}",
            )
            ref = f"refs/snap/{key}/{label}"
            suffix = 1
            base_ref = ref
            while self._ref_exists(ref):
                suffix += 1
                ref = f"{base_ref}-{suffix}"
            subject = f"snapshot {key} {label}"
        else:
            ref = f"refs/pre-rewind/{now_ms}-{key}"
            subject = f"pre-rewind {key} {now_ms}"

        # PawGit owns its snapshot boundary. Rebuild the parentless snapshot
        # index from scratch, bypassing every workspace .gitignore (including
        # nested coding_projects repos) while retaining PawGit exclusions.
        self._run_git("read-tree", "--empty")
        self._run_git(
            "add",
            "-f",
            "-A",
            "--",
            ".",
            *SNAPSHOT_EXCLUDE_PATHSPECS,
        )
        tree = self._run_git("write-tree")
        body = message.strip() if message else subject
        query = latest_user_query(
            self.workspace_dir,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )
        commit = self._run_git(
            "commit-tree",
            tree,
            input_text=f"{subject}\n\n{body}\n\n{encode_metadata(query)}\n",
        )
        self._run_git("update-ref", ref, commit)
        return ref

    async def timeline(
        self,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        limit: int | None = None,
        include_all: bool = False,
    ) -> list[TimelineEntry]:
        """Return timeline entries grouped by kind, newest first per group."""
        resolved_limit = (
            self.timeline_default_limit if limit is None else limit
        )
        resolved_limit = max(1, min(self.timeline_max_limit, resolved_limit))
        async with self._lock:
            return await asyncio.to_thread(
                self._timeline_sync,
                session_id,
                user_id,
                channel,
                resolved_limit,
                include_all,
            )

    def _timeline_sync(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        limit: int,
        include_all: bool,
    ) -> list[TimelineEntry]:
        key = session_key(
            channel=channel,
            user_id=user_id,
            session_id=session_id,
        )
        refs = self._list_pawgit_refs()
        if not include_all:
            refs = [
                item for item in refs if self._ref_session_key(item[0]) == key
            ]
        kind_priority = {"auto": 0, "snap": 1, "pre-rewind": 2}
        refs.sort(
            key=lambda item: (
                kind_priority.get(self._ref_kind(item[0]), 99),
                -self._entry_timestamp(item[0], item[1]),
            ),
        )
        entries = [
            self._entry_from_ref(ref, commit)
            for ref, commit in refs[: max(1, limit)]
        ]
        return entries

    @staticmethod
    def _ref_kind(ref: str) -> str:
        return ref_kind(ref)

    @staticmethod
    def _ref_session_key(ref: str) -> str:
        return ref_session_key(ref)

    def _list_pawgit_refs(self) -> list[tuple[str, str]]:
        output = self._run_git(
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/auto",
            "refs/snap",
            "refs/pre-rewind",
        )
        refs: list[tuple[str, str]] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) == 2:
                refs.append((parts[0], parts[1]))
        return refs

    def _entry_from_ref(self, ref: str, commit: str) -> TimelineEntry:
        parts = ref.split("/")
        kind = self._ref_kind(ref)
        key = self._ref_session_key(ref)
        name = "/".join(parts[3:]) if len(parts) > 3 else ""
        commit_message = self._run_git("log", "-1", "--format=%B", commit)
        subject = commit_message.splitlines()[0] if commit_message else ""
        return TimelineEntry(
            ref=ref,
            kind=kind,
            session_key=key,
            name=name,
            commit=commit,
            timestamp_ms=self._entry_timestamp(ref, commit),
            subject=subject,
            query=query_from_commit_message(commit_message),
        )

    def _latest_user_query(
        self,
        *,
        session_id: str,
        user_id: str,
        channel: str,
    ) -> str | None:
        return latest_user_query(
            self.workspace_dir,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )

    @staticmethod
    def _message_text(content: object) -> str | None:
        return message_text(content)

    @staticmethod
    def _query_from_commit_message(message: str) -> str | None:
        return query_from_commit_message(message)

    def _entry_timestamp(self, ref: str, commit: str) -> int:
        parts = ref.split("/")
        tail = parts[-1] if parts else ""
        if ref.startswith("refs/auto/") and tail.isdigit():
            return int(tail)
        if ref.startswith("refs/pre-rewind/"):
            maybe_ts = tail.split("-", 1)[0]
            if maybe_ts.isdigit():
                return int(maybe_ts)
        try:
            return (
                int(self._run_git("show", "-s", "--format=%ct", commit)) * 1000
            )
        except PawGitError:
            return 0

    async def rewind(
        self,
        *,
        target: str | None,
        session_id: str,
        user_id: str,
        channel: str,
        dry_run: bool = False,
    ) -> RewindResult:
        """Conv-only rewind to a timeline index, snapshot name, ref, or SHA."""
        if not target:
            raise PawGitError(
                "Usage: /rewind <N | snap_name | sha> [--dry-run]",
            )
        async with self._lock:
            return await asyncio.to_thread(
                self._rewind_sync,
                target,
                session_id,
                user_id,
                channel,
                dry_run,
            )

    def _rewind_sync(
        self,
        target: str,
        session_id: str,
        user_id: str,
        channel: str,
        dry_run: bool,
    ) -> RewindResult:
        entry = self._resolve_target(target, session_id, user_id, channel)
        conv_path = session_file_path(
            self.workspace_dir,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )
        rel = conv_path.relative_to(self.workspace_dir).as_posix()
        blob = self._read_blob(entry.commit, rel)
        pre_ref = None
        if not dry_run:
            pre_ref = self._make_snapshot_sync(
                "pre-rewind",
                session_id,
                user_id,
                channel,
                None,
                f"Before rewind to {target}",
            )
            self._restore_paths({rel: blob})
        return RewindResult(
            target=target,
            commit=entry.commit,
            restored_paths=(rel,),
            pre_rewind_ref=pre_ref,
            dry_run=dry_run,
        )

    def _resolve_target(
        self,
        target: str,
        session_id: str,
        user_id: str,
        channel: str,
    ) -> TimelineEntry:
        timeline = self._timeline_sync(
            session_id,
            user_id,
            channel,
            self.timeline_max_limit,
            False,
        )
        key = session_key(
            channel=channel,
            user_id=user_id,
            session_id=session_id,
        )
        snap_ref = f"refs/snap/{key}/{sanitize_ref_component(target)}"
        if self._ref_exists(snap_ref):
            return self._entry_from_ref(
                snap_ref,
                self._run_git("rev-parse", snap_ref),
            )
        if target.isdigit():
            index = int(target)
            if 1 <= index <= len(timeline):
                return timeline[index - 1]
        for entry in timeline:
            if entry.ref == target or entry.ref.endswith("/" + target):
                return entry
            if entry.commit.startswith(target):
                return entry
        try:
            commit = self._run_git(
                "rev-parse",
                "--verify",
                f"{target}^{{commit}}",
            )
            return TimelineEntry(
                ref=target,
                kind="sha",
                session_key=key,
                name=target,
                commit=commit,
                timestamp_ms=self._entry_timestamp(target, commit),
                subject=self._run_git("log", "-1", "--format=%s", commit),
                query=query_from_commit_message(
                    self._run_git("log", "-1", "--format=%B", commit),
                ),
            )
        except PawGitError as exc:
            if target.isdigit():
                raise PawGitError(
                    f"Timeline index out of range: {target}",
                ) from exc
            raise PawGitError(f"Unknown rewind target: {target}") from exc

    async def gc(
        self,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        compact: bool = False,
        all_sessions: bool = False,
        dry_run: bool = False,
        keep_count: int | None = None,
        keep_days: int | None = None,
        pre_rewind_days: int | None = None,
    ) -> GcResult:
        """Delete collectible auto/pre-rewind refs and run git gc."""
        resolved_keep_count = (
            self.gc_keep_count if keep_count is None else keep_count
        )
        resolved_keep_days = (
            self.gc_keep_days if keep_days is None else keep_days
        )
        resolved_pre_days = (
            self.pre_rewind_retention_days
            if pre_rewind_days is None
            else pre_rewind_days
        )
        async with self._lock:
            return await asyncio.to_thread(
                self._gc_sync,
                session_id,
                user_id,
                channel,
                compact,
                all_sessions,
                dry_run,
                resolved_keep_count,
                resolved_keep_days,
                resolved_pre_days,
            )

    def _gc_sync(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        compact: bool,
        all_sessions: bool,
        dry_run: bool,
        keep_count: int,
        keep_days: int,
        pre_rewind_days: int,
    ) -> GcResult:
        key = session_key(
            channel=channel,
            user_id=user_id,
            session_id=session_id,
        )
        refs = self._list_pawgit_refs()
        now_ms = int(time.time() * 1000)
        keep_cutoff_ms = now_ms - keep_days * 24 * 60 * 60 * 1000
        pre_cutoff_ms = now_ms - pre_rewind_days * 24 * 60 * 60 * 1000

        auto_refs = [
            item
            for item in refs
            if item[0].startswith("refs/auto/")
            and (all_sessions or item[0].split("/")[2] == key)
        ]
        auto_refs.sort(
            key=lambda item: self._entry_timestamp(item[0], item[1]),
            reverse=True,
        )
        kept_auto = {ref for ref, _ in auto_refs[:keep_count]}
        delete_refs: list[str] = []
        keep_refs: list[str] = []
        for ref, commit in auto_refs:
            ts = self._entry_timestamp(ref, commit)
            if compact:
                delete_refs.append(ref)
            elif ref in kept_auto or ts >= keep_cutoff_ms:
                keep_refs.append(ref)
            else:
                delete_refs.append(ref)

        pre_rewind_refs = [
            item
            for item in refs
            if item[0].startswith("refs/pre-rewind/")
            and (all_sessions or self._ref_session_key(item[0]) == key)
        ]
        for ref, commit in pre_rewind_refs:
            ts = self._entry_timestamp(ref, commit)
            if compact:
                delete_refs.append(ref)
            elif ts < pre_cutoff_ms:
                delete_refs.append(ref)
            else:
                keep_refs.append(ref)

        if not dry_run:
            for ref in delete_refs:
                self._run_git("update-ref", "-d", ref)
            self._run_git("gc", "--prune=now")
        return GcResult(
            deleted_refs=tuple(delete_refs),
            kept_refs=tuple(keep_refs),
            dry_run=dry_run,
        )

    def render_timeline(self, entries: list[TimelineEntry]) -> str:
        return render_timeline_entries(
            entries,
            query_preview_chars=self.query_preview_chars,
        )

    def render_rewind(self, result: RewindResult) -> str:
        return render_rewind_result(result)

    def render_gc(self, result: GcResult) -> str:
        return render_gc_result(result)
