from unittest.mock import MagicMock, mock_open
from unittest.mock import patch
import urllib.error

import pytest

from config import Config
from mattermost_api import MattermostAPI


@pytest.fixture
def api():
    config = Config(mattermost_url="example.com", mattermost_token="token")
    return MattermostAPI(config=config)


@pytest.mark.asyncio
async def test_get_me(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "bot_id", "username": "bot"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        me = await api.get_me()
        assert me["id"] == "bot_id"
        assert me["username"] == "bot"


@pytest.mark.asyncio
async def test_create_post(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "post_id"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen',
               return_value=mock_response) as mock_url:
        post = await api.create_post("channel_id",
                                     "hello message",
                                     root_id="root_id")
        assert post["id"] == "post_id"

        # Verify request details
        args, kwargs = mock_url.call_args
        req = args[0]
        assert req.get_full_url() == "https://example.com:443/api/v4/posts"
        assert req.get_method() == "POST"


@pytest.mark.asyncio
async def test_api_http_error(api):
    with patch('urllib.request.urlopen') as mock_url:
        mock_error = urllib.error.HTTPError("url", 500, "Internal Server Error",
                                            {}, None)
        mock_url.side_effect = mock_error

        res = await api.get_me()
        assert res is None

@pytest.mark.asyncio
async def test_get_user(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "u1", "username": "user1"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        user = await api.get_user("u1")
        assert user["id"] == "u1"
        assert user["username"] == "user1"

@pytest.mark.asyncio
async def test_get_post(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "p1", "message": "hello"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        post = await api.get_post("p1")
        assert post["id"] == "p1"
        assert post["message"] == "hello"

@pytest.mark.asyncio
async def test_get_file_info(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "f1", "name": "test.txt"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        info = await api.get_file_info("f1")
        assert info["id"] == "f1"
        assert info["name"] == "test.txt"

@pytest.mark.asyncio
async def test_download_file(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'raw file data'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        data = await api.download_file("f1")
        assert data == b'raw file data'

@pytest.mark.asyncio
async def test_get_direct_channels(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'[{"id": "c1"}]'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        channels = await api.get_direct_channels()
        assert len(channels) == 1
        assert channels[0]["id"] == "c1"

@pytest.mark.asyncio
async def test_get_my_teams(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'[{"id": "t1"}]'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        teams = await api.get_my_teams()
        assert len(teams) == 1
        assert teams[0]["id"] == "t1"

@pytest.mark.asyncio
async def test_get_my_channels(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'[{"id": "c1"}]'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        channels = await api.get_my_channels("t1")
        assert len(channels) == 1
        assert channels[0]["id"] == "c1"

@pytest.mark.asyncio
async def test_get_channel_posts(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"posts": {"p1": {"id": "p1"}}}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        res = await api.get_channel_posts("c1", 1000)
        assert "p1" in res["posts"]
        args, _ = mock_url.call_args
        assert "since=1000" in args[0].get_full_url()

@pytest.mark.asyncio
async def test_get_thread(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"posts": {"p1": {"id": "p1"}}}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        res = await api.get_thread("p1", per_page=10, from_create_at=500, direction="down")
        assert "p1" in res["posts"]
        url = mock_url.call_args[0][0].get_full_url()
        assert "perPage=10" in url
        assert "fromCreateAt=500" in url
        assert "direction=down" in url

@pytest.mark.asyncio
async def test_search_posts(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"posts": {"p1": {"id": "p1"}}}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        res = await api.search_posts("t1", "query")
        assert "p1" in res["posts"]
        req = mock_url.call_args[0][0]
        assert req.get_method() == "POST"

@pytest.mark.asyncio
async def test_search_users(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'[{"id": "u1"}]'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        res = await api.search_users("term")
        assert res[0]["id"] == "u1"
        req = mock_url.call_args[0][0]
        assert req.get_method() == "POST"

@pytest.mark.asyncio
async def test_create_direct_channel(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "c1"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        res = await api.create_direct_channel(["u1", "u2"])
        assert res["id"] == "c1"
        req = mock_url.call_args[0][0]
        assert req.get_method() == "POST"

@pytest.mark.asyncio
async def test_update_post(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"id": "p1", "message": "updated"}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        res = await api.update_post("p1", "updated")
        assert res["message"] == "updated"
        req = mock_url.call_args[0][0]
        assert req.get_method() == "PUT"

@pytest.mark.asyncio
async def test_upload_file(api):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"file_infos": [{"id": "f1"}]}'
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response) as mock_url:
        with patch('os.path.exists', return_value=True):
            with patch('mimetypes.guess_type', return_value=('text/plain', None)):
                with patch('builtins.open', mock_open(read_data=b"file content")):
                    res = await api.upload_file("c1", "test.txt")
                    assert res["file_infos"][0]["id"] == "f1"
                    req = mock_url.call_args[0][0]
                    assert req.get_method() == "POST"
                    assert "multipart/form-data" in req.get_header("Content-type")
