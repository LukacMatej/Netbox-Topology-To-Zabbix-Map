# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.10+ FastAPI web service (`zbx-map-sync` always starts it; there is no one-shot terminal mode) that reads topology from NetBox (normally the `netbox_topology_views` plugin XML export) and creates/updates Zabbix network maps (`map.create` / `map.update` via JSON-RPC). Maps are defined from a web form and saved to a JSON file. The UI is server-rendered Jinja2 plus htmx. htmx is vendored at `zabbix_map_sync/static/htmx.min.js`, with no CDN and no JS build step.

## Commands

```bash
pip install -e '.[test]'                      # dev install (pytest + httpx for FastAPI TestClient)
python -m pytest -q                           # full suite (fast, no network)
python -m pytest tests/test_sync.py::test_name -q   # single test
zbx-map-sync --host 0.0.0.0 --port 8080       # web server (UI at /)
docker build -t netbox-topology-zabbix-map:local .  # container (serves on 7010, maps stored in /data volume)
```

There is no linter or formatter config. CI (`.github/workflows/docker-image.yml`) only builds the Docker image and pushes it to Docker Hub on `main` and `v*` tags. It does not run tests.

Configuration comes entirely from environment variables (see README). `.env` holds real credentials: never read it into output or commit it.

## Architecture

Request flow: `web.py` routes → `runner.sync_maps(settings, map_definitions, dry_run)` (or `run_synchronization()` for all configured maps) → per map: `NetBoxClient.fetch_topology` → `sync.sync_topology_to_zabbix_map`.

- **Models (`models/`)**: every dataclass lives here (topology graph, settings/`MapDefinition`, results, `ZabbixHost`, NetBox/trigger-picker records) and is re-exported from `zabbix_map_sync.models`. The package must not import other project modules, to avoid import cycles. `models/zabbix_map.py` holds `ZabbixMap` → `MapElement` / `MapLink` → `MapLinkTrigger`. `ZabbixClient.get_map_by_name` returns a `ZabbixMap` parsed with `from_api()`, and `create_map` / `update_map` take one and serialize it with `to_api_payload()`. Rules for what gets sent back to Zabbix (no `linkid` / `linktriggerid`, `sysmapid` added only on update) live in those serializers.
- **Map store (`config.py`)**: `load_settings()` reads env. Map-level env vars (`default_map_definition()`) are only defaults: they pre-fill the web form and fill fields missing from saved entries. Saved maps live in the `ZABBIX_MAPS_CONFIG` JSON file (`{"maps": [...]}`, default `maps.json`, `/data/maps.json` in Docker). `parse_map_definition()` validates both file entries and form posts. `save_map_definition()` / `delete_map_definition()` write the file atomically (temp file + `os.replace`) under a process-local lock. `load_map_definitions()` returns the saved maps, or the single env map when none are saved. The runner logs in to Zabbix once, then syncs each map on its own. If one map fails, the runner records a `MapSyncError` and moves on, and JSON endpoints report `status: partial_error`.
- **NetBox topology pipeline (`netbox.py`)**: `fetch_topology` picks the parser from the response Content-Type. The XML path runs a fixed chain: parse → `_enrich_xml_node_labels` (resolve real device names) → `_enrich_xml_edge_triggers` (fetch cables, read the `zabbix_triggers` custom field) → `_collapse_patch_panel_passthrough` → `_filter_xml_nodes_by_tag`. The JSON path is only `_parse_topology_json`. Patch-panel splicing happens only when `patchpanel` is in the ignored device roles, and detection is label-based (`_is_patch_panel_label`). `_normalize_trigger_names` accepts JSON strings, Python-literal strings, lists and `{"triggers": [...]}` dicts, because NetBox custom-field values come in several shapes.
- **Map building (`sync.py`)**: `build_zabbix_map` matches node labels to Zabbix host visible names. It reuses existing `selementid`s and links from the current map, so updates are in-place rather than recreated. It resolves each cable trigger name to a Zabbix trigger (`ZabbixClient.find_trigger_id`: exact match, then wildcard search, on both endpoint hosts) and adds them as link indicators. Unresolved triggers are counted and reported but do not fail the sync. Unmatched nodes are either dropped (`skip`) or rendered as image elements (`image`).
- **Layout and position persistence**: before rewriting an existing map, `sync_topology_to_zabbix_map` takes a snapshot. `_live_positions_by_device_name` reads every element's current x/y: host elements are named via `ZabbixClient.get_hosts_by_ids` using the technical `host`, and image elements via their `label`. The snapshot is written to the NetBox device custom field `zabbix_map_coordinates` (JSON keyed by map name), including devices no longer in the topology. The sync then runs with those merged records. Position precedence (`_resolve_fixed_positions`): live Zabbix position (hosts, by `hostid`) > NetBox-stored position (which now also carries live image positions) > auto-layout. Only nodes without a known position enter the Fruchterman-Reingold layout. Each connected component gets canvas area in proportion to its size, and single-node components snap to a free grid cell. After the map is written, a final `_persist_device_positions` call stores the positions of newly placed nodes. Both NetBox writes send only changed entries. Failures are logged and swallowed on purpose, never failing the map sync, and the merged positions are still used from memory. Positions are keyed by map name, so renaming a map starts it from a fresh layout.
- **Zabbix client (`zabbix.py`)**: supports a bearer token (`ZABBIX_TOKEN`) or legacy `user.login`. If Zabbix rejects the legacy `auth` field in the payload (newer Zabbix), `_rpc` automatically retries in bearer mode.
- **Web (`web.py`, `templates/`, `trigger_picker.py`)**: the htmx UI endpoints (`/maps`, `/maps/preview`, `/maps/sync`, `/maps/delete`, `/maps/form`) return HTML partials from `templates/partials/`. Saving returns the result plus an out-of-band (`hx-swap-oob`) refresh of `#maps-list`. Errors come back as partials with real 4xx/5xx codes. `base.html` sets htmx `responseHandling` so those are still swapped in. Blocking NetBox/Zabbix calls from `async` handlers go through `run_in_threadpool`. `GET /sync` and `POST /webhook` sync all maps and return JSON. The webhook payload is only logged. `GET/POST /cables/<id>/triggers` is an HTML picker. It lists the triggers on both endpoint hosts of a cable and writes the selection back to the cable's `zabbix_triggers` custom field. It is meant to be linked from a NetBox Custom Link. `RUNTIME_MARKER` in `web.py` is a manually bumped version string exposed at `/debug`.

## Conventions

- Domain objects are frozen dataclasses in `models/`. Import them from `zabbix_map_sync.models`, not from the modules that use them.
- Tests use hand-written fakes (e.g. `FakeZabbix`, fake NetBox clients) and `monkeypatch` for env vars, not HTTP mocking libraries. New client methods called from `sync.py` need a matching method on those fakes. Web tests use FastAPI `TestClient`, patch `web.sync_maps` / `web.run_synchronization`, and point `ZABBIX_MAPS_CONFIG` at `tmp_path`.
- Templates and static files ship through `[tool.setuptools.package-data]` in `pyproject.toml`. A new template subdirectory must be added there, or it will be missing from the installed package and Docker image.
- Never render secrets (tokens, passwords) in templates. The UI shows only whether auth is configured.
- Besides `logging`, the code uses `print(..., flush=True)` with a `[zbx-map-sync]` prefix. This keeps key lifecycle events visible in container logs whatever the log level.
- The README is kept in two languages (`README.md` and `README.cs.md`). Update both when documenting config or behavior. Neither README documents `ZABBIX_MAPS_CONFIG` or the `zabbix_map_coordinates` position field yet.
