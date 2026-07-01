#!/usr/bin/env python3
"""
DCS-BIOS Controller Manager Web Interface
Web-based management interface accessible from any device on the network
"""

from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_cors import CORS
import datetime
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

# System monitoring functions
def get_cpu_temperature():
    """Get CPU temperature in Celsius"""
    try:
        # Try Raspberry Pi thermal zone
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read().strip()) / 1000.0
            return round(temp, 1)
    except:
        pass

    # Fallback: try other thermal zones
    try:
        for i in range(5):
            try:
                with open(f'/sys/class/thermal/thermal_zone{i}/temp', 'r') as f:
                    temp = int(f.read().strip()) / 1000.0
                    return round(temp, 1)
            except:
                continue
    except:
        pass

    # Try using vcgencmd (Raspberry Pi specific)
    try:
        result = subprocess.run(['vcgencmd', 'measure_temp'],
                              capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            # Output format: "temp=45.6'C"
            temp_str = result.stdout.strip()
            if "temp=" in temp_str:
                temp_value = temp_str.split('=')[1].split("'")[0]
                return round(float(temp_value), 1)
    except:
        pass

    return None

def get_memory_info():
    """Get memory information"""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
            mem_info = {}
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip().split()[0]  # Get the numeric value
                    try:
                        mem_info[key] = int(value)
                    except:
                        pass

            if 'MemTotal' in mem_info and 'MemAvailable' in mem_info:
                total_mb = mem_info['MemTotal'] // 1024
                available_mb = mem_info['MemAvailable'] // 1024
                used_mb = total_mb - available_mb
                return {
                    'total': total_mb,
                    'used': used_mb,
                    'available': available_mb,
                    'percentage': round((used_mb / total_mb) * 100, 1)
                }
    except:
        pass

    return None

def get_throttled_status():
    """Get Raspberry Pi throttled/undervoltage status via vcgencmd"""
    try:
        result = subprocess.run(
            ['vcgencmd', 'get_throttled'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if 'throttled=' not in output:
            return None

        hex_value = output.split('=', 1)[1].strip()
        # Allow values with or without 0x prefix
        parsed_hex = hex_value[2:] if hex_value.lower().startswith('0x') else hex_value
        value = int(parsed_hex, 16)

        return {
            'raw': hex_value,
            'undervoltage_now': bool(value & 0x1),
            'freq_capped_now': bool(value & 0x2),
            'throttled_now': bool(value & 0x4),
            'temp_limit_now': bool(value & 0x8),
            'undervoltage_occurred': bool(value & 0x10000),
            'freq_capped_occurred': bool(value & 0x20000),
            'throttled_occurred': bool(value & 0x40000),
            'temp_limit_occurred': bool(value & 0x80000)
        }
    except Exception:
        return None

# Import the manager from the TUI script
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reuse the configuration classes from TUI
HOME_DIR = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME_DIR, ".dcsbios")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
os.makedirs(CONFIG_DIR, exist_ok=True)

XHCI_RESET_SCRIPT = "/usr/local/sbin/xhci-reset"

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
        self.serial_input_monitoring = False  # Flag to control serial input monitoring

        # DCS-BIOS Configuration
        self.dcs_pc_ip = "192.168.1.2"
        self.udp_ip = "0.0.0.0"
        self.udp_port = 5010
        self.udp_dest_port = 7778
        self.multicast_group = "239.255.50.10"  # Multicast group for DCS-BIOS

        self.auto_start = False
        self.scheduled_reboot_time = None
        self.last_reboot_execution_date = None
        self.web_port = 5000  # Default web interface port
        self.serial_input_monitoring = False  # Whether to show DCS BIOS input stream in status messages
        self.max_reconnect_attempts = 5
        self.reconnect_delay_seconds = 3
        self.serial_open_spacing_seconds = 0.5
        self.low_voltage_event_logging = False
        self.last_low_voltage_detected_at = None
        self.last_power_status = None
        self.serial_open_lock = threading.Lock()
        self.next_serial_open_time = 0.0

        # USB watchdog configuration
        self.xhci_watchdog_enabled = True
        self.xhci_watchdog_check_interval = 60    # seconds between checks
        self.xhci_watchdog_error_threshold = 300  # seconds all devices must be errored before reset
        self.xhci_reset_count = 0                 # total resets performed this session
        self.xhci_last_reset_at = None            # timestamp of last reset

        self.load_config()

        # Start USB watchdog thread
        watchdog_thread = threading.Thread(target=self.usb_watchdog, daemon=True)
        watchdog_thread.start()

    def add_message(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {msg}"
        self.status_messages.append(formatted_msg)
        if len(self.status_messages) > self.max_messages:
            self.status_messages.pop(0)

    def format_serial_data(self, data: bytes) -> str:
        """
        Format binary serial data for readable output
        """
        # Decode to string, replacing non-decodable bytes with a placeholder
        try:
            decoded = data.decode('utf-8', errors='replace')
        except:
            decoded = str(data)

        # Convert non-printable characters to hex representation for better readability
        readable = ""
        for byte in data:
            if 32 <= byte <= 126:  # Printable ASCII range
                readable += chr(byte)
            elif byte == 10:  # Newline - skip (don't show)
                continue
            elif byte == 13:  # Carriage return - skip (don't show)
                continue
            else:
                readable += f"[{byte:02X}]"

        return readable

    def current_timestamp_iso(self) -> str:
        return datetime.datetime.now().astimezone().isoformat(timespec='seconds')

    def current_timestamp_label(self) -> str:
        return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def update_power_status(self):
        power = get_throttled_status()
        if power is None:
            self.last_power_status = None
            return None

        previous_power = self.last_power_status or {}
        new_undervoltage_event = (
            (power.get('undervoltage_now') and not previous_power.get('undervoltage_now', False))
            or (power.get('undervoltage_occurred') and not previous_power.get('undervoltage_occurred', False))
        )

        if new_undervoltage_event:
            self.last_low_voltage_detected_at = self.current_timestamp_iso()
            if self.low_voltage_event_logging:
                self.add_message(
                    f"Low voltage detected at {self.current_timestamp_label()} ({power.get('raw', 'unknown')})"
                )
            self.save_config(emit_message=False)

        power['last_low_voltage_detected_at'] = self.last_low_voltage_detected_at
        power['low_voltage_event_logging'] = self.low_voltage_event_logging
        self.last_power_status = dict(power)
        return power

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.devices = [DeviceConfig.from_dict(d) for d in data.get("devices", [])]
                    self.dcs_pc_ip = data.get("dcs_pc_ip", self.dcs_pc_ip)
                    self.auto_start = data.get("auto_start", False)
                    self.scheduled_reboot_time = data.get("scheduled_reboot_time", None)
                    self.last_reboot_execution_date = data.get("last_reboot_execution_date")
                    self.web_port = data.get("web_port", self.web_port)
                    self.udp_port = data.get("udp_port", self.udp_port)
                    self.multicast_group = data.get("multicast_group", self.multicast_group)
                    self.serial_input_monitoring = data.get("serial_input_monitoring", False)
                    self.max_reconnect_attempts = data.get("max_reconnect_attempts", self.max_reconnect_attempts)
                    self.reconnect_delay_seconds = data.get("reconnect_delay_seconds", self.reconnect_delay_seconds)
                    self.serial_open_spacing_seconds = data.get("serial_open_spacing_seconds", self.serial_open_spacing_seconds)
                    self.low_voltage_event_logging = data.get("low_voltage_event_logging", self.low_voltage_event_logging)
                    self.last_low_voltage_detected_at = data.get("last_low_voltage_detected_at")
                    self.xhci_watchdog_enabled = data.get("xhci_watchdog_enabled", self.xhci_watchdog_enabled)
                    self.xhci_watchdog_error_threshold = data.get("xhci_watchdog_error_threshold", self.xhci_watchdog_error_threshold)
                self.add_message(f"Loaded {len(self.devices)} devices from config")
                self.add_message(f"DCS PC IP: {self.dcs_pc_ip}")
                self.add_message(f"Auto start: {self.auto_start}")
                self.add_message(f"Scheduled reboot time: {self.scheduled_reboot_time}")
                self.add_message(f"Web port: {self.web_port}")
                self.add_message(f"UDP port: {self.udp_port}")
                self.add_message(f"Multicast group: {self.multicast_group}")
                self.add_message(f"Serial input monitoring: {self.serial_input_monitoring}")
                self.add_message(
                    f"Reconnect attempts: {self.max_reconnect_attempts}, delay: {self.reconnect_delay_seconds}s, open spacing: {self.serial_open_spacing_seconds:g}s"
                )
                self.add_message(f"Low voltage event logging: {self.low_voltage_event_logging}")
                self.add_message(
                    f"USB watchdog: {'enabled' if self.xhci_watchdog_enabled else 'disabled'}, "
                    f"threshold: {self.xhci_watchdog_error_threshold}s"
                )
            except Exception as e:
                self.add_message(f"Error loading config: {e}")
        else:
            self.add_message("No config file found, starting fresh")

    def save_config(self, emit_message: bool = True):
        try:
            data = {
                "devices": [d.to_dict() for d in self.devices],
                "dcs_pc_ip": self.dcs_pc_ip,
                "auto_start": self.auto_start,
                "scheduled_reboot_time": self.scheduled_reboot_time,
                "last_reboot_execution_date": self.last_reboot_execution_date,
                "web_port": self.web_port,
                "udp_port": self.udp_port,
                "multicast_group": self.multicast_group,
                "serial_input_monitoring": self.serial_input_monitoring,
                "max_reconnect_attempts": self.max_reconnect_attempts,
                "reconnect_delay_seconds": self.reconnect_delay_seconds,
                "serial_open_spacing_seconds": self.serial_open_spacing_seconds,
                "low_voltage_event_logging": self.low_voltage_event_logging,
                "last_low_voltage_detected_at": self.last_low_voltage_detected_at,
                "xhci_watchdog_enabled": self.xhci_watchdog_enabled,
                "xhci_watchdog_error_threshold": self.xhci_watchdog_error_threshold,
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            if emit_message:
                self.add_message(
                    f"Config saved - Web Port: {self.web_port}, DCS IP: {self.dcs_pc_ip}, Auto Start: {self.auto_start}, "
                    f"Scheduled Reboot: {self.scheduled_reboot_time}, UDP Port: {self.udp_port}, Multicast: {self.multicast_group}, "
                    f"Serial Input Monitoring: {self.serial_input_monitoring}, Reconnect Attempts: {self.max_reconnect_attempts}, "
                    f"Reconnect Delay: {self.reconnect_delay_seconds}s, Serial Open Spacing: {self.serial_open_spacing_seconds:g}s, "
                    f"Low Voltage Logging: {self.low_voltage_event_logging}, "
                    f"USB Watchdog: {'enabled' if self.xhci_watchdog_enabled else 'disabled'}, "
                    f"Watchdog Threshold: {self.xhci_watchdog_error_threshold}s"
                )
        except Exception as e:
            self.add_message(f"Error saving config: {e}")

    # -------------------------------------------------------------------------
    # USB / xHCI watchdog
    # -------------------------------------------------------------------------

    def reset_xhci_controller(self) -> bool:
        """
        Reset the xHCI USB controller via the dedicated root-owned wrapper script.
        The script is /usr/local/sbin/xhci-reset and is the only thing brad can
        sudo without a password, limiting the attack surface to that one action.
        """
        if not os.path.exists(XHCI_RESET_SCRIPT):
            self.add_message(
                f"xHCI reset script not found at {XHCI_RESET_SCRIPT} — skipping reset. "
                "Run the setup commands to install it."
            )
            return False

        self.add_message("Triggering xHCI controller reset via wrapper script...")
        try:
            result = subprocess.run(
                ['sudo', '-n', XHCI_RESET_SCRIPT],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                self.xhci_reset_count += 1
                self.xhci_last_reset_at = self.current_timestamp_iso()
                self.add_message(
                    f"xHCI reset complete (total resets this session: {self.xhci_reset_count})"
                )
                return True
            else:
                self.add_message(
                    f"xHCI reset script failed (exit {result.returncode}): "
                    f"{(result.stderr or result.stdout or '').strip()}"
                )
                return False
        except subprocess.TimeoutExpired:
            self.add_message("xHCI reset script timed out after 30s")
            return False
        except Exception as e:
            self.add_message(f"xHCI reset failed: {e}")
            return False

    def usb_watchdog(self):
        """
        Background thread — monitors device error states and triggers an xHCI
        controller reset if all enabled devices remain in error for longer than
        xhci_watchdog_error_threshold seconds.

        After a successful reset the manager is restarted so serial threads
        reconnect to the freshly-enumerated devices.
        """
        all_errored_since = None

        while True:
            time.sleep(self.xhci_watchdog_check_interval)

            if not self.xhci_watchdog_enabled:
                all_errored_since = None
                continue

            if not self.running:
                all_errored_since = None
                continue

            enabled = [d for d in self.devices if d.enabled]
            if not enabled:
                all_errored_since = None
                continue

            all_errored = all(d.status == "Error" for d in enabled)

            if all_errored:
                if all_errored_since is None:
                    all_errored_since = time.time()
                    self.add_message(
                        f"USB watchdog: all {len(enabled)} device(s) in error state — "
                        f"will reset xHCI in {self.xhci_watchdog_error_threshold}s if not recovered"
                    )
                elif time.time() - all_errored_since >= self.xhci_watchdog_error_threshold:
                    elapsed = int(time.time() - all_errored_since)
                    self.add_message(
                        f"USB watchdog: all devices stuck in error for {elapsed}s — "
                        "stopping manager and resetting xHCI controller"
                    )
                    was_running = self.running
                    self.stop()
                    time.sleep(2)

                    success = self.reset_xhci_controller()

                    if success and was_running:
                        # Give the kernel time to re-enumerate all devices
                        self.add_message("USB watchdog: waiting 8s for device re-enumeration...")
                        time.sleep(8)
                        self.start()
                        self.add_message("USB watchdog: manager restarted after xHCI reset")
                    elif not success:
                        self.add_message(
                            "USB watchdog: xHCI reset failed — manual intervention may be required"
                        )
                        if was_running:
                            self.start()

                    all_errored_since = None
            else:
                if all_errored_since is not None:
                    self.add_message(
                        "USB watchdog: devices recovered — cancelling reset countdown"
                    )
                all_errored_since = None

    # -------------------------------------------------------------------------
    # Serial / UDP core logic (unchanged)
    # -------------------------------------------------------------------------

    def wait_for_serial_open_slot(self):
        spacing = max(0.0, float(self.serial_open_spacing_seconds))
        if spacing == 0:
            return self.running

        while self.running:
            with self.serial_open_lock:
                now = time.time()
                wait_time = self.next_serial_open_time - now
                if wait_time <= 0:
                    self.next_serial_open_time = now + spacing
                    return True
            time.sleep(min(wait_time, 0.05) if wait_time > 0 else 0.01)

        return False

    def open_serial_port(self, device: DeviceConfig):
        if not self.wait_for_serial_open_slot():
            raise RuntimeError("Manager stopped before serial port open")
        ser = serial.Serial(device.port, device.baudrate, timeout=0.1)
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except:
            pass
        return ser

    def _retry_delay(self, consecutive_failures: int) -> float:
        if consecutive_failures <= 1:
            return self.reconnect_delay_seconds
        return min(self.reconnect_delay_seconds * (2 ** (consecutive_failures - 1)), 60)

    def setup_udp(self):
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind((self.udp_ip, self.udp_port))
            self.udp_sock.settimeout(0.5)

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
        consecutive_failures = 0
        port_missing_reported = False

        while self.running:
            try:
                if ser is None or not ser.is_open:
                    ser = self.open_serial_port(device)
                    consecutive_failures = 0
                    port_missing_reported = False
                    device.status = "Connected"
                    self.add_message(f"{device.name} connected on {device.port}")

                if ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    if data:
                        clean_data = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')

                        # Add DCS BIOS input stream to status messages if monitoring is enabled
                        if self.serial_input_monitoring:
                            formatted_data = self.format_serial_data(data)
                            self.add_message(f"DCS BIOS Input [{device.name}]: {formatted_data}")

                        self.udp_sock.sendto(clean_data, (self.dcs_pc_ip, self.udp_dest_port))
                        device.last_activity = time.time()
                        consecutive_failures = 0
                else:
                    time.sleep(0.005)

            except (serial.SerialException, PermissionError) as e:
                device.status = "Error"
                consecutive_failures += 1
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None

                delay = self._retry_delay(consecutive_failures)
                is_enoint = isinstance(e, OSError) and getattr(e, 'errno', None) == 2
                if is_enoint:
                    if not port_missing_reported:
                        self.add_message(f"{device.name} port {device.port} missing — retrying when available")
                        port_missing_reported = True
                else:
                    port_missing_reported = False
                    self.add_message(f"{device.name} error ({e}). Retry in {delay}s")
                time.sleep(delay)
            except Exception as e:
                device.status = "Error"
                consecutive_failures += 1
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None

                delay = self._retry_delay(consecutive_failures)
                self.add_message(f"{device.name} unexpected error ({e}). Retry in {delay}s")
                time.sleep(delay)

        if ser and ser.is_open:
            try:
                ser.close()
            except:
                pass
        if not self.running:
            device.status = "Stopped"

    def udp_to_serial(self):
        self.active_serial_ports = []

        for device in self.devices:
            if device.enabled:
                self.active_serial_ports.append({
                    "name": device.name,
                    "port": None,
                    "device": device,
                    "failures": 0,
                    "next_retry": 0.0,
                    "port_missing_reported": False
                })

        while self.running:
            now = time.time()

            for entry in self.active_serial_ports:
                ser = entry["port"]
                device = entry["device"]

                if ser and ser.is_open:
                    continue

                if now < entry["next_retry"]:
                    continue

                try:
                    entry["port"] = self.open_serial_port(device)
                    entry["failures"] = 0
                    entry["next_retry"] = 0.0
                    entry["port_missing_reported"] = False
                    device.status = "Connected"
                    self.add_message(f"Opened {device.name} for UDP forwarding")
                except Exception as e:
                    entry["failures"] += 1
                    device.status = "Error"
                    delay = self._retry_delay(entry["failures"])
                    entry["next_retry"] = now + delay
                    is_enoint = isinstance(e, OSError) and getattr(e, 'errno', None) == 2
                    if is_enoint:
                        if not entry["port_missing_reported"]:
                            self.add_message(f"{device.name} port {device.port} missing — retrying when available")
                            entry["port_missing_reported"] = True
                    else:
                        entry["port_missing_reported"] = False
                        self.add_message(f"Could not open {device.name} ({e}). Retry in {delay}s")

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
                            entry["failures"] = 0
                        except Exception as e:
                            try:
                                ser.close()
                            except:
                                pass
                            entry["port"] = None
                            entry["failures"] += 1
                            device.status = "Error"
                            delay = self._retry_delay(entry["failures"])
                            entry["next_retry"] = time.time() + delay
                            self.add_message(
                                f"UDP forwarding error on {device.name} ({e}). Retry in {delay}s"
                            )

            except socket.timeout:
                continue
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
        with self.serial_open_lock:
            self.next_serial_open_time = time.time()
        self.setup_udp()

        if self.serial_open_spacing_seconds > 0:
            self.add_message(
                f"Pacing serial open attempts by {self.serial_open_spacing_seconds:g}s"
            )

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


def launch_system_power_action(command, action_label: str):
    """Launch a system power action and report whether the OS accepted it."""
    if os.name == 'nt':
        return False, f"{action_label} is only supported on Linux targets"

    geteuid = getattr(os, 'geteuid', None)
    if callable(geteuid) and geteuid() == 0:
        full_command = command
    else:
        full_command = ['sudo', '-n', *command]

    try:
        process = subprocess.Popen(
            full_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.5)
        returncode = process.poll()

        if returncode is not None and returncode != 0:
            stdout, stderr = process.communicate(timeout=1)
            error_output = (stderr or stdout or '').strip()
            if not error_output:
                error_output = f"command exited with status {returncode}"
            return False, error_output

        return True, None
    except FileNotFoundError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def request_system_power_action(action_label: str, command):
    """Stop the manager, then trigger the power action after the HTTP response returns."""
    was_running = manager.running
    if was_running:
        manager.stop()

    manager.add_message(f"{action_label} requested")

    def worker():
        time.sleep(1)
        success, error = launch_system_power_action(command, action_label)
        if success:
            manager.add_message(f"{action_label} command accepted by the OS")
            return

        manager.add_message(f"{action_label} failed: {error}")
        if was_running:
            restarted = manager.start()
            if restarted:
                manager.add_message(f"Manager restarted after {action_label.lower()} failed")
            else:
                manager.add_message(f"Manager could not be restarted after {action_label.lower()} failed")

    threading.Thread(target=worker, daemon=True).start()
    return True, None

# Scheduled reboot checker
def reboot_checker():
    poll_interval_seconds = 300  # 5 minutes
    last_check = datetime.datetime.now()

    def window_reached(previous_ts: datetime.datetime, current_ts: datetime.datetime, target_time: datetime.time) -> bool:
        """Return True if the target time occurred between previous_ts and current_ts (inclusive)."""
        target_today = datetime.datetime.combine(current_ts.date(), target_time)
        target_yesterday = target_today - datetime.timedelta(days=1)
        return (previous_ts < target_today <= current_ts) or (previous_ts < target_yesterday <= current_ts)

    while True:
        time.sleep(poll_interval_seconds)
        now = datetime.datetime.now()

        if manager.scheduled_reboot_time:
            try:
                target_time = datetime.datetime.strptime(manager.scheduled_reboot_time, "%H:%M").time()
            except ValueError:
                manager.add_message(f"Invalid scheduled reboot time format: {manager.scheduled_reboot_time}")
                last_check = now
                continue

            if window_reached(last_check, now, target_time):
                today_str = now.strftime("%Y-%m-%d")
                if manager.last_reboot_execution_date == today_str:
                    last_check = now
                    continue

                manager.add_message(f"Scheduled reboot window reached ({manager.scheduled_reboot_time})")
                if manager.running:
                    manager.stop()
                try:
                    subprocess.run(["sudo", "uhubctl", "-l", "1-1", "-p", "2", "-a", "0"],
                                   capture_output=True, timeout=5)
                except Exception:
                    pass
                time.sleep(2)
                success, error = launch_system_power_action(["systemctl", "reboot"], "Scheduled reboot")
                if success:
                    manager.last_reboot_execution_date = today_str
                    manager.save_config()
                    break

                manager.add_message(f"Scheduled reboot failed: {error}")

        last_check = now

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
    power_status = manager.update_power_status()
    return jsonify({
        'running': manager.running,
        'dcs_pc_ip': manager.dcs_pc_ip,
        'auto_start': manager.auto_start,
        'scheduled_reboot_time': manager.scheduled_reboot_time,
        'web_port': manager.web_port,
        'udp_port': manager.udp_port,
        'multicast_group': manager.multicast_group,
        'max_reconnect_attempts': manager.max_reconnect_attempts,
        'reconnect_delay_seconds': manager.reconnect_delay_seconds,
        'serial_open_spacing_seconds': manager.serial_open_spacing_seconds,
        'low_voltage_event_logging': manager.low_voltage_event_logging,
        'serial_input_monitoring': manager.serial_input_monitoring,
        'xhci_watchdog_enabled': manager.xhci_watchdog_enabled,
        'xhci_watchdog_error_threshold': manager.xhci_watchdog_error_threshold,
        'xhci_reset_count': manager.xhci_reset_count,
        'xhci_last_reset_at': manager.xhci_last_reset_at,
        'devices': [d.to_dict() for d in manager.devices],
        'messages': manager.status_messages[-20:],
        'system': {
            'cpu_temp': get_cpu_temperature(),
            'memory': get_memory_info(),
            'power': power_status
        }
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
        restart_required = manager.running and device.enabled

        if restart_required:
            manager.add_message(f"Stopping manager to delete device: {device.name}")
            manager.stop()

        manager.devices.pop(index)
        manager.save_config()
        manager.add_message(f"Deleted device: {device.name}")

        restarted = False
        if restart_required:
            restarted = manager.start()
            if restarted:
                manager.add_message(f"Manager restarted after deleting device: {device.name}")
            else:
                manager.add_message(f"Manager could not be restarted after deleting device: {device.name}")

        return jsonify({'success': True, 'restarted': restarted})
    return jsonify({'success': False, 'error': 'Invalid device index'})

@app.route('/api/device/update/<int:index>', methods=['POST'])
def api_update_device(index):
    if 0 <= index < len(manager.devices):
        device = manager.devices[index]
        data = request.json
        new_name = data.get('name')
        new_port = data.get('port')
        new_baudrate = data.get('baudrate')

        if new_name:
            old_name = device.name
            device.name = new_name
        if new_port:
            device.port = new_port
        if new_baudrate:
            device.baudrate = new_baudrate

        manager.save_config()
        manager.add_message(f"Updated device: {device.name}")
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

@app.route('/api/settings/reconnect', methods=['POST'])
def api_set_reconnect_settings():
    data = request.json
    attempts = data.get('max_reconnect_attempts')
    delay = data.get('reconnect_delay_seconds')
    spacing = data.get('serial_open_spacing_seconds', manager.serial_open_spacing_seconds)

    if not isinstance(attempts, int) or attempts < 1 or attempts > 20:
        return jsonify({'success': False, 'error': 'Invalid reconnect attempts. Must be between 1 and 20'})

    if not isinstance(delay, int) or delay < 1 or delay > 60:
        return jsonify({'success': False, 'error': 'Invalid reconnect delay. Must be between 1 and 60 seconds'})

    if isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or spacing < 0 or spacing > 10:
        return jsonify({'success': False, 'error': 'Invalid serial open spacing. Must be between 0 and 10 seconds'})

    manager.max_reconnect_attempts = attempts
    manager.reconnect_delay_seconds = delay
    manager.serial_open_spacing_seconds = float(spacing)
    manager.save_config()
    manager.add_message(
        f"Reconnect settings updated: {attempts} attempts, {delay}s delay, {manager.serial_open_spacing_seconds:g}s open spacing"
    )
    return jsonify({'success': True})

@app.route('/api/settings/xhci_watchdog', methods=['POST'])
def api_set_xhci_watchdog():
    data = request.json
    enabled = data.get('enabled')
    threshold = data.get('error_threshold')

    if enabled is not None:
        manager.xhci_watchdog_enabled = bool(enabled)

    if threshold is not None:
        if not isinstance(threshold, int) or threshold < 60 or threshold > 3600:
            return jsonify({'success': False, 'error': 'Threshold must be between 60 and 3600 seconds'})
        manager.xhci_watchdog_error_threshold = threshold

    manager.save_config()
    manager.add_message(
        f"USB watchdog: {'enabled' if manager.xhci_watchdog_enabled else 'disabled'}, "
        f"threshold: {manager.xhci_watchdog_error_threshold}s"
    )
    return jsonify({'success': True})

@app.route('/api/xhci_reset', methods=['POST'])
def api_xhci_reset():
    """Manual xHCI reset endpoint — stops manager, resets, restarts."""
    was_running = manager.running

    def worker():
        if was_running:
            manager.stop()
            time.sleep(2)
        success = manager.reset_xhci_controller()
        if success:
            time.sleep(8)
            if was_running:
                manager.start()
                manager.add_message("Manager restarted after manual xHCI reset")
        else:
            if was_running:
                manager.start()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'success': True, 'message': 'xHCI reset initiated'})

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

@app.route('/api/settings/multicast', methods=['POST'])
def api_set_multicast():
    data = request.json
    new_group = data.get('multicast_group')
    new_port = data.get('udp_port')

    success = True
    error_msg = ""

    # Validate multicast group (basic validation)
    if new_group:
        # Basic check for valid multicast address format
        import re
        ip_pattern = r'^([0-9]{1,3}\.){3}[0-9]{1,3}$'
        if not re.match(ip_pattern, new_group):
            success = False
            error_msg = "Invalid multicast group format. Use x.x.x.x format"
        else:
            # Check if it's in multicast range (224.0.0.0 to 239.255.255.255)
            parts = new_group.split('.')
            if len(parts) == 4:
                first_octet = int(parts[0]) if parts[0].isdigit() else -1
                if first_octet < 224 or first_octet > 239:
                    success = False
                    error_msg = "Multicast group must be in range 224.0.0.0 to 239.255.255.255"

    # Validate UDP port
    if new_port and (not isinstance(new_port, int) or new_port < 1 or new_port > 65535):
        success = False
        error_msg = "Invalid UDP port. Must be between 1 and 65535" if not error_msg else error_msg + ", and UDP port must be between 1 and 65535"

    if success:
        if new_group:
            manager.multicast_group = new_group
        if new_port:
            manager.udp_port = new_port
        manager.save_config()
        if new_group:
            manager.add_message(f"Multicast group set to: {new_group}")
        if new_port:
            manager.add_message(f"UDP port set to: {new_port}")
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': error_msg})

@app.route('/api/settings/serial_input_monitoring', methods=['POST'])
def api_set_serial_input_monitoring():
    data = request.json
    enabled = data.get('enabled')

    if enabled is not None:
        manager.serial_input_monitoring = bool(enabled)
        manager.save_config()
        manager.add_message(f"Serial input monitoring {'enabled' if manager.serial_input_monitoring else 'disabled'}")
        return jsonify({'success': True, 'enabled': manager.serial_input_monitoring})

    return jsonify({'success': False, 'error': 'Missing enabled flag'})

@app.route('/api/settings/low_voltage_logging', methods=['POST'])
def api_set_low_voltage_logging():
    data = request.json
    enabled = data.get('enabled')

    if enabled is not None:
        manager.low_voltage_event_logging = bool(enabled)
        manager.save_config()
        manager.add_message(
            f"Low voltage event logging {'enabled' if manager.low_voltage_event_logging else 'disabled'}"
        )
        return jsonify({'success': True, 'enabled': manager.low_voltage_event_logging})

    return jsonify({'success': False, 'error': 'Missing enabled flag'})


@app.route('/api/reboot', methods=['POST'])
def api_reboot():
    success, error = request_system_power_action("Rebooting system", ["systemctl", "reboot"])
    if success:
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': error}), 500

@app.route('/api/shutdown', methods=['POST'])
def api_shutdown():
    success, error = request_system_power_action("Shutting down system", ["systemctl", "poweroff"])
    if success:
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': error}), 500

@app.route('/api/ports')
def api_list_ports():
    ports = []
    patterns = ['/dev/ttyACM*', '/dev/ttyUSB*', '/dev/ttyAMA*', '/dev/ttyS*']

    all_ports = []
    for pattern in patterns:
        all_ports.extend(glob.glob(pattern))
    all_ports.sort()

    configured_ports = {device.port for device in manager.devices}

    seen_targets = set()
    for port in all_ports:
        status = "configured" if port in configured_ports else "available"
        info = get_port_info(port)
        ports.append({
            'port': port,
            'info': info,
            'status': status
        })
        seen_targets.add(os.path.realpath(port))

    by_path_dir = '/dev/serial/by-path'
    if os.path.isdir(by_path_dir):
        by_path_seen = set()
        for name in sorted(os.listdir(by_path_dir)):
            if not name.startswith('pci-') and not name.startswith('platform-'):
                continue
            symlink = os.path.join(by_path_dir, name)
            if not os.path.islink(symlink):
                continue
            target = os.path.realpath(symlink)
            if target in by_path_seen:
                continue
            by_path_seen.add(target)
            status = "configured" if symlink in configured_ports else "available"
            ports.append({
                'port': symlink,
                'info': f"USB port {name} → {os.path.basename(target)}",
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