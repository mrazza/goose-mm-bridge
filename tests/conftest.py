import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_mm_api():
    return MagicMock()


@pytest.fixture
def mock_goose_client():
    return MagicMock()
