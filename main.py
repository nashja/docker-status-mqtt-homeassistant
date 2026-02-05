import argparse
import json
import logging
import sys
import time

import paho.mqtt.client as mqtt

from config import Config

log_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.addHandler(stdout_handler)
logger.setLevel(logging.DEBUG)


class DockerMQTT:
    def __init__(
        self,
        config: Config,
    ):
        self.config = config
        self.prefix = config.entity_prefix
        self.device_config = {
            "identifiers": [f"{self.prefix}containers"],
            "name": f"{config.entity_name} Containers",
            "model": "DockerMQTT",
            "manufacturer": "Sanfrancejan",
        }

        self.known_docker_statuses = {}
        self.known_container_metrics = {}  # Track last published metrics

        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt_client.username_pw_set(config.mqtt_user, config.mqtt_password)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message

        self.docker_manager = config.get_manager()

    def run(self):
        try:
            # just doing this doesn't create the entities
            #self.get_initial_docker_statuses()
            # try doing the below before connecting (and getting all the subscription messages)
            # none of the initial entities and status are actually being created as not yet connected..
            # it is doing a publish before connection ...
            #
            # also - it is publishing the metrics, when we don't need them, so something isn/t correct
            self.update_entities_and_statuses()
            time.sleep(5)
            self.connect()
            self.mqtt_client.loop_start()
            while True:
                self.update_entities_and_statuses()
                time.sleep(self.config.publish_interval)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt detected. Closing conenections.")
        except Exception as e:
            logger.critical(f"Critical error in main loop: {str(e)}")
        finally:
            logger.info("Stopping connections")
            try:
                self.docker_manager.close()
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception as e:
                logger.error(f"Error stopping connectionss: {str(e)}")
            logger.info("Service Closed")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        logger.info(f"[on_connect] Conected to MQTT with code {rc}")
        # we should always subscribe from on_connect callback to be sure
        # our subscribed is persisted across reconnections.
        logger.info("[on_connect] Subscribing to homeassistant/switch")
        client.subscribe("homeassistant/switch/#")

    def on_message(self, client, userdata, msg):
        """We receive to messages types:
        - Reatined config messages, ie, homeassistant entities configuration (first messages on connect)
        - commands from Home Assistant to start or stop containers (user interaction)
        """
        topic = msg.topic
        if self.prefix not in topic:
            return
        logger.debug(f"[on_message] received message topic {topic} payload = {msg.payload}")

        container_name = topic.split("/")[-2].replace(self.prefix, "")

        try:
            if topic.endswith("/command"):
                command = msg.payload.decode()
                logger.debug(f"[on_message] executing command {command} - container {container_name}")
                self.execute_command(command, container_name)
            elif topic.endswith("/config"):
                #if container_name not in self.known_docker_statuses and msg.payload:
                #    self.delete_entity(container_name)
                if container_name not in self.known_docker_statuses: # and msg.payload:
                    # a configuration command with an empty payload should delete - but otherwise not ...
                    logger.debug(
                        f"I am checking payload to delete {container_name}: command is  {msg.payload.decode()} topic is {topic}"
                     )
                    if not msg.payload :
                        self.delete_entity(container_name)
        except Exception as e:
            logger.error(
                f"Error executing {command} for container {container_name}: {str(e)}"
            )

    def execute_command(self, command, container_name):
        logger.info(f"Coommand recieved: {command} for {container_name}")
        if command == "ON":
            logger.info(f"Starting container {container_name}")

            self.docker_manager.start_container(container_name)
        elif command == "OFF":
            logger.info(f"Stopping container {container_name}")
            self.docker_manager.stop_container(container_name)
        else:
            logger.warning(f"Unknown command {command} for {container_name}")
        time.sleep(1)
        container_status = self.docker_manager.get_container_status(container_name)
        if self.docker_manager.is_container_incuded(container_name):
            self.update_entity_status(container_name, container_status)
        logger.info(f"Current state for {container_name}: {container_status}")

    def connect(self):
        try:
            self.mqtt_client.connect(self.config.mqtt_server, self.config.mqtt_port, 60)
            logger.info(f"MQTT connection established with {self.config.mqtt_server}")

        except Exception as e:
            logger.error(f"Error connecting with MQTT: {str(e)}")
            raise

    def get_initial_docker_statuses(self):
        logger.info("Getting initial status of docker containers")
        try:
            self.known_docker_statuses = self.docker_manager.get_docker_statuses()   
            for container_name, container_state in self.known_docker_statuses.items():
                logger.info(f"Initial state: Container {container_name} - state {container_state}")
        except Exception as e:
            logger.error(f"Unable to update docker status: {str(e)}")       

    def update_entities_and_statuses(self):
        """Update docker entities states and create new entities if needed"""
        logger.info("Publishing the state of docker containers in MQTT")
        try:
            docker_statuses = self.docker_manager.get_docker_statuses()
            last_docker_statuses = self.known_docker_statuses

            running_containers = sorted(
                [c for c in docker_statuses if docker_statuses[c].lower() == "running"]
            )
            logger.info(f"Running: {','.join(running_containers)}")

            for container_name, container_state in docker_statuses.items():
                self.update_entity_status(container_name, container_state)
                if container_name not in last_docker_statuses:
                    self.create_entity(container_name)
                    if self.config.enable_metrics:
                        self.create_metric_entities(container_name)
                # Update metrics for running containers
                if container_state.lower() == "running" and self.config.enable_metrics:
                    self.update_container_metrics(container_name)
                elif (
                    container_state.lower() != "running" and self.config.enable_metrics
                ):
                    # Clean up metrics cache for stopped containers
                    if container_name in self.known_container_metrics:
                        del self.known_container_metrics[container_name]

            # Update known statuses after processing all containers
            self.known_docker_statuses = docker_statuses

            # Update heartbeat file for health check
            self._update_heartbeat()

        except Exception as e:
            logger.error(f"Error publishing state of containers: {str(e)}")

    def _update_heartbeat(self):
        """Update heartbeat file for health checks"""
        try:
            import pathlib

            heartbeat_file = pathlib.Path("/tmp/docker-status-mqtt-heartbeat")
            heartbeat_file.touch()
        except Exception as e:
            logger.debug(f"Failed to update heartbeat: {e}")

    def create_entity(self, container_name):
        self.mqtt_client.publish(
            self._get_topic(container_name, "config"),
            json.dumps(
                {
                    "name": container_name,
                    "unique_id": f"{self.prefix}{container_name}",
                    "command_topic": self._get_topic(container_name, "command"),
                    "state_topic": self._get_topic(container_name, "state"),
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                    "device": self.device_config,
                }
            ),
            retain=True,
        )
        logger.debug(f"[create entity] published config for - {container_name}")
        logger.debug(f"[create entity] topic is  - {self._get_topic(container_name, "config")}")
        logger.debug(f"[create entity] payload is  - {json.dumps(
                {
                    "name": container_name,
                    "unique_id": f"{self.prefix}{container_name}",
                    "command_topic": self._get_topic(container_name, "command"),
                    "state_topic": self._get_topic(container_name, "state"),
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                    "device": self.device_config,
                }
            )} with Retain = True")     

    def delete_entity(self, container_name):
        self.mqtt_client.publish(self._get_topic(container_name, "config"), "")
        self.mqtt_client.publish(self._get_topic(container_name, ""), "")
        if self.config.enable_metrics:
            self.delete_metric_entities(container_name)
            # Clean up metrics cache
            if container_name in self.known_container_metrics:
                del self.known_container_metrics[container_name]
        logger.debug(f"deleted configuration for {container_name}")

    def update_entity_status(self, container_name, container_state):
        """
        Publish the state of the container to the MQTT broker only if changed
        Possible docker container states are: created, running, paused, restarting, removing, exited and dead.
        But we are only interested in running and stopped containers
        TODO - should do this periodically - on startup, and then maybe once an hour?
        """
        state = "ON" if container_state.lower() == "running" else "OFF"
        self.mqtt_client.publish(self._get_topic(container_name, "state"), state)
        logger.debug(f"Publishing current state for  {container_name}: {state}")
        # Only publish if state has changed
        last_state = self.known_docker_statuses.get(container_name)
        #if last_state is None or (last_state.lower() == "running") != (
        #    container_state.lower() == "running"
        #):
        #    self.mqtt_client.publish(self._get_topic(container_name, "state"), state)
        #    logger.debug(f"Estado actualizado para {container_name}: {state}")

    def _get_topic(self, container_name, topic):
        assert topic in ["state", "command", "config", ""]
        return f"homeassistant/switch/{self.prefix}{container_name}/{topic}"

    def _get_sensor_topic(self, container_name, metric, topic):
        """Get MQTT topic for sensor entities"""
        assert topic in ["state", "config", ""]
        return f"homeassistant/sensor/{self.prefix}{container_name}_{metric}/{topic}"

    def create_metric_entities(self, container_name):
        """Create MQTT sensor entities for container metrics"""
        metrics_config = {
            "cpu": {
                "name": f"{container_name} CPU",
                "unit": "%",
                "icon": "mdi:cpu-64-bit",
                "device_class": None,
                "state_class": "measurement",
            },
            "memory": {
                "name": f"{container_name} Memory",
                "unit": "%",
                "icon": "mdi:memory",
                "device_class": None,
                "state_class": "measurement",
            },
            "memory_usage": {
                "name": f"{container_name} Memory Usage",
                "unit": "MB",
                "icon": "mdi:memory",
                "device_class": None,
                "state_class": "measurement",
            },
            "network_rx": {
                "name": f"{container_name} Network RX",
                "unit": "MB",
                "icon": "mdi:download-network",
                "device_class": None,
                "state_class": "total_increasing",
            },
            "network_tx": {
                "name": f"{container_name} Network TX",
                "unit": "MB",
                "icon": "mdi:upload-network",
                "device_class": None,
                "state_class": "total_increasing",
            },
            "disk_read": {
                "name": f"{container_name} Disk Read",
                "unit": "MB",
                "icon": "mdi:harddisk",
                "device_class": None,
                "state_class": "total_increasing",
            },
            "disk_write": {
                "name": f"{container_name} Disk Write",
                "unit": "MB",
                "icon": "mdi:harddisk",
                "device_class": None,
                "state_class": "total_increasing",
            },
        }

        for metric, config in metrics_config.items():
            self.mqtt_client.publish(
                self._get_sensor_topic(container_name, metric, "config"),
                json.dumps(
                    {
                        "name": config["name"],
                        "unique_id": f"{self.prefix}{container_name}_{metric}",
                        "state_topic": self._get_sensor_topic(
                            container_name, metric, "state"
                        ),
                        "unit_of_measurement": config["unit"],
                        "icon": config["icon"],
                        "device_class": config.get("device_class"),
                        "state_class": config.get("state_class"),
                        "device": self.device_config,
                    }
                ),
                retain=True,
            )
            logger.debug(f"Metric entity created for {container_name} - {metric}")
            logger.debug(
                    f"{json.dumps(
                    {
                        "name": config["name"],
                        "unique_id": f"{self.prefix}{container_name}_{metric}",
                        "state_topic": self._get_sensor_topic(
                            container_name, metric, "state"
                        ),
                        "unit_of_measurement": config["unit"],
                        "icon": config["icon"],
                        "device_class": config.get("device_class"),
                        "state_class": config.get("state_class"),
                        "device": self.device_config,
                    }
                )} with retain = True")

    def update_container_metrics(self, container_name):
        """Update container resource metrics only if changed"""
        if not self.config.enable_metrics:
            return

        stats = self.docker_manager.get_container_stats(container_name)
        if not stats:
            return

        # Get current metrics
        current_metrics = {
            "cpu": round(stats.get("cpu_percent", 0), 2),
            "memory": round(stats.get("memory_percent", 0), 2),
            "memory_usage": round(stats.get("memory_usage_mb", 0), 2),
            "network_rx": round(stats.get("network_rx_mb", 0), 2),
            "network_tx": round(stats.get("network_tx_mb", 0), 2),
            "disk_read": round(stats.get("blkio_read_mb", 0), 2),
            "disk_write": round(stats.get("blkio_write_mb", 0), 2),
        }

        # Get last known metrics for this container
        last_metrics = self.known_container_metrics.get(container_name, {})

        # Only publish metrics that have changed
        for metric, value in current_metrics.items():
            if (
                metric not in last_metrics or abs(last_metrics[metric] - value) >= 0.01
            ):  # Threshold for change
                self.mqtt_client.publish(
                    self._get_sensor_topic(container_name, metric, "state"), str(value)
                )
                logger.debug(
                    f"[update metrics] Metric for {container_name}.{metric}: state set to -> {value}"
                )

        # Update known metrics
        self.known_container_metrics[container_name] = current_metrics

    def delete_metric_entities(self, container_name):
        """Delete MQTT sensor entities for container metrics"""
        metrics = [
            "cpu",
            "memory",
            "memory_usage",
            "network_rx",
            "network_tx",
            "disk_read",
            "disk_write",
        ]
        for metric in metrics:
            self.mqtt_client.publish(
                self._get_sensor_topic(container_name, metric, "config"), ""
            )
            self.mqtt_client.publish(
                self._get_sensor_topic(container_name, metric, ""), ""
            )
            logger.debug(
                    f"[delete metrics] Metric for {container_name}.{metric}/config/  - sent to delete")


def main(args):
    logger.info("Starting the service Docker Status MQTT")
    service = DockerMQTT(config=Config(**vars(args)))
    service.run()


if __name__ == "__main__":
    # Vamos a aceptar parámetros de línea de comandos para poder ejecutar el script en modo local
    # o en modo SSH
    # --verbose para activar el modo verbose (debug). Esto cambia el loggin a nivel DEBUG

    parser = argparse.ArgumentParser(
        description="Docker Status MQTT. Only mqtt_server is required. "
        "You can use environment variables too. Just use capital letters and underscores."
    )
    parser.add_argument("--verbose", action="store_true", help="Activar modo verbose")
    parser.add_argument(
        "--name",
        dest="entity_name",
        help="Nombre del dispositivo en Home Assistant",
        default="Unraid Docker",
    )
    parser.add_argument(
        "--unraid_host",
        "-H",
        help="Host de Unraid",
        default=None,
    )
    parser.add_argument(
        "--unraid_port",
        "-p",
        help="Puerto de Unraid",
        default=None,
    )
    parser.add_argument(
        "--unraid_user",
        "-u",
        help="Usuario de Unraid",
        default=None,
    )
    parser.add_argument(
        "--unraid_password",
        help="Contraseña de Unraid",
        default=None,
    )
    parser.add_argument(
        "--mqtt_server",
        help="URL del broker MQTT",
        default=None,
    )
    parser.add_argument(
        "--mqtt_port",
        help="Puerto del broker MQTT",
        default=None,
    )
    parser.add_argument(
        "--mqtt_user",
        help="Usuario del broker MQTT",
        default=None,
    )
    parser.add_argument(
        "--mqtt_password",
        help="Contraseña del broker MQTT",
        default=None,
    )
    parser.add_argument(
        "--publish_interval",
        help="Intervalo de publicación en segundos",
        default=None,
    )
    parser.add_argument(
        "--exclude_only",
        help="Contenedores a excluir",
        default=None,
    )
    parser.add_argument(
        "--include_only",
        help="Contenedores a incluir",
        default=None,
    )
    parser.add_argument(
        "--use_cmd_local",
        help="Usar comandos locales en lugar de SSH",
        action="store_true",
    )
    parser.add_argument(
        "--entity_prefix",
        help="Prefijo de los dispositivos en Home Assistant",
        default=None,
    )
    parser.add_argument(
        "--enable_metrics",
        help="Habilitar métricas de contenedores (CPU, memoria, red, disco)",
        action="store_true",
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    main(args)
