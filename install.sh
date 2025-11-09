#!/bin/bash
# DCS-BIOS Controller Manager - One-Command Installer
# Repository: https://github.com/Biggus22/DCSBIOS-web
# Usage: curl -sSL https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/main/install.sh | bash

set -e  # Exit on any error

echo "=========================================="
echo "  DCS-BIOS Controller Manager Installer"
echo "=========================================="
echo ""

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$HOME/dcsbios_web"
REPO_URL="https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/main"

# Check system
echo "🔍 Checking system..."
if ! grep -q "Raspberry Pi\|BCM" /proc/cpuinfo 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Warning: This doesn't appear to be a Raspberry Pi${NC}"
    echo "   Continuing anyway..."
fi
echo -e "${GREEN}✓${NC} System check complete"
echo ""

# Create project directory
echo "📁 Creating project directory..."
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR" || exit 1
echo -e "${GREEN}✓${NC} Created $PROJECT_DIR"
echo ""

# Update system and install dependencies
echo "📦 Installing system dependencies..."
echo "   This may take a few minutes..."
sudo apt update -qq
sudo apt install -y python3-venv python3-full curl > /dev/null 2>&1
echo -e "${GREEN}✓${NC} System dependencies installed"
echo ""

# Create virtual environment
echo "🐍 Setting up Python virtual environment..."
if [ -d "venv" ]; then
    echo "   Virtual environment already exists"
else
    python3 -m venv venv
fi
echo -e "${GREEN}✓${NC} Virtual environment ready"
echo ""

# Activate virtual environment
source venv/bin/activate

# Create requirements.txt
echo "📝 Creating requirements file..."
cat > requirements.txt << 'EOF'
flask==3.0.0
flask-cors==4.0.0
pyserial==3.5
EOF
echo -e "${GREEN}✓${NC} Requirements file created"
echo ""

# Install Python packages
echo "📚 Installing Python packages..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}✓${NC} Python packages installed"
echo ""

# Create templates directory
mkdir -p templates

# Download application files
echo "📄 Downloading application files..."

# Download main Python application
echo "   Downloading dcsbios_web.py..."
curl -sSL "${REPO_URL}/src/dcsbios_web.py" > dcsbios_web.py
chmod +x dcsbios_web.py

# Download HTML template
echo "   Downloading index.html..."
curl -sSL "${REPO_URL}/src/templates/index.html" > templates/index.html

echo -e "${GREEN}✓${NC} Application files downloaded"
echo ""

# Check for serial port access
echo "🔐 Checking permissions..."
if ! groups | grep -q dialout; then
    echo "   Adding user to dialout group..."
    sudo usermod -a -G dialout "$USER"
    echo -e "${YELLOW}⚠️  You need to LOG OUT and LOG BACK IN for serial access!${NC}"
    NEEDS_LOGOUT=true
fi
echo -e "${GREEN}✓${NC} Permissions configured"
echo ""

# Create helper scripts
echo "📝 Creating helper scripts..."

# Start script
cat > start.sh << 'EOFSTART'
#!/bin/bash
cd ~/dcsbios_web
source venv/bin/activate
python dcsbios_web.py
EOFSTART
chmod +x start.sh

# Stop script
cat > stop.sh << 'EOFSTOP'
#!/bin/bash
pkill -f "python.*dcsbios_web.py"
EOFSTOP
chmod +x stop.sh

echo -e "${GREEN}✓${NC} Helper scripts created"
echo ""

# Create README
cat > README.md << 'EOFREADME'
# DCS-BIOS Controller Manager

## Quick Start

### Start the web interface:
```bash
cd ~/dcsbios_web
./start.sh
```

Access at: http://<pi-ip>:5000
Find your Pi's IP: `hostname -I`

### Stop the web interface:
```bash
cd ~/dcsbios_web
./stop.sh
```

## First Time Setup

1. Open the web interface in your browser
2. Go to Settings and set your DCS PC IP address
3. Click "Add Device" to add your controllers
4. Click "Start" to begin managing devices

## Documentation

Full documentation: https://github.com/Biggus22/DCSBIOS-web

## Configuration

Config file: `~/.dcsbios/config.json`

## Troubleshooting

**Can't access serial ports?**
Log out and log back in (dialout group membership)

**Can't access web interface?**
- Check if running: `ps aux | grep dcsbios_web`
- Check Pi's IP: `hostname -I`

**Need to restart?**
```bash
./stop.sh
./start.sh
```
EOFREADME

echo -e "${GREEN}✓${NC} README created"
echo ""

# Final instructions
echo ""
echo "=========================================="
echo -e "${GREEN}✅ Installation Complete!${NC}"
echo "=========================================="
echo ""
echo "📍 Installation location: $PROJECT_DIR"
echo ""
echo "🚀 To start the web interface:"
echo "   cd $PROJECT_DIR"
echo "   ./start.sh"
echo ""
echo "🌐 Then open in your browser:"
echo "   http://$(hostname -I | awk '{print $1}'):5000"
echo ""

if [ "$NEEDS_LOGOUT" = true ]; then
    echo -e "${YELLOW}⚠️  IMPORTANT: Log out and log back in for serial port access!${NC}"
    echo ""
fi

echo "📖 Full documentation: https://github.com/Biggus22/DCSBIOS-web"
echo ""
echo "Happy flying! ✈️"
echo ""
