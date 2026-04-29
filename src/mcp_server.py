
import asyncio
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
import uvicorn
from typing import Any

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