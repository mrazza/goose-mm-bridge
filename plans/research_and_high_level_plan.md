# Research and High-Level Plan: User Segmentation in goose-mm-bridge

## 1. Introduction
The goal is to explore and plan the segmentation of tool usage, memory, and functionality within the `goose-mm-bridge` based on the Mattermost sender. Currently, the bridge provides a single Goose instance's capabilities to all approved users, with session isolation limited to the conversation thread.

## 2. Current Architecture Analysis
- **Session Mapping**: Mapping is done via `sender_id:root_id`.
- **Process**: A single `goose acp` subprocess is started.
- **Isolation**: Each session is "new" in terms of conversation but shares the bridge's working directory and global Goose configuration.
- **Tools**: No MCP servers are explicitly passed during session creation, meaning it uses the default set configured in the environment where the bridge runs.

## 3. Segmentation Options

### 3.1 Tool Segmentation (Functionality)
- **Dynamic MCP Injection**: The `session/new` ACP call accepts `mcpServers`. We can maintain a mapping of user roles to specific MCP server configurations.
- **Tool Allowlisting**: If the bridge manages the tool calls (though ACP usually handles this), it could filter them. However, injecting specific MCP servers at session creation is the "Goose-native" way.

### 3.2 Memory Segmentation
- **Conversation Memory**: Currently handled by unique session IDs per thread.
- **Long-term Memory (Summaries/Vector DB)**: If Goose uses a local store for long-term memory, we need to ensure it's partitioned. 
- **System Prompt Context**: Injecting a "You are helping [Username]" prompt at the start of a session to set context.

### 3.3 Resource/File Segmentation
- **User-Specific CWD**: Providing a unique `cwd` (Current Working Directory) to `session/new` for each user. This ensures that any files created or read by Goose are contained within a user-specific sandbox.

## 4. High-Level Plan
1.  **Architecture Update**: Shift from a single shared environment to a "Multi-Tenant" approach where each user has a dedicated workspace.
2.  **Configuration Schema**: Define a way to specify tool access levels (e.g., `admin`, `developer`, `basic`).
3.  **Workspace Management**: Automate the creation and maintenance of per-user directories.
4.  **Protocol Utilization**: Leverage the full capabilities of the Goose ACP `session/new` parameters.

## 5. Security Considerations
- **Directory Traversal**: Ensure user-specific `cwd` cannot be escaped if Goose uses file tools.
- **Token Leakage**: Ensure MCP server configurations (which might contain keys) are only shared with authorized users.
