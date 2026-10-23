import itertools

import pytest

from zabbix_map_sync.models import ZabbixMap
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


def test_get_map_by_name_returns_zabbix_map(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    def fake_rpc(method, params, auth=True, _retry=True):
        assert method == "map.get"
        return [{"sysmapid": "42", "name": "Core", "width": "800", "height": "600", "selements": [], "links": []}]

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    zabbix_map = client.get_map_by_name("Core")

    assert isinstance(zabbix_map, ZabbixMap)
    assert zabbix_map.sysmapid == "42"


def test_update_map_sends_serialized_map_with_sysmapid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")
    calls = []

    def fake_rpc(method, params, auth=True, _retry=True):
        calls.append((method, params))
        return {"sysmapids": ["42"]}

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    client.update_map("42", ZabbixMap(name="Core", width=800, height=600))

    method, params = calls[0]
    assert method == "map.update"
    assert params == {"sysmapid": "42", "name": "Core", "width": "800", "height": "600", "selements": [], "links": []}


def test_get_hosts_by_ids_keys_hosts_by_hostid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")
    calls = []

    def fake_rpc(method, params, auth=True, _retry=True):
        calls.append((method, params))
        return [{"hostid": "10", "host": "switch-1", "name": "Switch 1"}]

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    hosts = client.get_hosts_by_ids(["10", "10", ""])

    assert calls == [("host.get", {"output": ["hostid", "host", "name"], "hostids": ["10"]})]
    assert hosts["10"].host == "switch-1"


def test_get_hosts_by_ids_skips_rpc_without_ids() -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")

    assert client.get_hosts_by_ids([]) == {}


def test_get_hosts_by_names_reads_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")
    calls = []

    def fake_rpc(method, params, auth=True, _retry=True):
        calls.append(params)
        return [
            {"hostid": "10", "host": "sw1", "name": "sw1", "inventory_mode": "0", "inventory": {"type": "core-switch"}},
            # Zabbix returns an empty list as inventory when it is disabled.
            {"hostid": "11", "host": "sw2", "name": "sw2", "inventory_mode": "-1", "inventory": []},
        ]

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    hosts = client.get_hosts_by_names(["sw1", "sw2"])

    assert calls[0]["selectInventory"] == ["type"]
    assert (hosts["sw1"].inventory_mode, hosts["sw1"].inventory_type) == (0, "core-switch")
    assert (hosts["sw2"].inventory_mode, hosts["sw2"].inventory_type) == (-1, "")


def test_set_host_inventory_types_enables_manual_mode_only_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from zabbix_map_sync.models import ZabbixHost

    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")
    calls = []
    monkeypatch.setattr(client, "_rpc", lambda method, params, auth=True, _retry=True: calls.append((method, params)))

    client.set_host_inventory_types(
        [
            (ZabbixHost(hostid="10", host="sw1", name="sw1", inventory_mode=-1), "core-switch"),
            (ZabbixHost(hostid="11", host="sw2", name="sw2", inventory_mode=1), "access-switch"),
        ]
    )
    client.set_host_inventory_types([])

    assert calls == [
        (
            "host.update",
            [
                {"hostid": "10", "inventory": {"type": "core-switch"}, "inventory_mode": 0},
                {"hostid": "11", "inventory": {"type": "access-switch"}},
            ],
        )
    ]


def test_get_iconmap_id_filters_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZabbixClient(api_url="http://zabbix/api", user="", password="", api_token="token")
    calls = []

    def fake_rpc(method, params, auth=True, _retry=True):
        calls.append((method, params))
        return [{"iconmapid": "7", "name": "Role icons"}] if params["filter"]["name"] == ["Role icons"] else []

    monkeypatch.setattr(client, "_rpc", fake_rpc)

    assert client.get_iconmap_id("Role icons") == "7"
    assert client.get_iconmap_id("Missing") is None
    assert calls[0][0] == "iconmap.get"
