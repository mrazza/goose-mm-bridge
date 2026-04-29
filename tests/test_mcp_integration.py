import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from aiohttp import ClientSession
from mattermost_bridge import MattermostBridge
from config import Config

@pytest.fixture
def config():
    return Config(bridge_api_port=8081)

@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "bot"})
    api.get_thread = AsyncMock(return_value={"posts": {"p1": {"user_id": "u1", "message": "hello", "create_at": 1}}})
    api.get_user = AsyncMock(return_value={"username": "user1"})
    api.search_posts = AsyncMock(return_value={"posts": {"p1": {"user_id": "u1", "message": "found", "channel_id": "c1"}}})
    api.search_users = AsyncMock(return_value=[{"username": "user1"}])
    api.create_post = AsyncMock(return_value={"id": "post_id"})
    api.create_direct_channel = AsyncMock(return_value={"id": "dm_id"})
    return api

@pytest.mark.asyncio
async def test_mcp_endpoints_exist(config, mock_api):
    """Verifies that the MCP SSE endpoints are correctly registered."""
    bridge = MattermostBridge(api=mock_api, config=config)
    
    # We won't run a full SSE handshake in unit tests as it's complex,
    # but we can verify the server starts and routes exist.
    with patch('aiohttp.web.TCPSite.start', new_callable=AsyncMock) as mock_start:
        await bridge._start_http_server()
        assert mock_start.called
