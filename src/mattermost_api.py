import asyncio
from datetime import datetime
import json
import ssl
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

from config import default_config


class MattermostAPI:
    """Client for interacting with the Mattermost API."""

    def __init__(self, config=None):
        self.config = config or default_config
        self.base_url = f"{self.config.mattermost_scheme}://{self.config.mattermost_url}:{self.config.mattermost_port}/api/v4"
        self.token = self.config.mattermost_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        # Create SSL context that ignores cert issues if needed
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def _request(self,
                       path: str,
                       data: Optional[Dict[str, Any]] = None,
                       method: str = "GET") -> Optional[Dict[str, Any]]:
        """Makes an asynchronous request to the Mattermost API."""
        return await asyncio.to_thread(self._sync_request, path, data, method)

    def _sync_request(self, path: str, data: Optional[Dict[str, Any]],
                      method: str) -> Optional[Dict[str, Any]]:
        """Makes a synchronous request to the Mattermost API."""
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url,
                                     data=body,
                                     headers=self.headers,
                                     method=method)
        try:
            # Using our custom SSL context for all requests
            with urllib.request.urlopen(req,
                                        context=self.ssl_context) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(
                    f"[{datetime.now()}] MM API Error ({method} {path}): {e.code} {e.reason}"
                )
            return None
        except Exception as e:
            print(f"[{datetime.now()}] MM Request Error ({method} {path}): {e}")
            return None

    async def get_me(self) -> Optional[Dict[str, Any]]:
        return await self._request("/users/me")

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self._request(f"/users/{user_id}")

    async def get_direct_channels(self) -> Optional[list]:
        return await self._request("/users/me/channels")

    async def get_my_teams(self) -> Optional[list]:
        return await self._request("/users/me/teams")

    async def get_my_channels(self, team_id: str) -> Optional[list]:
        return await self._request(f"/users/me/teams/{team_id}/channels")

    async def get_channel_posts(self, channel_id: str,
                                since: int) -> Optional[Dict[str, Any]]:
        return await self._request(f"/channels/{channel_id}/posts?since={since}"
                                  )

    async def get_thread(self,
                         post_id: str,
                         per_page: int = 0,
                         from_create_at: int = 0,
                         direction: str = "up") -> Optional[Dict[str, Any]]:
        """Fetch a thread of posts.
        
        Args:
            post_id: The ID of any post in the thread.
            per_page: Number of posts to return (0 for server default, usually 60).
            from_create_at: The timestamp to return the next page of posts from.
            direction: The direction to return the posts. Either up or down.
        """
        query = f"perPage={per_page}&direction={direction}"
        if from_create_at:
            query += f"&fromCreateAt={from_create_at}"
        return await self._request(f"/posts/{post_id}/thread?{query}")

    async def search_posts(self, team_id: str,
                           terms: str) -> Optional[Dict[str, Any]]:
        return await self._request(f"/teams/{team_id}/posts/search",
                                   data={"terms": terms},
                                   method="POST")

    async def search_users(self, term: str) -> Optional[list]:
        return await self._request("/users/search",
                                   data={"term": term},
                                   method="POST")

    async def create_direct_channel(self,
                                    user_ids: list) -> Optional[Dict[str, Any]]:
        return await self._request("/channels/direct",
                                   data=user_ids,
                                   method="POST")

    async def create_post(
            self,
            channel_id: str,
            message: str,
            root_id: Optional[str] = None,
            props: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        data = {
            "channel_id": channel_id,
            "message": message,
            "root_id": root_id
        }
        if props:
            data["props"] = props
        return await self._request("/posts", data=data, method="POST")

    async def update_post(
            self,
            post_id: str,
            message: str,
            props: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        data = {"id": post_id, "message": message}
        if props:
            data["props"] = props
        return await self._request(f"/posts/{post_id}", data=data, method="PUT")
