import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from config import Config
from streaming import MattermostStreamRenderer


@pytest.fixture
def config():
    c = Config(mattermost_url="example.com", mattermost_token="token")
    c.goose_thinking_trace = True
    c.goose_thinking_trace_simplified = True
    return c


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.create_post = AsyncMock(return_value={"id": "post_id"})
    api.update_post = AsyncMock()
    return api


@pytest.mark.asyncio
async def test_simplified_thinking_trace(config, mock_api):
    client = MagicMock()

    async def mock_prompt_gen(sid, msg):
        yield {"type": "thinking", "text": "searching for files"}
        await asyncio.sleep(0.01)
        yield {"type": "tool", "name": "shell"}
        await asyncio.sleep(0.01)
        yield {"type": "final", "text": "found it"}

    client.prompt.side_effect = mock_prompt_gen

    current_time = [1000.0]

    def increment_time():
        current_time[0] += 1.1
        return current_time[0]

    renderer = MattermostStreamRenderer(
        api=mock_api,
        config=config,
        clock=increment_time,
    )
    await renderer.stream_response(client, "sid", "msg", "cid", "rid")

    calls = mock_api.update_post.call_args_list
    msgs = [c[0][1] for c in calls]

    assert any("Thinking...** *[searching for files]*" in m for m in msgs)
    assert any("Thinking...** *[Using tool: shell]*" in m for m in msgs)
    assert "found it" in msgs

    for call in calls[:-1]:
        props = call[1].get("props", {})
        assert "attachments" not in props
