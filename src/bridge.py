import asyncio

from config import default_config
from logging_config import configure_logging
from logging_config import get_logger
from mattermost_bridge import MattermostBridge
from mcp_server import MattermostMCPServer

logger = get_logger(__name__)


async def run_bridge():
    configure_logging(default_config.debug)
    bridge = MattermostBridge(config=default_config)

    tasks = [bridge.run()]

    if default_config.mcp_enabled:
        mcp = MattermostMCPServer(bridge.api, bridge.channel_cache)
        tasks.append(mcp.run(default_config.mcp_host, default_config.mcp_port))
        logger.info("Starting MCP server on %s:%s", default_config.mcp_host,
                    default_config.mcp_port)

    await asyncio.gather(*tasks)


if __name__ == "__main__":  # pragma: no cover
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
