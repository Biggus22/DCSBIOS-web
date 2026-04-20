# DCS-BIOS Controller Manager - Web Interface

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

Web-based management interface for DCS-BIOS serial controllers on Raspberry Pi. Control all your flight sim panels from any device on your network.

## ✨ Features

- 🌐 **Web Interface** - Access from any device (PC, tablet, phone)
- 🎮 **Device Management** - Add, configure, and monitor controllers
- 📊 **Real-time Status** - Live updates every 2 seconds
- 🔍 **Serial Input Monitoring** - Monitor DCS BIOS input stream in real-time
- 💾 **Client-Side Logging** - Download logs to your local computer
- 🔄 **Auto-reconnect** - Handles USB disconnections gracefully
- 🎛️ **Configurable Reconnect Policy** - Set retry attempts, retry delay, and serial open pacing from the web UI
- ⚙️ **Easy Configuration** - Visual port selection and settings
- 🚀 **Boot Service** - Optional headless operation
- 📱 **Mobile Friendly** - Responsive design for all screen sizes
- ⚡ **Power Health Alerts** - Detects Raspberry Pi undervoltage/throttling events

## 🚀 Quick Install

SSH into your Raspberry Pi and run:

### Main Branch (Stable Release)
```bash
curl -sSL https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/main/install.sh | bash -s -- --branch main
```

### Dev Branch (Latest Features)
```bash
curl -sSL https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/dev/install.sh | bash -s -- --branch dev
```

> Tip: The installer accepts `--branch <name>` to force the git checkout. Leaving it off defaults to `dev`.

The installation will automatically:
- Install all required dependencies
- Set up the Python virtual environment
- Configure the application
- Create and enable a systemd service for auto-start on boot

After installation, the interface will be accessible immediately:
Open in browser: `http://<raspberry-pi-ip>:5000`

To manually control the service:
```bash
sudo systemctl start dcsbios.service    # Start
sudo systemctl stop dcsbios.service     # Stop  
sudo systemctl status dcsbios.service   # Check status
sudo systemctl disable dcsbios.service  # Disable auto-start
```

## 📋 Requirements

- Raspberry Pi (any model with USB and network)
- Raspberry Pi OS (Bullseye or newer)
- Python 3.9+
- DCS-BIOS compatible controllers (Arduino, Teensy, etc.)
- Network connection to DCS PC

The installer targets Debian-based systems (tested on Raspberry Pi OS). It will warn but continue on other Linux flavors; non-Debian distros may need manual dependency tweaks. The script also installs `uhubctl` so the web UI can toggle USB power—safe to leave in place even if your hardware does not support it.

## 🎯 How It Works

```
[DCS World PC] <--UDP--> [Raspberry Pi] <--Serial--> [Controllers]
     Windows                  Linux                   Arduino/Teensy
```

The Raspberry Pi acts as a bridge between DCS World and your physical controllers, managing all serial connections and data forwarding via UDP multicast.

## 📖 Documentation

### Quick Start

1. **Install** (one command):

  **Main Branch (Stable Release):**
  ```bash
  curl -sSL https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/main/install.sh | bash -s -- --branch main
  ```

  **Dev Branch (Latest Features):**
  ```bash
  curl -sSL https://raw.githubusercontent.com/Biggus22/DCSBIOS-web/dev/install.sh | bash -s -- --branch dev
  ```

   The installation automatically sets up a systemd service that starts the interface on boot.

2. **If prompted**, log out and log back in (for serial port permissions)

3. **Open browser**: `http://<pi-ip>:5000` (Find IP with `hostname -I`)

4. **First time setup**:
   - Go to Settings → Set DCS PC IP address
   - Click "Add Device" → Select USB port → Name it
   - Click "Start" button

The interface is now configured to start automatically when your Raspberry Pi boots up.

### New Features Documentation

#### 🔍 Serial Input Monitoring

Monitor the raw DCS BIOS input stream directly in the status messages:

1. **Enable monitoring**: Go to Settings → Toggle "Monitor DCS BIOS Input Stream"
2. **View data**: Real-time DCS BIOS commands will appear in the Status Messages box
3. **Format**: Messages appear as `[HH:MM:SS] DCS BIOS Input [DeviceName]: COMMAND DATA`

**Example output**:
```
[22:58:42] DCS BIOS Input [ACM0]: PLT_ENGINE_MASTER_R 1
```

This feature is invaluable for:
- Debugging hardware issues
- Verifying switch/panel functionality
- Understanding DCS BIOS protocol flow
- Troubleshooting communication problems

#### 💾 Client-Side Logging

Download all serial communication data directly to your computer:

1. **Monitor messages**: Ensure "Monitor DCS BIOS Input Stream" is enabled if you want to log raw data
2. **Access logs**: Scroll down to "Client-Side Logging" section
3. **Download**: Click "Download Log" button to save all messages to your computer
4. **Manage logs**: Use "Clear Log Buffer" to reset the log when needed

**Key benefits**:
- Logs saved directly to your viewing computer (not server)
- Near real-time updates (every 0.5 seconds)
- All data preserved for post-session analysis
- Timestamped entries for chronological review

### Daily Usage

The interface starts automatically when the Raspberry Pi boots up. To manually control it:

**Start the service:**
```bash
sudo systemctl start dcsbios.service
```

**Stop the service:**
```bash
sudo systemctl stop dcsbios.service
```

**Check service status:**
```bash
sudo systemctl status dcsbios.service
```

**Access from browser:**
```
http://<raspberry-pi-ip>:5000
```

## 🔧 Configuration

### Device Management

- **Add Device**: Automatically detects available USB ports
- **Enable/Disable**: Toggle devices without removing them
- **Delete**: Remove devices (must stop manager first)
- **Monitor**: Real-time connection status for all devices

### Settings

- **DCS PC IP**: IP address of your Windows PC running DCS
- **Auto-start**: Automatically start manager when accessing web interface
- **Scheduled Reboot**: Optional daily reboot for long-term stability
- **Device Reconnect**: Configure retry attempts, retry delay, and serial open pacing before giving up on a failed device or hammering the USB stack at startup

### System Controls

- **Boot Service**: Configure automatic startup on Pi boot (headless mode)
- **Reboot Pi**: Remote reboot capability
- **USB Power Control**: Emergency USB power off (requires reboot to restore)
- **Power Health Alert**: Dashboard banner lights up when `vcgencmd get_throttled` reports undervoltage/throttling, so you know to check the Pi's power brick or cabling.

## 🌟 Advanced Features

### Running on Boot (Headless Mode)

For hands-free operation:

The systemd service is automatically configured during installation to start the manager on boot.
To disable this behavior:
```bash
sudo systemctl disable dcsbios.service
```

**Check service status:**
```bash
sudo systemctl status dcsbios.service
```

**View logs:**
```bash
sudo journalctl -u dcsbios.service -f
```

### Multiple Raspberry Pis

You can run multiple Pis on the same network:
- Each Pi manages its own set of controllers
- All connect to the same DCS PC
- Use different ports if accessing multiple web interfaces simultaneously

### Configuration File

Located at: `~/.dcsbios/config.json`

```json
{
  "devices": [
    {
      "name": "UFC Panel",
      "port": "/dev/ttyACM0",
      "baudrate": 250000,
      "enabled": true
    }
  ],
  "dcs_pc_ip": "192.168.1.2",
  "auto_start": false,
  "scheduled_reboot_time": null,
  "max_reconnect_attempts": 5,
  "reconnect_delay_seconds": 3,
  "serial_open_spacing_seconds": 0.5
}
```

**Backup your configuration:**
```bash
cp ~/.dcsbios/config.json ~/dcsbios_config_backup.json
```

## 🛠️ Troubleshooting

### Can't Access Web Interface

**Check if it's running:**
```bash
ps aux | grep dcsbios_web
```

**Check Pi's IP address:**
```bash
hostname -I
```

**Restart the interface:**
```bash
cd ~/dcsbios_web
./stop.sh
./start.sh
```

### Serial Port Permission Errors

If you see "Permission denied" errors:

```bash
# Check if you're in the dialout group
groups

# If not listed, you need to log out and log back in
exit
# Then SSH back in
```

### Devices Won't Connect

1. **Check USB cable** - Try a different cable
2. **Check baudrate** - Most DCS-BIOS devices use 250000
3. **Check device** - Does it work when connected to Windows?
4. **Check power** - May need powered USB hub for many devices

### DCS Not Receiving Data

1. **Check DCS PC IP** - Must be correct in Settings
2. **Check network** - Ping the DCS PC: `ping <dcs-pc-ip>`
3. **Check DCS-BIOS Export.lua** - Must be installed in DCS
4. **Check Windows firewall** - May need to allow UDP port 7778

## 🔒 Security Notes

- The web interface has **no authentication** by default
- Only use on trusted local networks
- Don't expose to the internet without proper security (VPN recommended)
- The interface requires `sudo` privileges for system operations (reboot, USB control)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **DCS-BIOS Project** - Original protocol and Arduino libraries
- **DCSBiosRP2350** - Jbel26's DCS BIOS fork for RP2350 and RP2040
- **DCS Community** - Testing and feedback
- **OpenPhantom Discord** - Support and suggestions

## 📧 Support

- **Issues**: [GitHub Issues](https://github.com/Biggus22/DCSBIOS-web/issues)
- **Discussions**: [GitHub Discussions](https://github.com/Biggus22/DCSBIOS-web/discussions)

## 🔗 Related Projects

- [DCS-BIOS](https://github.com/DCSFlightpanels/dcs-bios) - Original DCS-BIOS project
- [DCS-BIOS Arduino Library](https://github.com/DCSFlightpanels/dcs-bios-arduino-library)

---

**Made with ❤️ for the DCS community**

If this project helps you, please consider giving it a star! ⭐
