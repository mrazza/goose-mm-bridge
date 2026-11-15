import json
import os
from typing import Any, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

from channel_cache import ChannelCache


class MattermostMCPServer:
    """FastMCP server exposing Mattermost capabilities as tools for agents."""

    def __init__(
        self,
        api: Any,
        channel_cache: Optional[ChannelCache] = None,
    ):
        self.api = api
        self.channel_cache = (channel_cache if channel_cache is not None else
                              ChannelCache(self.api))

        self.mcp = FastMCP("mattermost-bridge-mcp")
        self.setup_tools()

    async def _upload_optional_file(
        self,
        channel_id: str,
        file_path: Optional[str],
    ) -> Tuple[Optional[List[str]], Optional[str]]:
        """Uploads a file if provided, returning (file_ids, error_message)."""
        if not file_path:
            return None, None
        upload_result = await self.api.upload_file(channel_id, file_path)
        if (upload_result and "file_infos" in upload_result and
                upload_result["file_infos"]):
            file_ids = [info["id"] for info in upload_result["file_infos"]]
            return file_ids, None
        return None, f"Failed to upload attachment: {file_path}"

    async def _send_message(
        self,
        channel_id: str,
        message: str,
        root_id: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> str:
        file_ids, err = await self._upload_optional_file(channel_id, file_path)
        if err:
            return err

        await self.api.create_post(channel_id,
                                   message,
                                   root_id=root_id,
                                   file_ids=file_ids)
        suffix = " with attachment" if file_ids else ""
        return f"Message sent successfully{suffix}"

    async def _get_channels(self) -> str:
        channels = await self.channel_cache.get_channels()
        return json.dumps(channels, indent=2)

    async def _get_thread_context(
        self,
        root_id: str,
        limit: int = 0,
        page: int = 0,
    ) -> str:
        all_posts_dict = {}
        from_create_at = 0
        per_page = limit if limit > 0 else 0

        while True:
            thread = await self.api.get_thread(
                root_id,
                per_page=per_page,
                from_create_at=from_create_at,
                direction="up",
            )
            if not thread or "posts" not in thread or not thread["posts"]:
                break

            all_posts_dict.update(thread["posts"])

            if not thread.get("has_next", False):
                break

            if limit > 0 and len(all_posts_dict) >= (page + 1) * limit + 1:
                break

            order = thread.get("order", [])
            if not order:
                break
            last_post_id = order[-1]
            from_create_at = thread["posts"][last_post_id]["create_at"]

        if not all_posts_dict:
            return "Thread not found or empty"

        posts = sorted(all_posts_dict.values(), key=lambda x: x["create_at"])

        if limit > 0:
            start = len(posts) - (page + 1) * limit
            end = len(posts) - page * limit
            if end <= 0:
                return "No more messages"
            posts = posts[max(0, start):end]

        if not posts:
            return "No messages found in the specified range"

        user_ids = list({p["user_id"] for p in posts})
        user_map = {}
        for uid in user_ids:
            uinfo = await self.api.get_user(uid)
            user_map[uid] = (uinfo.get("username", "unknown")
                             if uinfo else "unknown")

        formatted = []
        for p in posts:
            username = user_map.get(p["user_id"], "unknown")
            msg = p.get("message", "")
            file_ids = p.get("file_ids", [])
            if file_ids:
                msg = f"[Has {len(file_ids)} attachment(s)] {msg}"
            formatted.append(f"[Sender: @{username}] {msg}")

        return "\n".join(formatted)

    async def _search_messages(
        self,
        terms: str,
        page: int = 0,
        per_page: int = 60,
    ) -> str:
        teams = await self.api.get_my_teams()
        if not teams:
            return "No teams found to search in"

        all_results = []
        for team in teams:
            results = await self.api.search_posts(team["id"],
                                                  terms,
                                                  page=page,
                                                  per_page=per_page)
            if results and "posts" in results:
                all_results.extend(results["posts"].values())

        if not all_results:
            return "No messages found"

        all_results.sort(key=lambda x: x["create_at"], reverse=True)
        formatted = [(f"[Post ID: {p['id']}] [Channel ID: {p['channel_id']}] "
                      f"{p['message']}") for p in all_results[:per_page]]
        return "\n---\n".join(formatted)

    async def _search_users(self, term: str) -> str:
        users = await self.api.search_users(term)
        return json.dumps(users, indent=2)

    async def _get_user_info(self, user_id: str) -> str:
        user = await self.api.get_user(user_id)
        return json.dumps(user, indent=2)

    async def _send_direct_message(
        self,
        usernames: List[str],
        message: str,
        file_path: Optional[str] = None,
    ) -> str:
        user_ids = []
        for uname in usernames:
            clean_uname = uname[1:] if uname.startswith("@") else uname
            found = await self.api.search_users(clean_uname)
            if found:
                exact = next(
                    (u for u in found if u["username"] == clean_uname),
                    found[0],
                )
                user_ids.append(exact["id"])

        if not user_ids:
            return "No valid users found to message"

        me = await self.api.get_me()
        if me and me["id"] not in user_ids:
            user_ids.append(me["id"])

        channel = await self.api.create_direct_channel(user_ids)
        if not channel:
            return "Failed to create direct channel"

        file_ids, err = await self._upload_optional_file(
            channel["id"], file_path)
        if err:
            return err

        await self.api.create_post(channel["id"], message, file_ids=file_ids)
        suffix = " with attachment" if file_ids else ""
        return f"Direct message sent to channel {channel['id']}{suffix}"

    async def _get_post_details(self, post_id: str) -> str:
        post = await self.api.get_post(post_id)
        if not post:
            return f"Post {post_id} not found"
        return json.dumps(post, indent=2)

    async def _list_post_attachments(self, post_id: str) -> str:
        post = await self.api.get_post(post_id)
        if not post or "file_ids" not in post or not post["file_ids"]:
            return "No attachments found for this post"

        attachments = []
        for fid in post["file_ids"]:
            info = await self.api.get_file_info(fid)
            if info:
                attachments.append({
                    "id": fid,
                    "name": info.get("name", "unknown"),
                    "extension": info.get("extension", ""),
                    "size": info.get("size", 0),
                })
            else:
                attachments.append({"id": fid, "name": "unknown"})

        return json.dumps(attachments, indent=2)

    async def _download_attachment(self, file_id: str,
                                   destination_path: str) -> str:
        data = await self.api.download_file(file_id)
        if not data:
            return "Failed to download file"

        try:
            os.makedirs(os.path.dirname(os.path.abspath(destination_path)),
                        exist_ok=True)
            with open(destination_path, "wb") as f:
                f.write(data)
            return f"File downloaded successfully to {destination_path}"
        except Exception as e:  # pylint: disable=broad-exception-caught
            return f"Error saving file: {str(e)}"

    def setup_tools(self):
        """Registers Mattermost tools with the FastMCP server."""

        @self.mcp.tool()
        async def send_message(
            channel_id: str,
            message: str,
            root_id: Optional[str] = None,
            file_path: Optional[str] = None,
        ) -> str:
            """Send a message to a Mattermost channel.

            Args:
                channel_id: The ID of the channel to send the message to.
                message: The message text.
                root_id: Optional thread root ID to reply to a specific thread.
                file_path: Optional local path to a file to attach.
            """
            return await self._send_message(channel_id, message, root_id,
                                            file_path)

        @self.mcp.tool()
        async def get_channels() -> str:
            """Get list of available channels."""
            return await self._get_channels()

        @self.mcp.tool()
        async def get_thread_context(
            root_id: str,
            limit: int = 0,
            page: int = 0,
        ) -> str:
            """Fetch the history of a thread starting from the most recent post.

            Args:
                root_id: The ID of the thread root post.
                limit: Optional limit on the number of messages (0 for all).
                page: Optional page number when using limit.
            """
            return await self._get_thread_context(root_id, limit, page)

        @self.mcp.tool()
        async def search_messages(
            terms: str,
            page: int = 0,
            per_page: int = 60,
        ) -> str:
            """Search for messages across Mattermost.

            Args:
                terms: Search terms.
                page: Optional page number (defaults to 0).
                per_page: Optional number of results per page (defaults to 60).
            """
            return await self._search_messages(terms, page, per_page)

        @self.mcp.tool()
        async def search_users(term: str) -> str:
            """Search for users by name, username, or email.

            Args:
                term: Search term.
            """
            return await self._search_users(term)

        @self.mcp.tool()
        async def get_user_info(user_id: str) -> str:
            """Get detailed profile information for a user.

            Args:
                user_id: The ID of the user.
            """
            return await self._get_user_info(user_id)

        @self.mcp.tool()
        async def send_direct_message(
            usernames: List[str],
            message: str,
            file_path: Optional[str] = None,
        ) -> str:
            """Send a direct message to one or more users.

            Args:
                usernames: List of usernames (with or without @).
                message: The message text.
                file_path: Optional local path to a file to attach.
            """
            return await self._send_direct_message(usernames, message,
                                                   file_path)

        @self.mcp.tool()
        async def get_post_details(post_id: str) -> str:
            """Retrieve the full metadata for a specific post.

            Args:
                post_id: The ID of the post to retrieve.
            """
            return await self._get_post_details(post_id)

        @self.mcp.tool()
        async def list_post_attachments(post_id: str) -> str:
            """List names and IDs of files attached to a post.

            Args:
                post_id: The ID of the post.
            """
            return await self._list_post_attachments(post_id)

        @self.mcp.tool()
        async def download_attachment(
            file_id: str,
            destination_path: str,
        ) -> str:
            """Download an attachment from Mattermost to the local filesystem.

            Args:
                file_id: The ID of the file to download.
                destination_path: Local path where the file should be saved.
            """
            return await self._download_attachment(file_id, destination_path)

    async def run(self, host: str, port: int):
        """Run the MCP server using Streamable HTTP transport."""
        self.mcp.settings.host = host
        self.mcp.settings.port = port
        await self.mcp.run_streamable_http_async()
