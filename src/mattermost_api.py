import asyncio
import json
import mimetypes
import os
import ssl
from typing import Any, Dict, Optional
import urllib.error
import urllib.request
import uuid

from config import default_config
from logging_config import get_logger

logger = get_logger(__name__)


class MattermostAPI:
    """Client for interacting with the Mattermost API."""

    def __init__(self, config=None):
        self.config = config or default_config
        self.base_url = (f"{self.config.mattermost_scheme}://"
                         f"{self.config.mattermost_url}:"
                         f"{self.config.mattermost_port}/api/v4")
        self.token = self.config.mattermost_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        self.timeout = getattr(self.config, "mattermost_http_timeout", 30)
        self.ssl_context = ssl.create_default_context()
        if not getattr(self.config, "mattermost_ssl_verify", False):
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE

    async def _request(
        self,
        path: str,
        data: Optional[Any] = None,
        method: str = "GET",
    ) -> Optional[Any]:
        """Makes an asynchronous request to the Mattermost API."""
        return await asyncio.to_thread(self._sync_request, path, data, method)

    def _sync_request(
        self,
        path: str,
        data: Optional[Any],
        method: str,
    ) -> Optional[Any]:
        """Makes a synchronous request to the Mattermost API."""
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url,
                                     data=body,
                                     headers=self.headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req,
                                        timeout=self.timeout,
                                        context=self.ssl_context) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            if e.code != 404:
                logger.error("MM API Error (%s %s): %s %s", method, path,
                             e.code, e.reason)
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("MM Request Error (%s %s): %s", method, path, e)
            return None

    async def _request_raw(self,
                           path: str,
                           method: str = "GET") -> Optional[bytes]:
        """Makes an async request to the Mattermost API and returns bytes."""
        return await asyncio.to_thread(self._sync_request_raw, path, method)

    def _sync_request_raw(self, path: str, method: str) -> Optional[bytes]:
        """Makes a sync request to the Mattermost API and returns bytes."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req,
                                        timeout=self.timeout,
                                        context=self.ssl_context) as response:
                return response.read()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("MM Raw Request Error (%s %s): %s", method, path, e)
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

    async def get_post(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single post by ID."""
        return await self._request(f"/posts/{post_id}")

    async def get_thread(
        self,
        post_id: str,
        per_page: int = 0,
        from_create_at: int = 0,
        direction: str = "up",
    ) -> Optional[Dict[str, Any]]:
        """Fetch a thread of posts."""
        query = f"perPage={per_page}&direction={direction}"
        if from_create_at:
            query += f"&fromCreateAt={from_create_at}"
        return await self._request(f"/posts/{post_id}/thread?{query}")

    async def search_posts(
        self,
        team_id: str,
        terms: str,
        page: int = 0,
        per_page: int = 60,
    ) -> Optional[Dict[str, Any]]:
        return await self._request(
            f"/teams/{team_id}/posts/search",
            data={
                "terms": terms,
                "page": page,
                "per_page": per_page,
            },
            method="POST",
        )

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
        file_ids: Optional[list] = None,
        props: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        data: Dict[str, Any] = {
            "channel_id": channel_id,
            "message": message,
            "root_id": root_id,
        }
        if file_ids:
            data["file_ids"] = file_ids
        if props:
            data["props"] = props
        return await self._request("/posts", data=data, method="POST")

    async def update_post(
        self,
        post_id: str,
        message: str,
        props: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        data: Dict[str, Any] = {"id": post_id, "message": message}
        if props:
            data["props"] = props
        return await self._request(f"/posts/{post_id}", data=data, method="PUT")

    async def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a file."""
        return await self._request(f"/files/{file_id}/info")

    async def download_file(self, file_id: str) -> Optional[bytes]:
        """Download a file's raw data."""
        return await self._request_raw(f"/files/{file_id}")

    async def upload_file(self, channel_id: str,
                          file_path: str) -> Optional[Dict[str, Any]]:
        """Upload a file to a channel."""
        return await asyncio.to_thread(self._sync_upload_file, channel_id,
                                       file_path)

    def _sync_upload_file(self, channel_id: str,
                          file_path: str) -> Optional[Dict[str, Any]]:
        """Synchronously upload a file to a channel."""
        if not os.path.exists(file_path):
            logger.error("File not found for upload: %s", file_path)
            return None

        filename = os.path.basename(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"

        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"

        with open(file_path, "rb") as f:
            file_content = f.read()

        parts = [
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="channel_id"',
            b"",
            channel_id.encode(),
            f"--{boundary}".encode(),
            (f'Content-Disposition: form-data; name="files"; '
             f'filename="{filename}"').encode(),
            f"Content-Type: {mime_type}".encode(),
            b"",
            file_content,
            f"--{boundary}--".encode(),
            b"",
        ]
        body = b"\r\n".join(parts)

        url = f"{self.base_url}/files"
        headers = self.headers.copy()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["Content-Length"] = str(len(body))

        req = urllib.request.Request(url,
                                     data=body,
                                     headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req,
                                        timeout=self.timeout,
                                        context=self.ssl_context) as response:
                return json.loads(response.read().decode())
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("MM Upload Error: %s", e)
            return None
