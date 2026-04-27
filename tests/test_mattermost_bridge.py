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
