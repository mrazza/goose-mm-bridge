import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from mattermost_bridge import MattermostBridge
from config import Config

@pytest.fixture
def config():
    return Config(mattermost_url="example.com", mattermost_token="token")

@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "bot"})
    api.create_post = AsyncMock(return_value={"id": "post_id"})
    api.update_post = AsyncMock()
    api.get_user = AsyncMock(return_value={"username": "user1"})
    api.get_thread = AsyncMock(return_value={"posts": {}})
    return api

@pytest.fixture
def mock_goose_client():
    client = MagicMock()
    client.create_session = AsyncMock(return_value="session_1")
    client.send_request = AsyncMock(return_value={})
    
    # We'll use a mock that tracks calls
    client.prompt = MagicMock()
    
    async def mock_prompt_gen(sid, msg):
        yield {"type": "thinking", "text": "let me see"}
        yield {"type": "content", "text": "the answer is 42"}
        yield {"type": "final", "text": "the answer is 42"}
    
    client.prompt.side_effect = mock_prompt_gen
    return client

@pytest.mark.asyncio
async def test_bridge_initialization(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    success = await bridge.initialize()
    
    assert success is True
    assert bridge.bot_id == "bot_id"
    assert bridge.bot_mention == "@bot"

@pytest.mark.asyncio
async def test_handle_message(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    
    post = {
        "id": "post_1",
        "user_id": "user_id_1",
        "channel_id": "channel_1",
        "message": "@bot hello",
        "create_at": 1000
    }
    
    # We mock load_user_mapping to avoid file IO
    with patch('mattermost_bridge.load_user_mapping', return_value={"user_id_1": "linux_user"}):
        await bridge._handle_message(post, "linux_user")
        
        # Verify goose was prompted with combined context
        assert mock_goose_client.prompt.called
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: Mattermost Channel ID: channel_1" in prompt_text
        assert "Root Post ID (Thread ID): post_1" in prompt_text
        assert "hello" in prompt_text
        
        # Verify Mattermost posts were created/updated
        assert mock_api.create_post.called
        # First post is "Thinking..."
        assert mock_api.create_post.call_args_list[0][0][1] == ":thinking_face: **Thinking...**"

@pytest.mark.asyncio
async def test_handle_message_retry(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    
    post = {
        "id": "post_1",
        "user_id": "user_id_1",
        "channel_id": "channel_1",
        "message": "@bot hello",
        "create_at": 1000
    }

    # Simulate a failure on first prompt, then success on second
    call_count = 0
    async def mock_prompt_with_fail(sid, msg):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Goose connection lost")
        yield {"type": "final", "text": "retry success"}
        
    mock_goose_client.prompt.side_effect = mock_prompt_with_fail
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"user_id_1": "linux_user"}):
        await bridge._handle_message(post, "linux_user")
        
        # Should have called prompt twice
        assert mock_goose_client.prompt.call_count == 2
        
        # Second call should have the retry context
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "NOTE: The previous session for this thread terminated unexpectedly" in prompt_text
        assert "hello" in prompt_text
        
        # Verify we informed the user about the reset
        reset_post_call = [c for c in mock_api.create_post.call_args_list if "Notice: Connection to Goose was reset" in str(c)]
        assert len(reset_post_call) > 0

@pytest.mark.asyncio
async def test_session_pruning(config, mock_api, mock_goose_client):
    config.max_sessions = 2
    mock_goose_client.send_request = AsyncMock(return_value={})
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    
    # Fill sessions
    bridge.sessions = {
        "user:thread1": {"id": "s1", "linux_user": "u1"},
        "user:thread2": {"id": "s2", "linux_user": "u1"},
        "user:thread3": {"id": "s3", "linux_user": "u1"}
    }
    bridge.goose_clients["u1"] = mock_goose_client
    
    await bridge._prune_sessions()
    
    # Should have pruned (max_sessions // 5) = 0, but max(1, ...) = 1
    assert len(bridge.sessions) == 2
    assert "user:thread1" not in bridge.sessions
    assert mock_goose_client.send_request.called

@pytest.mark.asyncio
async def test_handle_stop_command(config, mock_api, mock_goose_client):
    mock_goose_client.cancel_prompt = AsyncMock(return_value=True)
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.sessions["user_id_1:root_1"] = {"id": "session_1", "linux_user": "linux_user"}
    bridge.goose_clients["linux_user"] = mock_goose_client
    
    post = {
        "id": "post_1",
        "user_id": "user_id_1",
        "channel_id": "channel_1",
        "root_id": "root_1",
        "message": "!stop"
    }
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"user_id_1": "linux_user"}):
        await bridge._handle_stop_command(post)
        assert mock_goose_client.cancel_prompt.called
        assert mock_api.create_post.called
        assert "cancelled" in mock_api.create_post.call_args[0][1]

@pytest.mark.asyncio
async def test_ignore_own_messages(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    bridge.bot_id = "bot_id"
    
    post = {"user_id": "bot_id", "message": "hello"}
    # Should return early
    await bridge._process_post(post, {})
    assert not mock_api.get_user.called

@pytest.mark.asyncio
async def test_require_user_mapping(config, mock_api):
    config.require_user_mapping = True
    config.approved_users = []
    bridge = MattermostBridge(api=mock_api, config=config)
    bridge.bot_mention = "@bot"
    
    post = {
        "id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot hello", "create_at": 1000
    }
    
    with patch('mattermost_bridge.load_user_mapping', return_value={}):
        await bridge._process_post(post, {"c1": {"type": "D"}})
        # Should post a warning to MM
        assert mock_api.create_post.called
        assert "isolation profile" in mock_api.create_post.call_args[0][1]

@pytest.mark.asyncio
async def test_concurrency_locking(config, mock_api, mock_goose_client):
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.bot_mention = "@bot"
    
    # Mock a slow prompt to test locking
    async def slow_prompt(*args):
        await asyncio.sleep(0.2)
        yield {"type": "final", "text": "done"}
    mock_goose_client.prompt = slow_prompt
    
    post = {"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot hello", "create_at": 1000}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # Start two tasks for the same thread
        t1 = asyncio.create_task(bridge._handle_message(post, "linux1"))
        t2 = asyncio.create_task(bridge._handle_message(post, "linux1"))
        
        await asyncio.gather(t1, t2)
        
        # Verify they shared the same lock
        assert "u1:p1" in bridge.session_locks

@pytest.mark.asyncio
async def test_polling_loop_recovery(config, mock_api):
    # We'll mock _update_channel_cache to fail once then succeed
    bridge = MattermostBridge(api=mock_api, config=config)
    
    call_count = 0
    original_update = bridge._update_channel_cache
    
    async def mock_update():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Transient Error")
        return await original_update()

    bridge._update_channel_cache = mock_update
    
    # Use a small poll interval and mock sleep to exit quickly
    config.poll_interval = 0.01
    
    with patch('asyncio.sleep', side_effect=[None, KeyboardInterrupt()]):
        await bridge.run()
    
    assert call_count >= 2
    print(f"Loop recovered after {call_count} calls")

@pytest.mark.asyncio
async def test_user_mapping_edge_cases(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    bridge.bot_mention = "@bot"
    
    # Case 1: Approved user but no mapping
    config.approved_users = ["user1"]
    config.require_user_mapping = True
    post = {"id": "p1", "user_id": "user1", "channel_id": "c1", "message": "@bot hello"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={}):
        mock_api.get_user = AsyncMock(return_value={"username": "user1"})
        await bridge._process_post(post, {"c1": {"type": "D"}})
        assert mock_api.create_post.called
        assert "isolation profile" in mock_api.create_post.call_args[0][1]

    # Case 2: Not approved user but has mapping (should still be ignored)
    mock_api.create_post.reset_mock()
    config.approved_users = ["other_user"]
    with patch('mattermost_bridge.load_user_mapping', return_value={"user1": "linux1"}):
        await bridge._process_post(post, {"c1": {"type": "D"}})
        assert not mock_api.create_post.called


@pytest.mark.asyncio
async def test_thinking_trace_truncation(config, mock_api):
    client = MagicMock()
    async def long_thinking_gen(sid, msg):
        yield {"type": "thinking", "text": "A" * 12000}
        yield {"type": "final", "text": "done"}
    
    client.prompt.side_effect = long_thinking_gen
    bridge = MattermostBridge(api=mock_api, config=config)
    
    # Ensure full trace is used (not simplified)
    config.goose_thinking_trace_simplified = False
    
    await bridge._stream_response_to_mattermost(client, "sid", "msg", "cid", "rid")
    
    # Check that update_post was called with truncated attachments
    update_calls = [c for c in mock_api.update_post.call_args_list if "attachments" in c[1].get("props", {})]
    assert len(update_calls) > 0
    
    last_trace = update_calls[-1][1]["props"]["attachments"][0]["text"]
    assert "... (truncated) ..." in last_trace
    assert len(last_trace) < 10000

@pytest.mark.asyncio
async def test_catchup_hint_merged_prompt(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    
    # Setup session with processed_count = 1
    bridge.sessions["u1:root1"] = {
        "id": "session_1",
        "linux_user": "linux1",
        "processed_count": 1
    }
    bridge.goose_clients["linux1"] = mock_goose_client
    
    # Mock thread with 3 posts (for lazy priming)
    mock_api.get_thread = AsyncMock(return_value={
        "posts": {
            "p1": {"id": "p1", "create_at": 1000},
            "p2": {"id": "p2", "create_at": 1100},
            "p3": {"id": "p3", "create_at": 1200}
        }
    })
    
    post = {
        "id": "p3",
        "user_id": "u1",
        "channel_id": "c1",
        "root_id": "root1",
        "message": "@bot hello",
        "create_at": 1200
    }
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # First call should prime the counter via api.get_thread
        await bridge._handle_message(post, "linux1")
        assert bridge.thread_counters["root1"] == 3
        
        # Now simulate a new message coming in via the poll loop
        await bridge._process_post({
            "id": "p4", "user_id": "other", "channel_id": "c1", "root_id": "root1", "message": "unrelated", "create_at": 1300
        }, {"c1": {"type": "O"}})
        
        assert bridge.thread_counters["root1"] == 4
        
        # Now handle another bot command
        post["id"] = "p5"
        post["create_at"] = 1400
        # Counter increments because bot sees its own command in process_post usually, 
        # but here we call _handle_message directly. Let's simulate the loop incrementing it first.
        bridge.thread_counters["root1"] += 1 # p5
        
        await bridge._handle_message(post, "linux1")
        
        # thread_size=5, processed_count=4 (from first handle), current_msg=1.
        # new_messages = 5 - 4 - 1 = 0. Wait, let's make it have more.
        
        bridge.thread_counters["root1"] = 10
        await bridge._handle_message(post, "linux1")
        
        # Verify prompt text contains the catchup hint
        # thread_size=10, last_processed=6 (from previous step), current_msg=1.
        # new_messages = 10 - 6 - 1 = 3.
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: There are 3 new messages" in prompt_text
        
        # Verify processed_count was updated (thread_size + 1)
        assert bridge.sessions["u1:root1"]["processed_count"] == 11
@pytest.mark.asyncio
async def test_thread_counter_initialization(config, mock_api, mock_goose_client):
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.bot_mention = "@bot"
    
    # Message with no root_id (start of thread)
    post = {"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot hello", "create_at": 1000}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # process_post should initialize counter to 1
        await bridge._process_post(post, {"c1": {"type": "D"}})
        assert bridge.thread_counters["p1"] == 1
        
        # handle_message should NOT call get_thread because counter exists
        mock_api.get_thread.reset_mock()
        await bridge._handle_message(post, "linux1")
        assert not mock_api.get_thread.called

@pytest.mark.asyncio
async def test_lazy_prime_failure(config, mock_api, mock_goose_client):
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.bot_mention = "@bot"
    mock_api.get_thread = AsyncMock(return_value=None) # Simulate failure
    
    post = {"id": "p1", "user_id": "u1", "channel_id": "c1", "root_id": "r1", "message": "@bot hello", "create_at": 1000}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # Should not crash, should default to 1
        await bridge._handle_message(post, "linux1")
        assert bridge.thread_counters["r1"] == 1
