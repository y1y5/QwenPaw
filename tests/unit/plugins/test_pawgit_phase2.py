# -*- coding: utf-8 -*-
"""PawGit Phase 2 memory rewind tests."""

# pylint: disable=protected-access,redefined-outer-name
# pylint: disable=wrong-import-order,wrong-import-position

from __future__ import annotations

import asyncio
import hashlib
import json
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

from pawgit import backend, handlers  # noqa: E402
from pawgit.engine import PawGitEngine, PawGitError  # noqa: E402
from pawgit.handlers import PawGitCommandHandler  # noqa: E402
from pawgit.memory_rewind import MemoryRewindCoordinator  # noqa: E402
from pawgit.support import RewindResult  # noqa: E402

SESSION = {
    "channel": "console",
    "user_id": "default",
    "session_id": "console:default",
}


def _session_path(workspace: Path) -> Path:
    filename = (
        f"{sanitize_filename(SESSION['user_id'])}_"
        f"{sanitize_filename(SESSION['session_id'])}.json"
    )
    return workspace / "sessions" / "console" / filename


def _write_sources(workspace: Path, value: str) -> None:
    session = _session_path(workspace)
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        json.dumps({"value": value}),
        encoding="utf-8",
    )
    (workspace / "MEMORY.md").write_text(value, encoding="utf-8")
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    (memory_dir / "note.md").write_text(value, encoding="utf-8")


class FakeMemoryManager:
    """Minimal closeable memory manager."""

    def __init__(self, *, close_error: Exception | None = None):
        self.closed = False
        self.close_error = close_error

    async def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error
        return True


class FakeTaskTracker:
    """Controllable task tracker for maintenance tests."""

    def __init__(self, *, active=(), completes=True):
        self.active = list(active)
        self.completes = completes
        self.stopped: list[str] = []
        self.wait_timeout: float | None = None

    async def list_active_tasks(self):
        return list(self.active)

    async def request_stop(self, run_key):
        self.stopped.append(run_key)
        if self.completes and run_key in self.active:
            self.active.remove(run_key)
        return True

    async def wait_all_done(self, timeout):
        self.wait_timeout = timeout
        return self.completes


class FakeCronManager:
    """Cron lifecycle facade."""

    def __init__(self, enabled=True, *, start_error=None):
        self._started = enabled
        self.stop_count = 0
        self.start_count = 0
        self.start_error = start_error

    async def stop(self):
        self.stop_count += 1
        self._started = False

    async def start(self):
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error
        self._started = True


class FakeServiceManager:
    """Rebuild a deterministic hash index when memory restarts."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        fail_starts=0,
        close_error: Exception | None = None,
    ):
        self.workspace_dir = workspace_dir
        self.fail_starts = fail_starts
        self.start_count = 0
        self.descriptors = {"memory_manager": object()}
        self.reused_services = {"memory_manager"}
        self.services = {
            "memory_manager": FakeMemoryManager(close_error=close_error),
            "runner": SimpleNamespace(memory_manager=None),
        }

    async def _start_service(self, descriptor):
        assert descriptor is self.descriptors["memory_manager"]
        self.start_count += 1
        if self.fail_starts:
            self.fail_starts -= 1
            raise RuntimeError("simulated restart failure")
        manager = FakeMemoryManager()
        self.services["memory_manager"] = manager
        self.services["runner"].memory_manager = manager
        content = (self.workspace_dir / "MEMORY.md").read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        index_dir = self.workspace_dir / "file_store"
        index_dir.mkdir(exist_ok=True)
        (index_dir / "memory_file_metadata.json").write_text(
            json.dumps({"MEMORY.md": {"sha256": digest}}),
            encoding="utf-8",
        )
        (self.workspace_dir / ".reme_store_test").touch()


class FakeWorkspace:
    """Workspace surface used by the Phase 2 coordinator."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        tracker=None,
        cron=None,
        fail_starts=0,
        current_run: str | None = None,
        close_error: Exception | None = None,
    ):
        self.workspace_dir = workspace_dir
        self.task_tracker = tracker or FakeTaskTracker()
        self.cron_manager = cron or FakeCronManager()
        self.chat_manager = SimpleNamespace(
            get_chat_id_by_session=AsyncMock(return_value=current_run),
        )
        self._service_manager = FakeServiceManager(
            workspace_dir,
            fail_starts=fail_starts,
            close_error=close_error,
        )

    @property
    def memory_manager(self):
        return self._service_manager.services.get("memory_manager")


@pytest.fixture
def engine(tmp_path: Path) -> PawGitEngine:
    return PawGitEngine(tmp_path)


async def _snapshot(engine: PawGitEngine, name: str) -> str:
    return await engine.snapshot(message=name, **SESSION)


async def test_include_memory_rewinds_sources_without_touching_search_index(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    (tmp_path / "memory" / "new.md").write_text("new", encoding="utf-8")
    (tmp_path / "file_store").mkdir()
    (tmp_path / "file_store" / "stale").write_text("old", encoding="utf-8")
    (tmp_path / ".reme_store_old").touch()
    workspace = FakeWorkspace(tmp_path)
    old_manager = workspace.memory_manager
    engine.workspace = workspace

    result = await engine.rewind_with_memory(target=name, **SESSION)

    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == "checkpoint"
    assert (tmp_path / "memory" / "note.md").read_text(encoding="utf-8") == "checkpoint"
    assert not (tmp_path / "memory" / "new.md").exists()
    assert json.loads(_session_path(tmp_path).read_text())["value"] == ("checkpoint")
    assert (tmp_path / "file_store" / "stale").read_text() == "old"
    assert (tmp_path / ".reme_store_old").exists()
    assert not old_manager.closed
    assert workspace._service_manager.start_count == 0
    assert result.include_memory
    assert result.pre_rewind_ref
    rendered = engine.render_rewind(result)
    assert "updates asynchronously" in rendered
    assert "Memory index rebuilt" not in rendered
    assert engine.query_gate.is_set()
    assert workspace.cron_manager.stop_count == 1
    assert workspace.cron_manager.start_count == 1
    assert workspace.cron_manager._started


async def test_include_memory_dry_run_has_no_maintenance_side_effects(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    tracker = FakeTaskTracker(active=("run-1",))
    cron = FakeCronManager()
    workspace = FakeWorkspace(tmp_path, tracker=tracker, cron=cron)
    old_manager = workspace.memory_manager
    engine.workspace = workspace

    result = await engine.rewind_with_memory(
        target=name,
        dry_run=True,
        **SESSION,
    )

    assert (tmp_path / "MEMORY.md").read_text() == "current"
    assert not tracker.stopped
    assert cron.stop_count == 0
    assert not old_manager.closed
    assert result.dry_run
    assert result.pre_rewind_ref is None


async def test_include_memory_timeout_aborts_before_snapshot_or_file_changes(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    tracker = FakeTaskTracker(
        active=("current-command", "run-1"),
        completes=False,
    )
    workspace = FakeWorkspace(
        tmp_path,
        tracker=tracker,
        current_run="current-command",
    )
    engine.workspace = workspace

    with pytest.raises(PawGitError, match="Timed out"):
        await engine.rewind_with_memory(target=name, **SESSION)

    assert (tmp_path / "MEMORY.md").read_text() == "current"
    assert tracker.stopped == ["run-1"]
    assert engine.query_gate.is_set()
    refs = engine._list_pawgit_refs()
    assert not any(ref.startswith("refs/pre-rewind/") for ref, _ in refs)


async def test_unknown_current_task_aborts_without_stopping_anything(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    tracker = FakeTaskTracker(active=("unknown-run",))
    engine.workspace = FakeWorkspace(tmp_path, tracker=tracker)

    with pytest.raises(PawGitError, match="Cannot safely identify"):
        await engine.rewind_with_memory(target=name, **SESSION)

    assert not tracker.stopped
    assert (tmp_path / "MEMORY.md").read_text() == "current"
    assert engine.query_gate.is_set()


async def test_quiesce_keeps_current_rewind_task_and_stops_other_tasks(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    tracker = FakeTaskTracker(active=("current-command", "other-run"))
    workspace = FakeWorkspace(
        tmp_path,
        tracker=tracker,
        current_run="current-command",
    )
    engine.workspace = workspace

    await engine.rewind_with_memory(target=name, **SESSION)

    assert tracker.stopped == ["other-run"]
    assert tracker.active == ["current-command"]


async def test_quiesce_stops_task_that_appears_after_initial_scan(
    engine: PawGitEngine,
    tmp_path: Path,
):
    class LateTaskTracker(FakeTaskTracker):
        def __init__(self):
            super().__init__(active=("current-command",))
            self.list_count = 0

        async def list_active_tasks(self):
            self.list_count += 1
            if self.list_count == 2:
                self.active.append("late-run")
            return list(self.active)

    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    tracker = LateTaskTracker()
    engine.workspace = FakeWorkspace(
        tmp_path,
        tracker=tracker,
        current_run="current-command",
    )

    await engine.rewind_with_memory(target=name, **SESSION)

    assert tracker.stopped == ["late-run"]
    assert tracker.active == ["current-command"]


async def test_restore_failure_rolls_back_sources(
    engine: PawGitEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    workspace = FakeWorkspace(tmp_path)
    engine.workspace = workspace

    original_apply = MemoryRewindCoordinator._apply_restore_plan
    apply_count = 0

    def fail_after_first_restore(self, blobs):
        nonlocal apply_count
        apply_count += 1
        original_apply(self, blobs)
        if apply_count == 1:
            raise RuntimeError("simulated restore failure")

    monkeypatch.setattr(
        MemoryRewindCoordinator,
        "_apply_restore_plan",
        fail_after_first_restore,
    )

    with pytest.raises(PawGitError, match="simulated restore failure"):
        await engine.rewind_with_memory(target=name, **SESSION)

    assert (tmp_path / "MEMORY.md").read_text() == "current"
    assert json.loads(_session_path(tmp_path).read_text())["value"] == "current"
    assert not workspace.memory_manager.closed
    assert workspace._service_manager.start_count == 0
    assert engine.query_gate.is_set()


async def test_memory_manager_is_not_closed_or_replaced(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    workspace = FakeWorkspace(tmp_path)
    old_manager = workspace.memory_manager
    engine.workspace = workspace

    await engine.rewind_with_memory(target=name, **SESSION)

    assert not old_manager.closed
    assert workspace.memory_manager is old_manager
    assert workspace._service_manager.start_count == 0
    assert (tmp_path / "MEMORY.md").read_text() == "checkpoint"
    assert engine.query_gate.is_set()


async def test_query_gate_reopens_when_cron_restart_fails(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    _write_sources(tmp_path, "current")
    cron = FakeCronManager(
        start_error=RuntimeError("simulated cron restart failure"),
    )
    engine.workspace = FakeWorkspace(tmp_path, cron=cron)

    with pytest.raises(RuntimeError, match="cron restart failure"):
        await engine.rewind_with_memory(target=name, **SESSION)

    assert engine.query_gate.is_set()
    assert (tmp_path / "MEMORY.md").read_text() == "checkpoint"


async def test_cancellation_waits_for_memory_transaction_cleanup(
    engine: PawGitEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "memory-point")
    engine.workspace = FakeWorkspace(tmp_path, cron=FakeCronManager(False))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_rewind(self, **kwargs):
        del self
        entered.set()
        await release.wait()
        return RewindResult(
            target=kwargs["target"],
            commit="a" * 40,
            restored_paths=(),
            pre_rewind_ref=None,
            dry_run=False,
            include_memory=True,
        )

    monkeypatch.setattr(
        MemoryRewindCoordinator,
        "_rewind_locked",
        slow_rewind,
    )
    task = asyncio.create_task(
        engine.rewind_with_memory(target=name, **SESSION),
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    assert not engine.query_gate.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.query_gate.is_set()


async def test_pre_reply_hook_waits_for_query_gate(
    engine: PawGitEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        backend.REGISTRY,
        "get_for_agent",
        lambda agent: engine,
    )
    engine.query_gate.clear()
    task = asyncio.create_task(backend._pre_reply_query_gate(object(), {}))
    await asyncio.sleep(0)

    assert not task.done()
    engine.query_gate.set()
    assert await task == {}


async def test_snapshot_waits_for_memory_maintenance(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "current")
    await engine.maintenance_lock.acquire()
    task = asyncio.create_task(
        engine.make_snapshot(kind="auto", **SESSION),
    )
    await asyncio.sleep(0)

    assert not task.done()
    engine.maintenance_lock.release()
    ref = await task
    assert ref.startswith("refs/auto/")


async def test_other_pawgit_commands_wait_for_memory_maintenance(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "current")
    await _snapshot(engine, "point")
    await engine.maintenance_lock.acquire()
    timeline_task = asyncio.create_task(engine.timeline(**SESSION))
    rewind_task = asyncio.create_task(
        engine.rewind(target="point", dry_run=True, **SESSION),
    )
    gc_task = asyncio.create_task(engine.gc(dry_run=True, **SESSION))
    await asyncio.sleep(0)

    assert not timeline_task.done()
    assert not rewind_task.done()
    assert not gc_task.done()
    engine.maintenance_lock.release()
    await asyncio.gather(timeline_task, rewind_task, gc_task)


async def test_memory_dry_run_waits_for_memory_maintenance(
    engine: PawGitEngine,
    tmp_path: Path,
):
    _write_sources(tmp_path, "checkpoint")
    name = await _snapshot(engine, "point")
    engine.workspace = FakeWorkspace(tmp_path)
    await engine.maintenance_lock.acquire()
    task = asyncio.create_task(
        engine.rewind_with_memory(
            target=name,
            dry_run=True,
            **SESSION,
        ),
    )
    await asyncio.sleep(0)

    assert not task.done()
    engine.maintenance_lock.release()
    result = await task
    assert result.dry_run


async def test_include_memory_handler_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = SimpleNamespace(
        rewind=AsyncMock(),
        rewind_with_memory=AsyncMock(),
        render_rewind=lambda result: "rewind",
    )
    monkeypatch.setattr(
        handlers.REGISTRY,
        "get_for_workspace",
        lambda workspace: fake,
    )
    context = SimpleNamespace(
        workspace=object(),
        channel=SimpleNamespace(channel="console"),
        session_id=SESSION["session_id"],
        user_id=SESSION["user_id"],
        args={"_raw_args": "rewind point --include-memory"},
    )

    output = await PawGitCommandHandler().handle(context)

    assert output.startswith("**Confirmation Required**")
    fake.rewind_with_memory.assert_not_awaited()

    context.args["_raw_args"] += " --confirm"
    fake.rewind_with_memory.return_value = SimpleNamespace()
    assert await PawGitCommandHandler().handle(context) == "rewind"
    fake.rewind_with_memory.assert_awaited_once_with(
        target="point",
        session_id=SESSION["session_id"],
        user_id=SESSION["user_id"],
        channel=SESSION["channel"],
        dry_run=False,
    )
