import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

import aiohttp
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP
mcp = FastMCP("mattermost")

BRIDGE_API_URL = os.getenv("BRIDGE_API_URL", "http://127.0.0.1:8080")
BRIDGE_SESSION_TOKEN = os.getenv("BRIDGE_SESSION_TOKEN", "")
SESSION_KEY = os.getenv("SESSION_KEY", "")

async def _call_bridge(tool_name: str, arguments: Dict[str, Any]) -> str:
    headers = {
        "X-Bridge-Token": BRIDGE_SESSION_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "session_key": SESSION_KEY,
        "tool": tool_name,
        "arguments": arguments
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{BRIDGE_API_URL}/tool", headers=headers, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    return f"Error from bridge: {response.status} {text}"
                
                result = await response.json()
                if "error" in result:
                    return f"Error: {result['error']}"
                
                return json.dumps(result.get("result", {}), indent=2)
        except Exception as e:
            return f"Failed to connect to bridge: {str(e)}"

@mcp.tool()
async def get_thread_context(post_id: str) -> str:
    """Fetch the full history of a thread. Use this to fill in gaps when you join mid-conversation."""
    return await _call_bridge("get_thread_context", {"post_id": post_id})

@mcp.tool()
async def search_messages(terms: str) -> str:
    """Search for information across the Mattermost instance."""
    return await _call_bridge("search_messages", {"terms": terms})

@mcp.tool()
async def list_channels() -> str:
    """List public channels you have access to."""
    return await _call_bridge("list_channels", {})

@mcp.tool()
async def search_users(term: str) -> str:
    """Search for users by name, username, or email. Use this before sending a DM."""
    return await _call_bridge("search_users", {"term": term})

@mcp.tool()
async def get_user_info(user_id: str) -> str:
    """Retrieve profile details for a specific user."""
    return await _call_bridge("get_user_info", {"user_id": user_id})

@mcp.tool()
async def send_message(channel_id: str, message: str, root_id: Optional[str] = None) -> str:
    """Send a message to a specific channel or thread."""
    return await _call_bridge("send_message", {"channel_id": channel_id, "message": message, "root_id": root_id})

@mcp.tool()
async def send_direct_message(user_ids: List[str], message: str) -> str:
    """Send a message directly to one or more users."""
    return await _call_bridge("send_direct_message", {"user_ids": user_ids, "message": message})

if __name__ == "__main__":
    mcp.run()
