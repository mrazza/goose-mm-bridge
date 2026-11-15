import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from channel_cache import ChannelCache
from config import Config
from mattermost_bridge import MattermostBridge


@pytest.fixture
def config(tmp_path):
    return Config(
        mattermost_url="example.com",
        mattermost_token="token",
        approved_users=[],
        poll_interval=0.01,
        debug=True,
        max_sessions=5,
        user_mapping_file=str(tmp_path / "missing_mapping.json"),
        user_agent_preferences_file=str(tmp_path / "prefs.json"),
        prewarm_config_file=str(tmp_path / "missing_prewarm.json"),
    )


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={
        "id": "bot_id",
        "username": "goose-bot"
    })
    api.get_direct_channels = AsyncMock(return_value=[{
        "id": "dm_1",
        "type": "D"
    }])
    api.get_my_teams = AsyncMock(return_value=[{"id": "team_1"}])
    api.get_my_channels = AsyncMock(return_value=[{
        "id": "chan_1",
        "type": "O"
    }])
    api.get_channel_posts = AsyncMock(return_value={"posts": {}, "order": []})
    api.create_post = AsyncMock(return_value={"id": "post_1"})
    api.update_post = AsyncMock()
    api.get_thread = AsyncMock(return_value={"posts": {}})
    api.get_user = AsyncMock(return_value={"id": "u1", "username": "alice"})
    return api


@pytest.mark.asyncio
async def test_bridge_initialization_success_and_failure(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    ok = await bridge.initialize()
    assert ok is True
    assert bridge.bot_id == "bot_id"
    assert bridge.bot_username == "goose-bot"
    assert bridge.bot_mention == "@goose-bot"
    assert bridge.pipeline.bot_mention == "@goose-bot"

    mock_api.get_me = AsyncMock(return_value=None)
    ok2 = await bridge.initialize()
    assert ok2 is False


@pytest.mark.asyncio
async def test_channel_cache_dedup_and_ttl(mock_api):
    # dm_1 appears in both get_direct_channels and get_my_channels
    mock_api.get_my_channels = AsyncMock(return_value=[
        {
            "id": "dm_1",
            "type": "D"
        },
        {
            "id": "chan_1",
            "type": "O"
        },
    ])
    now = [100.0]
    cache = ChannelCache(mock_api, ttl=60.0, clock=lambda: now[0], debug=True)

    cmap1 = await cache.get_channel_map()
    assert set(cmap1.keys()) == {"dm_1", "chan_1"}
    assert mock_api.get_direct_channels.await_count == 1

    # Within TTL: no API refresh
    now[0] += 10.0
    await cache.get_channel_map()
    assert mock_api.get_direct_channels.await_count == 1

    # Past TTL: refreshes
    now[0] += 60.0
    await cache.get_channel_map()
    assert mock_api.get_direct_channels.await_count == 2

    # Explicit force refresh
    await cache.get_channels(force=True)
    assert mock_api.get_direct_channels.await_count == 3


@pytest.mark.asyncio
async def test_polling_loop_ordering_filtering_and_error_recovery(
    config,
    mock_api,
):
    bridge = MattermostBridge(api=mock_api, config=config)
    bridge.last_since = 100

    processed = []

    async def capture_post(post, channel_map):
        processed.append(post["id"])

    bridge.pipeline.process_post = AsyncMock(side_effect=capture_post)

    # 1. Transient error -> 2. None/missing posts -> 3. Mixed timestamps -> 4. Stop
    call_num = [0]

    async def channel_posts_seq(cid, since):
        if cid != "chan_1":
            return {"posts": {}, "order": []}
        call_num[0] += 1
        if call_num[0] == 1:
            raise RuntimeError("transient network glitch")
        if call_num[0] == 2:
            return None
        if call_num[0] == 3:
            return {"no_posts_key": True}
        if call_num[0] == 4:
            return {
                "posts": {
                    "p_old": {
                        "id": "p_old",
                        "create_at": 100
                    },
                    "p_new2": {
                        "id": "p_new2",
                        "create_at": 200
                    },
                    "p_new1": {
                        "id": "p_new1",
                        "create_at": 150
                    },
                }
            }
        bridge.is_shutting_down = True
        return {"posts": {}}

    mock_api.get_channel_posts = AsyncMock(side_effect=channel_posts_seq)

    async def stop_after_batch(_seconds):
        if call_num[0] >= 4:
            bridge.is_shutting_down = True

    with patch("asyncio.sleep", side_effect=stop_after_batch):
        await bridge.run()

    assert processed == ["p_new1", "p_new2"]


@pytest.mark.asyncio
async def test_run_exits_cleanly_when_initialize_fails(config, mock_api):
    mock_api.get_me = AsyncMock(return_value=None)
    bridge = MattermostBridge(api=mock_api, config=config)
    await bridge.run()
    mock_api.get_channel_posts.assert_not_awaited()


@pytest.mark.asyncio
async def test_graceful_shutdown_full_flow_multiple_clients_and_kill_timeout(
    config,
    mock_api,
):
    bridge = MattermostBridge(api=mock_api, config=config)

    # Client 1: normal subprocess exiting cleanly on terminate()
    c1 = MagicMock()
    c1.send_request = AsyncMock(return_value={"result": {}})
    c1.cancel_prompt = AsyncMock()
    c1.session_queues = {"s1": asyncio.Queue(), "s2": asyncio.Queue()}
    c1.session_models = {"s1": {}, "s2": {}}
    c1.context_usage = {"s1": {"used": 1, "size": 2}}
    p1 = MagicMock()
    p1.returncode = None
    p1.wait = AsyncMock(return_value=0)
    c1.process = p1

    # Client 2: stubborn subprocess whose wait() times out -> kill()
    c2 = MagicMock()
    c2.send_request = AsyncMock(return_value={"result": {}})
    c2.cancel_prompt = AsyncMock()
    c2.session_queues = {"s3": asyncio.Queue()}
    c2.session_models = {"s3": {}}
    c2.context_usage = {"s3": {}}
    p2 = MagicMock()
    p2.returncode = None

    async def hung_wait():
        await asyncio.sleep(5.0)

    p2.wait = hung_wait
    c2.process = p2

    bridge.agent_pool.clients[("alice", "goose")] = c1
    bridge.agent_pool.clients[("bob", "hermes")] = c2

    bridge.sessions.activate("u1", "r1", "s1", "alice", "goose", None)
    bridge.sessions.activate("u1", "r2", "s2", "alice", "goose", None)
    bridge.sessions.activate("u2", "r3", "s3", "bob", "hermes", None)

    active_task = asyncio.create_task(asyncio.sleep(10.0))
    bg_task = asyncio.create_task(asyncio.sleep(10.0))
    bridge.sessions.register_active_task("u1:r1", active_task)
    bridge.background_tasks.add(bg_task)

    # Shorten terminate timeout inside terminate_all call by patching wait_for
    orig_terminate = bridge.agent_pool.terminate_all
    bridge.agent_pool.terminate_all = lambda timeout=0.02: orig_terminate(
        timeout=0.02)

    await bridge.graceful_shutdown()

    assert bridge.is_shutting_down is True
    assert len(bridge.sessions.sessions) == 0
    assert active_task.cancelled()
    assert bg_task.cancelled()
    c1.cancel_prompt.assert_any_await("s1")
    c1.cancel_prompt.assert_any_await("s2")
    c2.cancel_prompt.assert_awaited_once_with("s3")
    p1.terminate.assert_called_once()
    p1.kill.assert_not_called()
    p2.terminate.assert_called_once()
    p2.kill.assert_called_once()


@pytest.mark.asyncio
async def test_run_keyboard_interrupt_cleans_up_even_if_termination_errors(
    config,
    mock_api,
):
    client = MagicMock()
    client.terminate = AsyncMock(side_effect=RuntimeError("terminate error"))

    bridge = MattermostBridge(
        api=mock_api,
        config=config,
        agent_client_factory=lambda u, a: client,
    )
    bridge.agent_pool.get_or_create(None, "goose")
    mock_api.get_channel_posts = AsyncMock(side_effect=KeyboardInterrupt)

    bg_task = asyncio.create_task(asyncio.sleep(10.0))
    bridge.background_tasks.add(bg_task)

    await bridge.run()

    assert bg_task.cancelled()
    client.terminate.assert_awaited_once()
