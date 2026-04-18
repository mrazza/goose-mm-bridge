# Implementation Plan: Robust ACP Handling and Session Concurrency

This plan addresses reliability issues in the `goose-mm-bridge` related to the `goose acp` subprocess and improves session concurrency.

## 1. Problem Statement
- **Process Termination:** If `goose acp` crashes, the bridge fails permanently until manual restart.
- **Process Hangs:** If `goose acp` stops responding, the entire polling loop blocks, making the bot unresponsive to all users.
- **Sequential Processing:** Currently, messages are processed somewhat sequentially within the polling loop, which can lead to delays for other users if one session is slow.

## 2. Proposed Changes

### A. Automatic Process Recovery (Watchdog)
- **Restart Logic:** Modify `GooseACPClient` to detect when the subprocess has exited.
- **Re-initialization:** If the process is dead, `send_request` should trigger a restart and re-initialization (including the handshake) before retrying the request.
- **State Management:** The bridge needs to decide if it can recover sessions after a crash. Since `goose acp` sessions are typically ephemeral to the process, the bridge should log the failure and notify the user that their session was reset.

### B. Request Timeouts and Health Checks
- **RPC Timeouts:** Add a timeout (e.g., 30 seconds) to `send_request`. If `goose acp` doesn't respond within this window, the future should be failed with a `TimeoutError`.
- **Health Check:** Periodically send a "ping" or "nop" request to `goose acp` to ensure it's still responsive.

### C. Task-based Concurrency
- **Decoupled Polling:** The main loop in `run_bridge` should only be responsible for:
    1. Polling Mattermost for new posts.
    2. Tracking `last_since`.
    3. Spawning an `asyncio.Task` to handle each new message.
- **Session Locking:** While processing is concurrent, we should ensure that multiple messages in the *same* thread (session) are still processed in order to maintain conversation integrity. This can be achieved with a per-session `asyncio.Lock`.

## 3. Implementation Steps

1. **Refactor `GooseACPClient`**:
    - Add `ensure_running()` method to check process status and restart if necessary.
    - Update `send_request` to use `asyncio.wait_for` with a configurable timeout.
    - Update `_read_stdout` to handle EOF gracefully and mark the process as dead.

2. **Refactor `run_bridge`**:
    - Create a `handle_message` coroutine that takes a post and handles the prompt/response logic.
    - In the polling loop, use `asyncio.create_task(handle_message(...))` for each new post.
    - Implement a `session_locks: Dict[str, asyncio.Lock]` to prevent race conditions within the same thread.

3. **Error Feedback**:
    - Update the bridge to post a message to Mattermost if a session was lost due to a backend crash or if a timeout occurred.

## 4. Verification Plan
- **Crash Test:** Manually kill the `goose acp` process and verify the bridge restarts it on the next message.
- **Hang Test:** Use a mock `goose` script that sleeps indefinitely and verify the bridge times out and remains responsive to other requests.
- **Concurrency Test:** Send multiple messages from different users simultaneously and verify they are processed in parallel.
