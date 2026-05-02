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

async def wait_for_bridge(bridge):
    """Wait for all background tasks in the bridge to complete."""
    while bridge.background_tasks:
        await asyncio.gather(*list(bridge.background_tasks), return_exceptions=True)
    # Give a tiny bit more time for any final processing
    await asyncio.sleep(0)

@pytest.mark.asyncio
async def test_bridge_initialization(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    success = await bridge.initialize()
    
    assert success is True
    assert bridge.bot_id == "bot_id"
    assert bridge.bot_mention == "@bot"

@pytest.mark.asyncio
async def test_catchup_flow_new_session(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []  # Approve all
    
    # Mock history: thread has 5 messages already
    mock_api.get_thread = AsyncMock(return_value={
        "posts": {f"p{i}": {"id": f"p{i}", "user_id": "u1"} for i in range(1, 6)}
    })
    
    # Message p5 is the one we are processing
    post = {"id": "p5", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot hello"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        await bridge._process_post(post, {"c1": {"type": "O"}})
        await wait_for_bridge(bridge)
        
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: You have joined an existing thread with 4 earlier messages." in prompt_text

@pytest.mark.asyncio
async def test_catchup_flow_existing_session(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []  # Approve all
    
    # 1. First message to establish session
    mock_api.get_thread = AsyncMock(return_value={"posts": {"p1": {"id": "p1"}}})
    p1 = {"id": "p1", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot m1"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        await bridge._process_post(p1, {"c1": {"type": "O"}})
        await wait_for_bridge(bridge)
        
        # 2. Simulate 3 messages arriving that the bot doesn't respond to (not mentioned)
        for i in range(2, 5):
            await bridge._process_post({"id": f"p{i}", "user_id": "other", "channel_id": "c1", "root_id": "root1", "message": "unrelated"}, {"c1": {"type": "O"}})
        
        # 3. Next message for the bot
        p5 = {"id": "p5", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot m2"}
        await bridge._process_post(p5, {"c1": {"type": "O"}})
        await wait_for_bridge(bridge)
        
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: There are 3 new messages in this thread since your last response." in prompt_text

@pytest.mark.asyncio
async def test_catchup_flow_clearing_signal(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []  # Approve all
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # 1. Trigger a catch-up hint
        mock_api.get_thread = AsyncMock(return_value={"posts": {"p1": {"user_id": "u1"}, "p2": {"user_id": "u1"}}})
        await bridge._process_post({"id": "p2", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot hi"}, {"c1": {"type": "O"}})
        await wait_for_bridge(bridge)
        
        # Verify it had a hint
        assert bridge.sessions["u1:root1"]["had_catchup_hint"] is True
        
        # 2. Next message is perfectly in sync (p3)
        # We need to make sure thread_counters is updated by process_post
        await bridge._process_post({"id": "p3", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot next"}, {"c1": {"type": "O"}})
        await wait_for_bridge(bridge)
        
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: You are now caught up with the thread." in prompt_text
        assert bridge.sessions["u1:root1"]["had_catchup_hint"] is False

@pytest.mark.asyncio
async def test_no_catchup_needed_when_synced(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []  # Approve all
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # First message - history is just this message
        mock_api.get_thread = AsyncMock(return_value={"posts": {"p1": {"user_id": "u1"}}})
        await bridge._process_post({"id": "p1", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot hi"}, {"c1": {"type": "O"}})
        await wait_for_bridge(bridge)
        
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "joined an existing thread" not in prompt_text
        assert "since your last response" not in prompt_text
        assert "You are now caught up" not in prompt_text

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
    
    # Should have pruned max(1, 2//5) = 1
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
async def test_ignore_whitespace_only(config, mock_api, mock_goose_client):
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []  # Approve all
    
    post = {"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "   ", "create_at": 1000}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        await bridge._process_post(post, {"c1": {"type": "D"}})
        await wait_for_bridge(bridge)
        assert not mock_goose_client.prompt.called

@pytest.mark.asyncio
async def test_handle_message_retry(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []
    
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
        await bridge._process_post(post, {"channel_1": {"type": "D"}})
        await wait_for_bridge(bridge)
        
        # Should have called prompt twice
        assert mock_goose_client.prompt.call_count == 2
        
        # Second call should have the retry context
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "NOTE: The previous session for this thread terminated unexpectedly" in prompt_text
        
        # Verify we informed the user about the reset
        reset_post_call = [c for c in mock_api.create_post.call_args_list if "Notice: Connection to Goose was reset" in str(c)]
        assert len(reset_post_call) > 0

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
        await wait_for_bridge(bridge)
        # Should post a warning to MM
        assert mock_api.create_post.called
        assert "isolation profile" in mock_api.create_post.call_args[0][1]

@pytest.mark.asyncio
async def test_concurrency_locking(config, mock_api, mock_goose_client):
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.bot_mention = "@bot"
    bridge.config.approved_users = []
    
    # Mock a slow prompt to test locking
    async def slow_prompt(*args):
        await asyncio.sleep(0.2)
        yield {"type": "final", "text": "done"}
    mock_goose_client.prompt.side_effect = slow_prompt
    
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
        await wait_for_bridge(bridge)
        assert mock_api.create_post.called
        assert "isolation profile" in mock_api.create_post.call_args[0][1]

    # Case 2: Not approved user but has mapping (should still be ignored)
    mock_api.create_post.reset_mock()
    config.approved_users = ["other_user"]
    with patch('mattermost_bridge.load_user_mapping', return_value={"user1": "linux1"}):
        await bridge._process_post(post, {"c1": {"type": "D"}})
        await wait_for_bridge(bridge)
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
    config.goose_thinking_trace = True
    
    await bridge._stream_response_to_mattermost(client, "sid", "msg", "cid", "rid")
    
    # Check that update_post was called with truncated attachments
    update_calls = [c for c in mock_api.update_post.call_args_list if "attachments" in c[1].get("props", {})]
    assert len(update_calls) > 0
    
    last_trace = update_calls[-1][1]["props"]["attachments"][0]["text"]
    assert "... (truncated) ..." in last_trace
    assert len(last_trace) < 10000