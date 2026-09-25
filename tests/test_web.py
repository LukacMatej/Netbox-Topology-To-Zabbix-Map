import json

import pytest
from fastapi.testclient import TestClient

from zabbix_map_sync.models import CableTriggerContext, DryRunResult, MapSyncError, SyncResult, TriggerChoice


def _sync_result(map_name="NetBox Topology", created=True, unresolved=()) -> SyncResult:
    return SyncResult(
        created=created,
        map_name=map_name,
        total_nodes=3,
        matched_hosts=3,
        skipped_nodes=0,
        image_nodes=0,
        total_links=2,
        unresolved_link_rules=len(unresolved),
        unresolved_link_rule_details=tuple(unresolved),
    )


@pytest.fixture
def maps_file(tmp_path, monkeypatch):
    path = tmp_path / "maps.json"
    for key, value in {
        "NETBOX_URL": "http://netbox.local",
        "NETBOX_TOKEN": "nb-secret-token",
        "ZABBIX_URL": "http://zabbix.local/api_jsonrpc.php",
        "ZABBIX_TOKEN": "zbx-secret-token",
        "ZABBIX_MAP_NAME": "Env Map",
        "NETBOX_TOPOLOGY_QUERY": "site_id=1",
        "NETBOX_IGNORED_DEVICE_ROLES": "patchpanel,server",
        "ZABBIX_MAPS_CONFIG": str(path),
    }.items():
        monkeypatch.setenv(key, value)
    return path


@pytest.fixture
def client():
    import zabbix_map_sync.web as web

    return TestClient(web.create_app())


@pytest.fixture
def sync_calls(monkeypatch):
    import zabbix_map_sync.web as web

    calls = []

    def fake_sync_maps(settings, map_definitions, dry_run=False):
        calls.append((list(map_definitions), dry_run))
        if dry_run:
            return [DryRunResult(map_name=m.name, total_nodes=5, total_links=4) for m in map_definitions]
        return [_sync_result(map_name=m.name) for m in map_definitions]

    monkeypatch.setattr(web, "sync_maps", fake_sync_maps)
    return calls


def _form(**overrides) -> dict:
    data = {
        "name": "Core",
        "topology_path": "/api/plugins/netbox_topology_views/xml-export/",
        "topology_query": "site_id=7&role_id=2",
        "required_tag": "",
        "ignored_device_roles": "patchpanel, server",
        "width": "1600",
        "height": "900",
        "grid_x": "40",
        "grid_y": "40",
        "skipped_node_mode": "image",
        "skipped_node_icon_id": "",
        "icon_map": "Role icons",
    }
    data.update(overrides)
    return data


def _saved(path) -> list[dict]:
    return json.loads(path.read_text())["maps"]


def test_index_prefills_form_from_env_without_leaking_secrets(maps_file, client) -> None:
    response = client.get("/")
    body = response.text

    assert response.status_code == 200
    assert 'name="name" type="text" value="Env Map"' in body
    assert 'value="site_id=1"' in body
    assert 'value="patchpanel, server"' in body
    assert "htmx.min.js" in body
    assert "nb-secret-token" not in body
    assert "zbx-secret-token" not in body
    assert "No saved maps yet" in body


def test_index_shows_configuration_error(monkeypatch, client) -> None:
    monkeypatch.delenv("NETBOX_URL", raising=False)

    response = client.get("/")

    assert response.status_code == 200
    assert "Configuration error" in response.text


def test_preview_runs_dry_run_without_saving(maps_file, client, sync_calls) -> None:
    response = client.post("/maps/preview", data=_form())

    assert response.status_code == 200
    assert "preview" in response.text
    (map_defs, dry_run), = sync_calls
    assert dry_run is True
    assert map_defs[0].topology_query == "site_id=7&role_id=2"
    assert map_defs[0].ignored_device_roles == ("patchpanel", "server")
    assert not maps_file.exists()


def test_save_persists_map_syncs_it_and_refreshes_list(maps_file, client, sync_calls) -> None:
    response = client.post("/maps", data=_form())

    assert response.status_code == 200
    assert "created" in response.text
    assert 'id="maps-list" class="card" hx-swap-oob="true"' in response.text
    (map_defs, dry_run), = sync_calls
    assert dry_run is False
    assert map_defs[0].name == "Core"
    saved = _saved(maps_file)
    assert [entry["name"] for entry in saved] == ["Core"]
    assert saved[0]["width"] == 1600
    assert saved[0]["skipped_node_mode"] == "image"
    assert saved[0]["icon_map"] == "Role icons"
    assert "Role icons" in response.text


def test_save_rejects_invalid_input(maps_file, client, sync_calls) -> None:
    response = client.post("/maps", data=_form(width="wide"))

    assert response.status_code == 422
    assert "must be an integer" in response.text
    assert sync_calls == []
    assert not maps_file.exists()


def test_save_with_original_name_renames_in_place(maps_file, client, sync_calls) -> None:
    client.post("/maps", data=_form(name="First"))
    client.post("/maps", data=_form(name="Second"))

    client.post("/maps", data={**_form(name="First renamed"), "original_name": "First"})

    assert [entry["name"] for entry in _saved(maps_file)] == ["First renamed", "Second"]


def test_edit_form_loads_saved_map(maps_file, client, sync_calls) -> None:
    client.post("/maps", data=_form(name="Core", topology_query="site_id=9"))

    response = client.get("/maps/form", params={"name": "Core"})

    assert "Edit map" in response.text
    assert 'value="site_id=9"' in response.text
    assert 'name="original_name" value="Core"' in response.text


def test_sync_single_saved_map_and_all(maps_file, client, sync_calls) -> None:
    client.post("/maps", data=_form(name="A"))
    client.post("/maps", data=_form(name="B"))
    sync_calls.clear()

    client.post("/maps/sync", data={"name": "B"})
    client.post("/maps/sync", data={})

    assert [m.name for m in sync_calls[0][0]] == ["B"]
    assert [m.name for m in sync_calls[1][0]] == ["A", "B"]


def test_sync_unknown_map_returns_404(maps_file, client, sync_calls) -> None:
    client.post("/maps", data=_form(name="A"))

    response = client.post("/maps/sync", data={"name": "missing"})

    assert response.status_code == 404
    assert "No saved map named" in response.text


def test_delete_removes_saved_map(maps_file, client, sync_calls) -> None:
    client.post("/maps", data=_form(name="A"))
    client.post("/maps", data=_form(name="B"))

    response = client.post("/maps/delete", data={"name": "A"})

    assert response.status_code == 200
    assert [entry["name"] for entry in _saved(maps_file)] == ["B"]
    assert ">A<" not in response.text


def test_get_sync_endpoint(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: [_sync_result()])

    response = client.get("/sync")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["results"][0]["map_name"] == "NetBox Topology"
    assert payload["results"][0]["created"] is True


def test_post_webhook_endpoint(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    expected = _sync_result(created=False, unresolved=("Cable trigger: A <-> B | trigger='Down'",))
    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: [expected])

    response = client.post("/webhook", json={"event": "topology.changed"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["results"][0]["created"] is False
    assert payload["results"][0]["unresolved_link_rules"] == 1


def test_post_webhook_accepts_empty_body(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: [_sync_result()])

    response = client.post("/webhook")

    assert response.status_code == 200


def test_get_sync_endpoint_reports_partial_error(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    bad = MapSyncError(map_name="Bad Map", error="boom")
    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: [_sync_result("Good Map"), bad])

    payload = client.get("/sync").json()

    assert payload["status"] == "partial_error"
    assert payload["results"][1]["status"] == "error"
    assert payload["results"][1]["message"] == "boom"


def test_endpoint_error_handling(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    def boom(dry_run=False):
        raise ValueError("boom")

    monkeypatch.setattr(web, "run_synchronization", boom)

    response = client.get("/sync")

    assert response.status_code == 500
    assert response.json() == {"status": "error", "message": "boom"}


def _trigger_context(**overrides) -> CableTriggerContext:
    values = dict(
        cable_id="42",
        device_a="Switch <1>",
        device_b="Switch 2",
        selected_triggers=("Link down",),
        available_triggers=(
            TriggerChoice(triggerid="555", description="Link down"),
            TriggerChoice(triggerid="556", description="High CPU"),
        ),
    )
    values.update(overrides)
    return CableTriggerContext(**values)


def test_cable_triggers_page_marks_selected_and_escapes(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "load_settings", lambda: object())
    monkeypatch.setattr(web, "get_cable_trigger_context", lambda settings, cable_id: _trigger_context())

    response = client.get("/cables/42/triggers")
    body = response.text

    assert response.status_code == 200
    assert "Switch &lt;1&gt;" in body
    assert 'value="Link down" checked' in body
    assert 'value="High CPU">' in body
    assert 'action="/cables/42/triggers"' in body
    assert "Saved." not in body


def test_cable_triggers_page_saved_banner_and_empty_list(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "load_settings", lambda: object())
    monkeypatch.setattr(
        web,
        "get_cable_trigger_context",
        lambda settings, cable_id: _trigger_context(selected_triggers=(), available_triggers=()),
    )

    body = client.get("/cables/42/triggers?saved=1").text

    assert "Saved." in body
    assert "No Zabbix triggers found" in body


def test_cable_triggers_page_error_handling(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "load_settings", lambda: object())

    def raise_value_error(settings, cable_id):
        raise ValueError("no such cable")

    monkeypatch.setattr(web, "get_cable_trigger_context", raise_value_error)

    response = client.get("/cables/42/triggers")

    assert response.status_code == 500
    assert response.json()["message"] == "no such cable"


def test_post_cable_triggers_saves_selection_and_redirects(monkeypatch, client) -> None:
    import zabbix_map_sync.web as web

    captured = {}
    monkeypatch.setattr(web, "load_settings", lambda: object())

    def fake_apply(settings, cable_id, trigger_names):
        captured["cable_id"] = cable_id
        captured["trigger_names"] = trigger_names

    monkeypatch.setattr(web, "apply_cable_trigger_selection", fake_apply)

    response = client.post(
        "/cables/42/triggers",
        data={"trigger": ["Link down", "High CPU"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cables/42/triggers?saved=1"
    assert captured == {"cable_id": "42", "trigger_names": ["Link down", "High CPU"]}
