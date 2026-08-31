from zabbix_map_sync.sync import SyncResult


def test_root_endpoint_contains_sync_link() -> None:
    import zabbix_map_sync.web as web

    app = web.create_app()
    client = app.test_client()

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Run manual synchronization" in body
    assert "href='/sync'" in body


def test_get_sync_endpoint(monkeypatch) -> None:
    import zabbix_map_sync.web as web

    expected = SyncResult(
        created=True,
        map_name="NetBox Topology",
        total_nodes=3,
        matched_hosts=3,
        skipped_nodes=0,
        image_nodes=0,
        total_links=2,
        unresolved_link_rules=0,
        unresolved_link_rule_details=(),
    )
    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: expected)

    app = web.create_app()
    client = app.test_client()

    response = client.get("/sync")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["map_name"] == "NetBox Topology"
    assert payload["created"] is True


def test_post_webhook_endpoint(monkeypatch) -> None:
    import zabbix_map_sync.web as web

    expected = SyncResult(
        created=False,
        map_name="NetBox Topology",
        total_nodes=4,
        matched_hosts=3,
        skipped_nodes=1,
        image_nodes=0,
        total_links=3,
        unresolved_link_rules=1,
        unresolved_link_rule_details=("Rule #1: A <-> B | trigger='Down' | match='auto'",),
    )
    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: expected)

    app = web.create_app()
    client = app.test_client()

    response = client.post("/webhook", json={"event": "topology.changed"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["created"] is False
    assert payload["unresolved_link_rules"] == 1


def test_endpoint_error_handling(monkeypatch) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "run_synchronization", lambda dry_run=False: (_ for _ in ()).throw(ValueError("boom")))

    app = web.create_app()
    client = app.test_client()

    response = client.get("/sync")
    payload = response.get_json()

    assert response.status_code == 500
    assert payload["status"] == "error"
    assert payload["message"] == "boom"


def test_get_cable_triggers_page(monkeypatch) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "load_settings", lambda: object())
    monkeypatch.setattr(
        web,
        "get_cable_trigger_page",
        lambda settings, cable_id: f"<html>triggers for {cable_id}</html>",
    )

    app = web.create_app()
    client = app.test_client()

    response = client.get("/cables/42/triggers")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "triggers for 42" in body


def test_get_cable_triggers_page_error_handling(monkeypatch) -> None:
    import zabbix_map_sync.web as web

    monkeypatch.setattr(web, "load_settings", lambda: object())

    def raise_value_error(settings, cable_id):
        raise ValueError("no such cable")

    monkeypatch.setattr(web, "get_cable_trigger_page", raise_value_error)

    app = web.create_app()
    client = app.test_client()

    response = client.get("/cables/42/triggers")
    payload = response.get_json()

    assert response.status_code == 500
    assert payload["message"] == "no such cable"


def test_post_cable_triggers_saves_selection_and_redirects(monkeypatch) -> None:
    import zabbix_map_sync.web as web

    captured = {}
    monkeypatch.setattr(web, "load_settings", lambda: object())

    def fake_apply(settings, cable_id, trigger_names):
        captured["cable_id"] = cable_id
        captured["trigger_names"] = trigger_names

    monkeypatch.setattr(web, "apply_cable_trigger_selection", fake_apply)

    app = web.create_app()
    client = app.test_client()

    response = client.post(
        "/cables/42/triggers",
        data={"trigger": ["Link down", "High CPU"]},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/cables/42/triggers"
    assert captured["cable_id"] == "42"
    assert captured["trigger_names"] == ["Link down", "High CPU"]
