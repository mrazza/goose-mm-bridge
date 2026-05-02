import asyncio
import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from config import Config
from mattermost_bridge import MattermostBridge


@pytest.fixture
def config():
    c = Config(mattermost_url="example.com", mattermost_token="token")
    c.goose_thinking_trace = True
    c.goose_thinking_trace_simplified = True
    return c


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_me = AsyncMock(return_value={"id": "bot_id", "username": "bot"})
    api.create_post = AsyncMock(return_value={"id": "post_id"})
    api.update_post = AsyncMock()
    api.get_user = AsyncMock(return_value={"username": "user1"})
    return api


@pytest.mark.asyncio
async def test_simplified_thinking_trace(config, mock_api):
    client = MagicMock()

    async def mock_prompt_gen(sid, msg):
        yield {"type": "thinking", "text": "searching for files"}
        await asyncio.sleep(0.1)
        yield {"type": "tool", "name": "shell"}
        await asyncio.sleep(0.1)
        yield {"type": "final", "text": "found it"}

    client.prompt.side_effect = mock_prompt_gen

    bridge = MattermostBridge(api=mock_api, config=config)

    # Use a side_effect that increments time to ensure we cross the 1.0s threshold
    current_time = [1000.0]

    def increment_time():
        current_time[0] += 1.1
        return current_time[0]

    with patch('time.time', side_effect=increment_time):
        await bridge._stream_response_to_mattermost(client, "sid", "msg", "cid",
                                                    "rid")

    calls = mock_api.update_post.call_args_list
    msgs = [c[0][1] for c in calls]
    print(f"Captured messages: {msgs}")

    # We expect at least one thinking update and the final one.
    # Depending on exactly when increment_time is called, we might get more.
    assert any(
        "Thinking...** *[searching for files]*" in m for m in msgs) or any(
            "Thinking...** *[Using tool: shell]*" in m for m in msgs)
    assert "found it" in msgs

    for call in calls[:-1]:
        props = call[1].get("props", {})
        assert "attachments" not in props
