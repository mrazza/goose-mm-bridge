import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from goose_simulator import MockProcess
from goose_simulator import simulate_goose_behavior
import pytest

from config import Config
from acp_client import ACPClient
from mattermost_bridge import MattermostBridge


@pytest.fixture
def base_config():
    return Config(
        mattermost_url="http://mattermost.example.com",
        mattermost_token="fake-token",
        approved_users=[],
        poll_interval=0.01,
        debug=True,
    )


@pytest.fixture
def mock_api_base():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={
        "id": "bot_id",
        "username": "goose-bot"
    })
    api.get_direct_channels = AsyncMock(return_value=[])
    api.get_my_teams = AsyncMock(return_value=[{"id": "team_1"}])
    api.get_my_channels = AsyncMock(return_value=[{
        "id": "chan_1",
        "type": "O"
    }])
    api.get_user = AsyncMock(return_value={"id": "user_1", "username": "alice"})
    api.get_thread = AsyncMock(return_value={"posts": {}})
    api.create_post = AsyncMock(return_value={"id": "bot_post"})
    api.update_post = AsyncMock(return_value={"id": "bot_post"})
    return api


async def wait_for_prompts(prompts_list, count, timeout=3.0):
    start = asyncio.get_event_loop().time()
    while len(prompts_list) < count:
        if asyncio.get_event_loop().time() - start > timeout:
            raise TimeoutError(
                f"Timed out waiting for {count} prompts. Got {len(prompts_list)}. Prompts: {prompts_list}"
            )
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_bridge_integration_flow_with_goose_process(
        base_config, mock_api_base):
    # Setup stateful get_channel_posts
    test_post = {
        "id": "post_123",
        "user_id": "user_1",
        "channel_id": "chan_1",
        "message": "@goose-bot What is the meaning of life?",
        "create_at": 1000
    }

    call_tracker = {"done": False}

    async def side_effect_get_posts(channel_id, since):
        if not call_tracker["done"]:
            call_tracker["done"] = True
            return {"posts": {"post_123": test_post}, "order": ["post_123"]}
        return {"posts": {}, "order": []}

    mock_api_base.get_channel_posts = AsyncMock(
        side_effect=side_effect_get_posts)

    final_response_posted = asyncio.Event()

    async def side_effect_update(post_id, message, props=None):
        if "The answer is 42." in message:
            final_response_posted.set()
        return {"id": post_id}

    mock_api_base.update_post.side_effect = side_effect_update

    mock_process = MockProcess()
    bridge = MattermostBridge(
        api=mock_api_base,
        config=base_config,
        goose_client_factory=lambda u: ACPClient(u, config=base_config))
    bridge.last_since = 0

    with patch('mattermost_bridge.load_user_mapping', return_value={"user_1": "linux_alice"}), \
         patch('asyncio.create_subprocess_exec', return_value=mock_process):

        simulator_task = asyncio.create_task(
            simulate_goose_behavior(mock_process))
        bridge_task = asyncio.create_task(bridge.run())

        try:
            await asyncio.wait_for(final_response_posted.wait(), timeout=5.0)
        finally:
            bridge_task.cancel()
            simulator_task.cancel()

    assert "user_1:post_123" in bridge.sessions


@pytest.mark.asyncio
async def test_bridge_multi_turn_catchup_flow(base_config, mock_api_base):
    posts_queue = asyncio.Queue()

    async def side_effect_get_posts(channel_id, since):
        try:
            batch = posts_queue.get_nowait()
            return {
                "posts": {
                    p["id"]: p for p in batch
                },
                "order": [p["id"] for p in batch]
            }
        except asyncio.QueueEmpty:
            return {"posts": {}, "order": []}

    mock_api_base.get_channel_posts = AsyncMock(
        side_effect=side_effect_get_posts)

    mock_process = MockProcess()
    prompts_received = []

    def goose_factory(user):
        client = ACPClient(user, config=base_config)
        original_prompt = client.prompt

        async def tracking_prompt(sid, msg):
            prompts_received.append(msg)
            async for update in original_prompt(sid, msg):
                yield update

        client.prompt = tracking_prompt
        return client

    bridge = MattermostBridge(api=mock_api_base,
                              config=base_config,
                              goose_client_factory=goose_factory)
    bridge.last_since = 0

    with patch('mattermost_bridge.load_user_mapping', return_value={"user_1": "linux_alice"}), \
         patch('asyncio.create_subprocess_exec', return_value=mock_process):

        async def complex_simulator():
            await asyncio.sleep(0.01)
            mock_process.feed_stdout({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "capabilities": {}
                }
            })
            # Prompts responses.
            for i in range(2, 30):
                await asyncio.sleep(0.05)
                if i == 2:  # session/new for turn 1
                    mock_process.feed_stdout({
                        "jsonrpc": "2.0",
                        "id": i,
                        "result": {
                            "sessionId": "s1"
                        }
                    })
                else:  # Success response for prompts and session/use
                    mock_process.feed_stdout({
                        "jsonrpc": "2.0",
                        "id": i,
                        "result": {
                            "status": "completed"
                        }
                    })

        simulator_task = asyncio.create_task(complex_simulator())
        bridge_task = asyncio.create_task(bridge.run())

        try:
            # Turn 1: Initial (p1 is root). No history.
            mock_api_base.get_thread = AsyncMock(
                return_value={
                    "posts": {
                        "p1": {
                            "id": "p1",
                            "user_id": "user_1",
                            "message": "@goose-bot hi"
                        }
                    }
                })
            await posts_queue.put([{
                "id": "p1",
                "user_id": "user_1",
                "channel_id": "chan_1",
                "message": "@goose-bot hi",
                "create_at": 1000
            }])
            await wait_for_prompts(prompts_received, 1)

            # Turn 2: Catch-up (using p1 as root)
            # p2 is missed, p3 is current.
            await posts_queue.put([{
                "id": "p2",
                "user_id": "user_2",
                "channel_id": "chan_1",
                "root_id": "p1",
                "message": "msg 2",
                "create_at": 1100
            }, {
                "id": "p3",
                "user_id": "user_1",
                "channel_id": "chan_1",
                "root_id": "p1",
                "message": "@goose-bot check",
                "create_at": 1200
            }])
            await wait_for_prompts(prompts_received, 2)

            # Turn 3: Clear (using p1 as root)
            await posts_queue.put([{
                "id": "p4",
                "user_id": "user_1",
                "channel_id": "chan_1",
                "root_id": "p1",
                "message": "@goose-bot next",
                "create_at": 1300
            }])
            await wait_for_prompts(prompts_received, 3)

        finally:
            bridge_task.cancel()
            simulator_task.cancel()

    assert "SYSTEM: There are 1 new messages" in prompts_received[1]
    assert "SYSTEM: You are now caught up" in prompts_received[2]


@pytest.mark.asyncio
async def test_bridge_integration_ignore_empty_messages(base_config,
                                                        mock_api_base):
    posts_queue = asyncio.Queue()

    async def side_effect_get_posts(channel_id, since):
        try:
            batch = posts_queue.get_nowait()
            return {
                "posts": {
                    p["id"]: p for p in batch
                },
                "order": [p["id"] for p in batch]
            }
        except asyncio.QueueEmpty:
            return {"posts": {}, "order": []}

    mock_api_base.get_channel_posts = AsyncMock(
        side_effect=side_effect_get_posts)

    mock_process = MockProcess()
    prompts_received = []

    def goose_factory(user):
        client = ACPClient(user, config=base_config)
        original_prompt = client.prompt

        async def tracking_prompt(sid, msg):
            prompts_received.append(msg)
            async for update in original_prompt(sid, msg):
                yield update

        client.prompt = tracking_prompt
        return client

    bridge = MattermostBridge(api=mock_api_base,
                              config=base_config,
                              goose_client_factory=goose_factory)
    bridge.last_since = 0

    with patch('mattermost_bridge.load_user_mapping', return_value={"user_1": "linux_alice"}), \
         patch('asyncio.create_subprocess_exec', return_value=mock_process):

        async def simple_simulator():
            await asyncio.sleep(0.01)
            mock_process.feed_stdout({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "capabilities": {}
                }
            })
            # Prompts responses
            for i in range(2, 20):
                await asyncio.sleep(0.05)
                if i == 2:
                    mock_process.feed_stdout({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {
                            "sessionId": "s1"
                        }
                    })
                else:
                    mock_process.feed_stdout({
                        "jsonrpc": "2.0",
                        "id": i,
                        "result": {
                            "status": "completed"
                        }
                    })

        simulator_task = asyncio.create_task(simple_simulator())
        bridge_task = asyncio.create_task(bridge.run())

        try:
            # 1. Start thread with p3 (mentions bot). Baseline history (p1, p2, p3) has 2 non-empty messages (p1, p3).
            mock_api_base.get_thread = AsyncMock(
                return_value={
                    "posts": {
                        "p1": {
                            "id": "p1",
                            "user_id": "user_1",
                            "message": "hello"
                        },
                        "p2": {
                            "id": "p2",
                            "user_id": "user_1",
                            "message": "  "
                        },
                        "p3": {
                            "id": "p3",
                            "user_id": "user_1",
                            "message": "@goose-bot hi"
                        }
                    }
                })
            await posts_queue.put([{
                "id": "p3",
                "user_id": "user_1",
                "channel_id": "chan_1",
                "root_id": "p1",
                "message": "@goose-bot hi",
                "create_at": 1000
            }])
            await wait_for_prompts(prompts_received, 1)

            # 2. Add an empty message p4 and a real message p5 (mentions bot)
            await posts_queue.put([{
                "id": "p4",
                "user_id": "user_1",
                "channel_id": "chan_1",
                "root_id": "p1",
                "message": "   ",
                "create_at": 1100
            }, {
                "id": "p5",
                "user_id": "user_1",
                "channel_id": "chan_1",
                "root_id": "p1",
                "message": "@goose-bot next",
                "create_at": 1200
            }])
            await wait_for_prompts(prompts_received, 2)

        finally:
            bridge_task.cancel()
            simulator_task.cancel()

    assert "joined an existing thread with 1 earlier messages" in prompts_received[
        0]
    # The second prompt should have the clearing signal because the first one had a hint
    assert "SYSTEM: You are now caught up with the thread." in prompts_received[
        1]
