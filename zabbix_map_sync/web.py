from __future__ import annotations

import logging
import os
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .config import (
    VALID_SKIPPED_NODE_MODES,
    ConfigurationError,
    default_map_definition,
    delete_map_definition,
    load_map_definitions,
    load_saved_map_definitions,
    load_settings,
    parse_map_definition,
    save_map_definition,
)
from .logging_utils import configure_logging
from .models import DryRunResult, MapDefinition, MapSyncError, Settings, SyncResult
from .runner import run_synchronization, sync_maps
from .trigger_picker import apply_cable_trigger_selection, get_cable_trigger_context
from .zabbix import ZabbixAPIError


logger = logging.getLogger(__name__)
RUNTIME_MARKER = "zbx-map-sync-2.0.0"

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

# Errors that mean "sync could not run" (bad config, NetBox/Zabbix unreachable
# or rejecting the request) as opposed to a bug.
SYNC_ERRORS = (ConfigurationError, ZabbixAPIError, ValueError, requests.RequestException)

MAP_FORM_FIELDS = (
    "name",
    "topology_path",
    "topology_query",
    "required_tag",
    "ignored_device_roles",
    "width",
    "height",
    "grid_x",
    "grid_y",
    "skipped_node_mode",
    "skipped_node_icon_id",
    "icon_map",
)


def _sync_result_to_dict(result: SyncResult | DryRunResult | MapSyncError) -> dict:
    if isinstance(result, MapSyncError):
        return {"status": "error", "map_name": result.map_name, "message": result.error}
    if isinstance(result, DryRunResult):
        return {
            "status": "ok",
            "map_name": result.map_name,
            "dry_run": True,
            "total_nodes": result.total_nodes,
            "total_links": result.total_links,
        }
    return {
        "status": "ok",
        "created": result.created,
        "map_name": result.map_name,
        "total_nodes": result.total_nodes,
        "matched_hosts": result.matched_hosts,
        "image_nodes": result.image_nodes,
        "skipped_nodes": result.skipped_nodes,
        "total_links": result.total_links,
        "unresolved_link_rules": result.unresolved_link_rules,
        "unresolved_link_rule_details": list(result.unresolved_link_rule_details),
    }


def _sync_results_to_json(results: list[SyncResult | DryRunResult | MapSyncError]) -> dict:
    any_failed = any(isinstance(result, MapSyncError) for result in results)
    return {
        "status": "partial_error" if any_failed else "ok",
        "runtime_marker": RUNTIME_MARKER,
        "log_level": os.getenv("LOG_LEVEL", "DEBUG"),
        "results": [_sync_result_to_dict(result) for result in results],
    }


def _form_values(map_def: MapDefinition) -> dict[str, str]:
    return {
        "name": map_def.name,
        "topology_path": map_def.topology_path,
        "topology_query": map_def.topology_query,
        "required_tag": map_def.required_tag,
        "ignored_device_roles": ", ".join(map_def.ignored_device_roles),
        "width": str(map_def.width),
        "height": str(map_def.height),
        "grid_x": str(map_def.grid_x),
        "grid_y": str(map_def.grid_y),
        "skipped_node_mode": map_def.skipped_node_mode,
        "skipped_node_icon_id": map_def.skipped_node_icon_id,
        "icon_map": map_def.icon_map,
    }


def _connection_info(settings: Settings) -> dict[str, str]:
    # Secrets are never rendered -- only whether they are configured.
    if settings.zabbix_token:
        zabbix_auth = "API token"
    else:
        zabbix_auth = f"user {settings.zabbix_user}"
    return {
        "netbox_url": settings.netbox_url,
        "zabbix_url": settings.zabbix_url,
        "zabbix_auth": zabbix_auth,
        "maps_config": settings.zabbix_maps_config,
        "inventory_role_sync": "on" if settings.zabbix_inventory_role_sync else "off",
    }


def _maps_list_context(settings: Settings) -> dict:
    return {
        "saved_maps": load_saved_map_definitions(settings),
        "env_map_name": settings.zabbix_map_name,
    }


def _map_form_context(values: dict[str, str], original_name: str = "", error: str = "") -> dict:
    return {
        "values": values,
        "original_name": original_name,
        "form_error": error,
        "skipped_node_modes": VALID_SKIPPED_NODE_MODES,
    }


async def _read_map_form(request: Request) -> tuple[dict[str, str], str]:
    form = await request.form()
    values = {field: str(form.get(field, "")).strip() for field in MAP_FORM_FIELDS}
    return values, str(form.get("original_name", "")).strip()


def _render_results(
    request: Request,
    results: list[SyncResult | DryRunResult | MapSyncError],
    status_code: int = 200,
    settings: Settings | None = None,
) -> HTMLResponse:
    context: dict = {"results": results, "error": "", "refresh_maps": settings is not None}
    if settings is not None:
        context.update(_maps_list_context(settings))
    return templates.TemplateResponse(request, "partials/sync_result.html", context, status_code=status_code)


def _render_error(request: Request, message: str, status_code: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/sync_result.html",
        {"results": [], "error": message, "refresh_maps": False},
        status_code=status_code,
    )


def create_app() -> FastAPI:
    # Ensure logging is configured even when the app is started by an external ASGI server.
    configure_logging(os.getenv("LOG_LEVEL", "DEBUG"))
    print(
        f"[zbx-map-sync] create_app logging initialized LOG_LEVEL={os.getenv('LOG_LEVEL', 'DEBUG')}",
        flush=True,
    )
    app = FastAPI(title="Zabbix Map Sync", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(request: Request, exc: ConfigurationError):
        logger.error("Configuration error path=%s: %s", request.url.path, exc)
        return _render_error(request, f"Configuration error: {exc}", 500)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        try:
            settings = load_settings()
            context = {
                "config_error": "",
                "connection": _connection_info(settings),
                **_maps_list_context(settings),
                **_map_form_context(_form_values(default_map_definition(settings))),
            }
        except ConfigurationError as exc:
            logger.error("Cannot render index, configuration invalid: %s", exc)
            context = {"config_error": str(exc)}
        return templates.TemplateResponse(request, "index.html", context)

    @app.get("/debug")
    def debug_info():
        return {
            "status": "ok",
            "runtime_marker": RUNTIME_MARKER,
            "log_level": os.getenv("LOG_LEVEL", "DEBUG"),
            "module": __name__,
            "file": __file__,
        }

    @app.get("/maps/form", response_class=HTMLResponse)
    def map_form(request: Request, name: str = ""):
        settings = load_settings()
        map_def = default_map_definition(settings)
        original_name = ""
        if name:
            saved = {item.name: item for item in load_saved_map_definitions(settings)}
            if name in saved:
                map_def = saved[name]
                original_name = name
        return templates.TemplateResponse(
            request, "partials/map_form.html", _map_form_context(_form_values(map_def), original_name)
        )

    async def _parse_form_or_error(request: Request):
        values, original_name = await _read_map_form(request)
        settings = load_settings()
        try:
            map_def = parse_map_definition(values, default_map_definition(settings))
        except ConfigurationError as exc:
            return settings, None, original_name, _render_error(request, str(exc), 422)
        return settings, map_def, original_name, None

    @app.post("/maps/preview", response_class=HTMLResponse)
    async def preview_map(request: Request):
        settings, map_def, _, error = await _parse_form_or_error(request)
        if error:
            return error
        try:
            results = await run_in_threadpool(sync_maps, settings, [map_def], True)
        except SYNC_ERRORS as exc:
            logger.exception("Map preview failed")
            return _render_error(request, str(exc), 502)
        return _render_results(request, results)

    @app.post("/maps", response_class=HTMLResponse)
    async def save_and_sync_map(request: Request):
        settings, map_def, original_name, error = await _parse_form_or_error(request)
        if error:
            return error
        save_map_definition(settings, map_def, previous_name=original_name or None)
        logger.info("Saved map definition name=%s", map_def.name)
        try:
            results = await run_in_threadpool(sync_maps, settings, [map_def], False)
        except SYNC_ERRORS as exc:
            logger.exception("Map sync after save failed map=%s", map_def.name)
            return _render_error(request, f"Map saved, but synchronization failed: {exc}", 502)
        return _render_results(request, results, settings=settings)

    @app.post("/maps/sync", response_class=HTMLResponse)
    async def sync_saved_map(request: Request):
        form = await request.form()
        name = str(form.get("name", "")).strip()
        settings = load_settings()
        map_defs = [item for item in load_map_definitions(settings) if not name or item.name == name]
        if not map_defs:
            return _render_error(request, f"No saved map named {name!r}", 404)
        try:
            results = await run_in_threadpool(sync_maps, settings, map_defs, False)
        except SYNC_ERRORS as exc:
            logger.exception("Map sync failed name=%s", name or "<all>")
            return _render_error(request, str(exc), 502)
        return _render_results(request, results)

    @app.post("/maps/delete", response_class=HTMLResponse)
    async def delete_saved_map(request: Request):
        form = await request.form()
        name = str(form.get("name", "")).strip()
        settings = load_settings()
        if delete_map_definition(settings, name):
            logger.info("Deleted map definition name=%s", name)
        return templates.TemplateResponse(request, "partials/maps_list.html", _maps_list_context(settings))

    @app.get("/sync")
    def manual_sync():
        try:
            print("[zbx-map-sync] /sync requested", flush=True)
            logger.info("Manual sync requested")
            results = run_synchronization(dry_run=False)
            return _sync_results_to_json(results)
        except SYNC_ERRORS as exc:
            print(f"[zbx-map-sync] /sync failed error={exc}", flush=True)
            logger.exception("Manual sync failed")
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @app.post("/webhook")
    async def webhook_sync(request: Request):
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        try:
            logger.info("Webhook sync requested payload=%s", payload)
            results = await run_in_threadpool(run_synchronization, False)
            return _sync_results_to_json(results)
        except SYNC_ERRORS as exc:
            logger.exception("Webhook sync failed")
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @app.get("/cables/{cable_id}/triggers", response_class=HTMLResponse)
    def cable_triggers_page(request: Request, cable_id: str, saved: str = ""):
        try:
            context = get_cable_trigger_context(load_settings(), cable_id)
        except SYNC_ERRORS as exc:
            logger.exception("Failed to load trigger picker cable_id=%s", cable_id)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)
        return templates.TemplateResponse(
            request, "trigger_picker.html", {"context": context, "saved": saved == "1"}
        )

    @app.post("/cables/{cable_id}/triggers")
    async def save_cable_triggers(request: Request, cable_id: str):
        form = await request.form()
        trigger_names = [str(value) for value in form.getlist("trigger")]
        try:
            await run_in_threadpool(apply_cable_trigger_selection, load_settings(), cable_id, trigger_names)
        except (ConfigurationError, ValueError, requests.RequestException) as exc:
            logger.exception("Failed to save trigger selection cable_id=%s", cable_id)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)
        return RedirectResponse(f"/cables/{cable_id}/triggers?saved=1", status_code=303)

    return app


def run_server(host: str = "0.0.0.0", port: int = 8080, log_level: str = "DEBUG") -> None:
    configure_logging(log_level)
    print(f"[zbx-map-sync] run_server host={host} port={port} LOG_LEVEL={log_level}", flush=True)
    logger.info("Starting web server host=%s port=%s", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_level=log_level.lower())
