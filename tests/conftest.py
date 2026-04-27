import pytest
import asyncio
from unittest.mock import MagicMock

@pytest.fixture
def mock_mm_api():
    return MagicMock()

@pytest.fixture
def mock_goose_client():
    return MagicMock()
