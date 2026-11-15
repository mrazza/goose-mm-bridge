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
        admin_users=admin_users if admin_users is not None else ["admin_id"],
        user_mapping_file=str(tmp_path / "missing_mapping.json"),
        user_agent_preferences_file=str(tmp_path / "prefs.json"),
        prewarm_config_file=str(tmp_path / "missing_prewarm.json"),
    )
    api = MagicMock()
    api.create_post = AsyncMock(return_value={"id": "reply_1"})
    users = {
        "admin_id": {
            "id": "admin_id",
            "username": "admin"
        },
        "alice_id": {
            "id": "alice_id",
            "username": "alice"
        },
        "bot_id": {
            "id": "bot_id",
            "username": "goose-bot"
        },
    }
    api.get_user = AsyncMock(side_effect=lambda uid: users.get(uid))
    api.search_users = AsyncMock(return_value=[])

    user_store = UserStore(config, api)
    pool = AgentClientPool(config, client_factory=lambda u, a: MagicMock())
    sessions = SessionManager(config, pool)
    registry = create_default_registry()
    return config, api, user_store, sessions, pool, registry


@pytest.mark.asyncio
async def test_impersonate_permission_denied(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_harness(tmp_path)

    handled = await registry.dispatch(
        registry.parse("!impersonate @admin", "@goose-bot"),
        post={
            "id": "p1",
            "user_id": "alice_id",
            "channel_id": "dm_a",
            "message": "!impersonate @admin",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        bot_id="bot_id",
        bot_username="goose-bot",
    )
    assert handled is True
    assert "Only administrators can use the `!impersonate` command" in api.create_post.call_args[
        0][1]
    assert user_store.impersonations == {}


@pytest.mark.asyncio
async def test_impersonate_user_not_found_and_bot_self(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_harness(tmp_path)

    # Not found
    await registry.dispatch(
        registry.parse("!impersonate @ghost", "@goose-bot"),
        post={
            "id": "p1",
            "user_id": "admin_id",
            "channel_id": "dm_1",
            "message": "!impersonate @ghost",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        bot_id="bot_id",
        bot_username="goose-bot",
    )
    assert "User `ghost` could not be found" in api.create_post.call_args[0][1]

    # Bot self by ID and by username
    await registry.dispatch(
        registry.parse("!impersonate bot_id", "@goose-bot"),
        post={
            "id": "p2",
            "user_id": "admin_id",
            "channel_id": "dm_1",
            "message": "!impersonate bot_id",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p2",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        bot_id="bot_id",
        bot_username="goose-bot",
    )
    assert "cannot impersonate the bot itself" in api.create_post.call_args[0][
        1]

    api.search_users = AsyncMock(return_value=[{
        "id": "other_bot_id",
        "username": "goose-bot"
    }])
    await registry.dispatch(
        registry.parse("!impersonate goose-bot", "@goose-bot"),
        post={
            "id": "p3",
            "user_id": "admin_id",
            "channel_id": "dm_1",
            "message": "!impersonate goose-bot",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p3",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        bot_id="bot_id",
        bot_username="goose-bot",
    )
    assert "cannot impersonate the bot itself" in api.create_post.call_args[0][
        1]
    assert user_store.impersonations == {}


@pytest.mark.asyncio
async def test_impersonate_set_and_clear_even_when_target_unapproved(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_harness(
        tmp_path,
        approved_users=["admin"],  # alice is NOT approved or admin
        admin_users=["admin"],
    )
    api.search_users = AsyncMock(return_value=[{
        "id": "alice_id",
        "username": "alice"
    }])

    # Set impersonation to alice via @alice search
    assert await registry.dispatch(
        registry.parse("!impersonate @alice", "@goose-bot"),
        post={
            "id": "p1",
            "user_id": "admin_id",
            "channel_id": "dm_1",
            "message": "!impersonate @alice",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        bot_id="bot_id",
        bot_username="goose-bot",
    ) is True
    assert user_store.get_impersonation("admin_id") == {
        "id": "alice_id",
        "username": "alice",
    }
    assert "now impersonating @alice" in api.create_post.call_args[0][1]

    # Clear while impersonating an unapproved user must still succeed
    # (resolves_impersonation=False ensures actor remains admin)
    for clear_cmd in ("!impersonate clear", "!impersonate off",
                      "!impersonate stop", "!impersonate"):
        user_store.set_impersonation("admin_id", "alice_id", "alice")
        assert await registry.dispatch(
            registry.parse(clear_cmd, "@goose-bot"),
            post={
                "id": "p2",
                "user_id": "admin_id",
                "channel_id": "dm_1",
                "message": clear_cmd,
            },
            is_dm=True,
            is_mentioned=False,
            root_id="p2",
            config=config,
            api=api,
            user_store=user_store,
            sessions=sessions,
            agent_pool=pool,
            bot_id="bot_id",
            bot_username="goose-bot",
        ) is True
        assert user_store.get_impersonation("admin_id") is None
        assert "Impersonation cleared" in api.create_post.call_args[0][1]

    # Clearing again reports not impersonating
    await registry.dispatch(
        registry.parse("!impersonate clear", "@goose-bot"),
        post={
            "id": "p3",
            "user_id": "admin_id",
            "channel_id": "dm_1",
            "message": "!impersonate clear",
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p3",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        bot_id="bot_id",
        bot_username="goose-bot",
    )
    assert "not currently impersonating" in api.create_post.call_args[0][1]


@pytest.mark.asyncio
async def test_restart_command_permissions_scope_and_exit(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_harness(tmp_path)
    shutdown = AsyncMock()
    inv = registry.parse("@goose-bot !restart", "@goose-bot")

    # Non-admin denied
    handled = await registry.dispatch(
        inv,
        post={
            "id": "p1",
            "user_id": "alice_id",
            "channel_id": "c1",
            "message": "@goose-bot !restart",
        },
        is_dm=False,
        is_mentioned=True,
        root_id="p1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
        shutdown_callback=shutdown,
    )
    assert handled is True
    assert "Only administrators can use the `!restart` command" in api.create_post.call_args[
        0][1]
    shutdown.assert_not_awaited()

    # Admin with @bot !restart shuts down and exits with 0
    with pytest.raises(SystemExit) as exc_info:
        await registry.dispatch(
            inv,
            post={
                "id": "p2",
                "user_id": "admin_id",
                "channel_id": "c1",
                "message": "@goose-bot !restart",
            },
            is_dm=False,
            is_mentioned=True,
            root_id="p2",
            config=config,
            api=api,
            user_store=user_store,
            sessions=sessions,
            agent_pool=pool,
            shutdown_callback=shutdown,
        )
    assert exc_info.value.code == 0
    assert "Restarting goose-mm-bridge service" in api.create_post.call_args[0][
        1]
    shutdown.assert_awaited_once()
