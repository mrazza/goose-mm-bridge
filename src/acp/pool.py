import asyncio
import inspect
from typing import Any, Callable, Dict, Optional, Tuple

from acp.client import ACPClient
from acp.launcher import terminate_process
from logging_config import get_logger
from utils import load_prewarm_config

logger = get_logger(__name__)


class AgentClientPool:
    """Manages ACPClient instances keyed by (linux_user, agent_name)."""

    def __init__(
        self,
        config,
        client_factory: Optional[Callable[[Optional[str], str], Any]] = None,
    ):
        self.config = config
        self.client_factory = client_factory or (
            lambda user, agent: ACPClient(user, agent, config=self.config))
        self.clients: Dict[Tuple[Optional[str], str], Any] = {}

    def get(
        self,
        linux_user: Optional[str],
        agent_name: str,
    ) -> Optional[Any]:
        """Returns existing client for (linux_user, agent_name) if present."""
        return self.clients.get((linux_user, agent_name))

    def find_client(
        self,
        linux_user: Optional[str],
        agent_name: Optional[str] = None,
    ) -> Optional[Any]:
        """Finds client for (linux_user, agent_name) with default fallback."""
        resolved_agent = agent_name or self.config.default_agent
        client = self.clients.get((linux_user, resolved_agent))
        if client is None and linux_user is not None:
            client = self.clients.get((None, resolved_agent))
        return client

    def get_or_create(self, linux_user: Optional[str], agent_name: str) -> Any:
        """Returns existing client or creates one via client_factory."""
        key = (linux_user, agent_name)
        if key not in self.clients:
            self.clients[key] = self.client_factory(linux_user, agent_name)
        return self.clients[key]

    async def prewarm(self, user_store) -> None:
        """Pre-warms configured agent ACP processes on startup."""
        prewarm_config = load_prewarm_config(
            self.config.prewarm_config_file,
            self.config.prewarm_users,
        )
        if not prewarm_config:
            return

        logger.info("Pre-warming configured agent ACP processes...")

        for user_key, agent_specs in prewarm_config.items():
            linux_user = user_store.resolve_prewarm_linux_user(user_key)
            if self.config.require_user_mapping and not linux_user:
                logger.warning(
                    "Skipping pre-warm for '%s': No Linux user mapping and "
                    "REQUIRE_USER_MAPPING=true",
                    user_key,
                )
                continue

            for agent_spec in agent_specs:
                if agent_spec == "default":
                    agent_name = user_store.get_default_agent(user_key)
                else:
                    agent_name = agent_spec

                if self.config.agents and agent_name not in self.config.agents:
                    logger.warning(
                        "Unknown agent '%s' for pre-warm user '%s', falling "
                        "back to default agent '%s'",
                        agent_name,
                        user_key,
                        self.config.default_agent,
                    )
                    agent_name = self.config.default_agent

                try:
                    client = self.get_or_create(linux_user, agent_name)
                    logger.info(
                        "Pre-warming agent '%s' for user '%s' "
                        "(linux_user=%s)...",
                        agent_name,
                        user_key,
                        linux_user,
                    )
                    await client.ensure_running()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error(
                        "Failed to pre-warm agent '%s' for user '%s': %s",
                        agent_name,
                        user_key,
                        e,
                    )

    async def terminate_all(self, timeout: float = 3.0) -> None:
        """Terminates all managed ACP client subprocesses in parallel."""

        async def _terminate_one(client: Any) -> None:
            terminate_fn = getattr(client, "terminate", None)
            if callable(terminate_fn) and inspect.iscoroutinefunction(
                    terminate_fn):
                await terminate_fn(timeout=timeout)
                return

            if getattr(client,
                       "session_queues", None) is not None and isinstance(
                           client.session_queues, dict):
                client.session_queues.clear()
            if getattr(client,
                       "session_models", None) is not None and isinstance(
                           client.session_models, dict):
                client.session_models.clear()
            if getattr(client,
                       "context_usage", None) is not None and isinstance(
                           client.context_usage, dict):
                client.context_usage.clear()

            proc = getattr(client, "process", None)
            await terminate_process(proc,
                                    timeout=timeout,
                                    debug=self.config.debug)

        tasks = [
            _terminate_one(client) for client in list(self.clients.values())
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
