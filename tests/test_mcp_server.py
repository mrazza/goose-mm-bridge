import pytest
from unittest.mock import MagicMock, AsyncMock
from mcp_server import MattermostMCPServer

@pytest.fixture
def mock_bridge():
    bridge = MagicMock()
    bridge.api = MagicMock()
    bridge.api.create_post = AsyncMock(return_value={"id": "post_123"})
    bridge._update_channel_cache = AsyncMock()
    bridge.channels_cache = [{"id": "chan_1", "name": "town-square"}]
    return bridge

@pytest.fixture
def mcp_server(mock_bridge):
    return MattermostMCPServer(mock_bridge)

@pytest.mark.asyncio
async def test_list_tools(mcp_server):
    tools = await mcp_server.list_tools()
    assert len(tools) == 2
    tool_names = [t["name"] for t in tools]
    assert "send_message" in tool_names
    assert "get_channels" in tool_names

@pytest.mark.asyncio
async def test_call_tool_send_message(mcp_server, mock_bridge):
    arguments = {
        "channel_id": "c1",
        "message": "hello",
        "root_id": "r1"
    }
    
    result = await mcp_server.call_tool("send_message", arguments)
    
    assert len(result) == 1
    assert result[0]["text"] == "Message sent successfully"
    mock_bridge.api.create_post.assert_called_once_with("c1", "hello", root_id="r1")

@pytest.mark.asyncio
async def test_call_tool_get_channels(mcp_server, mock_bridge):
    result = await mcp_server.call_tool("get_channels", {})
    
    assert len(result) == 1
    assert "town-square" in result[0]["text"]
    mock_bridge._update_channel_cache.assert_called_once()

@pytest.mark.asyncio
async def test_call_tool_unknown(mcp_server):
    with pytest.raises(ValueError, match="Unknown tool: ghost_tool"):
        await mcp_server.call_tool("ghost_tool", {})
