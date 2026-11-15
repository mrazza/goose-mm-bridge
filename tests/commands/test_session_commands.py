from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from acp.pool import AgentClientPool
from commands import create_default_registry
from config import Config
from sessions import SessionManager
from user_store import UserStore


def _make_harness(tmp_path, approved_users=None, admin_users=None):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="token",
        approved_users=approved_users if approved_users is not None else [],
        admin_users=admin_users if admin_users is not None else [],
        user_mapping_file=str(tmp_path / "missing_mapping.json"),
        user_agent_preferences_file=str(tmp_path / "prefs.json"),
        prewarm_config_file=str(tmp_path / "missing_prewarm.json"),
    )
    api = MagicMock()
    api.create_post = AsyncMock(return_value={"id": "reply_1"})
    users = {
        "alice_id": {
            "id": "alice_id",
            "username": "alice"
        },
        "bob_id": {
            "id": "bob_id",
            "username": "bob"
        },
        "admin_id": {
            "id": "admin_id",
            "username": "admin"
        },
    }
    api.get_user = AsyncMock(side_effect=lambda uid: users.get(uid))

    client = MagicMock()
    client.cancel_prompt = AsyncMock()
    client.context_usage = {}

    user_store = UserStore(config, api)
    pool = AgentClientPool(config, client_factory=lambda u, a: client)
    sessions = SessionManager(config, pool)
    registry = create_default_registry()
    return config, api, user_store, sessions, pool, client, registry


@pytest.mark.asyncio
async def test_stop_command_cancels_active_session_and_task(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)
    pool.get_or_create("linux_alice", "goose")
    sessions.activate("alice_id", "root_1", "acp_sid_1", "linux_alice", "goose",
                      None)
    mock_task = MagicMock()
    sessions.register_active_task("alice_id:root_1", mock_task)

    inv = registry.parse("!stop", "@bot")
    post = {
        "id": "p2",
        "root_id": "root_1",
        "user_id": "alice_id",
        "channel_id": "c1",
        "message": "!stop",
    }
    handled = await registry.dispatch(
        inv,
        post=post,
        is_dm=False,
        is_mentioned=False,
        root_id="root_1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )

    assert handled is True
    client.cancel_prompt.assert_awaited_once_with("acp_sid_1")
    mock_task.cancel.assert_called_once()
    assert "Prompt cancelled" in api.create_post.call_args[0][1]


@pytest.mark.asyncio
async def test_stop_command_cancels_task_only_when_session_not_created_yet(
    tmp_path,):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)
    mock_task = MagicMock()
    sessions.register_active_task("alice_id:root_1", mock_task)

    handled = await registry.dispatch(
        registry.parse("!stop", "@bot"),
        post={
            "id": "p1",
            "user_id": "alice_id",
            "channel_id": "dm_1",
            "message": "!stop",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="root_1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert handled is True
    mock_task.cancel.assert_called_once()
    client.cancel_prompt.assert_not_awaited()
    assert "Prompt cancelled" in api.create_post.call_args[0][1]


@pytest.mark.asyncio
async def test_stop_command_noop_when_nothing_active(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)

    handled = await registry.dispatch(
        registry.parse("@bot !stop", "@bot"),
        post={
            "id": "p1",
            "user_id": "alice_id",
            "channel_id": "c1",
            "message": "@bot !stop",
        },
        is_dm=False,
        is_mentioned=True,
        root_id="p1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert handled is True
    api.create_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_and_context_work_for_unapproved_user(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path, approved_users=["alice"])
    pool.get_or_create(None, "goose")
    client.context_usage["acp_sid_bob"] = {"used": 250, "size": 1000}
    sessions.activate("bob_id", "root_b", "acp_sid_bob", None, "goose", None)

    assert await registry.dispatch(
        registry.parse("!context", "@bot"),
        post={
            "id": "p_ctx",
            "root_id": "root_b",
            "user_id": "bob_id",
            "channel_id": "c1",
            "message": "!context",
        },
        is_dm=False,
        is_mentioned=False,
        root_id="root_b",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    ) is True
    msg = api.create_post.call_args[0][1]
    assert "250" in msg and "1,000" in msg and "25.0%" in msg

    assert await registry.dispatch(
        registry.parse("!stop", "@bot"),
        post={
            "id": "p_stop",
            "root_id": "root_b",
            "user_id": "bob_id",
            "channel_id": "c1",
            "message": "!stop",
        },
        is_dm=False,
        is_mentioned=False,
        root_id="root_b",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    ) is True
    client.cancel_prompt.assert_awaited_once_with("acp_sid_bob")


@pytest.mark.asyncio
async def test_context_command_edge_cases(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)
    inv = registry.parse("!context", "@bot")

    # 1. No session
    await registry.dispatch(
        inv,
        post={
            "id": "p1",
            "user_id": "alice_id",
            "channel_id": "dm_1",
            "message": "!context"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_none",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "No active session" in api.create_post.call_args[0][1]

    # 2. Session exists, client not in pool
    sessions.activate("alice_id", "r_noclient", "sid_nc", "linux_alice",
                      "goose", None)
    await registry.dispatch(
        inv,
        post={
            "id": "p2",
            "root_id": "r_noclient",
            "user_id": "alice_id",
            "channel_id": "dm_1",
            "message": "!context"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_noclient",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Agent client not found" in api.create_post.call_args[0][1]

    # 3. Client exists, no usage info
    pool.get_or_create("linux_alice", "goose")
    await registry.dispatch(
        inv,
        post={
            "id": "p3",
            "root_id": "r_noclient",
            "user_id": "alice_id",
            "channel_id": "dm_1",
            "message": "!context"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_noclient",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "No context usage information" in api.create_post.call_args[0][1]

    # 4. Usage populated
    client.context_usage["sid_nc"] = {"used": 1500, "size": 10000}
    await registry.dispatch(
        inv,
        post={
            "id": "p4",
            "root_id": "r_noclient",
            "user_id": "alice_id",
            "channel_id": "dm_1",
            "message": "!context"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_noclient",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    msg = api.create_post.call_args[0][1]
    assert "1,500" in msg and "10,000" in msg and "15.0%" in msg


@pytest.mark.asyncio
async def test_impersonated_stop_and_context_route_to_target_session(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path, admin_users=["admin"])
    user_store.set_impersonation("admin_id", "alice_id", "alice")
    pool.get_or_create(None, "goose")
    sessions.activate("alice_id", "root_t", "sid_target", None, "goose", None)
    client.context_usage["sid_target"] = {"used": 500, "size": 2000}

    assert await registry.dispatch(
        registry.parse("!context", "@bot"),
        post={
            "id": "p1",
            "root_id": "root_t",
            "user_id": "admin_id",
            "channel_id": "c1",
            "message": "!context"
        },
        is_dm=False,
        is_mentioned=False,
        root_id="root_t",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    ) is True
    assert "25.0%" in api.create_post.call_args[0][1]

    assert await registry.dispatch(
        registry.parse("!stop", "@bot"),
        post={
            "id": "p2",
            "root_id": "root_t",
            "user_id": "admin_id",
            "channel_id": "c1",
            "message": "!stop"
        },
        is_dm=False,
        is_mentioned=False,
        root_id="root_t",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    ) is True
    client.cancel_prompt.assert_awaited_once_with("sid_target")
