import asyncio
from mattermost_bridge import MattermostBridge
from config import default_config


async def run_bridge():
    bridge = MattermostBridge(config=default_config)
    await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        print("\nShutting down...")
