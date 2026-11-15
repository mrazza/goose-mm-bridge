from typing import Any, Dict, Optional

from logging_config import get_logger

logger = get_logger(__name__)


def _parse_session_update(update: dict) -> Optional[Dict[str, Any]]:
    session_update = update.get("sessionUpdate")
    result: Optional[Dict[str, Any]] = None
    if session_update == "agent_message_chunk":
        content_obj = update.get("content", {})
        if content_obj.get("type") == "text":
            result = {"type": "content", "text": content_obj.get("text", "")}
    elif session_update == "agent_thought_chunk":
        content_obj = update.get("content", {})
        if content_obj.get("type") == "text":
            result = {"type": "thinking", "text": content_obj.get("text", "")}
    elif session_update == "agent_thinking_chunk":
        result = {"type": "thinking", "text": update.get("thinking", "")}
    elif session_update == "call_tool":
        tool_call = update.get("toolCall", {})
        result = {
            "type": "tool",
            "name": tool_call.get("name"),
            "arguments": tool_call.get("arguments"),
        }
    elif session_update == "tool_call":
        result = {
            "type": "tool",
            "name": update.get("title") or "tool",
            "arguments": {},
        }
    elif session_update == "tool_call_update":
        title = update.get("title")
        if title:
            result = {"type": "thinking", "text": title}
        elif update.get("status") == "completed":
            result = {"type": "thinking", "text": "working..."}
    elif session_update == "usage_update":
        result = {
            "type": "usage",
            "used": update.get("used"),
            "size": update.get("size"),
        }
    elif session_update == "session_info_update":
        result = {"type": "title", "title": update.get("title")}
    return result


def parse_update_chunk(chunk: dict,
                       debug: bool = False) -> Optional[Dict[str, Any]]:
    """Normalizes a raw ACP JSON-RPC update into a bridge event dict."""
    if debug:
        logger.debug("Parsing chunk: %s", chunk)
    params = chunk.get("params", {})

    if params.get("chunk", {}).get("type") == "text":
        return {"type": "content", "text": params["chunk"]["text"]}

    parsed = _parse_session_update(params.get("update", {}))
    if parsed is None and debug:
        logger.debug("Unknown or unhandled chunk format: %s", chunk)
    return parsed
