# -*- coding: utf-8 -*-
"""PawGit Phase 1 regression and slash-command tests."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.runner.session import sanitize_filename

pytestmark = pytest.mark.unit
PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "plugins"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from pawgit.engine import (  # noqa: E402
    GcResult,
    PawGitEngine,
    PawGitError,
    RewindResult,
)
from pawgit.handlers import (  # noqa: E402
    GcCommandHandler,
    RewindCommandHandler,
    SnapshotCommandHandler,
    TimelineCommandHandler,
    _parse_limit,
)

DEFAULT_SESSION = {
    "channel": "console",
    "user_id": "default",
    "session_id": "console:default",
}


def _session_path(
    workspace: Path,
    *,
    channel: str = "console",
    user_id: str = "default",
    session_id: str = "console:default",
) -> Path:
    directory = workspace / "sessions" / sanitize_filename(channel)
    filename = f"{sanitize_filename(user_id)}_{sanitize_filename(session_id)}.json"
    return directory / filename


def _write_session(
    workspace: Path,
    *,
    channel: str = "console",
    user_id: str = "default",
    session_id: str = "console:default",
    queries: tuple[str, ...] = ("hello",),
    assistant_after_last: bool = True,
) -> Path:
    path = _session_path(
        workspace,
        channel=channel,
        user_id=user_id,
        session_id=session_id,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    content: list[list[object]] = []
    for query in queries:
        content.append(
            [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": query}],
                },
                [],
            ],
        )
        content.append(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"answer: {query}"}],
                },
                [],
            ],
        )
    if not assistant_after_last:
        content.pop()
    path.write_text(
        json.dumps(
            {"agent": {"memory": {"content": content}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


async def _auto_snapshot(
    engine: PawGitEngine,
    *,
    channel: str = "console",
    user_id: str = "default",
    session_id: str = "console:default",
) -> str:
    ref = await engine.make_snapshot(
        kind="auto",
        channel=channel,
        user_id=user_id,
        session_id=session_id,
    )
    # Auto refs use millisecond names; avoid collisions in repeated test calls.
    await asyncio.sleep(0.002)
    return ref


def _control_context(raw: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        workspace=object(),
        channel=SimpleNamespace(channel="console"),
        session_id="console:default",
        user_id="default",
        args={"_raw_args": raw},
    )


@pytest.fixture
def engine(tmp_path: Path) -> PawGitEngine:
    return PawGitEngine(tmp_path)


# ---------------------------------------------------------------------------
# Critical regressions
# ---------------------------------------------------------------------------


async def test_snapshot_bypasses_workspace_gitignore_but_honors_pawgit_excludes(
    engine: PawGitEngine,
    tmp_path: Path,
):
    session = _write_session(tmp_path, queries=("ignored session",))
    ignored_file = tmp_path / "ignored-by-workspace.txt"
    ignored_file.write_text("must be captured", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "sessions/\nignored-by-workspace.txt\n",
        encoding="utf-8",
    )

    nested_repo = tmp_path / "coding_projects" / "unborn"
    nested_repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", str(nested_repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    (nested_repo / "draft.txt").write_text("must be excluded", encoding="utf-8")
    (tmp_path / "debug.log").write_text("must be excluded", encoding="utf-8")

    ref = await _auto_snapshot(engine)
    tree = set(engine._run_git("ls-tree", "-r", "--name-only", ref).splitlines())

    assert session.relative_to(tmp_path).as_posix() in tree
    assert "ignored-by-workspace.txt" in tree
    assert ".gitignore" in tree
    assert "coding_projects/unborn/draft.txt" not in tree
    assert "debug.log" not in tree
    exclude = (engine.git_dir / "info" / "exclude").read_text(encoding="utf-8")
    assert "/coding_projects/" in exclude


async def test_all_snapshot_kinds_store_latest_unicode_query(
    engine: PawGitEngine,
    tmp_path: Path,
):
    latest_query = "请保留这条最新问题：修复登录流程"
    _write_session(tmp_path, queries=("old query", latest_query))

    auto_ref = await _auto_snapshot(engine)
    snap_name = await engine.snapshot(message="中文里程碑", **DEFAULT_SESSION)
    await engine.rewind(target=auto_ref, **DEFAULT_SESSION)
    pre_refs = engine._run_git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/pre-rewind",
    ).splitlines()

    refs = [
        auto_ref,
        f"refs/snap/console-default-console--default/{snap_name}",
        *pre_refs,
    ]
    assert len(refs) == 3
    for ref in refs:
        message = engine._run_git("log", "-1", "--format=%B", ref)
        assert engine._query_from_commit_message(message) == latest_query

    rendered = engine.render_timeline(await engine.timeline(**DEFAULT_SESSION))
    assert latest_query in rendered
    assert "�" not in rendered


async def test_snapshots_are_parentless_and_packed_refs_remain_visible(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)
    ref = await _auto_snapshot(engine)
    commit = engine._run_git("rev-parse", ref)

    assert engine._run_git("show", "-s", "--format=%P", commit) == ""

    engine._run_git("pack-refs", "--all")
    assert not (engine.git_dir / ref).exists()
    entries = await engine.timeline(**DEFAULT_SESSION)
    assert [entry.ref for entry in entries] == [ref]


# ---------------------------------------------------------------------------
# /snapshot
# ---------------------------------------------------------------------------


async def test_snapshot_without_message_gets_non_numeric_generated_name(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)

    name = await engine.snapshot(message="", **DEFAULT_SESSION)

    assert name.startswith("snapshot-")
    assert not name.isdigit()
    assert engine._ref_exists(
        f"refs/snap/console-default-console--default/{name}",
    )


async def test_snapshot_with_message_uses_sanitized_unique_name(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)

    first = await engine.snapshot(message="Release candidate #1", **DEFAULT_SESSION)
    second = await engine.snapshot(message="Release candidate #1", **DEFAULT_SESSION)

    assert first == "Release-candidate-1"
    assert second == "Release-candidate-1-2"


# ---------------------------------------------------------------------------
# /timeline
# ---------------------------------------------------------------------------


async def test_timeline_defaults_to_current_session_and_groups_checkpoint_types(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path, queries=("current",))
    auto_ref = await _auto_snapshot(engine)
    await engine.snapshot(message="milestone", **DEFAULT_SESSION)
    await engine.rewind(target=auto_ref, **DEFAULT_SESSION)

    _write_session(
        tmp_path,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
        queries=("other channel",),
    )
    await _auto_snapshot(
        engine,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )

    entries = await engine.timeline(**DEFAULT_SESSION)

    assert [entry.kind for entry in entries] == [
        "auto",
        "snap",
        "pre-rewind",
    ]
    assert all(
        entry.session_key == "console-default-console--default" for entry in entries
    )


async def test_timeline_limit_and_all_sessions(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)
    for _ in range(3):
        await _auto_snapshot(engine)
    _write_session(
        tmp_path,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )
    await _auto_snapshot(
        engine,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )

    limited = await engine.timeline(limit=2, **DEFAULT_SESSION)
    current = await engine.timeline(**DEFAULT_SESSION)
    all_entries = await engine.timeline(include_all=True, **DEFAULT_SESSION)

    assert len(limited) == 2
    assert len(current) == 3
    assert len(all_entries) == 4


async def test_timeline_table_format_contains_dates_queries_and_rewind_commands(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path, queries=("query with | pipe\nand newline",))
    auto_ref = await _auto_snapshot(engine)
    snap_name = await engine.snapshot(message="release-v1", **DEFAULT_SESSION)
    await engine.rewind(target=auto_ref, **DEFAULT_SESSION)

    entries = await engine.timeline(**DEFAULT_SESSION)
    rendered = engine.render_timeline(entries)

    assert rendered.startswith("# PawGit Timeline")
    assert "## AUTO Checkpoints" in rendered
    assert "## SNAPSHOT Checkpoints" in rendered
    assert "## PRE-REWIND Checkpoints" in rendered
    assert "| # | Snapshot | SHA | Date | Query | Rewind |" in rendered
    assert "query with \\| pipe and newline" in rendered
    assert "`/rewind 1`" in rendered
    assert f"`/rewind {snap_name}`" in rendered
    assert all(f"`/rewind {entry.commit[:12]}`" in rendered for entry in entries)
    assert "+0" in rendered or "-0" in rendered


def test_timeline_limit_parser_clamps_and_recovers_from_invalid_values():
    assert _parse_limit("") == 20
    assert _parse_limit("--limit=5") == 5
    assert _parse_limit("--limit=0") == 1
    assert _parse_limit("--limit=999") == 200
    assert _parse_limit("--limit=oops") == 20


# ---------------------------------------------------------------------------
# /rewind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_kind", ["index", "snapshot_name", "sha"])
async def test_rewind_by_index_snapshot_name_and_sha(
    engine: PawGitEngine,
    tmp_path: Path,
    target_kind: str,
):
    session = _write_session(tmp_path, queries=("before",))
    snap_name = await engine.snapshot(message="before", **DEFAULT_SESSION)
    commit = engine._run_git(
        "rev-parse",
        f"refs/snap/console-default-console--default/{snap_name}",
    )
    session.write_text('{"changed": true}', encoding="utf-8")

    target = {
        "index": "1",
        "snapshot_name": snap_name,
        "sha": commit[:12],
    }[target_kind]
    result = await engine.rewind(target=target, **DEFAULT_SESSION)

    assert '"before"' in session.read_text(encoding="utf-8")
    assert result.commit == commit
    assert result.pre_rewind_ref


async def test_rewind_index_prefers_auto_and_numeric_snapshot_name_is_exact(
    engine: PawGitEngine,
    tmp_path: Path,
):
    session = _write_session(tmp_path, queries=("auto version",))
    auto_ref = await _auto_snapshot(engine)
    _write_session(tmp_path, queries=("numeric snapshot version",))
    await engine.make_snapshot(
        kind="snap",
        name="1234567890",
        **DEFAULT_SESSION,
    )

    assert engine._resolve_target("1", **DEFAULT_SESSION).ref == auto_ref
    numeric = engine._resolve_target("1234567890", **DEFAULT_SESSION)
    assert numeric.kind == "snap"
    assert numeric.name == "1234567890"

    session.write_text('{"changed": true}', encoding="utf-8")
    await engine.rewind(target="1", **DEFAULT_SESSION)
    assert "auto version" in session.read_text(encoding="utf-8")


async def test_rewind_only_restores_current_session(
    engine: PawGitEngine,
    tmp_path: Path,
):
    current = _write_session(tmp_path, queries=("current before",))
    other = _write_session(
        tmp_path,
        user_id="other",
        session_id="console:other",
        queries=("other before",),
    )
    await engine.snapshot(message="before", **DEFAULT_SESSION)
    current.write_text('{"current": "after"}', encoding="utf-8")
    other.write_text('{"other": "after"}', encoding="utf-8")

    await engine.rewind(target="before", **DEFAULT_SESSION)

    assert "current before" in current.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == '{"other": "after"}'


async def test_rewind_missing_session_blob_preserves_live_file_and_creates_no_safety_ref(
    engine: PawGitEngine,
    tmp_path: Path,
):
    (tmp_path / "notes.txt").write_text("unrelated", encoding="utf-8")
    unrelated_ref = await _auto_snapshot(
        engine,
        user_id="other",
        session_id="console:other",
    )
    commit = engine._run_git("rev-parse", unrelated_ref)
    current = _write_session(tmp_path, queries=("must survive",))
    original = current.read_bytes()

    with pytest.raises(PawGitError, match="does not contain session file"):
        await engine.rewind(target=commit, **DEFAULT_SESSION)

    assert current.read_bytes() == original
    assert (
        engine._run_git(
            "for-each-ref",
            "--format=%(refname)",
            "refs/pre-rewind",
        )
        == ""
    )


async def test_rewind_dry_run_does_not_modify_file_or_create_pre_rewind(
    engine: PawGitEngine,
    tmp_path: Path,
):
    session = _write_session(tmp_path, queries=("before",))
    ref = await _auto_snapshot(engine)
    session.write_text('{"changed": true}', encoding="utf-8")

    result = await engine.rewind(target=ref, dry_run=True, **DEFAULT_SESSION)

    assert result.dry_run is True
    assert result.pre_rewind_ref is None
    assert session.read_text(encoding="utf-8") == '{"changed": true}'


# ---------------------------------------------------------------------------
# /gc
# ---------------------------------------------------------------------------


async def test_gc_dry_run_reports_without_deleting_and_render_is_readable(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)
    auto_ref = await _auto_snapshot(engine)

    result = await engine.gc(compact=True, dry_run=True, **DEFAULT_SESSION)
    rendered = engine.render_gc(result)

    assert auto_ref in result.deleted_refs
    assert engine._ref_exists(auto_ref)
    assert "**Status:** Preview (no changes made)" in rendered
    assert "| Would remove refs | 1 |" in rendered
    assert "| Type | Session | Name |" in rendered


async def test_gc_compact_deletes_current_auto_but_preserves_snap_and_other_session(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)
    current_auto = await _auto_snapshot(engine)
    snap_name = await engine.snapshot(message="keep-me", **DEFAULT_SESSION)
    _write_session(
        tmp_path,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )
    other_auto = await _auto_snapshot(
        engine,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )

    result = await engine.gc(compact=True, **DEFAULT_SESSION)

    assert current_auto in result.deleted_refs
    assert not engine._ref_exists(current_auto)
    assert engine._ref_exists(
        f"refs/snap/console-default-console--default/{snap_name}",
    )
    assert engine._ref_exists(other_auto)


async def test_gc_all_sessions_and_pre_rewind_retention(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_session(tmp_path)
    console_auto = await _auto_snapshot(engine)
    await engine.rewind(target=console_auto, **DEFAULT_SESSION)
    _write_session(
        tmp_path,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )
    ding_auto = await _auto_snapshot(
        engine,
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
    )

    result = await engine.gc(
        compact=True,
        all_sessions=True,
        dry_run=True,
        pre_rewind_days=0,
        **DEFAULT_SESSION,
    )

    assert console_auto in result.deleted_refs
    assert ding_auto in result.deleted_refs
    assert any(ref.startswith("refs/pre-rewind/") for ref in result.deleted_refs)


# ---------------------------------------------------------------------------
# Slash-command argument forwarding
# ---------------------------------------------------------------------------


async def test_slash_handlers_forward_snapshot_timeline_rewind_and_gc_flags(
    monkeypatch: pytest.MonkeyPatch,
):
    import pawgit.handlers as handlers

    fake = SimpleNamespace(
        snapshot=AsyncMock(return_value="snapshot-123"),
        timeline=AsyncMock(return_value=[]),
        rewind=AsyncMock(
            return_value=RewindResult(
                target="abc",
                commit="a" * 40,
                restored_paths=("sessions/console/default.json",),
                pre_rewind_ref=None,
                dry_run=True,
            ),
        ),
        gc=AsyncMock(
            return_value=GcResult(
                deleted_refs=(),
                kept_refs=(),
                dry_run=True,
            ),
        ),
        render_timeline=lambda entries: "timeline",
        render_rewind=lambda result: "rewind",
        render_gc=lambda result: "gc",
    )
    monkeypatch.setattr(
        handlers.REGISTRY,
        "get_for_workspace",
        lambda workspace: fake,
    )

    snapshot_output = await SnapshotCommandHandler().handle(
        _control_context("release v1"),
    )
    timeline_output = await TimelineCommandHandler().handle(
        _control_context("--limit=7 --all"),
    )
    rewind_output = await RewindCommandHandler().handle(
        _control_context("abc --dry-run"),
    )
    gc_output = await GcCommandHandler().handle(
        _control_context("--compact --all-sessions --dry-run"),
    )

    assert snapshot_output == "Permanent PawGit snapshot created: `snapshot-123`"
    fake.snapshot.assert_awaited_once_with(
        session_id="console:default",
        user_id="default",
        channel="console",
        message="release v1",
    )
    assert timeline_output == "timeline"
    fake.timeline.assert_awaited_once_with(
        session_id="console:default",
        user_id="default",
        channel="console",
        limit=7,
        include_all=True,
    )
    assert rewind_output == "rewind"
    fake.rewind.assert_awaited_once_with(
        target="abc",
        session_id="console:default",
        user_id="default",
        channel="console",
        dry_run=True,
    )
    assert gc_output == "gc"
    fake.gc.assert_awaited_once_with(
        session_id="console:default",
        user_id="default",
        channel="console",
        compact=True,
        all_sessions=True,
        dry_run=True,
    )
