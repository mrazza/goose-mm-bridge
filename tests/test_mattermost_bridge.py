import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from mattermost_bridge import MattermostBridge
from config import Config

@pytest.fixture
def config():
    c = Config(mattermost_url="example.com", mattermost_token="token")
    c.approved_users = [] # Allow everyone
    return c

@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "bot"})
    api.create_post = AsyncMock(return_value={"id": "post_id"})
    api.update_post = AsyncMock()
    api.get_user = AsyncMock(return_value={"username": "user1", "id": "u1"})
    api.get_thread = AsyncMock(return_value={"posts": {}})
    return api

@pytest.fixture
def mock_goose_client():
    client = MagicMock()
    client.create_session = AsyncMock(return_value="session_1")
    client.send_request = AsyncMock(return_value={})
    
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
async def test_handle_message_core(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    
    post = {"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot hello", "create_at": 1000}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # We simulate the polling loop having already seen this message
        bridge.thread_counters["p1"] = 1
        await bridge._handle_message(post, "linux1")
        
        assert mock_goose_client.prompt.called
        assert bridge.sessions["u1:p1"]["processed_count"] == 1

@pytest.mark.asyncio
async def test_catchup_hint_new_session(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    
    # Mock thread with 5 messages already there (lazy priming case)
    mock_api.get_thread = AsyncMock(return_value={
        "posts": {f"p{i}": {"id": f"p{i}", "create_at": 1000+i} for i in range(1, 6)}
    })
    
    post = {"id": "p5", "user_id": "u1", "channel_id": "c1", "root_id": "root1", "message": "@bot hello", "create_at": 1005}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        await bridge._handle_message(post, "linux1")
        
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: You have joined an existing thread with 4 earlier messages." in prompt_text

@pytest.mark.asyncio
async def test_catchup_existing_session_new_messages(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    user_mapping = {"u1": "linux1"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value=user_mapping):
        # 1. First message to establish session
        bridge.thread_counters["p1"] = 1
        await bridge._handle_message({"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot start", "create_at": 1000}, "linux1")
        
        # 2. Simulate 3 more messages arriving in the thread
        bridge.thread_counters["p1"] = 4 
        
        # 3. Next bot mention
        post2 = {"id": "p5", "user_id": "u1", "channel_id": "c1", "root_id": "p1", "message": "@bot catchup", "create_at": 1010}
        bridge.thread_counters["p1"] += 1 # p5 itself
        
        await bridge._handle_message(post2, "linux1")
        
        # thread_size=5, processed_count=1 (from p1), current_msg=1 (p5). new_messages = 5 - 1 - 1 = 3.
        args, kwargs = mock_goose_client.prompt.call_args
        prompt_text = args[1]
        assert "SYSTEM: There are 3 new messages in this thread since your last response." in prompt_text

@pytest.mark.asyncio
async def test_catchup_clearing_prompt_after_sync(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    user_mapping = {"u1": "linux1"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value=user_mapping):
        # 1. Message 1
        bridge.thread_counters["p1"] = 1
        await bridge._handle_message({"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot p1", "create_at": 1000}, "linux1")
        
        # 2. Missed message (p2) + Message 3 (hint expected)
        bridge.thread_counters["p1"] = 3
        await bridge._handle_message({"id": "p3", "user_id": "u1", "channel_id": "c1", "root_id": "p1", "message": "@bot p3", "create_at": 1002}, "linux1")
        assert "SYSTEM: There are 1 new messages" in mock_goose_client.prompt.call_args[0][1]
        
        # 3. Message 4 (perfect sync, clearing message expected)
        bridge.thread_counters["p1"] = 4
        await bridge._handle_message({"id": "p4", "user_id": "u1", "channel_id": "c1", "root_id": "p1", "message": "@bot p4", "create_at": 1003}, "linux1")
        assert "SYSTEM: You are now caught up with the thread." in mock_goose_client.prompt.call_args[0][1]

@pytest.mark.asyncio
async def test_no_catchup_needed_when_synced(config, mock_api, mock_goose_client):
    factory = lambda user: mock_goose_client
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=factory)
    bridge.bot_mention = "@bot"
    user_mapping = {"u1": "linux1"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value=user_mapping):
        # 1. p1
        bridge.thread_counters["p1"] = 1
        await bridge._handle_message({"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot p1", "create_at": 1000}, "linux1")
        
        # 2. p2 (sync)
        bridge.thread_counters["p1"] = 2
        await bridge._handle_message({"id": "p2", "user_id": "u1", "channel_id": "c1", "root_id": "p1", "message": "@bot p2", "create_at": 1001}, "linux1")
        
        prompt_text = mock_goose_client.prompt.call_args[0][1]
        assert "SYSTEM:" not in prompt_text

@pytest.mark.asyncio
async def test_session_pruning(config, mock_api, mock_goose_client):
    config.max_sessions = 2
    mock_goose_client.send_request = AsyncMock(return_value={})
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.sessions = {
        "u1:t1": {"id": "s1", "linux_user": "u1"},
        "u1:t2": {"id": "s2", "linux_user": "u1"},
        "u1:t3": {"id": "s3", "linux_user": "u1"}
    }
    bridge.goose_clients["u1"] = mock_goose_client
    await bridge._prune_sessions()
    assert len(bridge.sessions) == 2

@pytest.mark.asyncio
async def test_handle_stop_command(config, mock_api, mock_goose_client):
    mock_goose_client.cancel_prompt = AsyncMock(return_value=True)
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    bridge.sessions["u1:r1"] = {"id": "s1", "linux_user": "l1"}
    bridge.goose_clients["l1"] = mock_goose_client
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "l1"}):
        await bridge._handle_stop_command({"user_id": "u1", "channel_id": "c1", "root_id": "r1", "id": "p1"})
        assert mock_goose_client.cancel_prompt.called

@pytest.mark.asyncio
async def test_ignore_own_messages(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    bridge.bot_id = "bot_id"
    await bridge._process_post({"user_id": "bot_id", "message": "hi"}, {})
    assert not mock_api.get_user.called

@pytest.mark.asyncio
async def test_ignore_whitespace_only(config, mock_api, mock_goose_client):
    bridge = MattermostBridge(api=mock_api, config=config, goose_client_factory=lambda u: mock_goose_client)
    await bridge._handle_message({"message": "   ", "user_id": "u1"}, "l1")
    assert not mock_goose_client.prompt.called
