import json
import os
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from config import Config
from user_store import UserStore


@pytest.mark.asyncio
async def test_user_mapping_mtime_cache_and_edge_cases(tmp_path):
    mapping_path = tmp_path / "user_mapping.json"
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        user_mapping_file=str(mapping_path),
        user_agent_preferences_file=str(tmp_path / "prefs.json"),
    )
    store = UserStore(config, MagicMock())

    # Missing file returns empty mapping
    assert store.get_user_mapping() == {}
    assert store.resolve_linux_user("u1", "alice") is None

    # Write valid mapping and bump explicit mtime
    mapping_path.write_text(json.dumps({"u1": "linux_alice"}))
    os.utime(mapping_path, (1000.0, 1000.0))

    with patch("user_store.load_user_mapping",
               return_value={"u1": "linux_alice"}) as mock_load:
        assert store.get_user_mapping() == {"u1": "linux_alice"}
        assert store.get_user_mapping() == {"u1": "linux_alice"}
        assert mock_load.call_count == 1  # cached on unchanged mtime

        # Update file and bump mtime -> reloads automatically
        os.utime(mapping_path, (2000.0, 2000.0))
        mock_load.return_value = {"u1": "linux_alice", "bob": "linux_bob"}
        reloaded = store.get_user_mapping()
        assert reloaded == {"u1": "linux_alice", "bob": "linux_bob"}
        assert mock_load.call_count == 2

    assert store.resolve_linux_user("u1", "alice") == "linux_alice"
    assert store.resolve_linux_user("u2", "bob") == "linux_bob"


def test_user_config_mtime_cache_reloads_on_change(tmp_path):
    user_dir = tmp_path / "user_configs"
    user_dir.mkdir()
    env_file = user_dir / "linux_alice.env"
    env_file.write_text("GOOSE_MODEL=model-v1\nPOLL_INTERVAL=5\n")
    os.utime(env_file, (1000.0, 1000.0))

    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        user_configs_dir=str(user_dir),
    )
    store = UserStore(config, MagicMock())

    cfg1 = store.get_user_config("linux_alice")
    assert cfg1.goose_model == "model-v1"
    assert cfg1.poll_interval == 5

    # Modify file with new mtime -> reloaded
    env_file.write_text("GOOSE_MODEL=model-v2\nALLOW_MODEL_CONTROL=false\n")
    os.utime(env_file, (2000.0, 2000.0))
    cfg2 = store.get_user_config("linux_alice")
    assert cfg2.goose_model == "model-v2"
    assert cfg2.allow_model_control is False

    # Missing user returns base config
    assert store.get_user_config("unknown_user") is config


def test_atomic_default_agent_preferences(tmp_path):
    prefs_path = tmp_path / "nested" / "prefs.json"
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        default_agent="goose",
        user_agent_preferences_file=str(prefs_path),
    )
    store = UserStore(config, MagicMock())
    assert store.get_default_agent("u1") == "goose"

    store.set_default_agent("u1", "hermes")
    assert json.loads(prefs_path.read_text()) == {"u1": "hermes"}
    assert store.get_default_agent("u1") == "hermes"

    # Fresh UserStore reads persisted preference from disk
    store2 = UserStore(config, MagicMock())
    assert store2.get_default_agent("u1") == "hermes"


@pytest.mark.asyncio
async def test_user_info_ttl_cache_and_permissions(tmp_path):
    now = [100.0]
    api = MagicMock()
    api.get_user = AsyncMock(return_value={"id": "u1", "username": "alice"})
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        approved_users=["bob"],
        admin_users=["alice"],
    )
    store = UserStore(
        config,
        api,
        clock=lambda: now[0],
        user_cache_ttl=10.0,
    )

    assert await store.get_username("u1") == "alice"
    assert await store.get_username("u1") == "alice"
    assert api.get_user.await_count == 1

    # Advance past TTL -> refetches
    now[0] += 11.0
    assert await store.get_username("u1") == "alice"
    assert api.get_user.await_count == 2

    # Admin checks and automatic approval of admins
    assert await store.is_admin("u1") is True
    assert await store.is_approved("u1") is True
    assert await store.is_admin("u_other", username="stranger") is False
    assert await store.is_approved("u_other", username="stranger") is False
    assert await store.is_approved("bob") is True


@pytest.mark.asyncio
async def test_resolve_identities_with_and_without_impersonation(tmp_path):
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps({
            "admin_id": "linux_admin",
            "target_id": "linux_target"
        }))
    user_dir = tmp_path / "user_configs"
    user_dir.mkdir()
    (user_dir / "linux_target.env").write_text("GOOSE_MODEL=target-model\n")

    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        admin_users=["admin"],
        approved_users=["admin"],
        user_mapping_file=str(mapping_path),
        user_configs_dir=str(user_dir),
    )
    api = MagicMock()
    users = {
        "admin_id": {
            "id": "admin_id",
            "username": "admin"
        },
        "target_id": {
            "id": "target_id",
            "username": "target_user"
        },
    }
    api.get_user = AsyncMock(side_effect=lambda uid: users.get(uid))
    store = UserStore(config, api)
    store.set_impersonation("admin_id", "target_id", "target_user")

    actor, eff = await store.resolve_identities("admin_id",
                                                resolve_impersonation=True)
    assert actor.user_id == "admin_id"
    assert actor.is_admin is True
    assert eff.is_impersonated is True
    assert eff.user_id == "target_id"
    assert eff.username == "target_user"
    assert eff.linux_user == "linux_target"
    assert eff.user_config.goose_model == "target-model"

    actor2, eff2 = await store.resolve_identities("admin_id",
                                                  resolve_impersonation=False)
    assert actor2.is_admin is True
    assert eff2.is_impersonated is False
    assert eff2.user_id == "admin_id"
    assert eff2.linux_user == "linux_admin"
