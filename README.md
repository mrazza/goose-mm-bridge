# Goose Mattermost Bridge 🦢💬

A bridge that connects [Goose](https://github.com/block/goose) to [Mattermost](https://mattermost.com/), allowing you to interact with your Goose agent directly from your Mattermost channels and direct messages.

## 🚀 Features

- **Seamless Integration**: Chat with Goose as if it were another user on Mattermost.
- **Session Management**: Maintains conversation context using Mattermost threads.
- **Multi-user Support**: Multiple users can interact with the bot simultaneously in their own sessions.
- **ACP Integration**: Communicates with Goose using the Agent Control Protocol (ACP).
- **Security**: Optional user allowlisting via `APPROVED_USERS`.

## 🛠 Prerequisites

- [Goose](https://github.com/block/goose) installed and available in your PATH.
- A Mattermost Bot account and Personal Access Token.
- Python 3.8+

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

## ⚙️ Configuration

The bridge is configured via environment variables in the `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `MATTERMOST_URL` | The URL of your Mattermost instance (e.g., `mattermost.example.com`) | |
| `MATTERMOST_TOKEN` | Your Mattermost Bot Access Token | |
| `MATTERMOST_SCHEME` | `http` or `https` | `https` |
| `MATTERMOST_PORT` | The port for your Mattermost instance | `443` |
| `APPROVED_USERS` | Comma-separated list of usernames or user IDs allowed to use the bot | (All allowed) |
| `POLL_INTERVAL` | The frequency (in seconds) to poll Mattermost for new messages | `1` |
| `DEBUG` | Enable verbose logging of JSON-RPC messages | `false` |
| `GOOSE_THINKING_TRACE` | Stream the agent's thinking process to Mattermost as attachments | `true` |
| `RPC_TIMEOUT` | Timeout for requests to the Goose subprocess (in seconds) | `60` |

## 🏃 Usage

You can start the bridge using the provided script (if running from the parent directory) or manually:

```bash
# Manual start
source venv/bin/activate
python bridge.py
```

The bot will start polling Mattermost for new messages and respond using the Goose ACP.

## 🏗 How it Works

1. **Mattermost Polling**: The bridge periodically polls the Mattermost API for new posts in channels the bot has joined.
2. **Session Mapping**: It tracks conversations by mapping the Mattermost `user_id` and `root_id` (thread ID) to a specific Goose ACP session.
3. **Goose ACP Subprocess**: The bridge spawns `goose acp` as a subprocess and communicates via JSON-RPC over standard input/output.
4. **Asynchronous Handling**: Uses `asyncio` to handle concurrent messages and streaming responses from Goose.
5. **Threaded Responses**: Replies are posted back to Mattermost as part of the original thread to maintain clean organization.

---
*Built with ❤️ for the Goose community.*