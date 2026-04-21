# Implementation Plan: Admin-Managed OS Segmentation

## Phase 1: Administrative Tooling
1.  **User Setup Script (`setup_user.sh`)**:
    *   Script to create a system user with no login shell (`/usr/sbin/nologin`).
    *   Create home directory and `.config/goose` directory.
    *   Initialize Goose configuration from a template.
    *   Set appropriate ownership and permissions.
2.  **Sudoers Policy**:
    *   Provide a sudoers template: `bridge_user ALL=(target_users) NOPASSWD: /usr/local/bin/goose acp`.

## Phase 2: Bridge Configuration & Mapping
1.  **Mapping Definition**:
    *   Add support for a `user_mapping.json` or environment-based mapping of `MATTERMOST_USER_ID:LINUX_USERNAME`.
2.  **User Validation**:
    *   On startup or first message, the bridge verifies that the mapped Linux user exists and the bridge has the necessary sudo permissions.

## Phase 3: Isolated Execution
1.  **ACP Client Update**:
    *   Modify execution logic to wrap the Goose command: `sudo -n -u {linux_user} goose acp`.
    *   Ensure environment variables needed for Goose are correctly passed or set in the user's environment.

## Phase 4: Template & Resource Management
1.  **Configuration Templates**:
    *   Maintain a master Goose config template that the `setup_user.sh` script uses.
2.  **Resource Limits**:
    *   Optionally configure `/etc/security/limits.d/` for the managed user range to prevent resource exhaustion.

## Phase 5: Verification
1.  **End-to-End Test**:
    *   Verify that messages from Mattermost User A result in a process running as Linux User A.
    *   Verify that User A cannot access files in User B's home directory even if commanded via Goose.
## Phase 6: Documentation & Onboarding
1.  **README Update**:
    *   Update the main README to reflect the new OS-native isolation architecture.
    *   Add instructions for administrators on how to use `setup_user.sh`.
    *   Document the requirement for sudoers configuration and user mapping.
    *   Include a "Security Model" section explaining how user data and tool access are isolated.
