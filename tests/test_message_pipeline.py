import asyncio
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from acp.pool import AgentClientPool
from commands import create_default_registry
from config import Config
from message_pipeline import MessagePipeline
from sessions import SessionManager
from streaming import MattermostStreamRenderer
from user_store import UserStore


def _make_pipeline(
    tmp_path,
    *,
    approved_users=None,
    admin_users=None,
    require_user_mapping=False,
    mapping=None,
    client=None,
):
    mapping_path = tmp_path / "mapping.json"
    if mapping is not None:
        mapping_path.write_text(json.dumps(mapping))

    config = Config(
        mattermost_url="example.com",
        mattermost_token="token",
        approved_users=approved_users if approved_users is not None else [],
        admin_users=admin_users if admin_users is not None else [],
        require_user_mapping=require_user_mapping,
        user_mapping_file=str(mapping_path),
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
    api.create_post = AsyncMock(return_value={"id": "bot_post"})
    api.update_post = AsyncMock(return_value={"id": "bot_post"})
    api.get_thread = AsyncMock(return_value={"posts": {}})
    users = {
        "u1": {
            "id": "u1",
            "username": "alice"
        },
        "u2": {
            "id": "u2",
            "username": "bob"
        },
        "admin_id": {
            "id": "admin_id",
            "username": "admin"
        },
    }
    api.get_user = AsyncMock(side_effect=lambda uid: users.get(uid))

    if client is None:
        client = MagicMock()
        client.create_session = AsyncMock(return_value="acp_sid_1")
        client.set_model = AsyncMock(return_value={})
        client.cancel_prompt = AsyncMock()
        client.close_session = AsyncMock()
        client.context_usage = {}

        async def default_prompt(sid, text):
            yield {"type": "final", "text": "Hello from agent"}

        client.prompt = default_prompt

    user_store = UserStore(config, api)
    pool = AgentClientPool(config, client_factory=lambda u, a: client)
    sessions = SessionManager(config, pool)
    renderer = MattermostStreamRenderer(api, config)
    commands = create_default_registry()
    pipeline = MessagePipeline(
        config=config,
        api=api,
        user_store=user_store,
        agent_pool=pool,
        sessions=sessions,
        renderer=renderer,
        commands=commands,
    )
    pipeline.set_bot_identity("bot_id", "goose-bot", "@goose-bot")
    return config, api, user_store, pool, sessions, client, pipeline


async def _drain_tasks(pipeline):
    if pipeline.background_tasks:
        await asyncio.gather(*list(pipeline.background_tasks))


@pytest.mark.asyncio
async def test_process_post_ignores_own_and_whitespace_posts(tmp_path):
    _, api, _, _, sessions, _, pipeline = _make_pipeline(tmp_path)
    pipeline.handle_message = AsyncMock()
    channel_map = {"dm_1": {"id": "dm_1", "type": "D"}}

    await pipeline.process_post(
        {
            "id": "p_bot",
            "user_id": "bot_id",
            "channel_id": "dm_1",
            "message": "own msg",
        },
        channel_map,
    )
    await pipeline.process_post(
        {
            "id": "p_empty",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "   \n\t ",
        },
        channel_map,
    )
    await _drain_tasks(pipeline)

    pipeline.handle_message.assert_not_awaited()
    assert sessions.thread_counters == {}
    api.create_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_post_commands_do_not_increment_thread_counters(tmp_path):
    _, _, _, _, sessions, _, pipeline = _make_pipeline(tmp_path)
    sessions.thread_counters["root_1"] = 3
    sessions.activate("u1", "root_1", "sid_1", None, "goose", None)

    await pipeline.process_post(
        {
            "id": "p_cmd",
            "root_id": "root_1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "!agents",
        },
        {"c1": {
            "id": "c1",
            "type": "O"
        }},
    )
    assert sessions.thread_counters["root_1"] == 3


@pytest.mark.asyncio
async def test_catchup_flow_new_existing_clearing_and_synced(tmp_path):
    prompts = []

    client = MagicMock()
    client.create_session = AsyncMock(return_value="sid_1")
    client.set_model = AsyncMock()

    async def capture_prompt(sid, text):
        prompts.append(text)
        yield {"type": "final", "text": "ok"}

    client.prompt = capture_prompt

    _, api, _, _, sessions, _, pipeline = _make_pipeline(tmp_path,
                                                         client=client)
    channel_map = {"c1": {"id": "c1", "type": "O"}}

    # 1. Existing thread with 2 non-empty earlier messages (plus p4 = 3)
    api.get_thread = AsyncMock(
        return_value={
            "posts": {
                "p1": {
                    "user_id": "u2",
                    "message": "msg 1"
                },
                "p2": {
                    "user_id": "u2",
                    "message": "   "
                },  # ignored empty
                "p3": {
                    "user_id": "u1",
                    "message": "msg 2"
                },
                "p4": {
                    "user_id": "u1",
                    "message": "@goose-bot join"
                },
            }
        })
    await pipeline.process_post(
        {
            "id": "p4",
            "root_id": "p1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "@goose-bot join",
        },
        channel_map,
    )
    await _drain_tasks(pipeline)
    assert "joined an existing thread with 2 earlier messages" in prompts[0]

    # 2. Missed message p5 (unmentioned) then mentioned p6
    await pipeline.process_post(
        {
            "id": "p5",
            "root_id": "p1",
            "user_id": "u2",
            "channel_id": "c1",
            "message": "side note",
        },
        channel_map,
    )
    await pipeline.process_post(
        {
            "id": "p6",
            "root_id": "p1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "@goose-bot catchup?",
        },
        channel_map,
    )
    await _drain_tasks(pipeline)
    assert "There are 1 new messages in this thread" in prompts[1]

    # 3. Caught-up next message clears hint
    await pipeline.process_post(
        {
            "id": "p7",
            "root_id": "p1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "@goose-bot next",
        },
        channel_map,
    )
    await _drain_tasks(pipeline)
    assert "You are now caught up with the thread." in prompts[2]

    # 4. Subsequent message when synced has neither catch-up nor cleared banner
    await pipeline.process_post(
        {
            "id": "p8",
            "root_id": "p1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "@goose-bot steady",
        },
        channel_map,
    )
    await _drain_tasks(pipeline)
    assert "earlier messages" not in prompts[3]
    assert "new messages" not in prompts[3]
    assert "now caught up" not in prompts[3]
    assert sessions.get("u1:p1").processed_count == 7


@pytest.mark.asyncio
async def test_require_user_mapping_and_approved_users_gating(tmp_path):
    _, api, _, _, _, _, pipeline = _make_pipeline(
        tmp_path,
        approved_users=["alice"],
        require_user_mapping=True,
        mapping={},
    )
    pipeline.handle_message = AsyncMock()
    channel_map = {"dm_1": {"id": "dm_1", "type": "D"}}

    # Unapproved Bob is ignored silently
    await pipeline.process_post(
        {
            "id": "p1",
            "user_id": "u2",
            "channel_id": "dm_1",
            "message": "hi"
        },
        channel_map,
    )
    await _drain_tasks(pipeline)
    pipeline.handle_message.assert_not_awaited()
    api.create_post.assert_not_awaited()

    # Approved Alice without Linux mapping gets warning post
    await pipeline.process_post(
        {
            "id": "p2",
            "user_id": "u1",
            "channel_id": "dm_1",
            "message": "hi"
        },
        channel_map,
    )
    await _drain_tasks(pipeline)
    pipeline.handle_message.assert_not_awaited()
    assert "no assigned OS-level isolation profile" in api.create_post.call_args[
        0][1]


@pytest.mark.asyncio
async def test_handle_message_formatting_attachments_and_empty(tmp_path):
    prompts = []
    client = MagicMock()
    client.create_session = AsyncMock(return_value="sid_1")

    async def capture_prompt(sid, text):
        prompts.append(text)
        yield {"type": "final", "text": "done"}

    client.prompt = capture_prompt
    _, _, _, _, _, _, pipeline = _make_pipeline(tmp_path, client=client)

    await pipeline.handle_message(
        {
            "id": "p_empty",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "   "
        },
        None,
        "alice",
    )
    assert prompts == []

    await pipeline.handle_message(
        {
            "id": "p1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "@goose-bot inspect these",
            "file_ids": ["f1", "f2"],
        },
        "linux_alice",
        "alice",
    )
    assert "[Sender: @alice] [Has 2 attachment(s)] inspect these" in prompts[0]


@pytest.mark.asyncio
async def test_handle_message_staged_model_apply_and_failure_reset(tmp_path):
    client = MagicMock()
    client.create_session = AsyncMock(side_effect=["sid_ok", "sid_fail"])
    client.set_model = AsyncMock(side_effect=[None, RuntimeError("bad model")])

    async def ok_prompt(sid, text):
        yield {"type": "final", "text": "ok"}

    client.prompt = ok_prompt
    _, api, _, _, sessions, _, pipeline = _make_pipeline(tmp_path,
                                                         client=client)

    # 1. Staged model succeeds on first session
    sessions.stage_model("u1", "r1", "gpt-4o")
    await pipeline.handle_message(
        {
            "id": "r1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "hello"
        },
        None,
        "alice",
    )
    client.set_model.assert_awaited_once_with("sid_ok", "gpt-4o")
    assert sessions.get("u1:r1").model_name == "gpt-4o"

    # 2. Staged model fails on second thread -> notifies user and resets model_name
    sessions.stage_model("u1", "r2", "broken-model")
    await pipeline.handle_message(
        {
            "id": "r2",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "hello"
        },
        None,
        "alice",
    )
    posts = [c.args[1] for c in api.create_post.call_args_list]
    assert any(
        "Failed to apply staged model `broken-model`" in p for p in posts)
    assert sessions.get("u1:r2").model_name is None


@pytest.mark.asyncio
async def test_handle_message_retry_preserves_model_and_reports_double_failure(
    tmp_path,):
    prompts = []
    client = MagicMock()
    client.create_session = AsyncMock(return_value="sid_retry")
    client.set_model = AsyncMock()

    attempt = [0]

    async def flaky_prompt(sid, text):
        attempt[0] += 1
        prompts.append((sid, text))
        if attempt[0] == 1:
            raise RuntimeError("socket dropped")
        yield {"type": "final", "text": "recovered"}

    client.prompt = flaky_prompt
    _, api, _, _, sessions, _, pipeline = _make_pipeline(tmp_path,
                                                         client=client)
    sessions.activate("u1", "r1", "sid_old", None, "goose", "claude-3-5-sonnet")

    await pipeline.handle_message(
        {
            "id": "p2",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "hello",
        },
        None,
        "alice",
    )
    client.set_model.assert_awaited_once_with("sid_retry", "claude-3-5-sonnet")
    assert sessions.get("u1:r1").model_name == "claude-3-5-sonnet"
    assert "terminated unexpectedly" in prompts[1][1]
    assert any("Connection to Goose was reset" in c.args[1]
               for c in api.create_post.call_args_list)

    # Double failure posts error summary
    async def always_fail(sid, text):
        raise ValueError("persistent crash")
        yield  # pragma: no cover

    client.prompt = always_fail
    await pipeline.handle_message(
        {
            "id": "p3",
            "root_id": "r1",
            "user_id": "u1",
            "channel_id": "c1",
            "message": "hello again",
        },
        None,
        "alice",
    )
    assert "Sorry, I encountered an error: persistent crash" in api.create_post.call_args[
        0][1]


@pytest.mark.asyncio
async def test_concurrency_locking_and_impersonation_execution(tmp_path):
    order = []
    client = MagicMock()
    client.create_session = AsyncMock(return_value="sid_lock")

    async def slow_prompt(sid, text):
        order.append(("start", text))
        await asyncio.sleep(0.03)
        order.append(("end", text))
        yield {"type": "final", "text": "ok"}

    client.prompt = slow_prompt
    _, _, user_store, _, sessions, _, pipeline = _make_pipeline(
        tmp_path,
        admin_users=["admin"],
        mapping={"u1": "linux_alice"},
        client=client,
    )

    # Admin impersonating Alice sends prompt -> executes in u1/linux_alice context
    user_store.set_impersonation("admin_id", "u1", "alice")
    await pipeline.process_post(
        {
            "id": "r1",
            "user_id": "admin_id",
            "channel_id": "dm_1",
            "message": "first",
        },
        {"dm_1": {
            "id": "dm_1",
            "type": "D"
        }},
    )
    await _drain_tasks(pipeline)
    assert sessions.get("u1:r1") is not None
    assert sessions.get("u1:r1").linux_user == "linux_alice"

    # Concurrent messages on the same thread serialize cleanly under lock
    order.clear()
    await asyncio.gather(
        pipeline.handle_message(
            {
                "id": "p2",
                "root_id": "r1",
                "user_id": "u1",
                "channel_id": "dm_1",
                "message": "one",
            },
            "linux_alice",
            "alice",
        ),
        pipeline.handle_message(
            {
                "id": "p3",
                "root_id": "r1",
                "user_id": "u1",
                "channel_id": "dm_1",
                "message": "two",
            },
            "linux_alice",
            "alice",
        ),
    )
    assert order[0][0] == "start" and order[1][0] == "end"
    assert order[2][0] == "start" and order[3][0] == "end"
