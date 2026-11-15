from dataclasses import dataclass
import json
import os
import pwd
import tempfile
import time
from typing import Any, Callable, Dict, Optional, Tuple

from dotenv import dotenv_values

from config import apply_user_env
from config import Config
from config import project_root
from logging_config import get_logger
from utils import load_user_mapping

logger = get_logger(__name__)


@dataclass(frozen=True)
class ActorIdentity:
    """Resolved Mattermost caller identity and permissions."""

    user_id: str
    username: str
    is_admin: bool
    is_approved: bool


@dataclass(frozen=True)
class EffectiveIdentity:
    """Resolved execution identity after optional impersonation."""

    user_id: str
    username: str
    linux_user: Optional[str]
    user_config: Config
    is_impersonated: bool = False


class UserStore:
    """Manages users, mappings, configs, preferences, and impersonation."""

    def __init__(
        self,
        config: Config,
        api: Any,
        clock: Callable[[], float] = time.monotonic,
        user_cache_ttl: float = 60.0,
    ):
        self.config = config
        self.api = api
        self._clock = clock
        self.user_cache_ttl = user_cache_ttl
        self.impersonations: Dict[str, Dict[str, str]] = {}

        self._user_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._mapping_cache: Optional[Tuple[str, float, Dict[str, str]]] = None
        self._prefs_cache: Optional[Tuple[str, float, Dict[str, str]]] = None
        self._user_env_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    @staticmethod
    def _resolve_path(path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(project_root, path)

    def get_impersonation(self, admin_id: str) -> Optional[Dict[str, str]]:
        """Returns active impersonation target dict for an admin if any."""
        return self.impersonations.get(admin_id)

    def set_impersonation(self, admin_id: str, target_id: str,
                          target_username: str) -> None:
        """Sets impersonation target for an admin user."""
        self.impersonations[admin_id] = {
            "id": target_id,
            "username": target_username,
        }

    def clear_impersonation(self, admin_id: str) -> Optional[Dict[str, str]]:
        """Clears impersonation state for an admin user."""
        return self.impersonations.pop(admin_id, None)

    async def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetches Mattermost user metadata with short-TTL caching."""
        now = self._clock()
        if self.user_cache_ttl > 0 and user_id in self._user_cache:
            cached_at, cached_user = self._user_cache[user_id]
            if (now - cached_at) <= self.user_cache_ttl:
                return cached_user

        user_info = await self.api.get_user(user_id)
        if user_info and self.user_cache_ttl > 0:
            self._user_cache[user_id] = (now, user_info)
        return user_info

    async def get_username(self, user_id: str) -> str:
        """Returns Mattermost username for user_id, defaulting to 'unknown'."""
        user_info = await self.get_user_info(user_id)
        if user_info and user_info.get("username"):
            return user_info["username"]
        return "unknown"

    async def is_admin(self,
                       user_id: str,
                       username: Optional[str] = None) -> bool:
        """Returns True if user_id or username is configured in ADMIN_USERS."""
        if not self.config.admin_users:
            return False
        if user_id in self.config.admin_users:
            return True
        if username is None:
            user_info = await self.get_user_info(user_id)
            username = user_info.get("username") if user_info else None
        return bool(username and username in self.config.admin_users)

    async def is_approved(
        self,
        user_id: str,
        username: Optional[str] = None,
        is_admin: Optional[bool] = None,
    ) -> bool:
        """Returns True if user is authorized by APPROVED_USERS/ADMIN_USERS."""
        if not self.config.approved_users:
            return True
        if user_id in self.config.approved_users:
            return True
        if is_admin is True:
            return True
        if username is None:
            user_info = await self.get_user_info(user_id)
            username = user_info.get("username") if user_info else None
        if username and username in self.config.approved_users:
            return True
        if is_admin is None:
            return await self.is_admin(user_id, username=username)
        return False

    def get_user_mapping(self) -> Dict[str, str]:
        """Loads user_mapping.json using file mtime caching."""
        mapping_path = self.config.user_mapping_file
        if not mapping_path or not os.path.exists(mapping_path):
            self._mapping_cache = None
            return {}

        try:
            mtime = os.path.getmtime(mapping_path)
        except OSError:
            self._mapping_cache = None
            return {}

        if (self._mapping_cache and self._mapping_cache[0] == mapping_path and
                self._mapping_cache[1] == mtime):
            return self._mapping_cache[2]

        data = load_user_mapping(mapping_path)
        self._mapping_cache = (mapping_path, mtime, data)
        return data

    def resolve_linux_user(
        self,
        user_id: str,
        username: Optional[str] = None,
    ) -> Optional[str]:
        """Maps a Mattermost user_id or username to a local Linux account."""
        mapping = self.get_user_mapping()
        return mapping.get(user_id) or (mapping.get(username)
                                        if username else None)

    def resolve_prewarm_linux_user(self, user_key: str) -> Optional[str]:
        """Resolves a pre-warm configuration key to a Linux username."""
        mapping = self.get_user_mapping()
        linux_user = None
        if mapping:
            linux_user = mapping.get(user_key)
            if not linux_user and user_key in mapping.values():
                linux_user = user_key

        if not linux_user and not self.config.require_user_mapping:
            try:
                pwd.getpwnam(user_key)
                linux_user = user_key
            except KeyError:
                linux_user = None

        return linux_user

    def get_user_config(self, linux_user: Optional[str]) -> Config:
        """Returns Config overridden with user_configs/{linux_user}.env."""
        if not linux_user:
            return self.config

        user_configs_dir = self._resolve_path(self.config.user_configs_dir)
        config_path = os.path.join(user_configs_dir, f"{linux_user}.env")
        if not os.path.exists(config_path):
            self._user_env_cache.pop(config_path, None)
            return self.config

        try:
            mtime = os.path.getmtime(config_path)
        except OSError:
            return self.config

        cached = self._user_env_cache.get(config_path)
        if cached and cached[0] == mtime:
            user_env = cached[1]
        else:
            try:
                user_env = dict(dotenv_values(config_path))
                self._user_env_cache[config_path] = (mtime, user_env)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error loading user config from %s: %s",
                             config_path, e)
                return self.config

        return apply_user_env(self.config, user_env)

    def _load_preferences(self) -> Dict[str, str]:
        prefs_path = self._resolve_path(self.config.user_agent_preferences_file)
        if not os.path.exists(prefs_path):
            self._prefs_cache = None
            return {}

        try:
            mtime = os.path.getmtime(prefs_path)
        except OSError:
            self._prefs_cache = None
            return {}

        if (self._prefs_cache and self._prefs_cache[0] == prefs_path and
                self._prefs_cache[1] == mtime):
            return dict(self._prefs_cache[2])

        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
                if not isinstance(prefs, dict):
                    prefs = {}
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error loading user agent preferences: %s", e)
            prefs = {}

        self._prefs_cache = (prefs_path, mtime, prefs)
        return dict(prefs)

    def get_default_agent(self, user_id: str) -> str:
        """Retrieves the persistent default agent for a user."""
        prefs = self._load_preferences()
        return prefs.get(user_id, self.config.default_agent)

    def set_default_agent(self, user_id: str, agent_name: str) -> None:
        """Atomically persists the default agent preference for a user."""
        prefs_path = self._resolve_path(self.config.user_agent_preferences_file)
        prefs = self._load_preferences()
        prefs[user_id] = agent_name

        parent_dir = os.path.dirname(os.path.abspath(prefs_path))
        os.makedirs(parent_dir, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=parent_dir,
                    prefix=".prefs_",
                    suffix=".tmp",
                    delete=False,
            ) as tmp:
                tmp_path = tmp.name
                json.dump(prefs, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, prefs_path)
            mtime = os.path.getmtime(prefs_path)
            self._prefs_cache = (prefs_path, mtime, dict(prefs))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error saving user agent preferences: %s", e)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    async def resolve_identities(
        self,
        sender_id: str,
        resolve_impersonation: bool = True,
    ) -> Tuple[ActorIdentity, EffectiveIdentity]:
        """Resolves real actor permissions and effective execution context."""
        user_info = await self.get_user_info(sender_id)
        fetched_username = (user_info.get("username") if user_info and
                            user_info.get("username") else None)
        actor_username = fetched_username or "unknown"

        admin = await self.is_admin(sender_id, username=fetched_username or "")
        approved = await self.is_approved(sender_id,
                                          username=fetched_username or "",
                                          is_admin=admin)

        actor = ActorIdentity(
            user_id=sender_id,
            username=actor_username,
            is_admin=admin,
            is_approved=approved,
        )

        imp = self.impersonations.get(sender_id)
        if resolve_impersonation and imp:
            eff_id = imp["id"]
            eff_username = imp["username"]
            is_impersonated = True
        else:
            eff_id = sender_id
            eff_username = actor_username
            is_impersonated = False

        linux_user = self.resolve_linux_user(eff_id, eff_username)
        user_config = self.get_user_config(linux_user)
        effective = EffectiveIdentity(
            user_id=eff_id,
            username=eff_username,
            linux_user=linux_user,
            user_config=user_config,
            is_impersonated=is_impersonated,
        )
        return actor, effective
