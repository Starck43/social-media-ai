"""Source-only POSIX files/locks and real ASGI router; never start real services.

Only module-local config/catalog/component doubles replace infrastructure.
No DB engines, app.main startup, channels, jobs or providers are imported.
"""

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
PRESENCE = load_isolated_source("_p8_t10_presence", ROOT / "app/core/runtime_presence.py")


def record(directory):
    return json.loads((directory / "presence.json").read_text())


def save(directory, **changes):
    value = {**record(directory), **changes}
    (directory / "presence.json").write_text(json.dumps(value))


def test_unconfigured_and_missing_are_read_only(tmp_path):
    assert PRESENCE.runtime_signal(None)["state"] == "unconfigured"
    assert PRESENCE.runtime_signal(tmp_path / "absent")["state"] == "missing"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("age,state", [(0, "fresh"), (60, "fresh"), (60.001, "stale")])
def test_freshness_boundary_and_component_identity(tmp_path, age, state):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=False) as owner:
        owner.write("running")
        save(tmp_path, heartbeat_at=1000)
        signal = PRESENCE.runtime_signal(tmp_path, now=1000 + age)
        assert signal["state"] == state
        assert signal["runtime_id"] == owner.runtime_id
        assert signal["component"] == "app.runtime"
        assert signal["scheduler_enabled"] is False
        assert signal["age_seconds"] == age
        assert "pid" not in signal and "hostname" not in signal


@pytest.mark.parametrize("phase", ["starting", "stopped"])
def test_nonrunning_phase_is_never_fresh(tmp_path, phase):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
        owner.write(phase)
        assert PRESENCE.runtime_signal(tmp_path)["state"] == phase


def test_released_lock_overrides_fresh_running_record(tmp_path):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
        owner.write("running")
        running = record(tmp_path)
    (tmp_path / "presence.json").write_text(json.dumps(running))
    assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"


def test_probe_shared_lock_cannot_masquerade_as_runtime(tmp_path):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
        owner.write("running")
        running = record(tmp_path)
    (tmp_path / "presence.json").write_text(json.dumps(running))
    with (tmp_path / "owner.lock").open("rb") as reader:
        fcntl.flock(reader, fcntl.LOCK_SH | fcntl.LOCK_NB)
        assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"


def test_duplicate_owner_refused_and_record_unchanged(tmp_path):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as first:
        first.write("running")
        before = record(tmp_path)
        with pytest.raises(PRESENCE.RuntimePresenceError, match="^runtime_presence_ownership_unavailable$"):
            with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=False):
                pytest.fail("second owner admitted")
        assert record(tmp_path) == before
        assert PRESENCE.runtime_signal(tmp_path)["runtime_id"] == first.runtime_id


def test_restart_has_new_identity_and_shutdown_releases_lock(tmp_path):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as first:
        assert record(tmp_path)["phase"] == "starting"
    assert record(tmp_path)["phase"] == "stopped"
    assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as second:
        assert second.runtime_id != first.runtime_id
    assert sorted(p.name for p in tmp_path.iterdir()) == ["owner.lock", "presence.json"]


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"runtime_id": "PRIVATE"},
        {"phase": "healthy"},
        {"scheduler_enabled": "true"},
        {"heartbeat_at": True},
        {"heartbeat_at": None},
        {"heartbeat_at": float("nan")},
        {"heartbeat_at": float("inf")},
        {"heartbeat_at": 10**999},
        {"heartbeat_at": -1},
        {"heartbeat_at": 2000},
        {"private": "PRIVATE"},
    ],
)
def test_invalid_or_future_evidence_fails_closed(tmp_path, changes):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
        owner.write("running")
        save(tmp_path, heartbeat_at=1000)
        save(tmp_path, **changes)
        signal = PRESENCE.runtime_signal(tmp_path, now=1000)
        assert signal["state"] == "invalid"
        assert "PRIVATE" not in repr(signal)


@pytest.mark.parametrize("raw", ["[]", "null", "{", "PRIVATE" * 1000])
def test_corrupt_and_oversized_record_cannot_report_fresh(tmp_path, raw):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True):
        (tmp_path / "presence.json").write_text(raw)
        signal = PRESENCE.runtime_signal(tmp_path)
        assert signal["state"] != "fresh"
        assert "PRIVATE" not in repr(signal)


def test_write_failure_is_redacted_and_initial_lock_released(tmp_path, monkeypatch):
    original = PRESENCE.os.replace

    def fail(*args):
        raise OSError("PRIVATE-PATH")

    monkeypatch.setattr(PRESENCE.os, "replace", fail)
    with pytest.raises(PRESENCE.RuntimePresenceError, match="^runtime_presence_write_failed$"):
        with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True):
            pytest.fail("failed writer admitted")
    assert [p.name for p in tmp_path.iterdir()] == ["owner.lock"]
    monkeypatch.setattr(PRESENCE.os, "replace", original)
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True):
        pass


def test_read_permission_failure_is_redacted(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("PRIVATE-PATH")

    monkeypatch.setattr(PRESENCE, "_open_regular", fail)
    assert PRESENCE.runtime_signal(tmp_path) == {"component": "app.runtime", "state": "unavailable"}


def load_runtime(directory, *, scheduler=True, seed=None, worker=None, listener=None, runner=None):
    return load_isolated_source(
        "_p8_t10_runtime",
        ROOT / "app/runtime.py",
        {
            "app.core.config": NS(
                settings=NS(LOG_LEVEL="WARNING", RUNTIME_STATE_DIR=directory, SCHEDULER_ENABLED=scheduler)
            ),
            "app.core.runtime_presence": PRESENCE,
            "app.tasks.bootstrap": NS(ensure_all_default_tasks=seed or AsyncMock()),
            "app.channels.listener": NS(listen_forever=listener or AsyncMock()),
            "app.jobs.dispatcher": NS(worker_forever=worker or AsyncMock()),
            "app.tasks.runner": NS(run_forever=runner or AsyncMock()),
        },
    )


@pytest.mark.parametrize("scheduler", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
def test_mocked_lifecycle_presence_and_cancellation(tmp_path, scheduler, enabled):
    async def scenario():
        started, cancelled = set(), set()

        def component(name):
            async def run():
                started.add(name)
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.add(name)

            return run

        seed = AsyncMock()
        directory = tmp_path if enabled else None
        module = load_runtime(
            directory,
            scheduler=scheduler,
            seed=seed,
            worker=component("worker"),
            listener=component("listener"),
            runner=component("scheduler"),
        )
        task = asyncio.create_task(module.main())
        expected = {"worker", "listener"} | ({"scheduler"} if scheduler else set())
        try:
            async with asyncio.timeout(1):
                while started != expected:
                    await asyncio.sleep(0)
            seed.assert_awaited_once()
            assert PRESENCE.runtime_signal(directory)["state"] == ("fresh" if enabled else "unconfigured")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cancelled == expected
        if enabled:
            assert PRESENCE.runtime_signal(directory)["state"] == "stopped"
        else:
            assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


def test_duplicate_runtime_stops_before_bootstrap(tmp_path):
    seed = AsyncMock()
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True):
        with pytest.raises(PRESENCE.RuntimePresenceError):
            asyncio.run(load_runtime(tmp_path, seed=seed).main())
    seed.assert_not_awaited()


def test_bootstrap_failure_stops_presence_without_starting_components(tmp_path):
    worker = AsyncMock()
    with pytest.raises(RuntimeError, match="seed_failed"):
        asyncio.run(
            load_runtime(tmp_path, seed=AsyncMock(side_effect=RuntimeError("seed_failed")), worker=worker).main()
        )
    worker.assert_not_awaited()
    assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"


def test_completed_components_do_not_leave_heartbeat_running(tmp_path):
    async def scenario():
        async with asyncio.timeout(1):
            await load_runtime(tmp_path).main()
        assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"

    asyncio.run(scenario())


def test_heartbeat_failure_cancels_components_and_releases_owner(tmp_path, monkeypatch):
    async def fail(self):
        await asyncio.sleep(0)
        raise PRESENCE.RuntimePresenceError("runtime_presence_write_failed")

    monkeypatch.setattr(PRESENCE.RuntimePresence, "heartbeat", fail)

    async def wait():
        await asyncio.Event().wait()

    with pytest.raises(PRESENCE.RuntimePresenceError):
        asyncio.run(load_runtime(tmp_path, worker=wait, listener=wait, runner=wait).main())
    assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"


def test_component_failure_cancels_others_and_releases_owner(tmp_path):
    async def fail():
        raise RuntimeError("component_failed")

    async def wait():
        await asyncio.Event().wait()

    with pytest.raises(RuntimeError, match="component_failed"):
        asyncio.run(load_runtime(tmp_path, worker=fail, listener=wait, runner=wait).main())
    assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"


def test_heartbeat_advances_while_event_loop_is_responsive(tmp_path, monkeypatch):
    monkeypatch.setattr(PRESENCE, "HEARTBEAT_SECONDS", 0)

    async def scenario():
        with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
            owner.write("running")
            before = record(tmp_path)["heartbeat_at"]
            task = asyncio.create_task(owner.heartbeat())
            try:
                async with asyncio.timeout(1):
                    while record(tmp_path)["heartbeat_at"] <= before:
                        await asyncio.sleep(0)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(scenario())


@pytest.mark.parametrize("configured", [False, True])
def test_real_asgi_probe_is_distinct_from_liveness_and_db(tmp_path, configured):
    async def scenario():
        count = AsyncMock(side_effect=RuntimeError("PRIVATE-DSN"))
        health = load_isolated_source(
            "_p8_t10_health",
            ROOT / "app/core/health.py",
            {
                "app.core.config": NS(settings=NS(RUNTIME_STATE_DIR=tmp_path if configured else None)),
                "app.core.runtime_presence": PRESENCE,
                "app.models": NS(Permission=NS(objects=NS(count=count))),
            },
        )
        app = FastAPI()
        app.include_router(health.router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            absent = await client.get("/runtimez")
            assert absent.status_code == 503
            assert absent.json()["state"] == ("missing" if configured else "unconfigured")
            with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=False) as owner:
                owner.write("running")
                current = await client.get("/runtimez")
                assert current.status_code == (200 if configured else 503)
                assert current.headers["cache-control"] == "no-store"
                assert current.json()["component"] == "app.runtime"
                assert "PRIVATE" not in current.text
                count.assert_not_awaited()
                live = await client.get("/livez")
                assert live.status_code == 200 and "component" not in live.json()
                count.assert_not_awaited()
                ready = await client.get("/readyz")
                assert ready.status_code == 503 and "component" not in ready.json()
                assert "PRIVATE" not in ready.text
            stopped = await client.get("/runtimez")
            assert stopped.status_code == 503

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["owner.lock", "presence.json"])
@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_nonregular_files_fail_closed_without_blocking(tmp_path, name, kind):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
        owner.write("running")
        target = tmp_path / name
        target.unlink()
        if kind == "fifo":
            os.mkfifo(target)
        else:
            target.symlink_to(tmp_path / "private-target")
        assert PRESENCE.runtime_signal(tmp_path)["state"] != "fresh"
        target.unlink()


def test_duplicate_record_keys_are_not_last_key_wins(tmp_path):
    with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
        owner.write("running")
        raw = (tmp_path / "presence.json").read_text()
        (tmp_path / "presence.json").write_text(raw[:-1] + ', "phase": "running"}')
        assert PRESENCE.runtime_signal(tmp_path)["state"] != "fresh"


def test_runtime_state_setting_is_opt_in_and_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SECRET_KEY", "synthetic-not-a-secret")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://synthetic:synthetic@db.invalid/synthetic")
    monkeypatch.delenv("RUNTIME_STATE_DIR", raising=False)
    config = load_isolated_source(
        "_p8_t10_config", ROOT / "app/core/config.py", {"dotenv": NS(load_dotenv=lambda: None)}
    )
    assert config.settings.RUNTIME_STATE_DIR is None
    assert config.Settings(_env_file=None, RUNTIME_STATE_DIR=tmp_path).RUNTIME_STATE_DIR == tmp_path
    with pytest.raises(ValueError, match="RUNTIME_STATE_DIR must be an absolute path"):
        config.Settings(_env_file=None, RUNTIME_STATE_DIR="relative/path")


@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_owner_is_retained_until_all_component_cleanup_finishes(tmp_path, repeat_cancel):
    async def scenario():
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow_worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        async def wait():
            await asyncio.Event().wait()

        task = asyncio.create_task(load_runtime(tmp_path, worker=slow_worker, listener=wait, runner=wait).main())
        try:
            async with asyncio.timeout(1):
                await started.wait()
                task.cancel()
                await cleaning.wait()
                await asyncio.sleep(0)
                if repeat_cancel:
                    task.cancel()
                    await asyncio.sleep(0)
                assert not task.done()
                assert PRESENCE.runtime_signal(tmp_path)["state"] == "fresh"
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_process_death_releases_real_cross_process_lock(tmp_path):
    # Synthetic owner only: this subprocess never imports/starts app.runtime,
    # catalog models, DB engines, scheduler, worker or listeners.
    script = """
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location('_presence_child', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.RuntimePresence(pathlib.Path(sys.argv[2]), scheduler_enabled=False) as owner:
    owner.write('running')
    time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(ROOT / "app/core/runtime_presence.py"), str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 3
        while PRESENCE.runtime_signal(tmp_path)["state"] != "fresh":
            assert child.poll() is None, "synthetic owner exited before publishing"
            assert time.monotonic() < deadline, "synthetic heartbeat not observed"
            time.sleep(0.01)
        with pytest.raises(PRESENCE.RuntimePresenceError):
            with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True):
                pytest.fail("cross-process duplicate admitted")
    finally:
        child.kill()
        child.communicate(timeout=3)
    # No graceful stopped write: the recent running record remains, but its
    # released OS ownership prevents a false green after abrupt death.
    assert record(tmp_path)["phase"] == "running"
    assert PRESENCE.runtime_signal(tmp_path)["state"] == "stopped"


@pytest.mark.parametrize("case", ["starting", "stale", "invalid", "future"])
def test_asgi_never_turns_unconfirmed_presence_green(tmp_path, case):
    async def scenario():
        count = AsyncMock(side_effect=AssertionError("runtime probe cannot query DB"))
        health = load_isolated_source(
            "_p8_t10_negative_health",
            ROOT / "app/core/health.py",
            {
                "app.core.config": NS(settings=NS(RUNTIME_STATE_DIR=tmp_path)),
                "app.core.runtime_presence": PRESENCE,
                "app.models": NS(Permission=NS(objects=NS(count=count))),
            },
        )
        app = FastAPI()
        app.include_router(health.router)
        with PRESENCE.RuntimePresence(tmp_path, scheduler_enabled=True) as owner:
            if case != "starting":
                owner.write("running")
            if case == "stale":
                save(tmp_path, heartbeat_at=time.time() - 61)
            elif case == "future":
                save(tmp_path, heartbeat_at=time.time() + 3600)
            elif case == "invalid":
                (tmp_path / "presence.json").write_text("PRIVATE-CORRUPT")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.get("/runtimez")
            assert response.status_code == 503
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["state"] != "fresh"
            assert "PRIVATE" not in response.text
            count.assert_not_awaited()

    asyncio.run(scenario())
