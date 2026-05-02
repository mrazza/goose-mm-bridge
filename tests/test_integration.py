import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from mattermost_bridge import MattermostBridge
from goose_acp_client import GooseACPClient
from config import Config
from goose_simulator import MockProcess, simulate_goose_behavior

@pytest.mark.asyncio
async def test_bridge_integration_flow_with_goose_process():
    # 1. Setup Mock Config
    config = Config(
        mattermost_url="http://mattermost.example.com",
        mattermost_token="fake-token",
        approved_users=["alice"],
        poll_interval=0.1,
        debug=True,
        goose_thinking_trace=True
    )

    # 2. Mock Mattermost API
    mock_api = MagicMock()
    mock_api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "goose-bot"})
    mock_api.get_direct_channels = AsyncMock(return_value=[])
    mock_api.get_my_teams = AsyncMock(return_value=[{"id": "team_1"}])
    mock_api.get_my_channels = AsyncMock(return_value=[{"id": "chan_1", "type": "O"}])
    mock_api.get_user = AsyncMock(return_value={"id": "user_1", "username": "alice"})
    mock_api.get_thread = AsyncMock(return_value={"posts": {}})
    
    test_post = {
        "id": "post_123",
        "user_id": "user_1",
        "channel_id": "chan_1",
        "message": "@goose-bot What is the meaning of life?",
        "create_at": 1000
    }
    
    call_tracker = {"get_posts_count": 0}
    async def side_effect_get_posts(channel_id, since):
        if call_tracker["get_posts_count"] == 0:
            call_tracker["get_posts_count"] += 1
            return {"posts": {"post_123": test_post}, "order": ["post_123"]}
        return {"posts": {}, "order": []}

    mock_api.get_channel_posts = AsyncMock(side_effect=side_effect_get_posts)
    mock_api.create_post = AsyncMock(return_value={"id": "bot_post_456"})
    
    # Event to signal when the final response is posted to Mattermost
    final_response_posted = asyncio.Event()
    
    async def side_effect_update_post(post_id, message, props=None):
        if "The answer is 42." in message:
            final_response_posted.set()
        return {"id": post_id}
        
    mock_api.update_post = AsyncMock(side_effect=side_effect_update_post)

    # 3. Setup Mock Goose Process
    mock_process = MockProcess()
    
    async def side_effect_subprocess(*args, **kwargs):
        return mock_process

    # 4. Setup Bridge with REAL GooseACPClient
    def goose_factory(user):
        return GooseACPClient(user, config=config)

    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=goose_factory)
    bridge.last_since = 0

    # 5. Execute Bridge Run in background
    with patch('mattermost_bridge.load_user_mapping', return_value={"user_1": "linux_alice"}), \
         patch('asyncio.create_subprocess_exec', side_effect=side_effect_subprocess):
        
        # Start the Goose simulator from the util file
        simulator_task = asyncio.create_task(simulate_goose_behavior(mock_process))
        
        # Start the bridge
        bridge_task = asyncio.create_task(bridge.run())
        
        try:
            # Wait for the flow to complete
            await asyncio.wait_for(final_response_posted.wait(), timeout=5.0)
            
            # Wait for background tasks in bridge to finish (streaming might take a moment)
            if bridge.background_tasks:
                await asyncio.gather(*bridge.background_tasks)
                
        finally:
            bridge_task.cancel()
            simulator_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass

    # 6. Verifications
    mock_api.get_me.assert_called_once()
    assert bridge.bot_id == "bot_id"
    
    # Check that GooseACPClient was actually used and created a session
    assert "user_1:post_123" in bridge.sessions
    assert bridge.sessions["user_1:post_123"]["id"] == "session_abc"
    
    # Verify Mattermost feedback
    assert mock_api.create_post.called
    assert mock_api.update_post.called
    
    # Verify final answer
    found_content = False
    for call in mock_api.update_post.call_args_list:
        if "The answer is 42." in call[0][1]:
            found_content = True
            break
    assert found_content, "Final answer not found in Mattermost updates"


@pytest.mark.asyncio
async def test_bridge_multi_turn_catchup_flow():
    """Integration test for the catch-up flow across multiple turns of polling."""
    config = Config(
        mattermost_url="http://mattermost.example.com",
        mattermost_token="fake-token",
        approved_users=[],
        poll_interval=0.05,  # Faster polling for test
        debug=True,
    )

    # 1. Mock Mattermost API
    mock_api = MagicMock()
    mock_api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "goose-bot"})
    mock_api.get_direct_channels = AsyncMock(return_value=[])
    mock_api.get_my_teams = AsyncMock(return_value=[{"id": "team_1"}])
    mock_api.get_my_channels = AsyncMock(return_value=[{"id": "chan_1", "type": "O"}])
    mock_api.get_user = AsyncMock(return_value={"id": "user_1", "username": "alice"})
    # Baseline history is empty
    mock_api.get_thread = AsyncMock(return_value={"posts": {}})
    mock_api.create_post = AsyncMock(return_value={"id": "bot_post"})
    mock_api.update_post = AsyncMock()

    posts_queue = asyncio.Queue()
    async def side_effect_get_posts(channel_id, since):
        try:
            batch = posts_queue.get_nowait()
            return {"posts": {p["id"]: p for p in batch}, "order": [p["id"] for p in batch]}
        except asyncio.QueueEmpty:
            return {"posts": {}, "order": []}
    mock_api.get_channel_posts = AsyncMock(side_effect=side_effect_get_posts)

    # 2. Setup Mock Goose Process
    mock_process = MockProcess()
    prompts_received = []
    
    def goose_factory(user):
        client = GooseACPClient(user, config=config)
        original_prompt = client.prompt
        async def tracking_prompt(sid, msg):
            prompts_received.append(msg)
            async for update in original_prompt(sid, msg):
                yield update
        client.prompt = tracking_prompt
        return client

    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=goose_factory)
    bridge.last_since = 0

    with patch('mattermost_bridge.load_user_mapping', return_value={"user_1": "linux_alice"}), \
         patch('asyncio.create_subprocess_exec', return_value=mock_process):
        
        async def complex_simulator():
            # 1. Handshake
            await asyncio.sleep(0.01)
            mock_process.feed_stdout({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}})
            
            # Turn 1: hi
            await asyncio.sleep(0.01)
            mock_process.feed_stdout({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}})
            await asyncio.sleep(0.01)
            mock_process.feed_stdout({"jsonrpc": "2.0", "id": 3, "result": {"status": "completed"}})
            
            # Turn 2: check (catchup)
            await asyncio.sleep(0.2)
            mock_process.feed_stdout({"jsonrpc": "2.0", "id": 4, "result": {"status": "completed"}})
            
            # Turn 3: next (sync clearing)
            await asyncio.sleep(0.2)
            mock_process.feed_stdout({"jsonrpc": "2.0", "id": 5, "result": {"status": "completed"}})

        simulator_task = asyncio.create_task(complex_simulator())
        bridge_task = asyncio.create_task(bridge.run())
        
        try:
            # Turn 1: First mention
            await posts_queue.put([{"id": "p1", "user_id": "user_1", "channel_id": "chan_1", "message": "@goose-bot hi", "create_at": 1000}])
            
            # Wait for first prompt
            while len(prompts_received) < 1:
                await asyncio.sleep(0.05)

            # Turn 2: Missed messages + next mention
            await posts_queue.put([
                {"id": "p2", "user_id": "user_2", "channel_id": "chan_1", "root_id": "p1", "message": "msg 2", "create_at": 1100},
                {"id": "p3", "user_id": "user_2", "channel_id": "chan_1", "root_id": "p1", "message": "msg 3", "create_at": 1200},
                {"id": "p4", "user_id": "user_2", "channel_id": "chan_1", "root_id": "p1", "message": "msg 4", "create_at": 1300},
                {"id": "p5", "user_id": "user_1", "channel_id": "chan_1", "root_id": "p1", "message": "@goose-bot check", "create_at": 1400}
            ])
            
            # Wait for second prompt
            while len(prompts_received) < 2:
                await asyncio.sleep(0.05)

            # Turn 3: Synced mention
            await posts_queue.put([{"id": "p6", "user_id": "user_1", "channel_id": "chan_1", "root_id": "p1", "message": "@goose-bot next", "create_at": 1500}])
            
            # Wait for third prompt
            while len(prompts_received) < 3:
                await asyncio.sleep(0.05)

            # Final cleanup of background tasks
            while bridge.background_tasks:
                await asyncio.gather(*list(bridge.background_tasks), return_exceptions=True)

        finally:
            bridge_task.cancel()
            simulator_task.cancel()

    # Verify Prompts
    assert len(prompts_received) == 3
    assert "new messages" not in prompts_received[0]
    assert "SYSTEM: There are 3 new messages" in prompts_received[1]
    assert "SYSTEM: You are now caught up" in prompts_received[2]
