# Zabbix map sync from NetBox topology

Small Python CLI app that reads topology data from NetBox (including topology-views plugin endpoints) and creates/updates a Zabbix map.

## What it does

- Fetches a topology graph from NetBox plugin (`nodes` + `edges` style payload)
- Resolves NetBox node labels against Zabbix host names
- Creates or updates one Zabbix map with host elements and links
- Uses a simple circular layout

## Requirements

- Python 3.10+
- API token for NetBox
- Zabbix API credentials

## Configuration

Copy `.env.example` values into your shell environment (or a `.env` loader you already use).

Required variables:

- `NETBOX_URL`
- `NETBOX_TOKEN`
- `ZABBIX_URL`
- `ZABBIX_USER` and `ZABBIX_PASSWORD` (or `ZABBIX_TOKEN`)

Optional variables:

- `NETBOX_TOPOLOGY_PATH` (default `/api/plugins/netbox_topology_views/xml-export/`)
- `NETBOX_TOPOLOGY_QUERY` (default `show_unconnected=True&show_cables=True&limit=0`, example: `site_id=1`)
- `NETBOX_REQUIRED_TAG` (optional, include only devices with this NetBox tag, example: `Zabbix Map`)
- `NETBOX_IGNORED_DEVICE_ROLES` (optional, comma-separated NetBox device-role names/slugs to treat as passthrough nodes, example: `patch-panel,power-panel`)
- `ZABBIX_TOKEN` (optional, for Bearer-token auth)
- `ZABBIX_MAP_NAME` (default `NetBox Topology`)
- `ZABBIX_MAP_WIDTH` (default `1280`)
- `ZABBIX_MAP_HEIGHT` (default `900`)
- `ZABBIX_LAYOUT_GRID_X` (default `40`)
- `ZABBIX_LAYOUT_GRID_Y` (default `40`)
- `LOG_LEVEL` (default `DEBUG`)

## Run

Dry-run:

```bash
zbx-map-sync --dry-run
```

Apply changes:

```bash
zbx-map-sync
```

Verbose debug logging:

```bash
zbx-map-sync --log-level DEBUG
```

Run web server:

```bash
zbx-map-sync --serve --host 0.0.0.0 --port 8080
```

Endpoints:

- `GET /` - simple page with clickable manual sync link
- `GET /sync` - manual synchronization trigger
- `POST /webhook` - webhook synchronization trigger

## Docker

Build image:

```bash
docker build -t zbx-map-sync:latest .
```

Run with environment file:

```bash
docker run --rm --env-file .env zbx-map-sync:latest
```

Run on Linux with host networking:

```bash
docker run --rm --network=host --env-file .env zbx-map-sync:latest
```

Dry-run in Docker:

```bash
docker run --rm --env-file .env zbx-map-sync:latest --dry-run
```

Show CLI help in Docker:

```bash
docker run --rm zbx-map-sync:latest --help
```

Run with inline environment variables:

```bash
docker run --rm \
	-e NETBOX_URL="https://netbox.example.com" \
	-e NETBOX_TOKEN="your-netbox-token" \
	-e ZABBIX_URL="https://zabbix.example.com/api_jsonrpc.php" \
	-e ZABBIX_USER="Admin" \
	-e ZABBIX_PASSWORD="zabbix" \
	zbx-map-sync:latest
```

## Notes

- Node label in NetBox is matched to Zabbix host `host` or `name`.
- Nodes that do not match a Zabbix host are skipped.
- If your topology endpoint shape differs, adjust parsing in `zabbix_map_sync/netbox.py`.
- `NETBOX_URL` must include scheme, for example `http://localhost:8000` or `https://netbox.example.com`.
- If you get `404` for the topology URL, networking is working; fix `NETBOX_TOPOLOGY_PATH` to match your NetBox topology-views endpoint.
- For `netbox_topology_views` plugin, `xml-export` returns XML and this app now parses it automatically.
- For `xml-export`, the app automatically includes `show_unconnected=True` and `show_cables=True` unless you override them in `NETBOX_TOPOLOGY_QUERY`.
- When using `xml-export`, the app enriches link-trigger data from the NetBox cables API because the XML export itself does not include cable custom-field trigger metadata.
- Nodes matching `NETBOX_IGNORED_DEVICE_ROLES` are collapsed as passthrough devices (for degree-2 transit links), so their neighbors stay directly connected.

## Link triggers on map links

Trigger mapping is read directly from NetBox cable custom field `zabbix_triggers` on each topology edge.

Expected custom-field value example:

```json
[{"triggers": ["trigger1", "trigger2"]}]
```

- Each trigger name is searched on both hosts connected by the cable.
- Matching triggers are added as Zabbix link indicators on that map link.
- If a trigger name is not found, sync completes and reports it in unresolved trigger details.
- DEBUG logs show the full path from NetBox edge parsing through Zabbix trigger lookup so you can see whether the trigger was missing in NetBox data, missing in Zabbix, or skipped during map assembly.
