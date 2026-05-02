import asyncio

from config import default_config
from mattermost_bridge import MattermostBridge
from mcp_server import MattermostMCPServer


async def run_bridge():
    bridge = MattermostBridge(config=default_config)

    tasks = [bridge.run()]

    if default_config.mcp_enabled:
        mcp = MattermostMCPServer(bridge)
        tasks.append(mcp.run(default_config.mcp_host, default_config.mcp_port))
        print(
            f"Starting MCP server on {default_config.mcp_host}:{default_config.mcp_port}"
        )

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        print("\nShutting down...")
