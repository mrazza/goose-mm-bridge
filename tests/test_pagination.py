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
        await api.get_thread("post123", per_page=10, from_post="p0", direction="down")
        
        args, _ = mock_url.call_args
        req = args[0]
        url = req.get_full_url()
        assert "perPage=10" in url
        assert "fromPost=p0" in url
        assert "direction=down" in url

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
    # Mock a thread with 10 posts
    posts = {f"p{i}": {"message": f"m{i}", "create_at": i, "user_id": "u1"} for i in range(10)}
    order = [f"p{i}" for i in range(10)]
    
    mock_bridge.api.get_thread = AsyncMock(return_value={
        "posts": posts,
        "order": order,
        "has_next": False
    })
    
    # Test limit=2, page=0 (should be the last 2 posts: m8, m9)
    result_list, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 2, "page": 0})
    result = result_list[0].text
    assert "m8" in result and "m9" in result
    assert "m7" not in result
    
    # Test limit=2, page=1 (should be m6, m7)
    result_list, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 2, "page": 1})
    result = result_list[0].text
    assert "m6" in result and "m7" in result
    assert "m8" not in result

@pytest.mark.asyncio
async def test_mcp_get_thread_context_all_pages(mcp_server, mock_bridge):
    # Mock multiple pages (direction="down")
    page1 = {
        "posts": {f"p{i}": {"message": f"m{i}", "create_at": i, "user_id": "u1"} for i in range(0, 60)},
        "order": [f"p{i}" for i in range(0, 60)],
        "has_next": True
    }
    page2 = {
        "posts": {f"p{i}": {"message": f"m{i}", "create_at": i, "user_id": "u1"} for i in range(60, 120)},
        "order": [f"p{i}" for i in range(60, 120)],
        "has_next": False
    }
    
    mock_bridge.api.get_thread = AsyncMock(side_effect=[page1, page2])
    
    result_list, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 0})
    result = result_list[0].text
    
    # With limit=0, it should only call once with per_page=0
    assert mock_bridge.api.get_thread.call_count == 1
    assert result.count("[Sender: @testuser]") == 60
    
    # Verify call arguments
    mock_bridge.api.get_thread.assert_any_call("r1", per_page=0, from_post="", direction="down")