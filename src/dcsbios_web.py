#!/usr/bin/env python3
"""
DCS-BIOS Controller Manager Web Interface
Web-based management interface accessible from any device on the network
"""

from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_cors import CORS
import json
import os
import subprocess
import threading
import time
from pathlib import Path
import glob
import socket
import struct
import serial

# Import the manager from the TUI script
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reuse the configuration classes from TUI
HOME_DIR = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME_DIR, ".dcsbios")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
os.makedirs(CONFIG_DIR, exist_ok=True)

class DeviceConfig:
    def __init__(self, name: str, port: str, baudrate: int = 250000, enabled: bool = True):
        self.name = name
        self.port = port
        self.baudrate = baudrate
        self.enabled = enabled
        self.status = "Stopped"
        self.last_activity = None

    def to_dict(self):
        return {
            "name": self.name,
            "port": self.port,
            "baudrate": self.baudrate,
            "enabled": self.enabled,
            "status": self.status,
            "last_activity": self.last_activity
        }

    @staticmethod
    def from_dict(data):
        return DeviceConfig(
            data.get("name", "Unknown"),
            data.get("port", ""),
            data.get("baudrate", 250000),
            data.get("enabled", True)
        )

class DCSBIOSWebManager:
    def __init__(self):
        self.devices = []
        self.running = False
        self.threads = []
        self.active_serial_ports = []
        self.udp_sock = None
        self.status_messages = []
        self.max_messages = 50

        # DCS-BIOS Configuration
        self.dcs_pc_ip = "192.168.1.2"
        self.udp_ip = "0.0.0.0"
        self.udp_port = 5010
        self.udp_dest_port = 7778
        self.multicast_group = "239.255.50.10"

        self.auto_start = False
        self.scheduled_reboot_time = None
        self.web_port = 5000  # Default web interface port

        self.load_config()

    def add_message(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        self.status_messages.append(f"[{timestamp}] {msg}")
        if len(self.status_messages) > self.max_messages:
            self.status_messages.pop(0)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.devices = [DeviceConfig.from_dict(d) for d in data.get("devices", [])]
                    self.dcs_pc_ip = data.get("dcs_pc_ip", self.dcs_pc_ip)
                    self.auto_start = data.get("auto_start", False)
                    self.scheduled_reboot_time = data.get("scheduled_reboot_time", None)
                    self.web_port = data.get("web_port", self.web_port)
                self.add_message(f"Loaded {len(self.devices)} devices from config")
                self.add_message(f"DCS PC IP: {self.dcs_pc_ip}")
                self.add_message(f"Auto start: {self.auto_start}")
                self.add_message(f"Scheduled reboot time: {self.scheduled_reboot_time}")
                self.add_message(f"Web port: {self.web_port}")
            except Exception as e:
                self.add_message(f"Error loading config: {e}")
        else:
            self.add_message("No config file found, starting fresh")

    def save_config(self):
        try:
            data = {
                "devices": [d.to_dict() for d in self.devices],
                "dcs_pc_ip": self.dcs_pc_ip,
                "auto_start": self.auto_start,
                "scheduled_reboot_time": self.scheduled_reboot_time,
                "web_port": self.web_port
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            self.add_message(f"Config saved - Web Port: {self.web_port}, DCS IP: {self.dcs_pc_ip}, Auto Start: {self.auto_start}, Scheduled Reboot: {self.scheduled_reboot_time}")
        except Exception as e:
            self.add_message(f"Error saving config: {e}")

    def setup_udp(self):
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind((self.udp_ip, self.udp_port))

            mreq = struct.pack("=4sl", socket.inet_aton(self.multicast_group), socket.INADDR_ANY)
            self.udp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self.add_message(f"UDP socket listening on port {self.udp_port}")
        except Exception as e:
            self.add_message(f"UDP setup error: {e}")

    def is_dcsbios_export_packet(self, data):
        return len(data) >= 4 and data[0] == 0x55 and data[1] == 0x55 and data[2] == 0x55 and data[3] == 0x55

    def serial_to_udp(self, device: DeviceConfig):
        if not device.enabled:
            return

        ser = None
        device.status = "Connecting"

        while self.running:
            try:
                if ser is None or not ser.is_open:
                    ser = serial.Serial(device.port, device.baudrate, timeout=0.1)
                    device.status = "Connected"
                    self.add_message(f"{device.name} connected on {device.port}")

                if ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    if data:
                        clean_data = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        self.udp_sock.sendto(clean_data, (self.dcs_pc_ip, self.udp_dest_port))
                        device.last_activity = time.time()
                else:
                    time.sleep(0.005)

            except (serial.SerialException, PermissionError) as e:
                device.status = "Error"
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None
                time.sleep(3)
            except Exception as e:
                device.status = "Error"
                time.sleep(5)

        if ser and ser.is_open:
            try:
                ser.close()
            except:
                pass
        device.status = "Stopped"

    def udp_to_serial(self):
        self.active_serial_ports = []

        for device in self.devices:
            if device.enabled:
                try:
                    ser = serial.Serial(device.port, device.baudrate, timeout=0.1)
                    self.active_serial_ports.append({
                        "name": device.name,
                        "port": ser,
                        "device": device
                    })
                    self.add_message(f"Opened {device.name} for UDP forwarding")
                except Exception as e:
                    self.add_message(f"Could not open {device.name}: {e}")

        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(1024)

                if addr[0] != self.dcs_pc_ip:
                    continue

                if not self.is_dcsbios_export_packet(data):
                    continue

                for entry in self.active_serial_ports:
                    ser = entry["port"]
                    device = entry["device"]
                    if ser and ser.is_open:
                        try:
                            ser.write(data)
                            device.last_activity = time.time()
                        except Exception:
                            pass

            except Exception as e:
                time.sleep(1)

        for entry in self.active_serial_ports:
            if entry["port"] and entry["port"].is_open:
                try:
                    entry["port"].close()
                except:
                    pass

    def start(self):
        if self.running:
            self.add_message("Already running!")
            return False

        self.running = True
        self.setup_udp()

        udp_thread = threading.Thread(target=self.udp_to_serial, daemon=True)
        udp_thread.start()
        self.threads.append(udp_thread)

        for device in self.devices:
            if device.enabled:
                thread = threading.Thread(target=self.serial_to_udp, args=(device,), daemon=True)
                thread.start()
                self.threads.append(thread)

        self.add_message("DCS-BIOS manager started")
        return True

    def stop(self):
        if not self.running:
            return False

        self.running = False
        self.add_message("Stopping DCS-BIOS manager...")
        time.sleep(1)
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except:
                pass
        for device in self.devices:
            device.status = "Stopped"
        self.threads = []
        self.add_message("DCS-BIOS manager stopped")
        return True

# Initialize Flask app
app = Flask(__name__)
CORS(app)
manager = DCSBIOSWebManager()

# Scheduled reboot checker
def reboot_checker():
    while True:
        if manager.scheduled_reboot_time:
            current_time = time.strftime("%H:%M")
            if current_time == manager.scheduled_reboot_time:
                manager.add_message(f"Scheduled reboot at {current_time}")
                if manager.running:
                    manager.stop()
                try:
                    subprocess.run(["sudo", "uhubctl", "-l", "1-1", "-p", "2", "-a", "0"],
                                 capture_output=True, timeout=5)
                except:
                    pass
                time.sleep(2)
                subprocess.run(["sudo", "reboot"])
                break
        time.sleep(30)

reboot_thread = threading.Thread(target=reboot_checker, daemon=True)
reboot_thread.start()

# Auto-start if enabled
if manager.auto_start:
    manager.start()
    print(f"Auto-start enabled: Manager started automatically as configured")

# Web Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    return jsonify({
        'running': manager.running,
        'dcs_pc_ip': manager.dcs_pc_ip,
        'auto_start': manager.auto_start,
        'scheduled_reboot_time': manager.scheduled_reboot_time,
        'web_port': manager.web_port,
        'devices': [d.to_dict() for d in manager.devices],
        'messages': manager.status_messages[-20:]
    })

@app.route('/api/start', methods=['POST'])
def api_start():
    success = manager.start()
    return jsonify({'success': success})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    success = manager.stop()
    return jsonify({'success': success})

@app.route('/api/device/toggle/<int:index>', methods=['POST'])
def api_toggle_device(index):
    if 0 <= index < len(manager.devices):
        device = manager.devices[index]
        device.enabled = not device.enabled
        manager.save_config()
        manager.add_message(f"{device.name}: {'Enabled' if device.enabled else 'Disabled'}")
        return jsonify({'success': True, 'enabled': device.enabled})
    return jsonify({'success': False, 'error': 'Invalid device index'})

@app.route('/api/device/add', methods=['POST'])
def api_add_device():
    data = request.json
    name = data.get('name')
    port = data.get('port')
    baudrate = data.get('baudrate', 250000)

    if name and port:
        new_device = DeviceConfig(name, port, baudrate, True)
        manager.devices.append(new_device)
        manager.save_config()
        manager.add_message(f"Added device: {name}")
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Missing name or port'})

@app.route('/api/device/delete/<int:index>', methods=['POST'])
def api_delete_device(index):
    if 0 <= index < len(manager.devices):
        device = manager.devices[index]
        if manager.running and device.enabled:
            return jsonify({'success': False, 'error': 'Stop manager first or disable device'})

        manager.devices.pop(index)
        manager.save_config()
        manager.add_message(f"Deleted device: {device.name}")
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid device index'})

@app.route('/api/settings/dcs_ip', methods=['POST'])
def api_set_dcs_ip():
    data = request.json
    new_ip = data.get('ip')
    if new_ip:
        manager.dcs_pc_ip = new_ip
        manager.save_config()
        manager.add_message(f"DCS PC IP set to: {new_ip}")
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid IP'})

@app.route('/api/settings/auto_start', methods=['POST'])
def api_toggle_auto_start():
    manager.auto_start = not manager.auto_start
    manager.save_config()
    manager.add_message(f"Auto-start {'enabled' if manager.auto_start else 'disabled'}")
    return jsonify({'success': True, 'auto_start': manager.auto_start})

@app.route('/api/settings/web_port', methods=['POST'])
def api_set_web_port():
    data = request.json
    new_port = data.get('port')
    if new_port and isinstance(new_port, int) and 1 <= new_port <= 65535:
        manager.web_port = new_port
        manager.save_config()
        manager.add_message(f"Web interface port set to: {new_port}")
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid port number. Must be between 1 and 65535'})

@app.route('/api/settings/schedule_reboot', methods=['POST'])
def api_schedule_reboot():
    data = request.json
    time_str = data.get('time')

    if time_str == "clear":
        manager.scheduled_reboot_time = None
        manager.save_config()
        manager.add_message("Scheduled reboot cleared")
        return jsonify({'success': True})

    # Validate time format
    if time_str and len(time_str) == 5 and time_str[2] == ':':
        try:
            hour, minute = time_str.split(':')
            h, m = int(hour), int(minute)
            if 0 <= h <= 23 and 0 <= m <= 59:
                manager.scheduled_reboot_time = time_str
                manager.save_config()
                manager.add_message(f"Reboot scheduled for {time_str}")
                return jsonify({'success': True})
        except:
            pass

    return jsonify({'success': False, 'error': 'Invalid time format. Use HH:MM'})

@app.route('/api/usb/off', methods=['POST'])
def api_usb_off():
    if manager.running:
        manager.stop()
        time.sleep(1)

    success = False
    error_msg = ""
    
    # Method 1: Try uhubctl with multiple approaches for USB power off
    try:
        # First, check what USB hubs are available
        hubs_result = subprocess.run(
            ["sudo", "uhubctl"],
            capture_output=True, text=True, timeout=5
        )
        manager.add_message(f"Available USB hubs: {hubs_result.stdout}")

        # Try multiple approaches for USB power off
        hub_port_commands = [
            ["sudo", "uhubctl", "-l", "1-1", "-p", "2", "-a", "0"],  # Current approach
            ["sudo", "uhubctl", "-a", "0", "-p", "2"],  # Alternative: Just target port 2
            ["sudo", "uhubctl", "-a", "0"],  # Turn off all ports
        ]
        
        for cmd in hub_port_commands:
            manager.add_message(f"Trying command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            manager.add_message(f"Command result: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}")
            
            if result.returncode == 0:
                manager.add_message("USB power off command executed successfully")
                success = True
                break
            else:
                error_msg = result.stderr
    
        # If uhubctl methods fail, try alternative approaches
        if not success:
            manager.add_message("uhubctl methods failed, trying alternative approaches...")
            
            # Method 2: Use modprobe to remove USB drivers (more aggressive)
            try:
                # Remove USB storage drivers to disconnect devices
                subprocess.run(["sudo", "modprobe", "-r", "usb-storage"], capture_output=True, timeout=5)
                manager.add_message("Removed usb-storage module")
            except:
                manager.add_message("Could not remove usb-storage module")
            
            # Method 3: Try to remove other USB-related modules
            try:
                subprocess.run(["sudo", "modprobe", "-r", "usbhid"], capture_output=True, timeout=5)
                manager.add_message("Removed usbhid module")
            except:
                manager.add_message("Could not remove usbhid module")
                
            # Method 4: Write directly to USB port power control (if available)
            try:
                # Try to find and modify USB power settings in sysfs
                import glob
                usb_power_paths = glob.glob("/sys/bus/usb/devices/*/authorized")
                for path in usb_power_paths:
                    try:
                        with open(path, 'w') as f:
                            f.write('0')  # Unauthorize the USB device
                        manager.add_message(f"Unauthorized USB device: {path}")
                        success = True
                    except Exception as e:
                        manager.add_message(f"Could not unauthorized USB device {path}: {str(e)}")
            except:
                manager.add_message("Could not access USB sysfs power controls")
        
        if success:
            manager.add_message("USB power control: Command executed - REBOOT REQUIRED for full effect")
            return jsonify({'success': True})
        else:
            manager.add_message(f"All USB power off methods failed: {error_msg}")
            return jsonify({'success': False, 'error': f'All methods failed: {error_msg}'})

    except FileNotFoundError:
        manager.add_message("Error: uhubctl not installed")
        return jsonify({'success': False, 'error': 'uhubctl not installed'})
    except Exception as e:
        manager.add_message(f"Error in USB OFF: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/reboot', methods=['POST'])
def api_reboot():
    if manager.running:
        manager.stop()
    manager.add_message("Rebooting system...")
    time.sleep(2)
    subprocess.Popen(["sudo", "reboot"])
    return jsonify({'success': True})

@app.route('/api/ports')
def api_list_ports():
    ports = []
    patterns = ['/dev/ttyACM*', '/dev/ttyUSB*', '/dev/ttyAMA*', '/dev/ttyS*']

    all_ports = []
    for pattern in patterns:
        all_ports.extend(glob.glob(pattern))
    all_ports.sort()

    configured_ports = {device.port for device in manager.devices}

    for port in all_ports:
        status = "configured" if port in configured_ports else "available"
        info = get_port_info(port)
        ports.append({
            'port': port,
            'info': info,
            'status': status
        })

    return jsonify({'ports': ports})

def get_port_info(port):
    """Get information about a serial port"""
    try:
        result = subprocess.run(
            ['udevadm', 'info', '-q', 'property', '-n', port],
            capture_output=True, text=True, timeout=2
        )

        if result.returncode == 0:
            props = {}
            for line in result.stdout.split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    props[key] = value

            vendor = props.get('ID_VENDOR', '')
            model = props.get('ID_MODEL', '')
            serial = props.get('ID_SERIAL_SHORT', '')

            if vendor and model:
                info = f"{vendor} {model}"
                if serial:
                    info += f" (S/N: {serial})"
                return info
    except:
        pass

    if 'ACM' in port:
        return "USB CDC ACM Device"
    elif 'USB' in port:
        return "USB to Serial Adapter"
    return "Serial Device"

@app.route('/api/boot_service/status')
def api_boot_service_status():
    service_file = "/etc/systemd/system/dcsbios.service"
    if os.path.exists(service_file):
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", "dcsbios.service"],
                capture_output=True, text=True
            )
            if result.returncode == 0 and "enabled" in result.stdout:
                return jsonify({'status': 'enabled'})
            else:
                return jsonify({'status': 'installed'})
        except:
            return jsonify({'status': 'installed'})
    return jsonify({'status': 'not_installed'})

@app.route('/api/boot_service/install', methods=['POST'])
def api_boot_service_install():
    if not manager.auto_start:
        return jsonify({'success': False, 'error': 'Enable auto-start first'})

    script_path = os.path.abspath(__file__)
    service_content = f"""[Unit]
Description=DCS-BIOS Controller Manager Web
After=network.target

[Service]
Type=simple
User={os.getenv('USER')}
WorkingDirectory={os.path.dirname(script_path)}
ExecStart=/usr/bin/python3 {script_path} --headless
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

    try:
        service_file = "/tmp/dcsbios.service"
        with open(service_file, 'w') as f:
            f.write(service_content)

        result = subprocess.run(
            ["sudo", "cp", service_file, "/etc/systemd/system/dcsbios.service"],
            capture_output=True, text=True
        )

        if result.returncode == 0:
            subprocess.run(["sudo", "systemctl", "daemon-reload"])
            subprocess.run(["sudo", "systemctl", "enable", "dcsbios.service"])
            manager.add_message("Boot service installed and enabled")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/boot_service/enable', methods=['POST'])
def api_boot_service_enable():
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "enable", "dcsbios.service"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            manager.add_message("Boot service enabled")
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/boot_service/disable', methods=['POST'])
def api_boot_service_disable():
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "disable", "dcsbios.service"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            manager.add_message("Boot service disabled")
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/boot_service/uninstall', methods=['POST'])
def api_boot_service_uninstall():
    try:
        subprocess.run(["sudo", "systemctl", "disable", "dcsbios.service"], capture_output=True)
        subprocess.run(["sudo", "systemctl", "stop", "dcsbios.service"], capture_output=True)
        result = subprocess.run(
            ["sudo", "rm", "/etc/systemd/system/dcsbios.service"],
            capture_output=True, text=True
        )
        subprocess.run(["sudo", "systemctl", "daemon-reload"])

        if result.returncode == 0:
            manager.add_message("Boot service uninstalled")
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='DCS-BIOS Web Interface')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, help='Port to bind to (defaults to value in config file)')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode (for systemd)')
    args = parser.parse_args()

    # Use port from config if not explicitly provided via command line
    if args.port is None:
        args.port = manager.web_port

    print(f"DCS-BIOS Web Interface")
    print(f"Config location: {CONFIG_FILE}")
    print(f"Starting web server on http://{args.host}:{args.port}")
    print(f"Access from your network at http://<raspberry-pi-ip>:{args.port}")
    print()

    app.run(host=args.host, port=args.port, debug=False, threaded=True)