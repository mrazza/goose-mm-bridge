import os
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from config import ENV_SPECS


@pytest.fixture(autouse=True)
def clean_bridge_env():
    """Isolates tests from ambient .env or host bridge environment vars."""
    with patch.dict(os.environ):
        for spec in ENV_SPECS:
            for env_var in spec.env_vars:
                os.environ.pop(env_var, None)
        yield


@pytest.fixture
def mock_mm_api():
    return MagicMock()


@pytest.fixture
def mock_goose_client():
    return MagicMock()
