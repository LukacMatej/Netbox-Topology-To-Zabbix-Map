# NetBox Topology to Zabbix Map Sync

Language:
[![English](https://img.shields.io/badge/English-active-0a7ea4)](README.md)
[![Cesky](https://img.shields.io/badge/Cesky-switch-cf2e2e)](README.cs.md)

Web service (FastAPI + htmx) that reads topology data from NetBox and creates or updates Zabbix maps.

## Features

- Imports topology from NetBox (including topology-views plugin XML export).
- Matches NetBox device labels to Zabbix hosts.
- Creates and updates multiple Zabbix maps, each with its own NetBox topology filter, from a web form.
- Lays out nodes automatically using a Fruchterman-Reingold force-directed algorithm.
- Dry-run preview of a map before it is written to Zabbix.
- Re-syncs all saved maps on webhook or manual trigger.
- Supports link indicators from NetBox cable custom field trigger mapping.
- Optionally splices out patch panel nodes, reconnecting the cables that pass through them.

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

3. Start the web server:

```bash
zbx-map-sync --host 0.0.0.0 --port 8080
```

4. Open `http://<host>:8080/`, fill in the map form (pre-filled from the environment variables) and use
   **Preview (dry-run)** or **Save & sync map**.

## Docker

The GitHub workflow publishes images to Docker Hub using this repository name:

```text
matejlukac/netbox-topology-zabbix-map
```

Pull and run (maps created in the UI are stored in `/data/maps.json`, keep them on a volume):

```bash
docker run -d -p 7010:7010 --env-file .env -v zbx-map-sync-data:/data matejlukac/netbox-topology-zabbix-map:latest
```

Build locally:

```bash
docker build -t netbox-topology-zabbix-map:local .
```

## Web UI and Endpoints

`zbx-map-sync` always starts the web server (`--host`, `--port`, `--log-level`).

The index page shows the NetBox/Zabbix connection (secrets are never displayed), a form for a new map
whose fields default to the environment variables below, and the list of saved maps with Sync / Edit /
Delete actions. The topology filter query is passed to the NetBox topology-views export, so any filter
the plugin supports (e.g. `site_id=1&role_id=3&tag=core`) produces a separate map.

- GET / : web UI
- POST /maps : save a map definition and sync it to Zabbix
- POST /maps/preview : dry-run a map definition (nothing is saved or written to Zabbix)
- POST /maps/sync : sync one saved map (`name` form field) or all of them
- POST /maps/delete : remove a saved map definition (the Zabbix map itself is kept)
- GET /sync : sync all maps, JSON result
- POST /webhook : webhook trigger, syncs all maps, JSON result
- GET/POST /cables/<cable_id>/triggers : link trigger picker for a cable

### Saved maps

Map definitions are stored in the JSON file given by `ZABBIX_MAPS_CONFIG` (default `maps.json` in the
working directory, `/data/maps.json` in Docker):

```json
{"maps": [{"name": "Core", "topology_query": "site_id=1", "ignored_device_roles": ["patchpanel"]}]}
```

Fields missing from an entry fall back to the environment defaults. `/sync` and `/webhook` sync every saved
map; while no map is saved, they sync the single map defined by the environment variables.

## Configuration

Required variables:

- NETBOX_URL
- NETBOX_TOKEN
- ZABBIX_URL
- ZABBIX_USER and ZABBIX_PASSWORD, or ZABBIX_TOKEN

Optional variables (map-level ones are the defaults of the web form):

- NETBOX_TOPOLOGY_PATH (default: /api/plugins/netbox_topology_views/xml-export/)
- NETBOX_TOPOLOGY_QUERY (default: show_unconnected=True&show_cables=True&limit=0)
- NETBOX_REQUIRED_TAG
- NETBOX_IGNORED_DEVICE_ROLES (comma-separated names/slugs)
- ZABBIX_TOKEN (Bearer token auth)
- ZABBIX_MAP_NAME (default: NetBox Topology)
- ZABBIX_MAP_WIDTH (default: 1920)
- ZABBIX_MAP_HEIGHT (default: 1200)
- ZABBIX_LAYOUT_GRID_X (default: 40)
- ZABBIX_LAYOUT_GRID_Y (default: 40)
- ZABBIX_SKIPPED_NODE_MODE (default: skip; one of skip, image)
- ZABBIX_SKIPPED_NODE_ICON_ID (icon ID used for image-mode skipped nodes; defaults to the built-in host icon)
- ZABBIX_MAPS_CONFIG (default: maps.json; file with saved map definitions)
- ZABBIX_ICON_MAP (default icon map name for new maps; empty = leave the map's icon map setting untouched)
- ZABBIX_INVENTORY_ROLE_SYNC (default: false; write NetBox device role slug to Zabbix host inventory "Type")
- LOG_LEVEL (default: DEBUG)

### Skipped Node Mode

Topology nodes that have no matching Zabbix host are, by default, left off the map (`skip`).
Set `ZABBIX_SKIPPED_NODE_MODE=image` to render them as image elements labeled with their NetBox
device name instead, optionally styled with `ZABBIX_SKIPPED_NODE_ICON_ID`.

### Patch Panel Splicing

Devices identified as patch panels by their NetBox label (e.g. containing "patch panel" or a
"PP" token) are only removed from the topology when `patchpanel` is included in
`NETBOX_IGNORED_DEVICE_ROLES`. When enabled, each patch panel node is spliced out and its two
cables are reconnected directly between the devices on either side, so the map shows the
effective link instead of the intermediate panel.

## Host Icons (Zabbix Icon Mapping)

Host icons are chosen by Zabbix itself through an icon map (Administration > General > Icon mapping):

1. Create an icon map with mappings on inventory field **Type**, one per NetBox device role slug. Expressions
   are regular expressions and match anywhere in the value, so anchor them, e.g. `^core-switch$`. Set a
   *Default* icon for everything else.
2. Set `ZABBIX_INVENTORY_ROLE_SYNC=true`. On every sync the device's role slug is written to the matched
   Zabbix host's inventory Type (only when it differs). Hosts with inventory disabled are switched to
   manual inventory; hosts in automatic mode keep it. This needs an Admin or Super admin API token.
3. Enter the icon map name in the map form (default from `ZABBIX_ICON_MAP`).

Icon mapping only applies to host elements; unmatched nodes shown as images keep
`ZABBIX_SKIPPED_NODE_ICON_ID`.

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
