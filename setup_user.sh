#!/bin/bash
# setup_user.sh - Provision a Linux user for Goose isolation

set -e

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

USERNAME=$1
GOOSE_CONFIG_DIR="/home/$USERNAME/.config/goose"

# Create system user if it doesn't exist
if id "$USERNAME" &>/dev/null; then
    echo "User $USERNAME already exists."
else
    echo "Creating user $USERNAME..."
    sudo useradd -m -s /usr/sbin/nologin "$USERNAME"
fi

# Create goose config directory
echo "Setting up Goose configuration for $USERNAME..."
sudo mkdir -p "$GOOSE_CONFIG_DIR"

# Initialize goose config if it doesn't exist (e.g. from a template if we had one)
# For now, we'll just ensure the directory exists and is owned by the user
sudo chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/.config"

echo "User $USERNAME is ready for Goose."
