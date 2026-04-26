#!/bin/bash
# setup_user.sh - Provision a Linux user for Goose isolation and configure sudoers

set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <target_username> [bridge_user]"
    echo "  target_username: The Linux user to create for Goose isolation."
    echo "  bridge_user: (Optional) The user running the bridge who needs sudo access. Defaults to current user."
    exit 1
fi

TARGET_USER=$1
BRIDGE_USER=${2:-$(whoami)}
GOOSE_CONFIG_DIR="/home/$TARGET_USER/.config/goose"
SUDOERS_FILE="/etc/sudoers.d/goose-mm-bridge"
GOOSE_PATH=$(which goose || echo "/usr/local/bin/goose")

# 1. Create system user if it doesn't exist
if id "$TARGET_USER" &>/dev/null; then
    echo "User $TARGET_USER already exists."
else
    echo "Creating user $TARGET_USER..."
    sudo useradd -m -s /usr/sbin/nologin "$TARGET_USER"
fi

# 2. Create goose config directory
echo "Setting up Goose configuration for $TARGET_USER..."
sudo mkdir -p "$GOOSE_CONFIG_DIR"
sudo chown -R "$TARGET_USER:$TARGET_USER" "/home/$TARGET_USER/.config"

# 3. Configure Sudoers
echo "Configuring sudoers for $BRIDGE_USER to run as $TARGET_USER..."
SUDO_LINE="$BRIDGE_USER ALL=($TARGET_USER) NOPASSWD: $GOOSE_PATH acp"

# Check if the line already exists to avoid duplicates
if [ -f "$SUDOERS_FILE" ] && grep -qF "$SUDO_LINE" "$SUDOERS_FILE"; then
    echo "Sudoers entry already exists in $SUDOERS_FILE"
else
    echo "$SUDO_LINE" | sudo tee -a "$SUDOERS_FILE" > /dev/null
    sudo chmod 0440 "$SUDOERS_FILE"
    echo "Added sudoers entry to $SUDOERS_FILE"
fi

echo "User $TARGET_USER is ready for Goose sessions initiated by $BRIDGE_USER."
