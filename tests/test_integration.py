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
