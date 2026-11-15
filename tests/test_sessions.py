import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from acp.pool import AgentClientPool
from config import Config
from sessions import SessionManager


def _make_manager(max_sessions=3, client=None):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="token",
        max_sessions=max_sessions,
        debug=True,
    )
    default_client = client or MagicMock()
    pool = AgentClientPool(config, client_factory=lambda u, a: default_client)
    now = [100.0]

    def clock():
        now[0] += 1.0
        return now[0]

    manager = SessionManager(config, pool, clock=clock)
    return config, pool, manager


@pytest.mark.asyncio
async def test_session_true_lru_eviction_respects_touch_and_active_tasks():
    client = MagicMock()
    client.send_request = AsyncMock(return_value={"result": {}})
    client.session_queues = {
        "sid1": asyncio.Queue(),
        "sid2": asyncio.Queue(),
        "sid3": asyncio.Queue(),
        "sid4": asyncio.Queue(),
    }
    client.session_models = {
        "sid1": {},
        "sid2": {},
        "sid3": {},
        "sid4": {},
    }
    client.context_usage = {
        "sid1": {
            "used": 1,
            "size": 10
        },
        "sid2": {
            "used": 2,
            "size": 10
        },
    }
    _, pool, manager = _make_manager(max_sessions=3, client=client)
    pool.get_or_create(None, "goose")

    manager.activate("u1", "r1", "sid1", None, "goose", None)
    manager.activate("u1", "r2", "sid2", None, "goose", None)
    manager.activate("u1", "r3", "sid3", None, "goose", None)
    # Touch r1 so r2 becomes the least-recently-used session
    manager.touch("u1:r1")
    manager.activate("u1", "r4", "sid4", None, "goose", None)

    # Mark r2 as actively running so r2 is skipped and r3 is evicted instead
    running_task = MagicMock()
    running_task.done.return_value = False
    manager.register_active_task("u1:r2", running_task)

    pruned = await manager.prune_if_needed()
    await asyncio.sleep(0)

    assert pruned == 1
    assert "u1:r3" not in manager.sessions
    assert "u1:r1" in manager.sessions
    assert "u1:r2" in manager.sessions
    assert "u1:r4" in manager.sessions

    # Now mark r2 done and prune until within max_sessions
    running_task.done.return_value = True
    # Add one more to exceed max_sessions=3 again, with r1 still active -> r2 oldest
    manager.activate("u1", "r5", "sid5", None, "goose", None)
    pruned2 = await manager.prune_if_needed()
    await asyncio.sleep(0)

    assert pruned2 == 1
    assert "u1:r2" not in manager.sessions
    assert "sid2" not in client.session_queues
    assert "sid2" not in client.session_models
    assert "sid2" not in client.context_usage


@pytest.mark.asyncio
async def test_close_session_clears_locks_tasks_and_calls_client_close():
    client = MagicMock()
    client.close_session = AsyncMock()
    _, pool, manager = _make_manager(max_sessions=5, client=client)
    pool.get_or_create("linux_a", "hermes")

    manager.activate("u1", "r1", "sid_close", "linux_a", "hermes", "m1")
    lock = manager.get_lock("u1:r1")
    assert lock is manager.locks["u1:r1"]
    active_task = MagicMock()
    active_task.done.return_value = False
    manager.register_active_task("u1:r1", active_task)

    closed = await manager.close_session(
        "u1:r1",
        cancel_task=True,
        await_rpc=True,
        cancel_prompt_first=True,
    )
    assert closed is True
    active_task.cancel.assert_called_once()
    assert "u1:r1" not in manager.sessions
    assert "u1:r1" not in manager.locks
    client.close_session.assert_awaited_once_with(
        "sid_close",
        await_rpc=True,
        cancel_first=True,
    )

    assert await manager.close_session("missing:key") is False


@pytest.mark.asyncio
async def test_thread_counters_bounded_and_ensure_counts_non_empty_posts():
    _, _, manager = _make_manager(max_sessions=1)
    assert manager.max_thread_counters == 500

    api = MagicMock()
    api.get_thread = AsyncMock(
        return_value={
            "posts": {
                "p1": {
                    "user_id": "u1",
                    "message": "hello"
                },
                "p2": {
                    "user_id": "bot_id",
                    "message": "bot reply"
                },
                "p3": {
                    "user_id": "u2",
                    "message": "   "
                },
                "p4": {
                    "user_id": "u2",
                    "message": "follow up"
                },
            }
        })
    count = await manager.ensure_thread_counter("r_test", api, "bot_id")
    assert count == 2

    # Second lookup hits cache without re-fetching thread
    count2 = await manager.ensure_thread_counter("r_test", api, "bot_id")
    assert count2 == 2
    api.get_thread.assert_awaited_once()

    # Empty/missing thread falls back to 1
    api.get_thread = AsyncMock(return_value=None)
    assert await manager.ensure_thread_counter("r_missing", api, "bot_id") == 1

    # Overflowing max_thread_counters evicts oldest entries
    for idx in range(510):
        api.get_thread = AsyncMock(return_value=None)
        await manager.ensure_thread_counter(f"r_{idx}", api, "bot_id")
    assert len(manager.thread_counters) == 500
    assert "r_test" not in manager.thread_counters


@pytest.mark.asyncio
async def test_close_all_cancels_tasks_and_closes_all_sessions():
    client = MagicMock()
    client.close_session = AsyncMock()
    _, pool, manager = _make_manager(max_sessions=10, client=client)
    pool.get_or_create(None, "goose")

    manager.activate("u1", "r1", "s1", None, "goose", None)
    manager.activate("u2", "r2", "s2", None, "goose", None)
    t1 = MagicMock()
    t1.done.return_value = False
    manager.register_active_task("u1:r1", t1)

    await manager.close_all()

    t1.cancel.assert_called_once()
    assert len(manager.sessions) == 0
    assert len(manager.locks) == 0
    assert client.close_session.await_count == 2
