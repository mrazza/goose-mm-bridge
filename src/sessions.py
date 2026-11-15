import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import inspect
import time
from typing import Any, Callable, Dict, Optional

from acp.pool import AgentClientPool
from config import Config
from logging_config import get_logger
from utils import get_session_key

logger = get_logger(__name__)


@dataclass
class ThreadSession:
    """State for an active or staged Mattermost thread conversation."""

    session_key: str
    user_id: str
    root_id: str
    acp_session_id: Optional[str] = None
    linux_user: Optional[str] = None
    agent_name: Optional[str] = None
    model_name: Optional[str] = None
    processed_count: int = 0
    had_catchup_hint: bool = False
    last_active_at: float = 0.0


class SessionManager:
    """Owns thread sessions, concurrency locks, tasks, and LRU eviction."""

    def __init__(
        self,
        config: Config,
        agent_pool: AgentClientPool,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.agent_pool = agent_pool
        self._clock = clock
        self.sessions: OrderedDict[str, ThreadSession] = OrderedDict()
        self.locks: Dict[str, asyncio.Lock] = {}
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.thread_counters: OrderedDict[str, int] = OrderedDict()

    @property
    def max_thread_counters(self) -> int:
        return max(500, self.config.max_sessions * 5)

    def has_thread(self, root_id: str) -> bool:
        """Returns True if any active or staged session belongs to root_id."""
        return any(s.root_id == root_id for s in self.sessions.values())

    def get(self, session_key: str) -> Optional[ThreadSession]:
        """Returns the ThreadSession for session_key if present."""
        return self.sessions.get(session_key)

    def touch(self, session_key: str) -> Optional[ThreadSession]:
        """Marks a session as recently used for LRU ordering."""
        session = self.sessions.get(session_key)
        if session is not None:
            session.last_active_at = self._clock()
            self.sessions.move_to_end(session_key)
        return session

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Returns or creates the per-session serialization lock."""
        if session_key not in self.locks:
            self.locks[session_key] = asyncio.Lock()
        return self.locks[session_key]

    def register_active_task(self, session_key: str,
                             task: Optional[asyncio.Task]) -> None:
        """Records the currently running prompt task for a session."""
        if task is not None:
            self.active_tasks[session_key] = task

    def clear_active_task(self, session_key: str,
                          task: Optional[asyncio.Task]) -> None:
        """Removes the active task record if it matches the given task."""
        if self.active_tasks.get(session_key) == task:
            del self.active_tasks[session_key]

    async def stage_agent(
        self,
        user_id: str,
        root_id: str,
        agent_name: str,
    ) -> ThreadSession:
        """Closes any existing session for the thread and stages a new agent."""
        session_key = get_session_key(user_id, root_id)
        await self.close_session(session_key, cancel_task=True, await_rpc=False)
        session = ThreadSession(
            session_key=session_key,
            user_id=user_id,
            root_id=root_id,
            acp_session_id=None,
            linux_user=None,
            agent_name=agent_name,
            model_name=None,
            processed_count=0,
            had_catchup_hint=False,
            last_active_at=self._clock(),
        )
        self.sessions[session_key] = session
        self.sessions.move_to_end(session_key)
        return session

    def stage_model(
        self,
        user_id: str,
        root_id: str,
        model_name: str,
    ) -> ThreadSession:
        """Stages a model override on an existing or newly staged session."""
        session_key = get_session_key(user_id, root_id)
        session = self.sessions.get(session_key)
        if session is None:
            session = ThreadSession(
                session_key=session_key,
                user_id=user_id,
                root_id=root_id,
                model_name=model_name,
                last_active_at=self._clock(),
            )
            self.sessions[session_key] = session
        else:
            session.model_name = model_name
            session.last_active_at = self._clock()
        self.sessions.move_to_end(session_key)
        return session

    def activate(
        self,
        user_id: str,
        root_id: str,
        acp_session_id: str,
        linux_user: Optional[str],
        agent_name: str,
        model_name: Optional[str],
        had_catchup_hint: bool = True,
    ) -> ThreadSession:
        """Stores or replaces an active ACP session for the thread."""
        session_key = get_session_key(user_id, root_id)
        session = ThreadSession(
            session_key=session_key,
            user_id=user_id,
            root_id=root_id,
            acp_session_id=acp_session_id,
            linux_user=linux_user,
            agent_name=agent_name,
            model_name=model_name,
            processed_count=0,
            had_catchup_hint=had_catchup_hint,
            last_active_at=self._clock(),
        )
        self.sessions[session_key] = session
        self.sessions.move_to_end(session_key)
        return session

    def record_thread_post(self, root_id: str) -> None:
        """Increments the counter for a thread already being tracked."""
        if root_id in self.thread_counters:
            self.thread_counters[root_id] += 1
            self.thread_counters.move_to_end(root_id)

    async def ensure_thread_counter(
        self,
        root_id: str,
        api: Any,
        bot_id: Optional[str],
    ) -> int:
        """Primes the non-empty post count for a thread if not tracked yet."""
        if root_id in self.thread_counters:
            self.thread_counters.move_to_end(root_id)
            return self.thread_counters[root_id]

        thread_data = await api.get_thread(root_id)
        if thread_data and "posts" in thread_data:
            others_posts = [
                p for p in thread_data["posts"].values()
                if p.get("user_id") != bot_id and p.get("message", "").strip()
            ]
            count = len(others_posts)
        else:
            count = 1

        self.thread_counters[root_id] = count
        self.thread_counters.move_to_end(root_id)
        while len(self.thread_counters) > self.max_thread_counters:
            self.thread_counters.popitem(last=False)
        return count

    async def close_session(
        self,
        session_key: str,
        cancel_task: bool = True,
        await_rpc: bool = False,
        cancel_prompt_first: bool = False,
    ) -> bool:
        """Cancels tasks, drops locks/session state, and closes ACP session."""
        if cancel_task:
            task = self.active_tasks.get(session_key)
            if task is not None:
                if not hasattr(task, "done") or not task.done():
                    task.cancel()

        session = self.sessions.pop(session_key, None)
        self.locks.pop(session_key, None)
        if session is None:
            return False

        sid = session.acp_session_id
        if sid is None:
            return True

        client = self.agent_pool.find_client(session.linux_user,
                                             session.agent_name)
        if client is None:
            return True

        close_fn = getattr(client, "close_session", None)
        if callable(close_fn) and inspect.iscoroutinefunction(close_fn):
            await close_fn(sid,
                           await_rpc=await_rpc,
                           cancel_first=cancel_prompt_first)
            return True

        # Fallback for mock clients without ACPClient.close_session
        if cancel_prompt_first and hasattr(client, "cancel_prompt"):
            try:
                await client.cancel_prompt(sid)
            except Exception as e:  # pylint: disable=broad-exception-caught
                if self.config.debug:
                    logger.debug("Error cancelling prompt for %s: %s", sid, e)

        for attr in ("session_queues", "session_models", "context_usage"):
            mapping = getattr(client, attr, None)
            if isinstance(mapping, dict):
                mapping.pop(sid, None)

        if hasattr(client, "send_request"):
            coro = client.send_request("session/close", {"sessionId": sid})
            if await_rpc:
                try:
                    await asyncio.wait_for(coro, timeout=3.0)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    if self.config.debug:
                        logger.debug("Error closing session %s: %s", sid, e)
            else:
                asyncio.create_task(coro)

        return True

    async def prune_if_needed(self) -> int:
        """Evicts least-recently-used inactive sessions beyond MAX_SESSIONS."""
        if len(self.sessions) <= self.config.max_sessions:
            return 0

        prune_target = max(1, self.config.max_sessions // 5)
        pruned = 0
        for key in list(self.sessions.keys()):
            if pruned >= prune_target:
                break
            task = self.active_tasks.get(key)
            if task is not None and (not hasattr(task, "done") or
                                     not task.done()):
                continue
            if self.config.debug:
                session = self.sessions.get(key)
                sid = session.acp_session_id if session else None
                logger.debug("Pruning LRU session for %s (%s)", key, sid)
            await self.close_session(key, cancel_task=False, await_rpc=False)
            pruned += 1
        return pruned

    async def close_all(self) -> None:
        """Cancels active prompt tasks and gracefully closes all sessions."""
        for task in list(self.active_tasks.values()):
            if task is not None and (not hasattr(task, "done") or
                                     not task.done()):
                task.cancel()

        keys = list(self.sessions.keys())
        if keys:
            await asyncio.gather(
                *(self.close_session(
                    key,
                    cancel_task=False,
                    await_rpc=True,
                    cancel_prompt_first=True,
                ) for key in keys),
                return_exceptions=True,
            )

        self.sessions.clear()
        self.locks.clear()
