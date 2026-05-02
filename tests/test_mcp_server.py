from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

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
    tools = await mcp_server.mcp.list_tools()
    assert len(tools) == 7
    tool_names = [t.name for t in tools]
    assert "send_message" in tool_names
    assert "get_channels" in tool_names
    assert "get_thread_context" in tool_names
    assert "search_messages" in tool_names
    assert "search_users" in tool_names
    assert "get_user_info" in tool_names
    assert "send_direct_message" in tool_names


@pytest.mark.asyncio
async def test_call_tool_send_message(mcp_server, mock_bridge):
    arguments = {"channel_id": "c1", "message": "hello", "root_id": "r1"}

    result, _ = await mcp_server.mcp.call_tool("send_message", arguments)

    assert len(result) == 1
    assert result[0].text == "Message sent successfully"
    mock_bridge.api.create_post.assert_called_once_with("c1",
                                                        "hello",
                                                        root_id="r1")


@pytest.mark.asyncio
async def test_call_tool_get_channels(mcp_server, mock_bridge):
    result, _ = await mcp_server.mcp.call_tool("get_channels", {})

    assert len(result) == 1
    assert "town-square" in result[0].text
    mock_bridge._update_channel_cache.assert_called_once()


@pytest.mark.asyncio
async def test_call_tool_get_thread_context(mcp_server, mock_bridge):
    mock_bridge.api.get_thread = AsyncMock(
        return_value={
            "posts": {
                "p1": {
                    "message": "first",
                    "create_at": 100,
                    "user_id": "u1"
                },
                "p2": {
                    "message": "second",
                    "create_at": 200,
                    "user_id": "u2"
                }
            }
        })
    mock_bridge.api.get_user = AsyncMock(
        side_effect=lambda uid: {"username": f"user_{uid}"})

    result, _ = await mcp_server.mcp.call_tool("get_thread_context",
                                               {"root_id": "r1"})

    assert len(result) == 1
    assert "[Sender: @user_u1] first" in result[0].text
    assert "[Sender: @user_u2] second" in result[0].text
    # Now called with per_page=60, from_create_at=0, direction="down" by default in the pagination loop
    mock_bridge.api.get_thread.assert_any_call("r1",
                                               per_page=0,
                                               from_create_at=0,
                                               direction="up")


@pytest.mark.asyncio
async def test_call_tool_search_messages(mcp_server, mock_bridge):
    mock_bridge.api.get_my_teams = AsyncMock(return_value=[{"id": "t1"}])
    mock_bridge.api.search_posts = AsyncMock(
        return_value={
            "posts": {
                "p1": {
                    "id": "p1",
                    "channel_id": "c1",
                    "message": "found it",
                    "create_at": 100
                }
            }
        })

    result, _ = await mcp_server.mcp.call_tool("search_messages",
                                               {"terms": "query"})

    assert len(result) == 1
    assert "found it" in result[0].text
    mock_bridge.api.search_posts.assert_called_once_with("t1", "query")


@pytest.mark.asyncio
async def test_call_tool_send_direct_message(mcp_server, mock_bridge):
    mock_bridge.api.get_me = AsyncMock(return_value={"id": "me_id"})
    mock_bridge.api.search_users = AsyncMock(side_effect=[[{
        "id": "u1_id",
        "username": "user1"
    }]])
    mock_bridge.api.create_direct_channel = AsyncMock(
        return_value={"id": "dm_channel"})

    result, _ = await mcp_server.mcp.call_tool("send_direct_message", {
        "usernames": ["@user1"],
        "message": "private hello"
    })

    assert "Direct message sent" in result[0].text
    mock_bridge.api.create_direct_channel.assert_called_once()
    # Should include both user1 and me
    user_ids = mock_bridge.api.create_direct_channel.call_args[0][0]
    assert "u1_id" in user_ids
    assert "me_id" in user_ids


@pytest.mark.asyncio
async def test_call_tool_unknown(mcp_server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="Unknown tool: ghost_tool"):
        await mcp_server.mcp.call_tool("ghost_tool", {})