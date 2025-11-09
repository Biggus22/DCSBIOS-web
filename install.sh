#!/bin/bash

# DCS-BIOS Controller Manager Web Interface - Installation Script
# This script installs and sets up the DCS-BIOS web interface on Raspberry Pi

set -e  # Exit on any error

echo "DCS-BIOS Controller Manager Web Interface - Installation Script"
echo "==============================================================="

# Check if running on Raspberry Pi or similar Linux system
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "Warning: This script is designed for Raspberry Pi/Linux systems"
    echo "Current OS: $OSTYPE"
fi

# Check if running as root, and if not, warn user
if [[ $EUID -eq 0 ]]; then
    echo "Warning: This script is running as root. It's recommended to run as regular user."
fi

echo "Installing DCS-BIOS Controller Manager Web Interface..."
echo

# Update package list
echo "[1/6] Updating package list..."
sudo apt update

# Install required dependencies
echo "[2/6] Installing required dependencies..."
sudo apt install -y python3 python3-pip python3-venv git curl

# Check if user is in dialout group (needed for serial port access)
if ! groups $USER | grep -q '\bdialout\b'; then
    echo "[3/6] Adding user to dialout group for serial port access..."
    sudo usermod -a -G dialout $USER
    echo "User $USER has been added to the dialout group."
    echo "You will need to log out and log back in for this change to take effect."
else
    echo "[3/6] User is already in dialout group."
fi

# Create installation directory
INSTALL_DIR="$HOME/dcsbios_web"
echo "[4/6] Creating installation directory: $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Clone the repository (or copy files if already present)
if [ -d ".git" ]; then
    echo "[5/6] Copying local repository files to $INSTALL_DIR..."
    cp -r src/* "$INSTALL_DIR/"
    cp -r scripts/* "$INSTALL_DIR/"
    cp requirements.txt "$INSTALL_DIR/"
else
    echo "[5/6] Cloning repository to $INSTALL_DIR..."
    if [ -d "$INSTALL_DIR/.git" ]; then
        # Update existing installation
        cd "$INSTALL_DIR"
        git pull origin main
    else
        # Fresh installation
        git clone https://github.com/Biggus22/DCSBIOS-web.git "$INSTALL_DIR"
    fi
fi

# Navigate to installation directory
cd "$INSTALL_DIR"

# Create Python virtual environment and install dependencies
echo "[6/6] Setting up Python virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip3 install --upgrade pip
pip3 install -r requirements.txt

# Create the configuration directory
mkdir -p "$HOME/.dcsbios"

echo
echo "Installation complete!"
echo
echo "To start the DCS-BIOS web interface:"
echo "  cd ~/dcsbios_web"
echo "  ./start.sh"
echo
echo "Then open your browser and go to:"
echo "  http://<your-pi-ip>:5000"
echo
echo "IMPORTANT: If this is your first installation, please log out and log back in"
echo "to apply the dialout group permissions for serial port access."
echo
echo "For headless operation (auto-start on boot), after configuring the web interface:"
echo "1. Enable 'Auto-start' in the web interface Settings"
echo "2. Click 'Boot Service' -> 'Install Service'"