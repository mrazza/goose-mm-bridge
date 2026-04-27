import asyncio
from mattermost_bridge import MattermostBridge


async def run_bridge():
    bridge = MattermostBridge()
    await bridge.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        print("\nShutting down...")
