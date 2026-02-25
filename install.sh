#!/bin/bash

# DCS-BIOS Controller Manager Web Interface - Complete Installation Script
# This script installs and sets up the DCS-BIOS web interface on Raspberry Pi
# Includes systemd service setup for auto-start on boot

set -e  # Exit on any error

INSTALL_BRANCH="dev"

show_help() {
    cat << EOF
Usage: install.sh [--branch <branch-name>]

Options:
  --branch <name>  Git branch to install (default: dev)
  -h, --help       Show this help message and exit

Examples:
  bash install.sh --branch main
  curl -sSL https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/dev/install.sh | bash -s -- --branch dev
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            if [[ -z "$2" ]]; then
                echo "Error: --branch requires a value"
                exit 1
            fi
            INSTALL_BRANCH="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

if [[ ! "$INSTALL_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "Invalid branch name: $INSTALL_BRANCH"
    exit 1
fi

echo "DCS-BIOS Controller Manager Web Interface - Installation Script"
echo "==============================================================="

echo "Selected branch: $INSTALL_BRANCH"

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

# Function to handle errors
error_exit() {
    echo
    echo "ERROR: Installation failed at step $1"
    echo "Error was: $2"
    exit 1
}

# Function to handle commands with error checking
run_command() {
    echo -n "[INFO] Running: $1 ... "
    if eval "$1"; then
        echo "SUCCESS"
    else
        error_exit "$2" "Command failed: $1"
    fi
}

# Check available disk space
echo "[INFO] Checking available disk space..."
available_space=$(df / | tail -1 | awk '{print $4}')
available_mb=$((available_space / 1024))
echo "[INFO] Available space: ${available_mb}MB"
if [ $available_mb -lt 500 ]; then
    error_exit "PRECHECK" "Insufficient disk space. At least 500MB free space required."
fi

# Check available memory
echo "[INFO] Checking available memory..."
available_mem=$(free -m | awk '/^Mem:/{print $7}')
echo "[INFO] Available memory: ${available_mb}MB"
if [ $available_mem -lt 100 ]; then
    echo "[WARNING] Available memory is low: ${available_mem}MB. This may cause installation to fail."
fi

# Update package list
echo
echo "[1/9] Updating package list..."
run_command "sudo apt update" "Package list update"

# Install required dependencies
echo
echo "[2/9] Installing required dependencies..."
run_command "sudo apt install -y python3-full python3-pip git curl uhubctl" "Dependency installation"

# Check if user is in dialout group (needed for serial port access)
echo
echo "[3/9] Checking dialout group membership..."
if ! groups $USER | grep -q '\bdialout\b'; then
    echo "[INFO] User $USER not in dialout group. Adding now..."
    run_command "sudo usermod -a -G dialout $USER" "Adding user to dialout group"
    echo "User $USER has been added to the dialout group."
    echo "You will need to log out and log back in for this change to take effect."
else
    echo "[INFO] User $USER is already in dialout group."
fi

# Create installation directory
INSTALL_DIR="$HOME/dcsbios_web"
echo
echo "[4/9] Creating installation directory: $INSTALL_DIR..."
run_command "mkdir -p '$INSTALL_DIR'" "Creating installation directory"

# Clone the repository (or copy files if already present)
echo
echo "[5/9] Setting up repository files in $INSTALL_DIR..."
if [ -d ".git" ] && [ -d "src" ] && [ -d "scripts" ]; then
    echo "[INFO] Copying local repository files..."
    run_command "cp -r src/* '$INSTALL_DIR/'" "Copying src directory"
    run_command "cp -r scripts/* '$INSTALL_DIR/'" "Copying scripts directory"
    if [ -f "requirements.txt" ]; then
        run_command "cp requirements.txt '$INSTALL_DIR/'" "Copying requirements.txt"
    else
        echo "[WARNING] requirements.txt not found in current directory"
    fi
else
    echo "[INFO] Cloning repository from GitHub ($INSTALL_BRANCH) branch..."
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "[INFO] Repository already exists, updating..."
        cd "$INSTALL_DIR"
        run_command "git fetch origin" "Fetching repository updates"
        run_command "git checkout $INSTALL_BRANCH" "Switching to $INSTALL_BRANCH branch"
        run_command "git pull origin $INSTALL_BRANCH" "Updating repository from $INSTALL_BRANCH branch"
    else
        run_command "git clone -b $INSTALL_BRANCH --single-branch https://github.com/Biggus22/DCSBIOS-web.git '$INSTALL_DIR'" "Cloning repository from $INSTALL_BRANCH branch"
    fi
fi

# Navigate to installation directory
cd "$INSTALL_DIR"

# Make scripts executable
echo
echo "[6/9] Setting executable permissions on scripts..."
run_command "chmod +x scripts/*.sh" "Setting executable permissions"

# Create Python virtual environment and install dependencies
echo
echo "[7/9] Setting up Python virtual environment and installing dependencies..."
echo "[INFO] Creating virtual environment..."
run_command "python3 -m venv venv" "Creating Python virtual environment"

echo "[INFO] Activating virtual environment..."
source venv/bin/activate

echo "[INFO] Upgrading pip..."
run_command "pip3 install --upgrade pip" "Upgrading pip"

if [ -f "requirements.txt" ]; then
    echo "[INFO] Installing Python dependencies from requirements.txt..."
    run_command "pip3 install -r requirements.txt" "Installing Python dependencies"
else
    echo "[WARNING] requirements.txt not found in installation directory"
    echo "[INFO] Installing default dependencies..."
    run_command "pip3 install flask==3.0.0 flask-cors==4.0.0 pyserial==3.5" "Installing default dependencies"
fi

# Create the configuration directory
echo
echo "[8/9] Creating configuration directory..."
run_command "mkdir -p '$HOME/.dcsbios'" "Creating config directory"

# Set up systemd service for auto-start on boot
echo
echo "[9/9] Setting up systemd service for auto-start on boot..."
SERVICE_FILE="/etc/systemd/system/dcsbios.service"

# Create the systemd service file
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=DCS-BIOS Controller Manager Web
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR/src
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/src/dcsbios_web.py --host=0.0.0.0 --headless
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "[INFO] Enabling systemd service..."
run_command "sudo systemctl daemon-reload" "Reloading systemd daemon"
run_command "sudo systemctl enable dcsbios.service" "Enabling dcsbios service"

echo
echo "Installation completed successfully!"
echo
echo "Configuration files are located at: ~/.dcsbios/"
echo "Service file is at: /etc/systemd/system/dcsbios.service"
echo
echo "To start the DCS-BIOS web interface manually:"
echo "  cd ~/dcsbios_web"
echo "  ./scripts/start.sh"
echo
echo "Or manage via systemd service:"
echo "  sudo systemctl start dcsbios.service    # Start"
echo "  sudo systemctl stop dcsbios.service     # Stop"
echo "  sudo systemctl status dcsbios.service   # Check status"
echo "  sudo systemctl disable dcsbios.service  # Disable auto-start"
echo
echo "Then open your browser and go to:"
echo "  http://<your-pi-ip>:5000"
echo
echo "IMPORTANT: If this is your first installation, please log out and log back in"
echo "to apply the dialout group permissions for serial port access."
echo
echo "The service has been set up to start automatically on boot."
echo "To disable auto-start, run: sudo systemctl disable dcsbios.service"