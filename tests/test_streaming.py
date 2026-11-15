from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from config import Config
from streaming import MattermostStreamRenderer


@pytest.fixture
def api():
    mock = MagicMock()
    mock.create_post = AsyncMock(return_value={"id": "post_1"})
    mock.update_post = AsyncMock(return_value={"id": "post_1"})
    return mock


def _step_clock(step=2.0):
    now = [100.0]

    def _clock():
        now[0] += step
        return now[0]

    return _clock


@pytest.mark.asyncio
async def test_stream_response_full_thinking_trace_and_throttling(api):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        goose_thinking_trace=True,
        goose_thinking_trace_simplified=False,
    )
    # Use a clock that stays under 1.0s on first chunk, then crosses 1.0s
    ticks = iter([100.0, 100.5, 102.0, 102.1])
    renderer = MattermostStreamRenderer(
        api,
        config,
        clock=lambda: next(ticks),
        update_interval=1.0,
    )

    async def fake_prompt(sid, text):
        yield {"type": "thinking", "text": "step 1"}  # throttled (0.5s <= 1.0s)
        yield {"type": "thinking", "text": "step 2"}  # sent (2.0s > 1.0s)
        yield {"type": "final", "text": "final answer"}

    client = MagicMock()
    client.prompt = fake_prompt

    await renderer.stream_response(client, "sid", "hi", "cid", "rid")

    api.create_post.assert_awaited_once_with("cid",
                                             ":thinking_face: **Thinking...**",
                                             root_id="rid")
    # step 1 skipped; only step 2 and final triggered update_post
    assert api.update_post.await_count == 2
    first_call = api.update_post.call_args_list[0]
    assert first_call.args[1] == ":thinking_face: **Thinking...**"
    attachments = first_call.kwargs["props"]["attachments"]
    assert "step 1" in attachments[0]["text"]
    assert "step 2" in attachments[0]["text"]

    final_call = api.update_post.call_args_list[1]
    assert final_call.args[1] == "final answer"


@pytest.mark.asyncio
async def test_stream_response_simplified_with_title_and_tool(api):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        goose_thinking_trace=True,
        goose_thinking_trace_simplified=True,
    )
    renderer = MattermostStreamRenderer(api, config, clock=_step_clock(2.0))

    async def fake_prompt(sid, text):
        yield {"type": "title", "title": "Building Widget"}
        yield {"type": "tool", "name": "shell"}
        yield {"type": "thinking", "text": "analyzing code"}
        yield {"type": "content", "text": "Partial text"}
        yield {"type": "final", "text": "All done"}

    client = MagicMock()
    client.prompt = fake_prompt

    await renderer.stream_response(client, "sid", "hi", "cid", "rid")

    msgs = [call.args[1] for call in api.update_post.call_args_list]
    assert any("**Working on building widget...** *[Using tool: shell]*" in m
               for m in msgs)
    assert any("**Working on building widget...** *[analyzing code]*" in m
               for m in msgs)
    assert "Partial text" in msgs
    assert msgs[-1] == "All done"
    for call in api.update_post.call_args_list:
        assert "attachments" not in call.kwargs["props"]


@pytest.mark.asyncio
async def test_stream_response_thinking_trace_truncation(api):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        goose_thinking_trace=True,
        goose_thinking_trace_simplified=False,
    )
    renderer = MattermostStreamRenderer(api, config, clock=_step_clock(2.0))

    async def fake_prompt(sid, text):
        yield {"type": "thinking", "text": "A" * 12000}
        yield {"type": "final", "text": "done"}

    client = MagicMock()
    client.prompt = fake_prompt

    await renderer.stream_response(client, "sid", "hi", "cid", "rid")

    trace = api.update_post.call_args_list[0].kwargs["props"]["attachments"][0][
        "text"]
    assert trace.startswith("... (truncated) ...\n")
    assert len(trace) <= 8100


@pytest.mark.asyncio
async def test_stream_response_fallback_create_post_when_initial_fails(api):
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        goose_thinking_trace=True,
        goose_thinking_trace_simplified=True,
    )
    # First create_post fails (returns None); fallback create_post succeeds
    api.create_post = AsyncMock(side_effect=[None, {"id": "fallback_id"}])
    renderer = MattermostStreamRenderer(api, config, clock=_step_clock(2.0))

    async def fake_prompt(sid, text):
        yield {"type": "tool", "name": "grep"}
        yield {"type": "final", "text": "Done"}

    client = MagicMock()
    client.prompt = fake_prompt

    await renderer.stream_response(client, "sid", "hi", "cid", "rid")

    assert api.create_post.await_count == 2
    api.update_post.assert_awaited_once_with(
        "fallback_id",
        "Done",
        props={},
    )
