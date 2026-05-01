# Goose Mattermost Bridge 🦢💬

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/mrazza/goose-mm-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/mrazza/goose-mm-bridge/actions/workflows/ci.yml)

A bridge that connects [Goose](https://github.com/block/goose) to [Mattermost](https://mattermost.com/), allowing you to interact with your Goose agent directly from your Mattermost channels and direct messages.

## 🚀 Features

- **Seamless Integration**: Chat with Goose as if it were another user on Mattermost.
- **Session Management**: Maintains conversation context using Mattermost threads.
- **Multi-user Support**: Multiple users can interact with the bot simultaneously in their own sessions.
- **OS-Native Isolation**: Map Mattermost users to dedicated Linux accounts for strict security and tool isolation.
- **MCP Tooling**: Automatically exposes Mattermost capabilities to Goose via the Model Context Protocol (MCP), allowing it to search history, find users, and send messages across channels.
- **Thinking Transparency**: Stream the agent's thinking process to Mattermost as message attachments.
- **Interactive Commands**: Use commands like `!stop` to interrupt the agent mid-response.

## 🏗 How it Works

1. **Mattermost Polling**: The bridge periodically polls the Mattermost API for new posts in channels the bot has joined.
2. **Session Mapping**: It tracks conversations by mapping the Mattermost `user_id` and `root_id` (thread ID) to a specific Goose ACP session.
3. **Goose ACP Subprocess**: The bridge spawns `goose acp` as a subprocess and communicates via JSON-RPC.
4. **Internal MCP Server**: The bridge runs an internal FastMCP server that Goose connects to, providing tools for Mattermost interaction.
5. **Asynchronous Handling**: Uses `asyncio` to handle concurrent messages and streaming responses from Goose.

## 🛠 MCP Tools for Goose

When interacting with Goose, it has access to the following Mattermost tools:
- `send_message`: Send messages to any channel or thread.
- `get_channels`: List available channels.
- `get_thread_context`: Fetch full history of a thread with user attribution.
- `search_messages`: Search for content across all accessible teams.
- `search_users`: Find users by name, username, or email.
- `get_user_info`: Get detailed profile information for a user.
- `send_direct_message`: Start or continue a DM with one or more users.

## 🛡️ Security Model: OS-Native Isolation

The bridge supports user segmentation by mapping Mattermost users to dedicated Linux accounts. Each user's Goose session runs in its own process under its specific UID/GID, providing:

- **Filesystem Isolation**: The AI can only access files that the mapped Linux user has permissions for.
- **Tool Isolation**: Shell commands are executed as the mapped user.
- **Memory/Config Isolation**: Goose configuration and history are stored in the user's home directory (`/home/username/.config/goose`).

## 🛠 Prerequisites

- [Goose](https://github.com/block/goose) installed and available in your PATH.
- A Mattermost Bot account and Personal Access Token.
- Python 3.8+
- (Optional) `sudo` access on the host for OS-native isolation.

## 📦 Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/mrazza/goose-mm-bridge.git
   cd goose-mm-bridge
   ```

2. **Set up a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your Mattermost details
   ```

## 🛡️ Administrative Setup (Optional Isolation)

If you wish to use the OS-native isolation feature:

1. **Provision Users**: Use the provided `setup_user.sh` script to create isolated Linux users:
   ```bash
   sudo ./setup_user.sh goose_user_1
   ```

2. **Configure Sudoers**: Allow the bridge user to execute Goose as these managed users. See `sudoers.template` for guidance.

3. **User Mapping**: Create a `user_mapping.json` file to associate Mattermost IDs with Linux usernames:
   ```json
   {
     "mattermost_user_id_1": "goose_user_1",
     "mattermost_username_2": "goose_user_2"
   }
   ```
   Set `USER_MAPPING_FILE` in your `.env` if you use a different path.

## ⚙️ Configuration

The bridge is configured via environment variables in the `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `MATTERMOST_URL` | The base URL of your Mattermost instance (e.g., `chat.example.com`). | |
| `MATTERMOST_TOKEN` | Your Mattermost Bot Personal Access Token. | |
| `MATTERMOST_SCHEME` | The protocol used by Mattermost (`http` or `https`). | `https` |
| `MATTERMOST_PORT` | The port number Mattermost is listening on. | `443` |
| `APPROVED_USERS` | A comma-separated list of usernames or user IDs authorized to use the bot. If empty, any user who can reach the bot can use it. | (None) |
| `USER_MAPPING_FILE` | Path to the JSON configuration file for OS-level user isolation. | `user_mapping.json` |
| `POLL_INTERVAL` | How often (in seconds) the bridge checks for new messages. | `1` |
| `DEBUG` | Set to `true` to see detailed JSON-RPC logs for troubleshooting. | `false` |
| `GOOSE_THINKING_TRACE` | When enabled, the agent's internal "thinking" steps are shown as message attachments. | `true` |
| `GOOSE_THINKING_TRACE_SIMPLIFIED` | When enabled, shows a compact "Thinking... [Last action]" status instead of full attachments. | `true` |
| `RPC_TIMEOUT` | Seconds to wait for Goose to respond before timing out. | `600` |
| `REQUIRE_USER_MAPPING` | If `true`, only users explicitly listed in the mapping file can use the bot. | `false` |
| `MAX_SESSIONS` | The maximum number of active thread contexts to keep before recycling. | `100` |
| `MCP_ENABLED` | Enables the internal MCP server, allowing Goose to call Mattermost tools. | `true` |
| `MCP_HOST` | The host address the internal MCP server binds to. | `localhost` |
| `MCP_PORT` | The port used by the internal MCP server. | `8080` |

> **💡 Note on Threading**: The bridge uses Mattermost thread IDs (`root_id`) to isolate conversations. This allows you to have multiple, independent discussions with the bot simultaneously—even within the same channel. Mentioning the bot in a reply will continue that specific conversation thread.

## 🎮 Commands

The bridge supports specific commands that can be typed directly into the Mattermost chat:

- **`!stop`**: Immediately cancels the active prompt in the current thread.

## 🏃 Usage

You can start the bridge manually:

```bash
source venv/bin/activate
python src/bridge.py
```

The bot will start polling Mattermost for new messages and respond using the Goose ACP.

---
*Built with ❤️ for the Goose community.*
