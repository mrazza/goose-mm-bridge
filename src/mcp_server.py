
import asyncio
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
import uvicorn
from typing import Any
import json

class MattermostMCPServer:
    def __init__(self, bridge):
        self.bridge = bridge
        self.server = Server("mattermost-bridge-mcp")
        self.setup_handlers()

    def setup_handlers(self):
        self.server.list_tools()(self.list_tools)
        self.server.call_tool()(self.call_tool)

    async def list_tools(self):
        return [
            {
                "name": "send_message",
                "description": "Send a message to a Mattermost channel",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string"},
                        "message": {"type": "string"},
                        "root_id": {"type": "string", "description": "Optional thread root ID"}
                    },
                    "required": ["channel_id", "message"]
                }
            },
            {
                "name": "get_channels",
                "description": "Get list of available channels",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_thread_context",
                "description": "Fetch the full history of a thread",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "root_id": {"type": "string", "description": "The ID of the thread root post"}
                    },
                    "required": ["root_id"]
                }
            },
            {
                "name": "search_messages",
                "description": "Search for messages across Mattermost",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "terms": {"type": "string", "description": "Search terms"}
                    },
                    "required": ["terms"]
                }
            },
            {
                "name": "search_users",
                "description": "Search for users by name, username, or email",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string", "description": "Search term"}
                    },
                    "required": ["term"]
                }
            },
            {
                "name": "get_user_info",
                "description": "Get detailed profile information for a user",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"}
                    },
                    "required": ["user_id"]
                }
            },
            {
                "name": "send_direct_message",
                "description": "Send a direct message to one or more users",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "usernames": {"type": "array", "items": {"type": "string"}, "description": "List of usernames (with or without @)"},
                        "message": {"type": "string"}
                    },
                    "required": ["usernames", "message"]
                }
            }
        ]

    async def call_tool(self, name: str, arguments: Any):
        if name == "send_message":
            channel_id = arguments["channel_id"]
            message = arguments["message"]
            root_id = arguments.get("root_id")
            await self.bridge.api.create_post(channel_id, message, root_id=root_id)
            return [{"type": "text", "text": "Message sent successfully"}]
        
        elif name == "get_channels":
            await self.bridge._update_channel_cache()
            channels = self.bridge.channels_cache
            return [{"type": "text", "text": str(channels)}]

        elif name == "get_thread_context":
            root_id = arguments["root_id"]
            thread = await self.bridge.api.get_thread(root_id)
            if not thread or "posts" not in thread:
                return [{"type": "text", "text": "Thread not found or empty"}]
            
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
            
            return [{"type": "text", "text": "\n".join(formatted)}]

        elif name == "search_messages":
            terms = arguments["terms"]
            teams = await self.bridge.api.get_my_teams()
            if not teams:
                return [{"type": "text", "text": "No teams found to search in"}]
            
            all_results = []
            for team in teams:
                results = await self.bridge.api.search_posts(team["id"], terms)
                if results and "posts" in results:
                    all_results.extend(results["posts"].values())
            
            if not all_results:
                return [{"type": "text", "text": "No messages found"}]
            
            all_results.sort(key=lambda x: x["create_at"], reverse=True)
            formatted = []
            for p in all_results[:20]:
                formatted.append(f"[Post ID: {p['id']}] [Channel ID: {p['channel_id']}] {p['message']}")
            
            return [{"type": "text", "text": "\n---\n".join(formatted)}]

        elif name == "search_users":
            term = arguments["term"]
            users = await self.bridge.api.search_users(term)
            return [{"type": "text", "text": json.dumps(users, indent=2)}]

        elif name == "get_user_info":
            user_id = arguments["user_id"]
            user = await self.bridge.api.get_user(user_id)
            return [{"type": "text", "text": json.dumps(user, indent=2)}]

        elif name == "send_direct_message":
            usernames = arguments["usernames"]
            message = arguments["message"]
            
            user_ids = []
            for uname in usernames:
                clean_uname = uname[1:] if uname.startswith("@") else uname
                found = await self.bridge.api.search_users(clean_uname)
                if found:
                    exact = next((u for u in found if u["username"] == clean_uname), found[0])
                    user_ids.append(exact["id"])
            
            if not user_ids:
                return [{"type": "text", "text": "No valid users found to message"}]
            
            me = await self.bridge.api.get_me()
            if me and me["id"] not in user_ids:
                user_ids.append(me["id"])
            
            channel = await self.bridge.api.create_direct_channel(user_ids)
            if not channel:
                return [{"type": "text", "text": "Failed to create direct channel"}]
            
            await self.bridge.api.create_post(channel["id"], message)
            return [{"type": "text", "text": f"Direct message sent to channel {channel['id']}"}]
        
        raise ValueError(f"Unknown tool: {name}")

    async def run(self, host: str, port: int):
        app = Starlette(debug=True)
        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
                await self.server.run(read_stream, write_stream, self.server.create_initialization_options())

        app.mount("/mcp", Mount("/messages", handle_sse))
        app.add_route("/messages", sse.handle_post_message, methods=["POST"])

        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()