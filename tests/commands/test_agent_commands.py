from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from acp.pool import AgentClientPool
from commands import create_default_registry
from config import Config
from sessions import SessionManager
from user_store import UserStore


def _make_harness(tmp_path):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="token",
        approved_users=[],
        default_agent="goose",
        user_mapping_file=str(tmp_path / "missing_mapping.json"),
        user_agent_preferences_file=str(tmp_path / "prefs.json"),
        prewarm_config_file=str(tmp_path / "missing_prewarm.json"),
        agents={
            "goose": {
                "command": ["goose", "acp"]
            },
            "hermes": {
                "command": ["hermes", "acp"]
            },
        },
    )
    api = MagicMock()
    api.create_post = AsyncMock(return_value={"id": "reply_1"})
    api.get_user = AsyncMock(return_value={"id": "u1", "username": "alice"})

    client = MagicMock()
    client.close_session = AsyncMock()

    user_store = UserStore(config, api)
    pool = AgentClientPool(config, client_factory=lambda u, a: client)
    sessions = SessionManager(config, pool)
    registry = create_default_registry()
    return config, api, user_store, sessions, pool, client, registry


@pytest.mark.asyncio
async def test_agents_command_lists_active_and_default(tmp_path):
    config, api, user_store, sessions, pool, _, registry = _make_harness(
        tmp_path)
    user_store.set_default_agent("u1", "hermes")
    sessions.activate("u1", "root_1", "sid_1", None, "goose", None)

    inv = registry.parse("!agents", "@bot")
    handled = await registry.dispatch(
        inv,
        post={
            "id": "p2",
            "root_id": "root_1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agents",
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
    msg = api.create_post.call_args[0][1]
    assert "`goose` (*active in thread*)" in msg
    assert "`hermes` (*your default*)" in msg


@pytest.mark.asyncio
async def test_agent_command_query_switch_resets_model_and_unknown(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)

    # 1. Query with no session
    await registry.dispatch(
        registry.parse("!agent", "@bot"),
        post={
            "id": "p1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "your default agent `goose`" in api.create_post.call_args[0][1]

    # 2. Switch from active goose session (with model) -> hermes resets model
    pool.get_or_create("linux_u1", "goose")
    sessions.activate("u1", "r1", "sid_old", "linux_u1", "goose", "gpt-4o")
    await registry.dispatch(
        registry.parse("!agent hermes", "@bot"),
        post={
            "id": "p2",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent hermes"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    client.close_session.assert_awaited_once()
    staged = sessions.get("u1:r1")
    assert staged is not None
    assert staged.acp_session_id is None
    assert staged.agent_name == "hermes"
    assert staged.model_name is None
    assert "Switched agent for this thread to `hermes`" in api.create_post.call_args[
        0][1]

    # 3. Query shows staged/active agent for thread
    await registry.dispatch(
        registry.parse("!agent", "@bot"),
        post={
            "id": "p3",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Active agent for this thread is `hermes`" in api.create_post.call_args[
        0][1]

    # 4. Unknown agent rejected
    await registry.dispatch(
        registry.parse("!agent invalid_bot", "@bot"),
        post={
            "id": "p4",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent invalid_bot"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Unknown agent `invalid_bot`" in api.create_post.call_args[0][1]


@pytest.mark.asyncio
async def test_agent_default_command_get_set_and_unknown(tmp_path):
    config, api, user_store, sessions, pool, _, registry = _make_harness(
        tmp_path)

    # Query default
    await registry.dispatch(
        registry.parse("!agent-default", "@bot"),
        post={
            "id": "p1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent-default"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "persistent default agent is `goose`" in api.create_post.call_args[
        0][1]

    # Set persistent default to hermes
    await registry.dispatch(
        registry.parse("!agent-default hermes", "@bot"),
        post={
            "id": "p2",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent-default hermes"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p2",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Persistent default agent set to `hermes`" in api.create_post.call_args[
        0][1]
    assert user_store.get_default_agent("u1") == "hermes"

    # Reject unknown agent
    await registry.dispatch(
        registry.parse("!agent-default nope", "@bot"),
        post={
            "id": "p3",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!agent-default nope"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p3",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Unknown agent `nope`" in api.create_post.call_args[0][1]
