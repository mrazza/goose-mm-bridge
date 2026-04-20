# Implementation Plan: OS-Native User Segmentation

## Phase 1: OS Privilege & Execution Model
1.  **Sudoers Configuration**:
    *   Add a rule to `/etc/sudoers.d/goose-bridge` allowing the service user to run `goose acp` as other users without a password prompt.
2.  **ACP Client Modification**:
    *   Update `GooseACPClient.start()` to accept a `run_as_user` parameter.
    *   Change the execution command to: `sudo -u {run_as_user} goose acp`.

## Phase 2: User Provisioning Engine
1.  **Local User Management**:
    *   Implement a module to check for/create local users (`useradd`).
    *   Users should be created with restricted shells and no password login enabled.
2.  **Directory Initialization**:
    *   For each new user, populate `~/.config/goose` with a base `config.yaml` that defines their default allowed tools and model.

## Phase 3: Bridge Logic & Mapping
1.  **User Mapper**:
    *   Develop a `UserMapper` class that handles the translation of Mattermost sender IDs to Linux usernames.
    *   Handle sanitization of usernames to comply with Linux naming conventions.
2.  **Session Tracking**:
    *   Ensure the bridge tracks which PID belongs to which Mattermost user/thread.

## Phase 4: Shared Config & Resource Control
1.  **Template Management**:
    *   Define a central location for the master Goose configuration.
    *   Implement a sync mechanism to push updates to all local users when the master config changes.
2.  **Resource Constraints**:
    *   Apply `systemd-run` or similar wrappers to limit the CPU/Memory of user processes.

## Phase 5: Verification & Hardening
1.  **Isolation Audit**: Verify that a Goose session for User A cannot read `/home/user_b/.config/goose/config.yaml`.
2.  **Permission Test**: Ensure shell tools correctly report the current Linux UID.
