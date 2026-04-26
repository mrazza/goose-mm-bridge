# Refactoring Plan: Improving Readability and Maintainability

## Objective
The goal is to refactor large methods and functions in `goose-mm-bridge` to improve readability, testability, and maintainability. Specifically, we target the `GooseACPClient.prompt` method and the `handle_message` and `run_bridge` functions in `bridge.py`.

## Current Issues

### 1. `GooseACPClient.prompt`
- **Size**: ~120 lines.
- **Complexity**: Handles session validation, request orchestration, complex multi-format JSON-RPC response parsing, timeout management, and queue draining.
- **Coupling**: The logic for interpreting "agent_message_chunk", "agent_thinking_chunk", and "tool_call" is interleaved with the async streaming logic.

### 2. `bridge.py:handle_message`
- **Size**: ~140 lines.
- **Complexity**: Contains a large inner function `run_prompt` that manages Mattermost UI updates (thinking traces, tool calls) during the streaming process.
- **Responsibility**: It handles everything from session lock acquisition to error recovery and UI formatting.

### 3. `bridge.py:run_bridge`
- **Size**: ~200 lines.
- **Complexity**: A monolithic polling loop that manages API caching, post filtering, command parsing (`!stop`), authorization, user mapping, and session pruning.
- **State Management**: It manages multiple dictionaries (`sessions`, `active_tasks`, `session_locks`) as local variables, making it hard to extend.

## Proposed Changes

### GooseACPClient Refactor
- **Extract Response Parsing**: Create a helper method `_parse_update_chunk(chunk: dict) -> Optional[dict]` to unify the handling of different JSON-RPC response formats.
- **Decompose `prompt`**:
    - `_wait_for_updates()`: Handle the async selection between chunks and the final response future.
    - `_drain_remaining_chunks()`: Encapsulate the cleanup logic after the final response is received.

### Bridge Refactor
- **Introduce `MattermostBridge` Class**:
    - Move state (`sessions`, `active_tasks`, `session_locks`, `goose_clients`) from `run_bridge` into class attributes.
    - Encapsulate polling logic in a `run()` or `listen()` method.
- **Decompose `run_bridge`**:
    - `_update_channel_cache()`: Handle channel/team discovery.
    - `_process_post(post)`: Determine if a post needs action and handle commands.
    - `_handle_stop_command(post)`: Specific logic for `!stop`.
    - `_prune_sessions()`: Logic for cleaning up old sessions.
- **Refactor `handle_message`**:
    - Move `run_prompt` logic into a separate method `_stream_response_to_mattermost`.
    - Simplify session acquisition and retry logic.

## Implementation Steps
1. Create a GitHub issue describing the tasks.
2. Refactor `GooseACPClient.prompt` into smaller, semantically clear methods.
3. Refactor `bridge.py` by introducing the `MattermostBridge` class and decomposing the large functions.
4. Verify functionality through manual testing (or automated tests if available).
