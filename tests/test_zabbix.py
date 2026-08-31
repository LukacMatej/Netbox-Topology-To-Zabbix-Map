import itertools

import pytest

from zabbix_map_sync.zabbix import ZabbixAPIError, ZabbixClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_login_with_token_skips_rpc() -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    client.login()

    assert client._auth == "token"


def test_login_calls_user_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="Admin", password="secret")

    def fake_rpc(method, params, auth=True, _retry=True):
        assert method == "user.login"
        assert auth is False
        return "new-token"

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    client.login()

    assert client._auth == "new-token"


def test_rpc_fallbacks_from_legacy_to_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")
    client._auth_mode = "legacy"
    client._request_id = itertools.count(1)

    calls = {"count": 0}

    def fake_post(url, json, headers, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            assert json.get("auth") == "token"
            return FakeResponse({"error": {'message': 'Invalid params.', 'data': 'unexpected parameter "auth"'}})
        assert headers.get("Authorization") == "Bearer token"
        assert "auth" not in json
        return FakeResponse({"result": [{"hostid": "1", "host": "h1", "name": "H1"}]})

    monkeypatch.setattr("zabbix_map_sync.zabbix.requests.post", fake_post)

    result = client._rpc("host.get", {"output": ["hostid"]})

    assert result[0]["hostid"] == "1"
    assert client._auth_mode == "bearer"


def test_rpc_raises_for_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    def fake_post(url, json, headers, timeout):
        return FakeResponse({"error": {"message": "Failed"}})

    monkeypatch.setattr("zabbix_map_sync.zabbix.requests.post", fake_post)

    with pytest.raises(ZabbixAPIError, match="Zabbix API error"):
        client._rpc("host.get", {})


def test_get_hosts_by_names_maps_host_and_visible_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    def fake_rpc(method, params, auth=True, _retry=True):
        return [{"hostid": "10", "host": "switch-1", "name": "Switch 1"}]

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    hosts = client.get_hosts_by_names(["Switch 1"])

    assert hosts["switch-1"].hostid == "10"
    assert hosts["Switch 1"].hostid == "10"


def test_find_trigger_id_exact_and_contains(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    calls = {"count": 0}

    def fake_rpc(method, params, auth=True, _retry=True):
        calls["count"] += 1
        if calls["count"] == 1:
            return []
        return [{"triggerid": "555"}]

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    trigger_id = client.find_trigger_id(["10", "20"], "Link down", match="auto")

    assert trigger_id == "555"
    assert calls["count"] == 2


def test_list_triggers_for_hosts_returns_rpc_result(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    captured = {}

    def fake_rpc(method, params, auth=True, _retry=True):
        captured["method"] = method
        captured["params"] = params
        return [{"triggerid": "555", "description": "Link down"}]

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    triggers = client.list_triggers_for_hosts(["10", "20"])

    assert triggers == [{"triggerid": "555", "description": "Link down"}]
    assert captured["method"] == "trigger.get"
    assert captured["params"]["hostids"] == ["10", "20"]


def test_list_triggers_for_hosts_skips_rpc_without_hostids() -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    assert client.list_triggers_for_hosts([]) == []
