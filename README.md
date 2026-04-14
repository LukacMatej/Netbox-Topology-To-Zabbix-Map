# NetBox Topology to Zabbix Map Sync

Language:
[![English](https://img.shields.io/badge/English-active-0a7ea4)](README.md)
[![Cesky](https://img.shields.io/badge/Cesky-switch-cf2e2e)](README.cs.md)

Python CLI and optional webhook service that reads topology data from NetBox and creates or updates a Zabbix map.

## Features

- Imports topology from NetBox (including topology-views plugin XML export).
- Matches NetBox device labels to Zabbix hosts.
- Creates or updates one Zabbix map with nodes and links.
- Supports dry-run mode for safe validation.
- Supports webhook/manual sync through a lightweight web server.
- Supports link indicators from NetBox cable custom field trigger mapping.

## Requirements

- Python 3.10+
- NetBox API token
- Zabbix API credentials or token

## Quick Start

1. Install:

```bash
pip install .
```

2. Export required environment variables:

```bash
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="your-netbox-token"
export ZABBIX_URL="https://zabbix.example.com/api_jsonrpc.php"
export ZABBIX_USER="Admin"
export ZABBIX_PASSWORD="zabbix"
```

3. Test without writing changes:

```bash
zbx-map-sync --dry-run
```

4. Apply changes:

```bash
zbx-map-sync
```

## Docker

The GitHub workflow publishes images to Docker Hub using this repository name:

```text
<dockerhub-username>/netbox-topology-zabbix-map
```

Pull and run:

```bash
docker run --rm --env-file .env <dockerhub-username>/netbox-topology-zabbix-map:latest
```

Dry-run in Docker:

```bash
docker run --rm --env-file .env <dockerhub-username>/netbox-topology-zabbix-map:latest --dry-run
```

Build locally:

```bash
docker build -t netbox-topology-zabbix-map:local .
```

## Web Mode

Start HTTP mode:

```bash
zbx-map-sync --serve --host 0.0.0.0 --port 8080
```

Endpoints:

- GET / : simple page with a manual sync link
- GET /sync : manual synchronization trigger
- POST /webhook : webhook synchronization trigger

## Configuration

Required variables:

- NETBOX_URL
- NETBOX_TOKEN
- ZABBIX_URL
- ZABBIX_USER and ZABBIX_PASSWORD, or ZABBIX_TOKEN

Optional variables:

- NETBOX_TOPOLOGY_PATH (default: /api/plugins/netbox_topology_views/xml-export/)
- NETBOX_TOPOLOGY_QUERY (default: show_unconnected=True&show_cables=True&limit=0)
- NETBOX_REQUIRED_TAG
- NETBOX_IGNORED_DEVICE_ROLES (comma-separated names/slugs)
- ZABBIX_TOKEN (Bearer token auth)
- ZABBIX_MAP_NAME (default: NetBox Topology)
- ZABBIX_MAP_WIDTH (default: 1280)
- ZABBIX_MAP_HEIGHT (default: 900)
- ZABBIX_LAYOUT_GRID_X (default: 40)
- ZABBIX_LAYOUT_GRID_Y (default: 40)
- LOG_LEVEL (default: DEBUG)

## Link Trigger Mapping

Trigger mapping is read from NetBox cable custom field named zabbix_triggers.

Example value:

```json
[{"triggers": ["trigger1", "trigger2"]}]
```

Behavior:

- Each trigger name is searched on both hosts connected by the cable.
- Matching triggers are added as Zabbix link indicators.
- Missing triggers are reported as unresolved and do not stop synchronization.
