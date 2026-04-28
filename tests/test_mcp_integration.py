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
async def test_bridge_api_tool_call(config, mock_api):
    bridge = MattermostBridge(api=mock_api, config=config)
    bridge.bridge_tokens["s1"] = "test-token"
    
    # Start API in background
    await bridge._start_bridge_api()
    
    async with ClientSession() as session:
        # 1. Test get_thread_context
        headers = {"X-Bridge-Token": "test-token"}
        payload = {
            "session_key": "s1",
            "tool": "get_thread_context",
            "arguments": {"post_id": "p1"}
        }
        async with session.post("http://127.0.0.1:8081/tool", headers=headers, json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "[@user1]: hello" in data["result"][0]

        # 2. Test send_message
        payload = {
            "session_key": "s1",
            "tool": "send_message",
            "arguments": {"channel_id": "c1", "message": "hi"}
        }
        async with session.post("http://127.0.0.1:8081/tool", headers=headers, json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["result"]["id"] == "post_id"

        # 3. Test invalid token
        headers = {"X-Bridge-Token": "wrong"}
        async with session.post("http://127.0.0.1:8081/tool", headers=headers, json=payload) as resp:
            assert resp.status == 403

    # Cleanup (not strictly necessary for tests but good practice)
    # Runner cleanup would be needed if we were keeping it alive
