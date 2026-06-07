#!/usr/bin/env bash
# uninstall_service.sh — entfernt den AI-Framework-systemd-Dienst wieder.
#   ./scripts/uninstall_service.sh
set -euo pipefail
SERVICE_NAME="${SERVICE_NAME:-ai-framework}"
UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
sudo rm -f "$UNIT"
sudo systemctl daemon-reload
echo "✓ Dienst „${SERVICE_NAME}\" entfernt."
