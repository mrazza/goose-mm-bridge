from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
from mattermost_api import MattermostAPI
from mcp_server import MattermostMCPServer

@pytest.fixture
def api():
    from config import Config
    config = Config(mattermost_url="example.com", mattermost_token="token")
    return MattermostAPI(config=config)

@pytest.mark.asyncio
async def test_api_get_thread_pagination(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"posts": {"p1": {"id": "p1"}}, "order": ["p1"]}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        await api.get_thread("post123", per_page=10, page=2)
        
        args, _ = mock_url.call_args
        req = args[0]
        assert "per_page=10" in req.get_full_url()
        assert "page=2" in req.get_full_url()

@pytest.fixture
def mock_bridge():
    bridge = MagicMock()
    bridge.api = MagicMock()
    bridge.api.get_user = AsyncMock(return_value={"username": "testuser"})
    return bridge

@pytest.fixture
def mcp_server(mock_bridge):
    return MattermostMCPServer(mock_bridge)

@pytest.mark.asyncio
async def test_mcp_get_thread_context_with_limit(mcp_server, mock_bridge):
    mock_bridge.api.get_thread = AsyncMock(return_value={
        "posts": {"p1": {"message": "hi", "create_at": 1, "user_id": "u1"}},
        "order": ["p1"]
    })
    
    await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 5, "page": 1})
    
    mock_bridge.api.get_thread.assert_called_once_with("r1", per_page=5, page=1)

@pytest.mark.asyncio
async def test_mcp_get_thread_context_all_pages(mcp_server, mock_bridge):
    # Mock multiple pages
    page1 = {
        "posts": {f"p{i}": {"message": f"m{i}", "create_at": i, "user_id": "u1"} for i in range(60, 120)},
        "order": [f"p{i}" for i in range(60, 120)],
        "has_next": True
    }
    page2 = {
        "posts": {f"p{i}": {"message": f"m{i}", "create_at": i, "user_id": "u1"} for i in range(0, 60)},
        "order": [f"p{i}" for i in range(0, 60)],
        "has_next": True
    }
    page3 = {"posts": {}, "order": [], "has_next": False}
    
    mock_bridge.api.get_thread = AsyncMock(side_effect=[page1, page2, page3])
    
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 0})
    
    assert mock_bridge.api.get_thread.call_count == 3
    # Check that we received 120 messages total
    assert result[0].text.count("[Sender: @testuser]") == 120
    
    # Verify call arguments for pagination
    mock_bridge.api.get_thread.assert_any_call("r1", per_page=0, page=0)
    mock_bridge.api.get_thread.assert_any_call("r1", per_page=0, page=1)
    mock_bridge.api.get_thread.assert_any_call("r1", per_page=0, page=2)
