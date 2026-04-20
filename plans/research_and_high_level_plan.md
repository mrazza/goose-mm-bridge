# Research and High-Level Plan: OS-Native User Segmentation in goose-mm-bridge

## 1. Introduction
The goal is to segment tool usage, memory, and functionality within `goose-mm-bridge` by leveraging host-level OS isolation. Each Mattermost sender will be mapped to a dedicated Linux user account, ensuring that every Goose session runs within a strictly defined security boundary.

## 2. Core Architecture: OS-Native Multi-Tenancy
The bridge will transition from a shared-process model to a **Privileged Orchestrator** model. 

### 2.1 Identity Mapping
- **Mattermost ID -> Local User**: A deterministic mapping (e.g., `mm_user_<id>` or `u_<username>`) will associate Mattermost senders with local Linux accounts.
- **User Provisioning**: The bridge will ensure these users exist and have the necessary directory structures upon first interaction.

### 2.2 Process Isolation
- **Execution**: The bridge (running as a service user like `goose-bridge`) will spawn `goose acp` using `sudo -u <target_user>`.
- **Security**: Isolation is enforced by the Linux kernel (UID/GID boundaries). One user's Goose process cannot access another user's files, environment variables, or memory space.

### 2.3 Segmentation Benefits
- **Tool Segmentation**: Shell tools (`Developer.shell`) are naturally restricted by standard Linux permissions. A user can only "see" and "do" what their Linux account is permitted to do.
- **Memory Segmentation**: Since Goose stores configuration and session history in the user's home directory (`~/.config/goose`), memory is automatically partitioned by the OS.
- **Resource Limits**: `cgroups` or `ulimit` can be applied per Linux user to prevent a single user from consuming all system resources.

## 3. High-Level Plan
1.  **Privileged Execution Setup**: Configure `sudoers` to allow the bridge user to execute `goose` as any managed user.
2.  **User Lifecycle Management**: Implement logic to create and configure local users on-demand.
3.  **Goose Config Template**: Maintain a system-wide "Gold Template" for Goose configuration that is applied to new users.
4.  **Session Orchestration**: Update the ACP client to handle multi-user process management.

## 4. Security Considerations
- **Sudo Scope**: The bridge must have a very narrow `sudo` scope (only for the `goose` command).
- **UID Management**: Ensure local UID ranges do not conflict with system services.
