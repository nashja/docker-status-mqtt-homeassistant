# Docker Status MQTT Home Assistant

Monitor and control your Docker containers through Home Assistant via MQTT.

## Overview

This container publishes the status of your Docker containers to an MQTT broker and creates Home Assistant switch entities for each container. It allows you to:

- **Monitor** container states (running/stopped) in real-time
- **Control** containers (start/stop) directly from Home Assistant
- **Filter** which containers to monitor using include/exclude lists
- **Connect** to local or remote Docker hosts via SSH

Originally created for Unraid servers but works with any Docker host.

## Quick Start

```bash
docker run -d \
  --name docker-status-mqtt \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e MQTT_SERVER=YOUR_MQTT_IP \
  -e MQTT_USER=YOUR_MQTT_USER \
  -e MQTT_PASSWORD=YOUR_MQTT_PASSWORD \
  pcarorevuelta/docker-status-mqtt-homeassistant
```

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `MQTT_SERVER` | MQTT broker IP/hostname | - | Yes |
| `MQTT_USER` | MQTT username | - | No |
| `MQTT_PASSWORD` | MQTT password | - | No |
| `MQTT_PORT` | MQTT broker port | 1883 | No |
| `PUBLISH_INTERVAL` | Status update interval (seconds) | 60 | No |
| `INCLUDE_ONLY` | Comma-separated container names to monitor | - | No |
| `EXCLUDE_ONLY` | Comma-separated container names to exclude | - | No |

### SSH Mode (Remote Docker Host)

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SSH_HOST` | Remote Docker host IP/hostname | - | No* |
| `SSH_PORT` | SSH port | 22 | No |
| `SSH_USER` | SSH username | - | No* |
| `SSH_PASSWORD` | SSH password | - | No* |

*Required only for SSH mode

## Usage Examples

### Local Docker Socket (Default)
```bash
docker run -d \
  --name docker-status-mqtt \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e MQTT_SERVER=192.168.1.100 \
  -e MQTT_USER=homeassistant \
  -e MQTT_PASSWORD=mypassword \
  pcarorevuelta/docker-status-mqtt-homeassistant
```

### Remote Docker via SSH
```bash
docker run -d \
  --name docker-status-mqtt \
  -e SSH_HOST=192.168.1.50 \
  -e SSH_USER=root \
  -e SSH_PASSWORD=rootpassword \
  -e MQTT_SERVER=192.168.1.100 \
  -e MQTT_USER=homeassistant \
  -e MQTT_PASSWORD=mypassword \
  pcarorevuelta/docker-status-mqtt-homeassistant
```

### Filter Containers
```bash
# Monitor only specific containers
docker run -d \
  --name docker-status-mqtt \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e MQTT_SERVER=192.168.1.100 \
  -e INCLUDE_ONLY=plex,sonarr,radarr \
  pcarorevuelta/docker-status-mqtt-homeassistant

# Exclude specific containers
docker run -d \
  --name docker-status-mqtt \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e MQTT_SERVER=192.168.1.100 \
  -e EXCLUDE_ONLY=watchtower,portainer \
  pcarorevuelta/docker-status-mqtt-homeassistant
```

## Docker Compose

```yaml
services:
  docker-status-mqtt-homeassistant:
    image: pcarorevuelta/docker-status-mqtt-homeassistant
    container_name: docker-status-mqtt
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - MQTT_SERVER=192.168.1.100
      - MQTT_USER=homeassistant
      - MQTT_PASSWORD=mypassword
      - PUBLISH_INTERVAL=30
```

## Home Assistant Integration

Once running, the container will:

1. Automatically create MQTT switch entities for each Docker container
2. Publish container states to `homeassistant/switch/{container_name}/state`
3. Listen for commands on `homeassistant/switch/{container_name}/set`

The switches will appear in Home Assistant under MQTT integration with the ability to:
- View current container state (on = running, off = stopped)
- Start/stop containers by toggling the switch

## MQTT Topics

- **State**: `homeassistant/switch/unraid_docker_{container}/state`
- **Command**: `homeassistant/switch/unraid_docker_{container}/set`
- **Config**: `homeassistant/switch/unraid_docker_{container}/config` (auto-discovery)

## Troubleshooting

### Permission Denied Error (Docker Socket)

If you get a "Permission denied" error when accessing the Docker socket:

**For Unraid users:**
- Make sure you're running the container as root (default behavior)
- Verify the Docker socket path is correct: `/var/run/docker.sock:/var/run/docker.sock`

**For other systems:**
```bash
# Option 1: Run with user matching docker group
docker run -d \
  --name docker-status-mqtt \
  --user $(id -u):$(getent group docker | cut -d: -f3) \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e MQTT_SERVER=YOUR_MQTT_IP \
  pcarorevuelta/docker-status-mqtt-homeassistant

# Option 2: Add your user to docker group
sudo usermod -aG docker $USER
```

### MQTT Connection Issues

- Verify MQTT broker is accessible from the container
- Check MQTT credentials are correct
- Ensure MQTT_SERVER uses IP address or resolvable hostname

## Support

- **GitHub**: [https://github.com/pcaro/docker-status-mqtt-homeassistant](https://github.com/pcaro/docker-status-mqtt-homeassistant)
- **Issues**: [https://github.com/pcaro/docker-status-mqtt-homeassistant/issues](https://github.com/pcaro/docker-status-mqtt-homeassistant/issues)

## Development

### Using .env file

For development, you can use a `.env` file instead of environment variables:

1. Copy `.env.example` to `.env`
2. Fill in your configuration
3. Run with: `docker run -d --name docker-status-mqtt --env-file .env pcarorevuelta/docker-status-mqtt-homeassistant`

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

MIT