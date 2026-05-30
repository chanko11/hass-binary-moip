# Home Assistant dev environment

A throwaway Home Assistant instance for testing the `binary_moip` integration.
It mounts this repo's `custom_components/` into the container, so HA loads our
code directly. The `config/` directory is local-only (gitignored).

## Networking

Runs with `network_mode: host` and HA's standard port **8123**. This dev
machine has its own IP (distinct from the production HA), so there is no port
collision. See the comment in `docker-compose.yml`.

## Start

```bash
docker compose -f ha-dev/docker-compose.yml up -d
```

First boot takes a minute or two while HA initializes its config.

## Web UI

http://localhost:8123  (complete the onboarding wizard on first launch)

## View logs (important for debugging)

```bash
# Follow live:
docker compose -f ha-dev/docker-compose.yml logs -f

# Just our integration's lines:
docker compose -f ha-dev/docker-compose.yml logs -f | grep binary_moip
```

## Restart HA after code changes

HA loads `custom_components/` at startup, so after editing integration code:

```bash
docker compose -f ha-dev/docker-compose.yml restart
```

## Stop

```bash
# Stop (keep container + config):
docker compose -f ha-dev/docker-compose.yml stop

# Stop and remove the container (config/ persists on disk):
docker compose -f ha-dev/docker-compose.yml down
```
