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
    api.get_user = AsyncMock(return_value={"username": "user1"})
    return api

@pytest.fixture
def mock_goose_client():
    client = MagicMock()
    client.create_session = AsyncMock(return_value="session_1")
    
    async def mock_prompt(sid, msg):
        yield {"type": "thinking", "text": "let me see"}
        yield {"type": "content", "text": "the answer is 42"}
        yield {"type": "final", "text": "the answer is 42"}
    
    client.prompt = mock_prompt
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
        "message": "@bot hello"
    }
    
    # We mock load_user_mapping to avoid file IO
    with patch('mattermost_bridge.load_user_mapping', return_value={"user_id_1": "linux_user"}):
        await bridge._handle_message(post, "linux_user")
        
        # Verify goose was prompted
        # The prompt is an async generator, so we check if it was called
        # mock_goose_client.prompt is replaced by our mock_prompt function in the fixture
        
        # Verify Mattermost posts were created/updated
        assert mock_api.create_post.called
        # First post is "Thinking..."
        assert mock_api.create_post.call_args_list[0][0][1] == ":thinking_face: **Thinking...**"

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
        "id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot hello"
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
    
    post = {"id": "p1", "user_id": "u1", "channel_id": "c1", "message": "@bot hello"}
    
    with patch('mattermost_bridge.load_user_mapping', return_value={"u1": "linux1"}):
        # Start two tasks for the same thread
        t1 = asyncio.create_task(bridge._handle_message(post, "linux1"))
        t2 = asyncio.create_task(bridge._handle_message(post, "linux1"))
        
        await asyncio.gather(t1, t2)
        
        # Verify they shared the same lock
        assert "u1:p1" in bridge.session_locks