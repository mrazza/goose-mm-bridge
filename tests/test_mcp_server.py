from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from unittest.mock import mock_open

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
    assert len(tools) == 10
    tool_names = [t.name for t in tools]
    assert "send_message" in tool_names
    assert "get_channels" in tool_names
    assert "get_thread_context" in tool_names
    assert "search_messages" in tool_names
    assert "search_users" in tool_names
    assert "get_user_info" in tool_names
    assert "send_direct_message" in tool_names
    assert "get_post_details" in tool_names
    assert "list_post_attachments" in tool_names
    assert "download_attachment" in tool_names


@pytest.mark.asyncio
async def test_call_tool_send_message(mcp_server, mock_bridge):
    arguments = {"channel_id": "c1", "message": "hello", "root_id": "r1"}

    result, _ = await mcp_server.mcp.call_tool("send_message", arguments)

    assert len(result) == 1
    assert result[0].text == "Message sent successfully"
    mock_bridge.api.create_post.assert_called_once_with("c1",
                                                        "hello",
                                                        root_id="r1",
                                                        file_ids=None)


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
                    "user_id": "u1",
                    "file_ids": ["f1"]
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
    assert "[Sender: @user_u1] [Has 1 attachment(s)] first" in result[0].text
    assert "[Sender: @user_u2] second" in result[0].text
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
    mock_bridge.api.search_posts.assert_called_once_with("t1",
                                                         "query",
                                                         page=0,
                                                         per_page=60)


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
    user_ids = mock_bridge.api.create_direct_channel.call_args[0][0]
    assert "u1_id" in user_ids
    assert "me_id" in user_ids


@pytest.mark.asyncio
async def test_call_tool_send_message_with_attachment(mcp_server, mock_bridge):
    mock_bridge.api.upload_file = AsyncMock(return_value={"file_infos": [{"id": "f1"}]})
    arguments = {"channel_id": "c1", "message": "hello", "file_path": "test.txt"}

    result, _ = await mcp_server.mcp.call_tool("send_message", arguments)

    assert "with attachment" in result[0].text
    mock_bridge.api.upload_file.assert_called_once_with("c1", "test.txt")
    mock_bridge.api.create_post.assert_called_once_with("c1", "hello", root_id=None, file_ids=["f1"])


@pytest.mark.asyncio
async def test_call_tool_send_direct_message_with_attachment(mcp_server, mock_bridge):
    mock_bridge.api.get_me = AsyncMock(return_value={"id": "me_id"})
    mock_bridge.api.search_users = AsyncMock(return_value=[{"id": "u1_id", "username": "user1"}])
    mock_bridge.api.create_direct_channel = AsyncMock(return_value={"id": "dm_channel"})
    mock_bridge.api.upload_file = AsyncMock(return_value={"file_infos": [{"id": "f1"}]})
    
    arguments = {
        "usernames": ["@user1"],
        "message": "private hello",
        "file_path": "test.txt"
    }

    result, _ = await mcp_server.mcp.call_tool("send_direct_message", arguments)

    assert "with attachment" in result[0].text
    mock_bridge.api.upload_file.assert_called_once_with("dm_channel", "test.txt")
    mock_bridge.api.create_post.assert_called_once_with("dm_channel", "private hello", file_ids=["f1"])


@pytest.mark.asyncio
async def test_call_tool_unknown(mcp_server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="Unknown tool: ghost_tool"):
        await mcp_server.mcp.call_tool("ghost_tool", {})

@pytest.mark.asyncio
async def test_call_tool_search_users(mcp_server, mock_bridge):
    mock_bridge.api.search_users = AsyncMock(return_value=[{"id": "u1", "username": "user1"}])
    
    result, _ = await mcp_server.mcp.call_tool("search_users", {"term": "user1"})
    
    assert len(result) == 1
    assert "user1" in result[0].text
    mock_bridge.api.search_users.assert_called_once_with("user1")

@pytest.mark.asyncio
async def test_call_tool_get_user_info(mcp_server, mock_bridge):
    mock_bridge.api.get_user = AsyncMock(return_value={"id": "u1", "username": "user1", "email": "user1@example.com"})
    
    result, _ = await mcp_server.mcp.call_tool("get_user_info", {"user_id": "u1"})
    
    assert len(result) == 1
    assert "user1@example.com" in result[0].text
    mock_bridge.api.get_user.assert_called_once_with("u1")

@pytest.mark.asyncio
async def test_call_tool_get_post_details(mcp_server, mock_bridge):
    mock_bridge.api.get_post = AsyncMock(return_value={"id": "p1", "file_ids": ["f1"]})
    
    result, _ = await mcp_server.mcp.call_tool("get_post_details", {"post_id": "p1"})
    
    assert "f1" in result[0].text
    mock_bridge.api.get_post.assert_called_once_with("p1")

@pytest.mark.asyncio
async def test_call_tool_list_post_attachments(mcp_server, mock_bridge):
    mock_bridge.api.get_post = AsyncMock(return_value={"id": "p1", "file_ids": ["f1"]})
    mock_bridge.api.get_file_info = AsyncMock(return_value={"id": "f1", "name": "test.png"})
    
    result, _ = await mcp_server.mcp.call_tool("list_post_attachments", {"post_id": "p1"})
    
    assert "test.png" in result[0].text
    mock_bridge.api.get_file_info.assert_called_once_with("f1")

@pytest.mark.asyncio
async def test_call_tool_download_attachment(mcp_server, mock_bridge):
    mock_bridge.api.download_file = AsyncMock(return_value=b"content")
    
    with patch("builtins.open", mock_open()) as mocked_file:
        with patch("os.makedirs"):
            result, _ = await mcp_server.mcp.call_tool("download_attachment", {
                "file_id": "f1",
                "destination_path": "/tmp/test.png"
            })
            
            assert "successfully" in result[0].text
            mock_bridge.api.download_file.assert_called_once_with("f1")
            mocked_file.assert_called_once_with("/tmp/test.png", "wb")

@pytest.mark.asyncio
async def test_call_tool_send_message_upload_fail(mcp_server, mock_bridge):
    mock_bridge.api.upload_file = AsyncMock(return_value=None)
    arguments = {"channel_id": "c1", "message": "hello", "file_path": "test.txt"}
    result, _ = await mcp_server.mcp.call_tool("send_message", arguments)
    assert "Failed to upload attachment" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_get_thread_context_no_thread(mcp_server, mock_bridge):
    mock_bridge.api.get_thread = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1"})
    assert "Thread not found or empty" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_get_thread_context_empty_posts(mcp_server, mock_bridge):
    mock_bridge.api.get_thread = AsyncMock(return_value={"posts": {}})
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1"})
    assert "Thread not found or empty" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_get_thread_context_limit(mcp_server, mock_bridge):
    # Setup posts: we want to test three pagination/limit branches:
    # 1) break early: limit > 0 and len(all_posts_dict) >= (page + 1) * limit + 1
    # 2) end <= 0 -> "No more messages"
    # 3) posts is empty -> "No messages found in the specified range"
    
    mock_bridge.api.get_user = AsyncMock(return_value={"username": "user1"})

    # Test case 1: "No more messages" when page is out of bounds (end <= 0)
    mock_bridge.api.get_thread = AsyncMock(return_value={
        "posts": {
            "p1": {"id": "p1", "create_at": 100, "user_id": "u1", "message": "m1"},
            "p2": {"id": "p2", "create_at": 200, "user_id": "u1", "message": "m2"}
        },
        "order": ["p1", "p2"],
        "has_next": False
    })
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 1, "page": 5})
    assert "No more messages" in result[0].text

    # Test case 2: Empty slice/posts -> "No messages found in the specified range"
    # We can patch 'sorted' to return [] so that posts becomes empty and triggers line 113.
    mock_bridge.api.get_thread = AsyncMock(return_value={
        "posts": {
            "p1": {"id": "p1", "create_at": 100, "user_id": "u1", "message": "m1"}
        },
        "order": ["p1"],
        "has_next": False
    })
    with patch("mcp_server.sorted", return_value=[]):
        result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1"})
        assert "No messages found in the specified range" in result[0].text

    # Test case 3: Break early during pagination fetching
    # We want (page + 1) * limit + 1 to be exceeded so that we break on the first page!
    # page=0, limit=1. Then (page+1)*limit + 1 = 2.
    # Our first return value has 2 posts. So it should break immediately and not request the second page.
    mock_bridge.api.get_thread = AsyncMock(return_value={
        "posts": {
            "p1": {"id": "p1", "create_at": 100, "user_id": "u1", "message": "m1"},
            "p2": {"id": "p2", "create_at": 200, "user_id": "u1", "message": "m2"}
        },
        "order": ["p1", "p2"],
        "has_next": True  # normally would fetch next page, but should break early
    })
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1", "limit": 1, "page": 0})
    assert "m2" in result[0].text
    # Check that it was only called once because we broke early
    assert mock_bridge.api.get_thread.call_count == 1

    # Test case 4: order is empty or not in thread -> break
    # To cover lines 94-95, we also want to test a successful page fetch sequence where order is NOT empty
    # and has_next becomes False on the second page. Let's trace lines 94-95 in the loop:
    # We call get_thread, it returns order=["p1"], and we update from_create_at = thread["posts"]["p1"]["create_at"].
    # Then it goes back to start of loop and calls get_thread again with from_create_at=100.
    mock_bridge.api.get_thread = AsyncMock(side_effect=[
        {
            "posts": {
                "p1": {"id": "p1", "create_at": 100, "user_id": "u1", "message": "m1"}
            },
            "order": ["p1"],
            "has_next": True
        },
        {
            "posts": {},
            "order": [],
            "has_next": False
        }
    ])
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1"})
    assert "m1" in result[0].text
    assert mock_bridge.api.get_thread.call_count == 2

    # Let's hit line 93 (if not order: break) directly with has_next=True but order is empty!
    mock_bridge.api.get_thread = AsyncMock(return_value={
        "posts": {
            "p1": {"id": "p1", "create_at": 100, "user_id": "u1", "message": "m1"}
        },
        "order": [],
        "has_next": True
    })
    result, _ = await mcp_server.mcp.call_tool("get_thread_context", {"root_id": "r1"})
    assert "m1" in result[0].text
    assert mock_bridge.api.get_thread.call_count == 1

@pytest.mark.asyncio
async def test_call_tool_search_messages_no_teams(mcp_server, mock_bridge):
    mock_bridge.api.get_my_teams = AsyncMock(return_value=[])
    result, _ = await mcp_server.mcp.call_tool("search_messages", {"terms": "query"})
    assert "No teams found to search in" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_search_messages_no_results(mcp_server, mock_bridge):
    mock_bridge.api.get_my_teams = AsyncMock(return_value=[{"id": "t1"}])
    mock_bridge.api.search_posts = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("search_messages", {"terms": "query"})
    assert "No messages found" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_send_direct_message_no_users(mcp_server, mock_bridge):
    mock_bridge.api.search_users = AsyncMock(return_value=[])
    result, _ = await mcp_server.mcp.call_tool("send_direct_message", {
        "usernames": ["@nonexistent"],
        "message": "hello"
    })
    assert "No valid users found to message" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_send_direct_message_create_fail(mcp_server, mock_bridge):
    mock_bridge.api.get_me = AsyncMock(return_value={"id": "me_id"})
    mock_bridge.api.search_users = AsyncMock(return_value=[{"id": "u1_id", "username": "user1"}])
    mock_bridge.api.create_direct_channel = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("send_direct_message", {
        "usernames": ["@user1"],
        "message": "hello"
    })
    assert "Failed to create direct channel" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_send_direct_message_upload_fail(mcp_server, mock_bridge):
    mock_bridge.api.get_me = AsyncMock(return_value={"id": "me_id"})
    mock_bridge.api.search_users = AsyncMock(return_value=[{"id": "u1_id", "username": "user1"}])
    mock_bridge.api.create_direct_channel = AsyncMock(return_value={"id": "dm_channel"})
    mock_bridge.api.upload_file = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("send_direct_message", {
        "usernames": ["@user1"],
        "message": "hello",
        "file_path": "test.txt"
    })
    assert "Failed to upload attachment" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_get_post_details_not_found(mcp_server, mock_bridge):
    mock_bridge.api.get_post = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("get_post_details", {"post_id": "p1"})
    assert "not found" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_list_post_attachments_no_attachments(mcp_server, mock_bridge):
    mock_bridge.api.get_post = AsyncMock(return_value={"id": "p1", "file_ids": []})
    result, _ = await mcp_server.mcp.call_tool("list_post_attachments", {"post_id": "p1"})
    assert "No attachments found" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_list_post_attachments_info_fail(mcp_server, mock_bridge):
    mock_bridge.api.get_post = AsyncMock(return_value={"id": "p1", "file_ids": ["f1"]})
    mock_bridge.api.get_file_info = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("list_post_attachments", {"post_id": "p1"})
    assert "unknown" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_download_attachment_download_fail(mcp_server, mock_bridge):
    mock_bridge.api.download_file = AsyncMock(return_value=None)
    result, _ = await mcp_server.mcp.call_tool("download_attachment", {
        "file_id": "f1",
        "destination_path": "/tmp/test.png"
    })
    assert "Failed to download file" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_download_attachment_save_fail(mcp_server, mock_bridge):
    mock_bridge.api.download_file = AsyncMock(return_value=b"content")
    with patch("builtins.open", side_effect=IOError("Permission denied")):
        with patch("os.makedirs"):
            result, _ = await mcp_server.mcp.call_tool("download_attachment", {
                "file_id": "f1",
                "destination_path": "/tmp/test.png"
            })
            assert "Error saving file" in result[0].text

@pytest.mark.asyncio
async def test_mcp_server_run(mcp_server):
    mcp_server.mcp.run_streamable_http_async = AsyncMock()
    await mcp_server.run("localhost", 5000)
    assert mcp_server.mcp.settings.host == "localhost"
    assert mcp_server.mcp.settings.port == 5000
    mcp_server.mcp.run_streamable_http_async.assert_called_once()

