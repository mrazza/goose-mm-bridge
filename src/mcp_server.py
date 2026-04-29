import asyncio
import json
from typing import List, Optional
from mcp.server.fastmcp import FastMCP

class MattermostMCPServer:
    def __init__(self, bridge):
        self.bridge = bridge
        self.mcp = FastMCP("mattermost-bridge-mcp")
        self.setup_tools()

    def setup_tools(self):
        @self.mcp.tool()
        async def send_message(channel_id: str, message: str, root_id: Optional[str] = None) -> str:
            """Send a message to a Mattermost channel.
            
            Args:
                channel_id: The ID of the channel to send the message to.
                message: The message text.
                root_id: Optional thread root ID to reply to a specific thread.
            """
            await self.bridge.api.create_post(channel_id, message, root_id=root_id)
            return "Message sent successfully"

        @self.mcp.tool()
        async def get_channels() -> str:
            """Get list of available channels."""
            await self.bridge._update_channel_cache()
            channels = self.bridge.channels_cache
            return json.dumps(channels, indent=2)

        @self.mcp.tool()
        async def get_thread_context(root_id: str) -> str:
            """Fetch the full history of a thread.
            
            Args:
                root_id: The ID of the thread root post.
            """
            thread = await self.bridge.api.get_thread(root_id)
            if not thread or "posts" not in thread:
                return "Thread not found or empty"
            
            posts = sorted(thread["posts"].values(), key=lambda x: x["create_at"])
            
            # Fetch usernames for attribution
            user_ids = list(set(p["user_id"] for p in posts))
            user_map = {}
            for uid in user_ids:
                uinfo = await self.bridge.api.get_user(uid)
                user_map[uid] = uinfo.get("username", "unknown") if uinfo else "unknown"
            
            formatted = []
            for p in posts:
                username = user_map.get(p["user_id"], "unknown")
                formatted.append(f"[Sender: @{username}] {p['message']}")
            
            return "\n".join(formatted)

        @self.mcp.tool()
        async def search_messages(terms: str) -> str:
            """Search for messages across Mattermost.
            
            Args:
                terms: Search terms.
            """
            teams = await self.bridge.api.get_my_teams()
            if not teams:
                return "No teams found to search in"
            
            all_results = []
            for team in teams:
                results = await self.bridge.api.search_posts(team["id"], terms)
                if results and "posts" in results:
                    all_results.extend(results["posts"].values())
            
            if not all_results:
                return "No messages found"
            
            all_results.sort(key=lambda x: x["create_at"], reverse=True)
            formatted = []
            for p in all_results[:20]:
                formatted.append(f"[Post ID: {p['id']}] [Channel ID: {p['channel_id']}] {p['message']}")
            
            return "\n---\n".join(formatted)

        @self.mcp.tool()
        async def search_users(term: str) -> str:
            """Search for users by name, username, or email.
            
            Args:
                term: Search term.
            """
            users = await self.bridge.api.search_users(term)
            return json.dumps(users, indent=2)

        @self.mcp.tool()
        async def get_user_info(user_id: str) -> str:
            """Get detailed profile information for a user.
            
            Args:
                user_id: The ID of the user.
            """
            user = await self.bridge.api.get_user(user_id)
            return json.dumps(user, indent=2)

        @self.mcp.tool()
        async def send_direct_message(usernames: List[str], message: str) -> str:
            """Send a direct message to one or more users.
            
            Args:
                usernames: List of usernames (with or without @).
                message: The message text.
            """
            user_ids = []
            for uname in usernames:
                clean_uname = uname[1:] if uname.startswith("@") else uname
                found = await self.bridge.api.search_users(clean_uname)
                if found:
                    exact = next((u for u in found if u["username"] == clean_uname), found[0])
                    user_ids.append(exact["id"])
            
            if not user_ids:
                return "No valid users found to message"
            
            me = await self.bridge.api.get_me()
            if me and me["id"] not in user_ids:
                user_ids.append(me["id"])
            
            channel = await self.bridge.api.create_direct_channel(user_ids)
            if not channel:
                return "Failed to create direct channel"
            
            await self.bridge.api.create_post(channel["id"], message)
            return f"Direct message sent to channel {channel['id']}"

    async def run(self, host: str, port: int):
        """Run the MCP server using Streamable HTTP transport."""
        self.mcp.settings.host = host
        self.mcp.settings.port = port
        await self.mcp.run_streamable_http_async()