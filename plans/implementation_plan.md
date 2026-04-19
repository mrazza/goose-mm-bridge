# Implementation Plan: User Segmentation

## Phase 1: Workspace Isolation
1.  **Modify `bridge.py` Configuration**:
    *   Add `BASE_WORKSPACE_DIR` to environment variables (defaulting to `./workspaces`).
2.  **User Workspace Logic**:
    *   Implement a helper function `get_user_workspace(user_id)` that creates and returns a path like `workspaces/{user_id}`.
3.  **Update Session Creation**:
    *   Update `GooseACPClient.create_session(cwd, mcp_servers)` to accept these parameters.
    *   In `run_bridge`, call `create_session` with the user-specific path.

## Phase 2: Tool Segmentation
1.  **Tool Configuration File**:
    *   Create `tools_config.json` to define sets of MCP servers.
    *   Example:
        ```json
        {
          "roles": {
            "admin": ["filesystem", "shell", "memory"],
            "user": ["memory"]
          },
          "user_mapping": {
            "user_id_1": "admin"
          }
        }
        ```
2.  **Dynamic Loading**:
    *   Load this configuration in the bridge and pass the appropriate `mcpServers` list to the Goose session.

## Phase 3: Contextual Memory
1.  **User Identity Injection**:
    *   When a new session is created, immediately send an initial hidden prompt or use a system prompt (if ACP supports it) to identify the user: `"You are interacting with user: {username}. Please tailor your assistance accordingly."`

## Phase 4: Testing and Validation
1.  **Verify Isolation**: Ensure User A cannot see files created by User B.
2.  **Verify Tool Restriction**: Ensure a "basic" user cannot call restricted tools like `shell`.
## Phase 5: Leveraging Goose Built-ins
1.  **Top Of Mind Context**:
    *   Explore using the `tom` extension to inject "User: [Name]" into every turn automatically, ensuring Goose always "remembers" who it is talking to without relying on the session history alone.
2.  **Profile Mapping**:
    *   Instead of just listing tools, map Mattermost roles to Goose Profiles.
    *   The bridge can read the Goose `config.yaml` to see available profiles and use their corresponding MCP configurations.
