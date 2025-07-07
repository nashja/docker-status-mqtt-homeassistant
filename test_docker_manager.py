import unittest
from unittest.mock import MagicMock, patch

import docker

from docker_manager import (
    DockerCommandManager,
    DockerSocketManager,
    LocalCommandExecutor,
    SSHCommandExecutor,
)


class TestDockerManager(unittest.TestCase):
    def test_docker_command_manager_start_container(self):
        mock_executor = MagicMock()
        manager = DockerCommandManager(mock_executor, include_only=["container1"])
        manager.start_container("container1")
        mock_executor.run_command.assert_called_once_with("docker start container1")

    def test_docker_command_manager_stop_container(self):
        mock_executor = MagicMock()
        manager = DockerCommandManager(mock_executor, exclude=["container2"])
        manager.stop_container("container1")
        mock_executor.run_command.assert_called_once_with("docker stop container1")

    def test_docker_command_manager_start_container_excluded(self):
        mock_executor = MagicMock()
        manager = DockerCommandManager(mock_executor, exclude=["container1"])
        manager.start_container("container1")
        mock_executor.run_command.assert_not_called()

    def test_docker_command_manager_stop_container_excluded(self):
        mock_executor = MagicMock()
        manager = DockerCommandManager(mock_executor, include_only=["container2"])
        manager.stop_container("container1")
        mock_executor.run_command.assert_not_called()

    def test_docker_command_manager_get_container_stats(self):
        mock_executor = MagicMock()
        mock_executor.run_command.side_effect = [
            "running",  # First call to get_container_status
            "25.5%|1.5GiB / 8GiB|18.75%|1.2MB / 2.3MB|1.5GB / 2.1GB"  # docker stats output
        ]
        manager = DockerCommandManager(mock_executor)
        stats = manager.get_container_stats("container1")
        
        self.assertEqual(stats['cpu_percent'], 25.5)
        self.assertAlmostEqual(stats['memory_usage_mb'], 1536.0, places=0)  # 1.5GiB in MB
        self.assertEqual(stats['memory_percent'], 18.75)
        self.assertAlmostEqual(stats['network_rx_mb'], 1.2, places=1)
        self.assertAlmostEqual(stats['network_tx_mb'], 2.3, places=1)
        self.assertAlmostEqual(stats['blkio_read_mb'], 1536.0, places=0)  # 1.5GB in MB  
        self.assertAlmostEqual(stats['blkio_write_mb'], 2150.4, places=0)  # 2.1GB in MB

    def test_docker_command_manager_get_container_stats_not_running(self):
        mock_executor = MagicMock()
        mock_executor.run_command.return_value = "stopped"
        manager = DockerCommandManager(mock_executor)
        stats = manager.get_container_stats("container1")
        self.assertEqual(stats, {})

    def test_docker_command_manager_parse_size(self):
        mock_executor = MagicMock()
        manager = DockerCommandManager(mock_executor)
        
        # Test various size formats
        self.assertEqual(manager._parse_size("1.5GiB"), 1.5 * 1024**3)
        self.assertEqual(manager._parse_size("500MB"), 500 * 1024**2)
        self.assertEqual(manager._parse_size("2GB"), 2 * 1024**3)
        self.assertEqual(manager._parse_size("1024B"), 1024)
        self.assertEqual(manager._parse_size(""), 0)
        self.assertEqual(manager._parse_size("invalid"), 0)

    @patch("docker.from_env")
    def test_docker_socket_manager_get_all_statuses(self, mock_docker):
        container1 = MagicMock(spec=docker.models.containers.Container)
        container1.name = "container1"
        container1.status = "running"
        container2 = MagicMock(spec=docker.models.containers.Container)
        container2.name = "container2"
        container2.status = "stopped"
        mock_docker.return_value.containers.list.return_value = [container1, container2]
        manager = DockerSocketManager()
        expected_statuses = {"container1": "running", "container2": "stopped"}
        self.assertEqual(manager.get_all_statuses(), expected_statuses)

    @patch("docker.from_env")
    def test_docker_socket_manager_start_container(self, mock_docker):
        mock_container = MagicMock()
        mock_docker.return_value.containers.get.return_value = mock_container
        manager = DockerSocketManager(include_only=["container1"])
        manager.start_container("container1")
        mock_container.start.assert_called_once()

    @patch("docker.from_env")
    def test_docker_socket_manager_stop_container(self, mock_docker):
        mock_container = MagicMock()
        mock_docker.return_value.containers.get.return_value = mock_container
        manager = DockerSocketManager(exclude=["container2"])
        manager.stop_container("container1")
        mock_container.stop.assert_called_once()

    @patch("docker.from_env")
    def test_docker_socket_manager_get_container_status(self, mock_docker):
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        manager = DockerSocketManager()
        status = manager.get_container_status("container1")
        self.assertEqual(status, "running")

    @patch("docker.from_env")
    def test_docker_socket_manager_get_container_stats(self, mock_docker):
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.stats.return_value = {
            'cpu_stats': {
                'cpu_usage': {'total_usage': 2000000000, 'percpu_usage': [1000000000, 1000000000]},
                'system_cpu_usage': 4000000000
            },
            'precpu_stats': {
                'cpu_usage': {'total_usage': 1000000000, 'percpu_usage': [500000000, 500000000]},
                'system_cpu_usage': 2000000000
            },
            'memory_stats': {
                'usage': 1073741824,  # 1GB
                'cache': 536870912,   # 0.5GB
                'limit': 2147483648   # 2GB
            },
            'networks': {
                'eth0': {'rx_bytes': 1048576, 'tx_bytes': 2097152}  # 1MB RX, 2MB TX
            },
            'blkio_stats': {
                'io_service_bytes_recursive': [
                    {'op': 'read', 'value': 10485760},   # 10MB
                    {'op': 'write', 'value': 20971520}   # 20MB
                ]
            }
        }
        mock_docker.return_value.containers.get.return_value = mock_container
        manager = DockerSocketManager()
        stats = manager.get_container_stats("container1")
        
        self.assertEqual(stats['cpu_percent'], 100.0)  # (1B / 2B) * 2 CPUs * 100
        self.assertEqual(stats['memory_usage_mb'], 512.0)  # (1GB - 0.5GB cache) / 1024 / 1024
        self.assertEqual(stats['memory_percent'], 25.0)  # (512MB / 2048MB) * 100
        self.assertEqual(stats['network_rx_mb'], 1.0)  # 1MB
        self.assertEqual(stats['network_tx_mb'], 2.0)  # 2MB
        self.assertEqual(stats['blkio_read_mb'], 10.0)  # 10MB
        self.assertEqual(stats['blkio_write_mb'], 20.0)  # 20MB

    @patch("docker.from_env")
    def test_docker_socket_manager_get_container_stats_not_running(self, mock_docker):
        mock_container = MagicMock()
        mock_container.status = "stopped"
        mock_docker.return_value.containers.get.return_value = mock_container
        manager = DockerSocketManager()
        stats = manager.get_container_stats("container1")
        self.assertEqual(stats, {})

    def test_ssh_command_executor(self):
        executor = SSHCommandExecutor(
            "test_host", 22, "test_user", "test_password", connect=False
        )
        with patch("paramiko.SSHClient.connect") as mock_connect:
            with patch("paramiko.SSHClient.exec_command") as mock_exec:
                mock_stdin = MagicMock()
                mock_stdout = MagicMock()
                mock_stderr = MagicMock()
                mock_stdout.read.return_value = b"test_output\n"
                mock_exec.return_value = (mock_stdin, mock_stdout, mock_stderr)
                executor.connect()
                mock_connect.assert_called_once_with(
                    "test_host", port=22, username="test_user", password="test_password"
                )
                output = executor.run_command("test_command")
                mock_exec.assert_called_once_with("test_command")
                self.assertEqual(output, "test_output")

    @patch("subprocess.run")
    def test_local_command_executor(self, mock_run):
        executor = LocalCommandExecutor()
        mock_run.return_value.stdout = "test_output\n"
        output = executor.run_command("test_command")
        mock_run.assert_called_once_with(
            "test_command", shell=True, text=True, capture_output=True
        )
        self.assertEqual(output, "test_output")


if __name__ == "__main__":
    unittest.main()
