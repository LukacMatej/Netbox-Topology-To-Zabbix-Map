from __future__ import annotations

import logging
import os

import requests
from flask import Flask, jsonify, redirect, request

from .config import ConfigurationError, load_settings
from .logging_utils import configure_logging
from .runner import run_synchronization
from .sync import SyncResult
from .trigger_picker import apply_cable_trigger_selection, get_cable_trigger_page
from .zabbix import ZabbixAPIError


logger = logging.getLogger(__name__)
RUNTIME_MARKER = "zbx-map-sync-1.0.3"


def _sync_result_to_json(result: SyncResult) -> dict:
    return {
        "status": "ok",
        "runtime_marker": RUNTIME_MARKER,
        "log_level": os.getenv("LOG_LEVEL", "DEBUG"),
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


def create_app() -> Flask:
    # Ensure logging is configured even when this module is started directly.
    configure_logging(os.getenv("LOG_LEVEL", "DEBUG"))
    print(
        f"[zbx-map-sync] create_app logging initialized LOG_LEVEL={os.getenv('LOG_LEVEL', 'DEBUG')}",
        flush=True,
    )
    app = Flask(__name__)

    @app.get("/")
    def index():
        return (
            "<html><body><h1>Zabbix Map Sync</h1>"
            "<p><a href='/sync'>Run manual synchronization</a></p>"
            "<p><a href='/debug'>Debug info</a></p>"
            "<p>Pick triggers for a specific link at /cables/&lt;cable_id&gt;/triggers</p>"
            "</body></html>"
        )

    @app.get("/debug")
    def debug_info():
        return (
            jsonify(
                {
                    "status": "ok",
                    "runtime_marker": RUNTIME_MARKER,
                    "log_level": os.getenv("LOG_LEVEL", "DEBUG"),
                    "module": __name__,
                    "file": __file__,
                }
            ),
            200,
        )

    @app.get("/sync")
    def manual_sync():
        try:
            print("[zbx-map-sync] /sync requested", flush=True)
            logger.info("Manual sync requested")
            result = run_synchronization(dry_run=False)
            print(
                "[zbx-map-sync] /sync finished "
                f"created={result.created} matched_hosts={result.matched_hosts} "
                f"links={result.total_links} unresolved={result.unresolved_link_rules}",
                flush=True,
            )
            return jsonify(_sync_result_to_json(result)), 200
        except (ConfigurationError, ZabbixAPIError, ValueError, requests.RequestException) as exc:
            print(f"[zbx-map-sync] /sync failed error={exc}", flush=True)
            logger.exception("Manual sync failed")
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.get("/cables/<cable_id>/triggers")
    def cable_triggers_page(cable_id):
        try:
            settings = load_settings()
            body = get_cable_trigger_page(settings, cable_id)
            return body, 200
        except (ConfigurationError, ZabbixAPIError, ValueError, requests.RequestException) as exc:
            logger.exception("Failed to load trigger picker cable_id=%s", cable_id)
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.post("/cables/<cable_id>/triggers")
    def save_cable_triggers(cable_id):
        trigger_names = request.form.getlist("trigger")
        try:
            settings = load_settings()
            apply_cable_trigger_selection(settings, cable_id, trigger_names)
        except (ConfigurationError, ValueError, requests.RequestException) as exc:
            logger.exception("Failed to save trigger selection cable_id=%s", cable_id)
            return jsonify({"status": "error", "message": str(exc)}), 500
        return redirect(f"/cables/{cable_id}/triggers")

    @app.post("/webhook")
    def webhook_sync():
        payload = request.get_json(silent=True)
        try:
            logger.info("Webhook sync requested payload=%s", payload)
            result = run_synchronization(dry_run=False)
            return jsonify(_sync_result_to_json(result)), 200
        except (ConfigurationError, ZabbixAPIError, ValueError, requests.RequestException) as exc:
            logger.exception("Webhook sync failed")
            return jsonify({"status": "error", "message": str(exc)}), 500

    return app


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    configure_logging(os.getenv("LOG_LEVEL", "DEBUG"))
    print(
        f"[zbx-map-sync] run_server host={host} port={port} LOG_LEVEL={os.getenv('LOG_LEVEL', 'DEBUG')}",
        flush=True,
    )
    app = create_app()
    logger.info("Starting web server host=%s port=%s", host, port)
    app.run(host=host, port=port)
