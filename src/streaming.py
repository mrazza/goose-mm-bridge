import time
from typing import Any, Callable, Dict, Optional

from config import Config

THINKING_MSG = ":thinking_face: **Thinking...**"
MAX_TRACE_LENGTH = 10000
TRUNCATED_TRACE_TAIL = 8000


class MattermostStreamRenderer:
    """Streams ACP agent updates into a Mattermost post with throttling."""

    def __init__(
        self,
        api: Any,
        config: Config,
        clock: Callable[[], float] = time.monotonic,
        update_interval: float = 1.0,
    ):
        self.api = api
        self.config = config
        self._clock = clock
        self.update_interval = update_interval

    def _format_in_progress_message(
        self,
        full_response: str,
        last_thinking_text: str,
        title: str,
    ) -> str:
        if full_response:
            return full_response
        if (self.config.goose_thinking_trace_simplified and last_thinking_text):
            if title:
                return (f":thinking_face: **Working on {title.lower()}...** "
                        f"*[{last_thinking_text}]*")
            return f"{THINKING_MSG} *[{last_thinking_text}]*"
        return THINKING_MSG

    def _build_props(self, thinking_trace: str) -> Dict[str, Any]:
        if (self.config.goose_thinking_trace and thinking_trace and
                not self.config.goose_thinking_trace_simplified):
            return {
                "attachments": [{
                    "text": thinking_trace,
                    "title": "Thinking Trace",
                    "color": "#9b9b9b",
                }]
            }
        return {}

    async def stream_response(
        self,
        client: Any,
        session_id: str,
        prompt_text: str,
        channel_id: str,
        root_id: str,
    ) -> None:
        """Prompts the ACP client and streams updates to Mattermost."""
        thinking_post: Optional[dict] = await self.api.create_post(
            channel_id, THINKING_MSG, root_id=root_id)
        last_update_time = self._clock()

        full_response = ""
        thinking_trace = ""
        last_thinking_text = ""
        title = ""

        async for update in client.prompt(session_id, prompt_text):
            update_type = update["type"]
            if update_type == "thinking":
                thinking_trace += f"\n\n**Working**: {update['text']}"
                last_thinking_text = update["text"].strip()
            elif update_type == "tool":
                tool_name = update.get("name") or "tool"
                thinking_trace += f"\n\n**Using tool**: `{tool_name}`\n"
                last_thinking_text = f"Using tool: {tool_name}"
            elif update_type == "title":
                title = update["title"]
            elif update_type in ("content", "final"):
                full_response = update["text"]

            if len(thinking_trace) > MAX_TRACE_LENGTH:
                thinking_trace = ("... (truncated) ...\n" +
                                  thinking_trace[-TRUNCATED_TRACE_TAIL:])

            now = self._clock()
            is_final = update_type == "final"
            has_visible_progress = bool(
                (self.config.goose_thinking_trace and thinking_trace) or
                full_response)
            should_update = is_final or (
                (now - last_update_time > self.update_interval) and
                has_visible_progress)

            if not should_update:
                continue

            if is_final:
                resp_msg = full_response
            else:
                resp_msg = self._format_in_progress_message(
                    full_response, last_thinking_text, title)
            props = self._build_props(thinking_trace)

            if not thinking_post:
                thinking_post = await self.api.create_post(
                    channel_id,
                    resp_msg,
                    root_id=root_id,
                    props=props,
                )
            else:
                await self.api.update_post(
                    thinking_post["id"],
                    resp_msg,
                    props=props,
                )
            last_update_time = now
