#!/bin/bash

# This script generates and installs a systemd user service for client.py
# Run this script as the regular user (not sudo) from the directory containing client.py

# Get the current directory (absolute path)
SCRIPT_DIR=$(pwd)

# Check if client.py exists in the current directory
if [ ! -f "$SCRIPT_DIR/client.py" ]; then
  echo "client.py not found in the current directory: $SCRIPT_DIR"
  exit 1
fi

# Service name
SERVICE_NAME="atp-stt-client.service"

# Path to user service directory
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR"

# Path to service file
SERVICE_FILE="$USER_SYSTEMD_DIR/$SERVICE_NAME"

# Create the service file
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ATP STT Whisper.cpp Python Client Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $SCRIPT_DIR/client.py
WorkingDirectory=$SCRIPT_DIR
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$SCRIPT_DIR/atp-stt-client.log
StandardError=inherit

[Install]
WantedBy=default.target
EOF

# Reload systemd user daemon
systemctl --user daemon-reload

# Start the service
systemctl --user start $SERVICE_NAME

# Enable the service to start on login
systemctl --user enable $SERVICE_NAME

echo "User service $SERVICE_NAME has been created, started, and enabled to start on login."
echo "Check $SCRIPT_DIR/atp-stt-client.log for output and errors."

# Enable lingering to allow the service to run even when the user is not logged in
loginctl enable-linger