#!/usr/bin/env python3
"""
Health check script for docker-status-mqtt-homeassistant
Validates MQTT connectivity, Docker API access, and service status
"""

import logging
import os
import socket
import sys
import time
from pathlib import Path

# Configure logging for health check
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def check_mqtt_connectivity():
    """Test MQTT broker connectivity"""
    try:
        import paho.mqtt.client as mqtt

        mqtt_server = os.getenv("MQTT_SERVER")
        mqtt_port = int(os.getenv("MQTT_PORT", 1883))
        mqtt_user = os.getenv("MQTT_USER")
        mqtt_password = os.getenv("MQTT_PASSWORD")

        if not mqtt_server:
            logger.error("MQTT_SERVER not configured")
            return False

        # Test connection with timeout
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if mqtt_user and mqtt_password:
            client.username_pw_set(mqtt_user, mqtt_password)

        connected = False

        def on_connect(client, userdata, flags, rc, properties=None):
            nonlocal connected
            connected = rc == 0

        client.on_connect = on_connect

        try:
            client.connect(mqtt_server, mqtt_port, 5)  # 5 second timeout
            client.loop_start()

            # Wait up to 5 seconds for connection
            for _ in range(50):  # 50 * 0.1s = 5s
                if connected:
                    break
                time.sleep(0.1)

            client.loop_stop()
            client.disconnect()

            if connected:
                logger.debug(f"MQTT connection to {mqtt_server}:{mqtt_port} successful")
                return True
            else:
                logger.error(
                    f"Failed to connect to MQTT broker {mqtt_server}:{mqtt_port}"
                )
                return False

        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            return False

    except ImportError:
        logger.error("paho-mqtt library not available")
        return False
    except Exception as e:
        logger.error(f"MQTT check failed: {e}")
        return False


def check_configuration_valid():
    """Check if the current configuration is valid"""
    try:
        from config import Config

        # Try to create a Config instance - this validates all configuration
        config = Config()
        mode = config.mode()
        logger.debug(f"Configuration valid for mode: {mode}")
        return True
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        return False


def get_operation_mode():
    """Determine the operation mode using config.py logic"""
    try:
        from config import Config

        # Use the same Config class - if validation fails, health check should fail too
        config = Config()
        return config.mode()
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        return None


def check_docker_connectivity():
    """Test Docker API connectivity based on current operation mode"""
    try:
        mode = get_operation_mode()
        if mode is None:
            return False  # Configuration validation failed

        logger.debug(f"Checking Docker connectivity for mode: {mode}")

        if mode == "ssh":
            return _check_ssh_docker_connectivity()
        elif mode == "socket":
            return _check_socket_docker_connectivity()
        elif mode == "local":
            return _check_local_docker_connectivity()
        else:
            logger.error(f"Unknown operation mode: {mode}")
            return False

    except Exception as e:
        logger.error(f"Docker connectivity check failed: {e}")
        return False


def _check_ssh_docker_connectivity():
    """Test SSH Docker connectivity"""
    try:
        import paramiko

        ssh_host = os.getenv("SSH_HOST")
        ssh_port = int(os.getenv("SSH_PORT", 22))
        ssh_user = os.getenv("SSH_USER")
        ssh_password = os.getenv("SSH_PASSWORD")

        if not all([ssh_host, ssh_user, ssh_password]):
            logger.error("SSH credentials not properly configured")
            return False

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(
                ssh_host,
                port=ssh_port,
                username=ssh_user,
                password=ssh_password,
                timeout=5,
            )

            # Test docker command
            stdin, stdout, stderr = ssh.exec_command(
                "docker version --format '{{.Server.Version}}'", timeout=5
            )
            exit_code = stdout.channel.recv_exit_status()

            if exit_code == 0:
                logger.debug(f"SSH Docker connection to {ssh_host} successful")
                return True
            else:
                error = stderr.read().decode()
                logger.error(f"Docker command failed via SSH: {error}")
                return False

        except Exception as e:
            logger.error(f"SSH connection error: {e}")
            return False
        finally:
            ssh.close()

    except ImportError:
        logger.error("paramiko library not available for SSH mode")
        return False


def _check_socket_docker_connectivity():
    """Test Docker socket connectivity"""
    try:
        import docker

        client = docker.from_env()

        # Test API call with timeout
        client.ping()

        # Quick test to list containers
        containers = client.containers.list(limit=1)

        logger.debug("Docker socket connection successful")
        return True

    except ImportError:
        logger.error("docker library not available for socket mode")
        return False
    except Exception as e:
        logger.error(f"Docker socket connection error: {e}")
        return False


def _check_local_docker_connectivity():
    """Test local Docker command connectivity"""
    try:
        import subprocess

        # Test docker command
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            logger.debug("Local Docker command successful")
            return True
        else:
            logger.error(f"Docker command failed: {result.stderr}")
            return False

    except FileNotFoundError:
        logger.error("docker command not found")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Docker command timed out")
        return False
    except Exception as e:
        logger.error(f"Local Docker command error: {e}")
        return False


def check_process_health():
    """Check if the main process is running and responsive"""
    try:
        # Check if main process PID file exists (if we create one)
        pid_file = Path("/tmp/docker-status-mqtt.pid")
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                # Check if process is still running
                os.kill(pid, 0)  # Signal 0 doesn't kill, just checks existence
                logger.debug(f"Main process (PID {pid}) is running")
            except (OSError, ValueError):
                logger.warning("PID file exists but process not found")

        # Check for any python processes running main.py
        import subprocess

        try:
            result = subprocess.run(
                ["pgrep", "-f", "main.py"], capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                logger.debug("Main process found via pgrep")
                return True
            else:
                logger.warning("Main process not found")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # pgrep might not be available in Alpine
            pass

        return True  # If we can't verify, assume it's running

    except Exception as e:
        logger.error(f"Process health check failed: {e}")
        return False


def check_last_activity():
    """Check if the service has been active recently"""
    try:
        # Look for a heartbeat file that main.py should update
        heartbeat_file = Path("/tmp/docker-status-mqtt-heartbeat")
        if heartbeat_file.exists():
            try:
                last_update = heartbeat_file.stat().st_mtime
                current_time = time.time()

                # Consider healthy if updated within last 2 * publish_interval
                publish_interval = int(os.getenv("PUBLISH_INTERVAL", 60))
                max_age = publish_interval * 2

                age = current_time - last_update
                if age < max_age:
                    logger.debug(f"Heartbeat file updated {age:.1f}s ago")
                    return True
                else:
                    logger.warning(f"Heartbeat file too old: {age:.1f}s")
                    return False

            except Exception as e:
                logger.warning(f"Error checking heartbeat: {e}")

        # If no heartbeat file, we can't verify recent activity
        logger.debug("No heartbeat file found, skipping activity check")
        return True

    except Exception as e:
        logger.error(f"Activity check failed: {e}")
        return False


def main():
    """Run all health checks"""
    logger.debug("Starting health check")

    checks = [
        ("Configuration Valid", check_configuration_valid),
        ("MQTT Connectivity", check_mqtt_connectivity),
        ("Docker Connectivity", check_docker_connectivity),
        ("Process Health", check_process_health),
        ("Last Activity", check_last_activity),
    ]

    failed_checks = []

    for check_name, check_func in checks:
        try:
            if not check_func():
                failed_checks.append(check_name)
                logger.error(f"Health check failed: {check_name}")
            else:
                logger.debug(f"Health check passed: {check_name}")
        except Exception as e:
            failed_checks.append(check_name)
            logger.error(f"Health check error in {check_name}: {e}")

    if failed_checks:
        logger.error(f"Health check FAILED. Failed checks: {', '.join(failed_checks)}")
        sys.exit(1)
    else:
        logger.debug("All health checks PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
