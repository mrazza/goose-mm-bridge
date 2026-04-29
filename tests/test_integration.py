import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from mattermost_bridge import MattermostBridge
from config import Config

@pytest.mark.asyncio
async def test_bridge_integration_flow():
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
    # Mock initialization calls
    mock_api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "goose-bot"})
    mock_api.get_direct_channels = AsyncMock(return_value=[])
    mock_api.get_my_teams = AsyncMock(return_value=[{"id": "team_1"}])
    mock_api.get_my_channels = AsyncMock(return_value=[{"id": "chan_1", "type": "O"}])
    
    # Mock user info
    mock_api.get_user = AsyncMock(return_value={"id": "user_1", "username": "alice"})
    
    # Mock post fetching - first call returns a post, subsequent calls return nothing
    test_post = {
        "id": "post_123",
        "user_id": "user_1",
        "channel_id": "chan_1",
        "message": "@goose-bot What is the meaning of life?",
        "create_at": 1000
    }
    
    # Track calls to simulate a single loop iteration
    call_tracker = {"get_posts_count": 0}
    
    async def side_effect_get_posts(channel_id, since):
        # The bridge polls all channels in the cache. 
        # In this test, there's only 'chan_1'.
        if call_tracker["get_posts_count"] == 0:
            call_tracker["get_posts_count"] += 1
            # Note: The bridge sorts posts by create_at and then calls _process_post
            return {"posts": {"post_123": test_post}, "order": ["post_123"]}
        return {"posts": {}, "order": []}

    mock_api.get_channel_posts = AsyncMock(side_effect=side_effect_get_posts)
    
    # Mock post creation and updates
    created_post = {"id": "bot_post_456"}
    mock_api.create_post = AsyncMock(return_value=created_post)
    mock_api.update_post = AsyncMock(return_value=created_post)

    # 3. Mock Goose ACP Client
    mock_goose = MagicMock()
    mock_goose.create_session = AsyncMock(return_value="session_abc")
    mock_goose.process = MagicMock()
    mock_goose.process.returncode = None
    
    async def mock_prompt(sid, message):
        assert sid == "session_abc"
        assert "meaning of life" in message
        yield {"type": "thinking", "text": "Analyzing the universe..."}
        yield {"type": "content", "text": "The answer is 42."}
        yield {"type": "final", "text": "The answer is 42."}
    
    mock_goose.prompt = mock_prompt
    
    # 4. Setup Bridge with Factory
    def goose_factory(user):
        return mock_goose

    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=goose_factory)
    # Ensure our test post (create_at=1000) is processed
    bridge.last_since = 0

    # 5. Execute Bridge Run with a controlled exit
    # We need the loop to run at least once and then wait for tasks.
    # The bridge.run() calls _update_channel_cache, then polls, THEN sleeps.
    
    loop_count = 0
    # Capture the real sleep so we don't recurse
    real_sleep = asyncio.sleep
    
    async def side_effect_sleep(interval):
        nonlocal loop_count
        loop_count += 1
        # Wait for any background tasks to start and finish
        # _process_post spawns a task, so we must yield control.
        for _ in range(10):
            if not bridge.background_tasks:
                await real_sleep(0)
                break
            await asyncio.gather(*bridge.background_tasks)
            
        raise KeyboardInterrupt()

    with patch('mattermost_bridge.load_user_mapping', return_value={"user_1": "linux_alice"}), \
         patch('asyncio.sleep', side_effect=side_effect_sleep):
        await bridge.run()

    # 6. Verifications
    
    # Verify initialization
    mock_api.get_me.assert_called_once()
    assert bridge.bot_id == "bot_id"
    assert bridge.bot_mention == "@goose-bot"

    # Verify polling happened
    assert mock_api.get_channel_posts.called
    
    # Verify session creation
    mock_goose.create_session.assert_called_once()
    assert "user_1:post_123" in bridge.sessions
    assert bridge.sessions["user_1:post_123"]["linux_user"] == "linux_alice"
    
    # Verify message processing
    # Note: _handle_message is spawned as a task, so we might need a small wait if it's async
    # However, since we used KeyboardInterrupt in the main loop AFTER calling _process_post,
    # the task was created. We need to ensure it finished.
    # In this test, because we didn't await the tasks in the loop (they are background tasks),
    # we should wait for them.
    
    if bridge.background_tasks:
        await asyncio.gather(*bridge.background_tasks)

    # Verify Mattermost feedback
    # Expectation: 
    # 1. Thinking post created
    # 2. Post updated with content
    # 3. Post updated with final result
    
    assert mock_api.create_post.called
    # First creation is thinking msg
    first_post_call = mock_api.create_post.call_args_list[0]
    assert ":thinking_face:" in first_post_call[0][1]
    
    # Final update
    assert mock_api.update_post.called
    last_update_call = mock_api.update_post.call_args_list[-1]
    assert "The answer is 42." in last_update_call[0][1]
    # Check thinking trace in attachments
    assert "attachments" in last_update_call[1]["props"]
    assert "Analyzing the universe..." in last_update_call[1]["props"]["attachments"][0]["text"]
