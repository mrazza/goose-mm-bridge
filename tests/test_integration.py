import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from mattermost_bridge import MattermostBridge
from config import Config
from mock_goose import MockSubprocess, process_manager

@pytest.fixture
def integration_config():
    return Config(
        mattermost_url="mattermost.example.com",
        mattermost_token="test_token",
        mattermost_port=443,
        mattermost_scheme="https",
        bridge_api_host="127.0.0.1",
        bridge_api_port=8080,
        poll_interval=0.01,
        max_sessions=10,
        debug=True,
        goose_thinking_trace=True,
        approved_users=["user1"]
    )

@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "goosebot"})
    api.get_user = AsyncMock(return_value={"id": "user_id_1", "username": "user1"})
    api.get_direct_channels = AsyncMock(return_value=[{"id": "channel_1", "type": "D"}])
    api.get_my_teams = AsyncMock(return_value=[{"id": "team_1", "name": "team1"}])
    api.get_my_channels = AsyncMock(return_value=[{"id": "channel_1", "type": "D"}])
    api.create_post = AsyncMock(return_value={"id": "post_thinking_id"})
    api.update_post = AsyncMock(return_value={"id": "post_thinking_id"})
    return api

@pytest.mark.asyncio
async def test_integration_flow_with_process_mock(integration_config, mock_api):
    """
    Validates the end-to-end flow by mocking the underlying Goose process.
    """
    mock_proc = MockSubprocess()
    
    # Use the real GooseACPClient but mock the subprocess creation
    with patch('asyncio.create_subprocess_exec', return_value=mock_proc) as mock_exec,          patch('mattermost_bridge.load_user_mapping', return_value={"user_id_1": "linux_user_1"}),          patch('mattermost_bridge.get_session_key', return_value="user_id_1:post_1"):
        
        # Start the process simulator
        sim_task = asyncio.create_task(process_manager(mock_proc))
        
        # 1. Setup Bridge
        bridge = MattermostBridge(api=mock_api, config=integration_config)
        bridge.last_since = 0
        bridge._start_http_server = AsyncMock()

        # 2. Mock Mattermost API polling behavior
        post_data = {
            "posts": {
                "post_1": {
                    "id": "post_1",
                    "user_id": "user_id_1",
                    "channel_id": "channel_1",
                    "message": "@goosebot hello",
                    "create_at": 1000
                }
            },
            "order": ["post_1"]
        }
        
        # We need to make sure bridge.run doesn't exit before processing the message
        # We'll use a signal to stop after we see update_post called
        stop_event = asyncio.Event()
        
        original_update = mock_api.update_post
        async def mock_update_side_effect(*args, **kwargs):
            res = await original_update(*args, **kwargs)
            if len(args) > 1 and "The answer is 42." in args[1]:
                stop_event.set()
            return res
        mock_api.update_post.side_effect = mock_update_side_effect

        mock_api.get_channel_posts = AsyncMock(side_effect=[
            post_data,
            {},
            {}, {}, {}, {}, {} # Keep returning empty to stay in loop
        ])
        
        # 3. Run the bridge in a task
        bridge_task = asyncio.create_task(bridge.run())
        
        # 4. Wait for message handling to complete or timeout
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        
        # 5. Assertions
        
        # Verify goose process was started correctly
        mock_exec.assert_called()
        
        # Verify final response was posted to Mattermost
        found = False
        for call in mock_api.update_post.call_args_list:
            if len(call[0]) > 1 and "The answer is 42." in call[0][1]:
                found = True
                break
        assert found, "Final response not found in Mattermost update_post calls"

        # Cleanup
        bridge_task.cancel()
        mock_proc.terminate()
        sim_task.cancel()
        await asyncio.gather(bridge_task, sim_task, return_exceptions=True)

