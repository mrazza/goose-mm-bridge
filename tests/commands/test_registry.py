from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from acp.pool import AgentClientPool
from commands import AccessLevel
from commands import create_default_registry
from config import Config
from message_pipeline import MessagePipeline
from sessions import SessionManager
from streaming import MattermostStreamRenderer
from user_store import UserStore


def _make_deps(tmp_path, approved_users=None, admin_users=None):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="token",
        approved_users=approved_users if approved_users is not None else [],
        admin_users=admin_users if admin_users is not None else ["admin_id"],
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
    api.get_thread = AsyncMock(return_value={"posts": {}})

    users = {
        "admin_id": {
            "id": "admin_id",
            "username": "admin"
        },
        "alice_id": {
            "id": "alice_id",
            "username": "alice"
        },
        "bob_id": {
            "id": "bob_id",
            "username": "bob"
        },
    }
    api.get_user = AsyncMock(side_effect=lambda uid: users.get(uid))

    user_store = UserStore(config, api)
    pool = AgentClientPool(config, client_factory=lambda u, a: MagicMock())
    sessions = SessionManager(config, pool)
    registry = create_default_registry()
    return config, api, user_store, sessions, pool, registry


@pytest.mark.parametrize(
    "raw,expected_name,expected_trigger,expected_args",
    [
        ("!model", "model", "!model", ""),
        ("!models", "model", "!models", ""),
        ("!models claude-3", "model", "!models", "claude-3"),
        ("@goose-bot !stop", "stop", "!stop", ""),
        ("@goose-bot   !context", "context", "!context", ""),
        ("!agent-default hermes", "agent-default", "!agent-default", "hermes"),
        ("!HELP", "help", "!help", ""),
    ],
)
def test_registry_parse_valid_commands(
    raw,
    expected_name,
    expected_trigger,
    expected_args,
):
    registry = create_default_registry()
    inv = registry.parse(raw, "@goose-bot")
    assert inv is not None
    assert inv.name == expected_name
    assert inv.trigger == expected_trigger
    assert inv.raw_args == expected_args


@pytest.mark.parametrize(
    "raw",
    [
        "!modeling ideas",
        "@goose-bot !modeling something",
        "!agentic workflow",
        "!impersonated user",
        "!stopping now",
        "!unknown",
        "hello world",
        "   ",
    ],
)
def test_registry_parse_ignores_non_commands_and_prefix_lookalikes(raw):
    registry = create_default_registry()
    assert registry.parse(raw, "@goose-bot") is None


@pytest.mark.asyncio
async def test_process_post_modeling_word_not_intercepted(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_deps(tmp_path)
    renderer = MattermostStreamRenderer(api, config)
    pipeline = MessagePipeline(
        config=config,
        api=api,
        user_store=user_store,
        agent_pool=pool,
        sessions=sessions,
        renderer=renderer,
        commands=registry,
    )
    pipeline.set_bot_identity("bot_id", "goose-bot", "@goose-bot")
    pipeline.handle_message = AsyncMock()

    post = {
        "id": "p1",
        "user_id": "alice_id",
        "channel_id": "c1",
        "message": "@goose-bot !modeling climate data",
    }
    api.get_thread = AsyncMock(return_value={"posts": {"p1": post}})
    channel_map = {"c1": {"id": "c1", "type": "O"}}

    await pipeline.process_post(post, channel_map)

    assert len(pipeline.background_tasks) == 1
    await list(pipeline.background_tasks)[0]
    pipeline.handle_message.assert_awaited_once()
    assert sessions.thread_counters["p1"] == 1


@pytest.mark.asyncio
async def test_channel_scope_thread_or_mention_option_a(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_deps(tmp_path)
    post = {
        "id": "p2",
        "root_id": "root_1",
        "user_id": "alice_id",
        "channel_id": "c1",
        "message": "!agents",
    }
    inv = registry.parse(post["message"], "@goose-bot")
    assert inv is not None

    # Top-level / untracked public channel without @bot -> ignored
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
    assert handled is False
    api.create_post.assert_not_awaited()

    # Mentioned in channel -> executes
    handled = await registry.dispatch(
        inv,
        post=post,
        is_dm=False,
        is_mentioned=True,
        root_id="root_1",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert handled is True
    assert api.create_post.await_count == 1

    # DM -> executes without mention
    handled = await registry.dispatch(
        inv,
        post=post,
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
    assert api.create_post.await_count == 2

    # Bare command inside active or staged thread -> executes
    await sessions.stage_agent("alice_id", "root_1", "hermes")
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
    assert api.create_post.await_count == 3


@pytest.mark.asyncio
async def test_channel_scope_dm_or_mention_for_restart(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_deps(tmp_path)
    sessions.activate("admin_id", "root_1", "sid_1", None, "goose", None)
    post = {
        "id": "p2",
        "root_id": "root_1",
        "user_id": "admin_id",
        "channel_id": "c1",
        "message": "!restart",
    }
    inv = registry.parse("!restart", "@goose-bot")
    shutdown = AsyncMock()

    # Even inside an active thread, bare !restart in a channel is out of scope
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
        shutdown_callback=shutdown,
    )
    assert handled is False
    shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_access_levels_public_approved_and_admin(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_deps(
        tmp_path,
        approved_users=["alice"],
        admin_users=["admin"],
    )
    sessions.activate("bob_id", "dm_root", "sid_bob", None, "goose", None)

    # Bob is NOT in approved_users:
    # 1) !stop and !context (PUBLIC) are allowed
    stop_post = {
        "id": "p_stop",
        "user_id": "bob_id",
        "channel_id": "dm_1",
        "message": "!stop",
    }
    ctx_post = {
        "id": "p_ctx",
        "root_id": "dm_root",
        "user_id": "bob_id",
        "channel_id": "dm_1",
        "message": "!context",
    }
    assert await registry.dispatch(
        registry.parse("!stop", "@bot"),
        post=stop_post,
        is_dm=True,
        is_mentioned=False,
        root_id="dm_root",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    ) is True
    assert await registry.dispatch(
        registry.parse("!context", "@bot"),
        post=ctx_post,
        is_dm=True,
        is_mentioned=False,
        root_id="dm_root",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    ) is True
    assert api.create_post.await_count == 1  # !context replied

    # 2) APPROVED and ADMIN commands are ignored for unapproved Bob
    for cmd in ("!help", "!agents", "!agent hermes", "!model",
                "!impersonate alice", "!restart"):
        inv = registry.parse(cmd, "@bot")
        handled = await registry.dispatch(
            inv,
            post={
                "id": "p_x",
                "user_id": "bob_id",
                "channel_id": "dm_1",
                "message": cmd
            },
            is_dm=True,
            is_mentioned=False,
            root_id="p_x",
            config=config,
            api=api,
            user_store=user_store,
            sessions=sessions,
            agent_pool=pool,
        )
        assert handled is False

    # 3) Approved non-admin Alice gets permission denied on ADMIN commands
    handled = await registry.dispatch(
        registry.parse("!impersonate admin", "@bot"),
        post={
            "id": "p_imp",
            "user_id": "alice_id",
            "channel_id": "dm_a",
            "message": "!impersonate admin"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="p_imp",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert handled is True
    reply_msg = api.create_post.call_args[0][1]
    assert "Only administrators can use the `!impersonate` command" in reply_msg


@pytest.mark.asyncio
async def test_help_command_visibility_for_standard_and_admin_users(tmp_path):
    config, api, user_store, sessions, pool, registry = _make_deps(
        tmp_path,
        approved_users=["alice", "admin"],
        admin_users=["admin"],
    )

    help_inv = registry.parse("!help", "@bot")
    assert help_inv is not None

    # Standard approved user should not see Admin Commands
    await registry.dispatch(
        help_inv,
        post={
            "id": "p1",
            "user_id": "alice_id",
            "channel_id": "dm_a",
            "message": "!help"
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
    user_help = api.create_post.call_args[0][1]
    assert "Available Commands" in user_help
    assert "!stop" in user_help
    assert "!context" in user_help
    assert "!models" in user_help
    assert "Admin Commands" not in user_help
    assert "!impersonate" not in user_help
    assert "!restart" not in user_help

    # Admin user sees both standard and Admin Commands (even if impersonating)
    user_store.set_impersonation("admin_id", "alice_id", "alice")
    await registry.dispatch(
        help_inv,
        post={
            "id": "p2",
            "user_id": "admin_id",
            "channel_id": "dm_adm",
            "message": "!help"
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
    admin_help = api.create_post.call_args[0][1]
    assert "Admin Commands" in admin_help
    assert "!impersonate" in admin_help
    assert "!restart" in admin_help

    # Sanity check AccessLevel annotations
    specs_by_name = {s.name: s for s in registry.specs}
    assert specs_by_name["stop"].access == AccessLevel.PUBLIC
    assert specs_by_name["context"].access == AccessLevel.PUBLIC
    assert specs_by_name["help"].access == AccessLevel.APPROVED
    assert specs_by_name["impersonate"].access == AccessLevel.ADMIN
    assert specs_by_name["restart"].access == AccessLevel.ADMIN
