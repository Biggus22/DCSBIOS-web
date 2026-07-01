#!/usr/bin/env bash
set -euo pipefail

# Combined installer for Raspberry Pi: DCSBIOS-web + DCS-BIOS-TUI
# Usage: sudo -u pi bash install_pi_all.sh [--web-branch BRANCH] [--tui-branch BRANCH]

WEB_REPO_URL="https://github.com/Biggus22/DCSBIOS-web.git"
TUI_REPO_URL="https://github.com/Biggus22/DCS-BIOS-TUI.git"
WEB_BRANCH="dev"
TUI_BRANCH="main"

show_help() {
    cat <<EOF
Usage: sudo -u pi bash install_pi_all.sh [options]

Options:
  --web-branch <branch>   Git branch for DCSBIOS-web (default: dev)
  --tui-branch <branch>   Git branch for DCS-BIOS-TUI (default: main)
  -h, --help              Show this help

This script will:
  - install system packages (python3, venv, pip, git)
  - clone or update the two repos into /home/pi
  - create virtualenvs and install Python dependencies
  - create and enable systemd services for web and daemon
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --web-branch)
            WEB_BRANCH="$2"; shift 2;;
        --tui-branch)
            TUI_BRANCH="$2"; shift 2;;
        -h|--help)
            show_help; exit 0;;
        *) echo "Unknown option: $1"; show_help; exit 1;;
    esac
done

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Please run this script as the 'pi' user (do not run as root). Use sudo where needed when prompted." >&2
    exit 1
fi

echo "Updating apt and installing prerequisites..."
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl uhubctl

USER_HOME="${HOME}"
WEB_DIR="$USER_HOME/dcsbios_web"
TUI_DIR="$USER_HOME/DCS-BIOS-TUI"

echo "Creating/updating DCSBIOS-web in $WEB_DIR (branch: $WEB_BRANCH)"
if [[ -d "$WEB_DIR/.git" ]]; then
    pushd "$WEB_DIR" >/dev/null
    git fetch origin
    git checkout "$WEB_BRANCH" || git checkout -b "$WEB_BRANCH" origin/$WEB_BRANCH || true
    git pull origin "$WEB_BRANCH" || true
    popd >/dev/null
else
    git clone -b "$WEB_BRANCH" --single-branch "$WEB_REPO_URL" "$WEB_DIR"
fi

echo "Creating/updating DCS-BIOS-TUI in $TUI_DIR (branch: $TUI_BRANCH)"
if [[ -d "$TUI_DIR/.git" ]]; then
    pushd "$TUI_DIR" >/dev/null
    git fetch origin
    git checkout "$TUI_BRANCH" || git checkout -b "$TUI_BRANCH" origin/$TUI_BRANCH || true
    git pull origin "$TUI_BRANCH" || true
    popd >/dev/null
else
    git clone -b "$TUI_BRANCH" --single-branch "$TUI_REPO_URL" "$TUI_DIR"
fi

echo "Setting up Python virtualenv and installing requirements for web..."
python3 -m venv "$WEB_DIR/venv"
"$WEB_DIR/venv/bin/python" -m pip install --upgrade pip
if [[ -f "$WEB_DIR/requirements.txt" ]]; then
    "$WEB_DIR/venv/bin/pip" install -r "$WEB_DIR/requirements.txt"
else
    "$WEB_DIR/venv/bin/pip" install flask==3.0.0 flask-cors==4.0.0 pyserial==3.5
fi

echo "Setting up Python virtualenv and installing requirements for TUI..."
python3 -m venv "$TUI_DIR/venv"
"$TUI_DIR/venv/bin/python" -m pip install --upgrade pip
if [[ -f "$TUI_DIR/requirements.txt" ]]; then
    "$TUI_DIR/venv/bin/pip" install -r "$TUI_DIR/requirements.txt"
else
    "$TUI_DIR/venv/bin/pip" install pyserial
fi

echo "Ensure config dir exists at ~/.dcsbios"
mkdir -p "$USER_HOME/.dcsbios"

echo "Add user to dialout group for serial access (may require logout)"
sudo usermod -a -G dialout "$USER"

WEB_SERVICE="/etc/systemd/system/dcsbios-web.service"
TUI_SERVICE="/etc/systemd/system/dcsbios-tui.service"

echo "Writing systemd service for web: $WEB_SERVICE"
sudo tee "$WEB_SERVICE" > /dev/null <<EOF
[Unit]
Description=DCS-BIOS Web Interface
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$WEB_DIR
Environment=PATH=$WEB_DIR/venv/bin
ExecStart=$WEB_DIR/venv/bin/python $WEB_DIR/src/dcsbios_web.py --host=0.0.0.0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Writing systemd service for TUI daemon: $TUI_SERVICE"
sudo tee "$TUI_SERVICE" > /dev/null <<EOF
[Unit]
Description=DCS-BIOS TUI Daemon
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$TUI_DIR
Environment=PATH=$TUI_DIR/venv/bin
ExecStart=$TUI_DIR/venv/bin/python $TUI_DIR/dcsbios_daemon.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and enabling services"
sudo systemctl daemon-reload
sudo systemctl enable --now dcsbios-web.service || true
sudo systemctl enable --now dcsbios-tui.service || true

echo
echo "Installation complete. Check service statuses with:" 
echo "  sudo systemctl status dcsbios-web.service"
echo "  sudo systemctl status dcsbios-tui.service"
echo
echo "If you want the interactive TUI instead of the daemon, SSH into the Pi and run:" 
echo "  $TUI_DIR/venv/bin/python $TUI_DIR/dcsbios_tui.py"

exit 0
