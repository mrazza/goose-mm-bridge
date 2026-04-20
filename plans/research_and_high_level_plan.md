# Research and High-Level Plan: OS-Native User Segmentation in goose-mm-bridge

## 1. Introduction
The goal is to segment tool usage, memory, and functionality within `goose-mm-bridge` by leveraging host-level OS isolation. Each approved Mattermost sender is mapped to a dedicated, pre-provisioned Linux user account.

## 2. Core Architecture: Admin-Managed Multi-Tenancy
The bridge operates as a **Privileged Orchestrator** that executes Goose sessions as specific system users.

### 2.1 Identity Mapping
- **Mattermost ID -> Local User**: A mapping is maintained (e.g., in `.env` or a config file) associating Mattermost User IDs with specific local Linux accounts.
- **Admin-Controlled Provisioning**: Unlike automatic creation, users are created and configured by a System Administrator using a provided setup script. This ensures auditability and manual oversight of which users gain OS-level access.

### 2.2 Process Isolation
- **Execution**: The bridge spawns `goose acp` using `sudo -u <target_user>`.
- **Security**: The Linux kernel enforces boundaries. Each user's Goose instance has its own UID/GID, home directory, and process environment.

### 2.3 Segmentation Benefits
- **Tool Segmentation**: OS permissions naturally restrict what the AI can do via shell or filesystem tools.
- **Memory Segmentation**: Configuration and session history are isolated in the user's home directory.
- **Resource Limits**: Standard OS tools (`ulimit`, `cgroups`) can limit resource consumption per user.

## 3. High-Level Plan
1.  **Provisioning Tooling**: Create a `setup_user.sh` script for administrators to safely create and configure Goose-specific Linux users.
2.  **Sudoers Configuration**: Define the minimal sudo permissions required for the bridge to execute Goose as managed users.
3.  **Bridge Mapping Logic**: Implement a lookup mechanism for Mattermost ID to Linux username.
4.  **Goose Session Management**: Update the bridge to initiate sessions under the correct OS identity.

## 4. Security Considerations
- **Controlled Access**: Only users explicitly provisioned by an admin can interact with the bridge.
- **Least Privilege**: The bridge only needs permission to execute `goose` as the specific managed users, not general root access.
