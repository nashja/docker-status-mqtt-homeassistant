import logging
import os
import socket
import subprocess
from abc import ABC, abstractmethod

import docker
import paramiko

logger = logging.getLogger(__name__)


class DockerManager(ABC):
    def __init__(self, include_only=None, exclude=None):
        self.include_only = include_only
        self.exclude = exclude if exclude else []
        self._self_excluded = False  # Track if we've already auto-excluded

    def get_docker_statuses(self) -> dict[str, str]:
        # Auto-exclude self container on first call
        if not self._self_excluded:
            self._auto_exclude_self()
            self._self_excluded = True

        all_statuses = self.get_all_statuses()
        if self.include_only:
            return {k: v for k, v in all_statuses.items() if k in self.include_only}
        elif self.exclude:
            return {k: v for k, v in all_statuses.items() if k not in self.exclude}
        return all_statuses

    def _auto_exclude_self(self):
        """Auto-exclude self container from monitoring"""
        try:
            # Method 1: Use hostname (usually the container name)
            hostname = socket.gethostname()

            # Method 2: Check all containers to find ourselves
            all_statuses = self.get_all_statuses()

            # Look for containers with common self names
            possible_self_names = [
                hostname,
                "docker-status-mqtt-homeassistant",
                "docker-status-mqtt",
                "docker-status-mqtt-homea",  # Truncated version
            ]

            for name in possible_self_names:
                if name in all_statuses and name not in self.exclude:
                    self.exclude.append(name)
                    logger.info(f"Auto-excluding self container: {name}")
                    break

        except Exception as e:
            logger.debug(f"Could not auto-exclude self container: {e}")

    def is_container_incuded(self, container_name):
        if self.include_only:
            return container_name in self.include_only
        elif self.exclude:
            return container_name not in self.exclude
        return True

    @abstractmethod
    def get_all_statuses(self) -> dict[str, str]:
        pass

    def stop_container(self, container_name):
        if self.exclude and container_name in self.exclude:
            logger.warning(f"Contenedor {container_name} no se detendrá, está excluido")
            return
        if self.include_only and container_name not in self.include_only:
            logger.warning(
                f"Contenedor {container_name} no se detendrá, no está incluido"
            )
            return
        self._stop_container(container_name)

    @abstractmethod
    def _stop_container(self, container_name):
        pass

    def start_container(self, container_name):
        if self.exclude and container_name in self.exclude:
            logger.warning(f"Contenedor {container_name} no se iniciará, está excluido")
            return
        if self.include_only and container_name not in self.include_only:
            logger.warning(
                f"Contenedor {container_name} no se iniciará, no está incluido"
            )
            return
        self._start_container(container_name)

    @abstractmethod
    def _start_container(self, container_name):
        pass

    def close(self):
        pass

    def get_container_status(self, container_name) -> str:
        return self.get_docker_statuses().get(container_name, "Not found")
    
    @abstractmethod
    def get_container_stats(self, container_name) -> dict:
        """Get container resource statistics (CPU, memory, disk, network)"""
        pass


class DockerCommandManager(DockerManager):
    def __init__(self, command_executor, include_only=None, exclude=None):
        super().__init__(include_only, exclude)
        self.command_executor = command_executor

    def get_all_statuses(self) -> dict[str, str]:
        """
        Retrieves the status of all Docker containers.

        Returns:
            A dictionary where keys are container names and values are their states.
        """
        output = self.command_executor.run_command(
            "docker ps -a --format '{{.Names}}:{{.State}}'"
        )
        status_dict = {}
        for line in output.splitlines():
            name, state = line.split(":", 1)
            status_dict[name] = state
        return status_dict

    def _start_container(self, container_name):
        return self.command_executor.run_command(f"docker start {container_name}")

    def _stop_container(self, container_name):
        return self.command_executor.run_command(f"docker stop {container_name}")

    def get_container_status(self, container_name):
        return self.command_executor.run_command(
            f"docker inspect --format='{{{{.State.Status}}}}' {container_name}"
        )
    
    def get_container_stats(self, container_name) -> dict:
        """Get container resource statistics using docker stats command"""
        try:
            # Check if container is running
            status = self.get_container_status(container_name).strip()
            if status != 'running':
                return {}
            
            # Get stats with docker stats command (no-stream for single snapshot)
            stats_output = self.command_executor.run_command(
                f"docker stats {container_name} --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}|{{{{.MemPerc}}}}|{{{{.NetIO}}}}|{{{{.BlockIO}}}}'"
            )
            
            if not stats_output:
                return {}
            
            # Parse the output
            parts = stats_output.strip().split('|')
            if len(parts) != 5:
                return {}
            
            cpu_str, mem_usage_str, mem_perc_str, net_io_str, block_io_str = parts
            
            # Parse CPU percentage
            cpu_percent = float(cpu_str.rstrip('%'))
            
            # Parse memory usage (e.g., "1.5GiB / 8GiB")
            mem_parts = mem_usage_str.split(' / ')
            mem_usage = self._parse_size(mem_parts[0])
            mem_limit = self._parse_size(mem_parts[1]) if len(mem_parts) > 1 else 0
            mem_percent = float(mem_perc_str.rstrip('%'))
            
            # Parse network I/O (e.g., "1.5GB / 2.3GB")
            net_parts = net_io_str.split(' / ')
            net_rx = self._parse_size(net_parts[0]) if net_parts else 0
            net_tx = self._parse_size(net_parts[1]) if len(net_parts) > 1 else 0
            
            # Parse block I/O (e.g., "1.5GB / 2.3GB")
            block_parts = block_io_str.split(' / ')
            block_read = self._parse_size(block_parts[0]) if block_parts else 0
            block_write = self._parse_size(block_parts[1]) if len(block_parts) > 1 else 0
            
            return {
                'cpu_percent': round(cpu_percent, 2),
                'memory_usage_mb': round(mem_usage / 1024 / 1024, 2),
                'memory_limit_mb': round(mem_limit / 1024 / 1024, 2),
                'memory_percent': round(mem_percent, 2),
                'network_rx_mb': round(net_rx / 1024 / 1024, 2),
                'network_tx_mb': round(net_tx / 1024 / 1024, 2),
                'blkio_read_mb': round(block_read / 1024 / 1024, 2),
                'blkio_write_mb': round(block_write / 1024 / 1024, 2)
            }
        except Exception as e:
            logger.error(f"Error getting stats for container {container_name}: {e}")
            return {}
    
    def _parse_size(self, size_str: str) -> float:
        """Parse size string like '1.5GiB' or '500MB' to bytes"""
        size_str = size_str.strip()
        if not size_str:
            return 0
        
        units = [
            ('TIB', 1024**4),
            ('TI', 1024**4),
            ('TB', 1024**4),
            ('GIB', 1024**3),
            ('GI', 1024**3),
            ('GB', 1024**3),
            ('MIB', 1024**2),
            ('MI', 1024**2),
            ('MB', 1024**2),
            ('KIB', 1024),
            ('KI', 1024),
            ('KB', 1024),
            ('B', 1)
        ]
        
        for unit, multiplier in units:
            if size_str.upper().endswith(unit):
                number_part = size_str[:-len(unit)].strip()
                try:
                    return float(number_part) * multiplier
                except ValueError:
                    return 0
        
        # If no unit found, assume bytes
        try:
            return float(size_str)
        except ValueError:
            return 0

    def close(self):
        self.command_executor.close()


class DockerSocketManager(DockerManager):
    """
    DockerSocketManager is a class that manages Docker containers using the Docker API.
    """

    def __init__(self, include_only=None, exclude=None):
        super().__init__(include_only, exclude)
        self.client = docker.from_env()

    def get_all_statuses(self):
        containers = self.client.containers.list(all=True)
        return {c.name: c.status for c in containers}

    def _start_container(self, container_name):
        container = self.client.containers.get(container_name)
        container.start()

    def _stop_container(self, container_name):
        container = self.client.containers.get(container_name)
        container.stop()

    def get_container_status(self, container_name):
        container = self.client.containers.get(container_name)
        return container.status
    
    def get_container_stats(self, container_name) -> dict:
        """Get container resource statistics using Docker API"""
        try:
            container = self.client.containers.get(container_name)
            if container.status != 'running':
                return {}
            
            stats = container.stats(stream=False)
            
            # Calculate CPU percentage
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
            system_cpu_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
            number_cpus = len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', []))
            cpu_percent = 0.0
            if system_cpu_delta > 0.0 and cpu_delta > 0.0:
                cpu_percent = (cpu_delta / system_cpu_delta) * number_cpus * 100.0
            
            # Calculate memory usage
            mem_usage = stats['memory_stats']['usage'] - stats['memory_stats'].get('cache', 0)
            mem_limit = stats['memory_stats']['limit']
            mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0
            
            # Network stats (sum of all interfaces)
            rx_bytes = 0
            tx_bytes = 0
            if 'networks' in stats:
                for interface in stats['networks'].values():
                    rx_bytes += interface.get('rx_bytes', 0)
                    tx_bytes += interface.get('tx_bytes', 0)
            
            # Block I/O stats
            blkio_read = 0
            blkio_write = 0
            if 'blkio_stats' in stats and 'io_service_bytes_recursive' in stats['blkio_stats']:
                for item in stats['blkio_stats']['io_service_bytes_recursive']:
                    if item['op'] == 'read':
                        blkio_read += item['value']
                    elif item['op'] == 'write':
                        blkio_write += item['value']
            
            return {
                'cpu_percent': round(cpu_percent, 2),
                'memory_usage_mb': round(mem_usage / 1024 / 1024, 2),
                'memory_limit_mb': round(mem_limit / 1024 / 1024, 2),
                'memory_percent': round(mem_percent, 2),
                'network_rx_mb': round(rx_bytes / 1024 / 1024, 2),
                'network_tx_mb': round(tx_bytes / 1024 / 1024, 2),
                'blkio_read_mb': round(blkio_read / 1024 / 1024, 2),
                'blkio_write_mb': round(blkio_write / 1024 / 1024, 2)
            }
        except Exception as e:
            logger.error(f"Error getting stats for container {container_name}: {e}")
            return {}


class CommandExecutor(ABC):
    @abstractmethod
    def run_command(self, command):
        pass

    def close(self):
        pass


class SSHCommandExecutor(CommandExecutor):
    def __init__(self, host, port, user, password, connect=True):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if connect:
            self.connect()

    def connect(self):
        try:
            self.client.connect(
                self.host, port=self.port, username=self.user, password=self.password
            )
            logger.info(f"Conexión SSH establecida con {self.host}")
        except paramiko.AuthenticationException:
            logger.error("Error de autenticación SSH. Verifica las credenciales.")
            raise
        except paramiko.SSHException as ssh_ex:
            logger.error(f"Error al establecer la conexión SSH: {str(ssh_ex)}")
            raise
        except Exception as e:
            logger.error(f"Error inesperado al conectar por SSH: {str(e)}")
            raise

    def run_command(self, command):
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            return stdout.read().decode("utf-8").strip()
        except Exception as e:
            logger.error(f"Error al ejecutar el comando SSH '{command}': {str(e)}")
            raise

    def close(self):
        self.client.close()


class LocalCommandExecutor(CommandExecutor):
    def run_command(self, command):
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.stdout.strip()
