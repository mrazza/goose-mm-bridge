import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from acp.pool import AgentClientPool
from commands import create_default_registry
from config import Config
from sessions import SessionManager
from user_store import UserStore


def _make_harness(
    tmp_path,
    *,
    allow_model_control=True,
    model_control_users=None,
    goose_model="claude-3-5-sonnet",
    agents=None,
):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="token",
        approved_users=[],
        allow_model_control=allow_model_control,
        model_control_users=model_control_users,
        goose_model=goose_model,
        user_mapping_file=str(tmp_path / "mapping.json"),
        user_configs_dir=str(tmp_path / "user_configs"),
        user_agent_preferences_file=str(tmp_path / "prefs.json"),
        prewarm_config_file=str(tmp_path / "missing_prewarm.json"),
        agents=agents or {
            "goose": {
                "command": ["goose", "acp"],
                "model_env": ["GOOSE_MODEL"]
            },
            "hermes": {
                "command": ["hermes", "acp"],
                "model_env": ["HERMES_MODEL", "HERMES_INFERENCE_MODEL"]
            },
        },
    )
    api = MagicMock()
    api.create_post = AsyncMock(return_value={"id": "reply_1"})
    users = {
        "u1": {
            "id": "u1",
            "username": "alice"
        },
        "u2": {
            "id": "u2",
            "username": "bob"
        },
    }
    api.get_user = AsyncMock(side_effect=lambda uid: users.get(uid))

    client = MagicMock()
    client.set_model = AsyncMock(return_value={})
    client.get_models = MagicMock(return_value=None)

    user_store = UserStore(config, api)
    pool = AgentClientPool(config, client_factory=lambda u, a: client)
    sessions = SessionManager(config, pool)
    registry = create_default_registry()
    return config, api, user_store, sessions, pool, client, registry


@pytest.mark.asyncio
async def test_model_query_active_session_and_models_with_arg(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)
    sessions.activate("u1", "r1", "sid_1", None, "goose", "gpt-4o")
    client.get_models.return_value = {
        "current_model":
            "gpt-4o",
        "available_models": [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "description": "OpenAI flagship"
            },
            "claude-3-5-sonnet",
        ],
    }

    await registry.dispatch(
        registry.parse("!model", "@bot"),
        post={
            "id": "p1",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model"
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
    msg = api.create_post.call_args[0][1]
    assert "`gpt-4o` (*active in thread*)" in msg
    assert "GPT-4o - OpenAI flagship (*active*)" in msg
    assert "`claude-3-5-sonnet`" in msg

    # !models <arg> must remain a query and not call set_model
    await registry.dispatch(
        registry.parse("!models claude-3-5-sonnet", "@bot"),
        post={
            "id": "p2",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!models claude-3-5-sonnet"
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
    client.set_model.assert_not_awaited()
    assert "Model Status" in api.create_post.call_args[0][1]


@pytest.mark.asyncio
async def test_model_query_no_session_does_not_leak_other_thread_model(
    tmp_path,):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path, goose_model="env-default-model")
    # Simulate client having another thread's active model in last_known_models
    client.get_models.return_value = {
        "current_model": "other-thread-model",
        "available_models": ["env-default-model", "other-thread-model"],
    }

    await registry.dispatch(
        registry.parse("!model", "@bot"),
        post={
            "id": "p1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="fresh_thread",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    msg = api.create_post.call_args[0][1]
    assert "`env-default-model` (*default from environment*)" in msg
    assert "Current model: `other-thread-model`" not in msg


@pytest.mark.asyncio
async def test_model_query_uses_generalized_agent_model_env(tmp_path):
    custom_agents = {
        "custom-agent": {
            "command": ["custom-acp"],
            "model_env": ["CUSTOM_MODEL"],
            "env": {
                "CUSTOM_MODEL": "custom-default-v2"
            },
        }
    }
    config, api, user_store, sessions, pool, _, registry = _make_harness(
        tmp_path, goose_model=None, agents=custom_agents)
    config.default_agent = "custom-agent"

    await registry.dispatch(
        registry.parse("!model", "@bot"),
        post={
            "id": "p1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model"
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
    assert "`custom-default-v2` (*default from environment*)" in api.create_post.call_args[
        0][1]


@pytest.mark.asyncio
async def test_model_switch_active_staged_unknown_and_error(tmp_path):
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path)
    client.get_models.return_value = {
        "current_model": "claude-3-5-sonnet",
        "available_models": [{
            "id": "claude-3-5-sonnet"
        }, {
            "id": "gpt-4o"
        }],
    }

    # 1. Stage model when no session exists
    await registry.dispatch(
        registry.parse("!model gpt-4o", "@bot"),
        post={
            "id": "p1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model gpt-4o"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_staged",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert sessions.get("u1:r_staged").model_name == "gpt-4o"
    assert "Model set to `gpt-4o` for this thread" in api.create_post.call_args[
        0][1]

    # Query reports staged status
    await registry.dispatch(
        registry.parse("!model", "@bot"),
        post={
            "id": "p2",
            "root_id": "r_staged",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_staged",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "`gpt-4o` (*staged for next session*)" in api.create_post.call_args[
        0][1]

    # 2. Unknown model rejected
    await registry.dispatch(
        registry.parse("!model nope-model", "@bot"),
        post={
            "id": "p3",
            "root_id": "r_staged",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model nope-model"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_staged",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Unknown model `nope-model`" in api.create_post.call_args[0][1]

    # 3. Switch on active session
    sessions.activate("u1", "r_active", "sid_1", None, "goose",
                      "claude-3-5-sonnet")
    await registry.dispatch(
        registry.parse("!model gpt-4o", "@bot"),
        post={
            "id": "p4",
            "root_id": "r_active",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model gpt-4o"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_active",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    client.set_model.assert_awaited_once_with("sid_1", "gpt-4o")
    assert sessions.get("u1:r_active").model_name == "gpt-4o"
    assert "Switched model for this thread to `gpt-4o`" in api.create_post.call_args[
        0][1]

    # 4. RPC failure during set_model
    client.set_model.side_effect = RuntimeError("unsupported")
    await registry.dispatch(
        registry.parse("!model claude-3-5-sonnet", "@bot"),
        post={
            "id": "p5",
            "root_id": "r_active",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model claude-3-5-sonnet"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_active",
        config=config,
        api=api,
        user_store=user_store,
        sessions=sessions,
        agent_pool=pool,
    )
    assert "Failed to switch model to `claude-3-5-sonnet`: unsupported" in api.create_post.call_args[
        0][1]


@pytest.mark.asyncio
async def test_model_permissions_global_user_env_and_allowlist(tmp_path):
    # 1. Global allow_model_control=False
    config, api, user_store, sessions, pool, client, registry = _make_harness(
        tmp_path, allow_model_control=False)

    await registry.dispatch(
        registry.parse("!model", "@bot"),
        post={
            "id": "p1",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model"
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
    assert "locked by administrator" in api.create_post.call_args[0][1]

    await registry.dispatch(
        registry.parse("!model gpt-4o", "@bot"),
        post={
            "id": "p2",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model gpt-4o"
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
    assert "Model switching is disabled" in api.create_post.call_args[0][1]
    client.set_model.assert_not_awaited()

    # 2. model_control_users allowlist + user_configs/<linux>.env override
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({"u2": "linux_bob"}))
    user_dir = tmp_path / "user_configs"
    user_dir.mkdir()
    (user_dir / "linux_bob.env").write_text("ALLOW_MODEL_CONTROL=false\n")

    config2, api2, user_store2, sessions2, pool2, client2, reg2 = _make_harness(
        tmp_path,
        allow_model_control=True,
        model_control_users=["alice"],
    )
    # Alice allowed
    await reg2.dispatch(
        reg2.parse("!model gpt-4o", "@bot"),
        post={
            "id": "p3",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "!model gpt-4o"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_alice",
        config=config2,
        api=api2,
        user_store=user_store2,
        sessions=sessions2,
        agent_pool=pool2,
    )
    assert sessions2.get("u1:r_alice").model_name == "gpt-4o"

    # Bob blocked both by allowlist and user env
    await reg2.dispatch(
        reg2.parse("!model gpt-4o", "@bot"),
        post={
            "id": "p4",
            "user_id": "u2",
            "channel_id": "dm_2",
            "message": "!model gpt-4o"
        },
        is_dm=True,
        is_mentioned=False,
        root_id="r_bob",
        config=config2,
        api=api2,
        user_store=user_store2,
        sessions=sessions2,
        agent_pool=pool2,
    )
    assert "Model switching is disabled" in api2.create_post.call_args[0][1]
