# Goose Mattermost Bridge 🦢💬

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/mrazza/goose-mm-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/mrazza/goose-mm-bridge/actions/workflows/ci.yml)


A bridge that connects [Goose](https://github.com/block/goose) to [Mattermost](https://mattermost.com/), allowing you to interact with your Goose agent directly from your Mattermost channels and direct messages.

## 🚀 Features

- **Seamless Integration**: Chat with Goose as if it were another user on Mattermost.
- **Session Management**: Maintains conversation context using Mattermost threads.
- **Multi-user Support**: Multiple users can interact with the bot simultaneously in their own sessions.
- **OS-Native Isolation**: Optionally map Mattermost users to dedicated Linux accounts for strict security and tool isolation.
- **ACP Integration**: Communicates with Goose using the Agent Control Protocol (ACP).
- **Thinking Transparency**: Stream the agent's thinking process to Mattermost as message attachments.
- **Interactive Commands**: Use commands like `!stop` to interrupt the agent mid-response.

## 🏗 How it Works

1. **Mattermost Polling**: The bridge periodically polls the Mattermost API for new posts in channels the bot has joined.
2. **Session Mapping**: It tracks conversations by mapping the Mattermost `user_id` and `root_id` (thread ID) to a specific Goose ACP session.
3. **Goose ACP Subprocess**: The bridge spawns `goose acp` as a subprocess and communicates via JSON-RPC over standard input/output.
4. **Asynchronous Handling**: Uses `asyncio` to handle concurrent messages and streaming responses from Goose.
5. **Threaded Responses**: Replies are posted back to Mattermost as part of the original thread to maintain clean organization.

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
   git clone https://github.com/block/goose-mm-bridge.git
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
| `MATTERMOST_URL` | The URL of your Mattermost instance (e.g., `mattermost.example.com`) | |
| `MATTERMOST_TOKEN` | Your Mattermost Bot Access Token | |
| `MATTERMOST_SCHEME` | `http` or `https` | `https` |
| `MATTERMOST_PORT` | The port for your Mattermost instance | `443` |
| `APPROVED_USERS` | Comma-separated list of usernames or user IDs allowed to use the bot | (All allowed) |
| `USER_MAPPING_FILE` | Path to the JSON file mapping Mattermost users to Linux accounts | `user_mapping.json` |
| `POLL_INTERVAL` | The frequency (in seconds) to poll Mattermost for new messages | `1` |
| `DEBUG` | Enable verbose logging of JSON-RPC messages | `false` |
| `GOOSE_THINKING_TRACE` | Stream the agent's thinking process to Mattermost as attachments | `true` |
| `RPC_TIMEOUT` | Timeout for requests to the Goose subprocess (in seconds) | `600` |
| `REQUIRE_USER_MAPPING` | If `true`, reject users not found in `user_mapping.json` | `false` |
| `MAX_SESSIONS` | Maximum number of concurrent sessions to track before pruning old ones | `100` |


## 🎮 Commands

The bridge supports specific commands that can be typed directly into the Mattermost chat:

- **`!stop`**: Immediately cancels the active prompt in the current thread. This is useful if the agent is stuck in a loop or performing a long-running task you wish to terminate.

## 🏃 Usage

You can start the bridge manually:

```bash
source venv/bin/activate
python src/bridge.py
```

The bot will start polling Mattermost for new messages and respond using the Goose ACP.

---
*Built with ❤️ for the Goose community.*