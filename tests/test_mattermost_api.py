from unittest.mock import MagicMock
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
