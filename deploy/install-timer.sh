#!/bin/bash
# Install VetScan Email Import systemd timer
# Run this script on the production server as root

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VETSCAN_DIR="/var/www/vetscan"

echo "Installing VetScan Email Import Timer..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo $0)"
    exit 1
fi

# Copy service and timer files
cp "$SCRIPT_DIR/vetscan-email-import.service" /etc/systemd/system/
cp "$SCRIPT_DIR/vetscan-email-import.timer" /etc/systemd/system/

# Set permissions
chmod 644 /etc/systemd/system/vetscan-email-import.service
chmod 644 /etc/systemd/system/vetscan-email-import.timer

# Ensure directories exist with correct permissions
mkdir -p "$VETSCAN_DIR/logs" "$VETSCAN_DIR/uploads" "$VETSCAN_DIR/data"
chown -R www-data:www-data "$VETSCAN_DIR/logs" "$VETSCAN_DIR/uploads" "$VETSCAN_DIR/data"

# Reload systemd
systemctl daemon-reload

# Enable and start the timer
systemctl enable vetscan-email-import.timer
systemctl start vetscan-email-import.timer

echo ""
echo "Installation complete!"
echo ""
echo "Useful commands:"
echo "  systemctl status vetscan-email-import.timer    # Check timer status"
echo "  systemctl list-timers                          # List all timers"
echo "  journalctl -u vetscan-email-import.service     # View logs"
echo "  systemctl start vetscan-email-import.service   # Run manually"
echo ""
