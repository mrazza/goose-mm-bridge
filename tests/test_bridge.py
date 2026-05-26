import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from bridge import run_bridge
from config import default_config


@pytest.mark.asyncio
async def test_run_bridge_mcp_disabled():
    with patch("bridge.MattermostBridge") as mock_bridge_cls:
        mock_bridge = MagicMock()
        mock_bridge.run = AsyncMock()
        mock_bridge_cls.return_value = mock_bridge

        with patch.object(default_config, "mcp_enabled", False):
            await run_bridge()

            mock_bridge_cls.assert_called_once_with(config=default_config)
            mock_bridge.run.assert_called_once()


@pytest.mark.asyncio
async def test_run_bridge_mcp_enabled():
    with patch("bridge.MattermostBridge") as mock_bridge_cls:
        mock_bridge = MagicMock()
        mock_bridge.run = AsyncMock()
        mock_bridge_cls.return_value = mock_bridge

        with patch("bridge.MattermostMCPServer") as mock_mcp_cls:
            mock_mcp = MagicMock()
            mock_mcp.run = AsyncMock()
            mock_mcp_cls.return_value = mock_mcp

            with patch.object(default_config, "mcp_enabled", True):
                with patch.object(default_config, "mcp_host", "localhost"):
                    with patch.object(default_config, "mcp_port", 5000):
                        await run_bridge()

                        mock_bridge_cls.assert_called_once_with(config=default_config)
                        mock_bridge.run.assert_called_once()
                        mock_mcp_cls.assert_called_once_with(mock_bridge)
                        mock_mcp.run.assert_called_once_with("localhost", 5000)
