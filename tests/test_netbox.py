import pytest

from zabbix_map_sync.models import TopologyGraph
from zabbix_map_sync.netbox import (
    NetBoxClient,
    _clean_xml_label,
    _merge_query_defaults,
    _parse_topology_json,
    _parse_topology_xml,
    _tag_variants,
)


class FakeResponse:
    def __init__(self, *, text: str = "", json_data=None, headers=None, status_code: int = 200, ok: bool = True) -> None:
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}
        self.status_code = status_code
        self.ok = ok

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


def test_parse_topology_json_fallback_shapes() -> None:
    payload = {
        "data": {
            "nodes": [{"id": "n1", "label": "Switch 1"}],
            "edges": [{"source": "n1", "target": "n2"}],
        }
    }

    graph = _parse_topology_json(payload)

    assert graph.nodes[0].node_id == "n1"
    assert graph.edges[0].target_id == "n2"


def test_parse_topology_json_extracts_cable_custom_field_triggers() -> None:
    payload = {
        "nodes": [
            {"id": "n1", "label": "Switch 1"},
            {"id": "n2", "label": "Switch 2"},
        ],
        "edges": [
            {
                "source": "n1",
                "target": "n2",
                "cable": {
                    "custom_fields": {
                        "zabbix_triggers": [{"triggers": ["trigger1", "trigger2"]}]
                    }
                },
            }
        ],
    }

    graph = _parse_topology_json(payload)

    assert len(graph.edges) == 1
    assert graph.edges[0].trigger_names == ("trigger1", "trigger2")


def test_parse_topology_json_extracts_python_literal_trigger_string() -> None:
    payload = {
        "nodes": [
            {"id": "n1", "label": "Switch 1"},
            {"id": "n2", "label": "Switch 2"},
        ],
        "edges": [
            {
                "source": "n1",
                "target": "n2",
                "cable": {
                    "custom_fields": {
                        "zabbix_triggers": "[{'triggers': ['trigger1', 'trigger2']}]"
                    }
                },
            }
        ],
    }

    graph = _parse_topology_json(payload)

    assert len(graph.edges) == 1
    assert graph.edges[0].trigger_names == ("trigger1", "trigger2")


def test_parse_topology_xml_extracts_vertices_and_edges() -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='Switch 1'/>
        <mxCell id='node_2' vertex='1' value='Switch 2'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_2'/>
      </root>
    </mxGraphModel>
    """

    graph = _parse_topology_xml(xml_payload)

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.nodes[0].label == "Switch 1"


def test_parse_topology_xml_invalid_payload_raises() -> None:
    with pytest.raises(ValueError, match="Invalid XML topology payload"):
        _parse_topology_xml("<mxGraphModel>")


def test_helpers_merge_defaults_and_tag_variants() -> None:
    query = _merge_query_defaults("show_unconnected=False", {"show_cables": "True", "limit": "0"})
    variants = _tag_variants("Zabbix Map")

    assert "show_cables=True" in query
    assert "limit=0" in query
    assert "zabbix-map" in variants
    assert _clean_xml_label("Switch<br/>1") == "Switch 1"


def test_fetch_topology_xml_enrich_and_tag_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='node_1'/>
        <mxCell id='node_2' vertex='1' value='node_2'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_2'/>
      </root>
    </mxGraphModel>
    """

    calls = {"count": 0}

    def fake_get(url, params=None, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            assert "show_unconnected=True" in url
            assert "show_cables=True" in url
            return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})
        assert params is not None
        return FakeResponse(
            json_data={
                "results": [
                    {"id": 1, "name": "Switch 1", "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}]},
                    {"id": 2, "name": "Switch 2", "tags": [{"name": "Other", "slug": "other"}]},
                ]
            },
            headers={"Content-Type": "application/json"},
        )

    client = NetBoxClient(base_url="http://netbox.local", token="token", required_tag="Zabbix Map")
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    assert isinstance(graph, TopologyGraph)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].label == "Switch 1"
    assert len(graph.edges) == 0


def test_fetch_topology_json_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "edges": [{"source": "a", "target": "b"}],
    }

    def fake_get(url, timeout=30):
        assert url.endswith("/api/plugin-topology")
        return FakeResponse(json_data=payload, headers={"Content-Type": "application/json"})

    client = NetBoxClient(base_url="http://netbox.local", token="token")
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("api/plugin-topology")

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1


def test_fetch_topology_xml_enriches_cable_custom_field_triggers(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='node_1'/>
        <mxCell id='node_2' vertex='1' value='node_2'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_2'/>
      </root>
    </mxGraphModel>
    """

    calls = {"count": 0}

    def fake_get(url, params=None, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})
        if calls["count"] == 2:
            return FakeResponse(
                json_data={
                    "results": [
                        {"id": 1, "name": "Switch 1"},
                        {"id": 2, "name": "Switch 2"},
                    ]
                },
                headers={"Content-Type": "application/json"},
            )
        return FakeResponse(
            json_data={
                "results": [
                    {
                        "id": 55,
                        "custom_fields": {
                            "zabbix_triggers": [{"triggers": ["trigger1", "trigger2"]}]
                        },
                        "a_terminations": [{"device": {"id": 1}}],
                        "b_terminations": [{"device": {"id": 2}}],
                    }
                ]
            },
            headers={"Content-Type": "application/json"},
        )

    client = NetBoxClient(base_url="http://netbox.local", token="token")
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    assert len(graph.edges) == 1
    assert graph.edges[0].trigger_names == ("trigger1", "trigger2")


def test_fetch_topology_xml_collapses_patch_panel_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='Switch 1'/>
        <mxCell id='node_2' vertex='1' value='Patch Panel A'/>
        <mxCell id='node_3' vertex='1' value='Switch 2'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_2'/>
        <mxCell id='edge_2' edge='1' source='node_2' target='node_3'/>
      </root>
    </mxGraphModel>
    """

    calls = {"count": 0}

    def fake_get(url, params=None, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})
        if calls["count"] == 2:
            return FakeResponse(json_data={"results": []}, headers={"Content-Type": "application/json"})
        return FakeResponse(
            json_data={
                "results": [
                    {"id": 1, "name": "Switch 1", "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}]},
                    {"id": 2, "name": "Patch Panel A", "tags": [{"name": "Other", "slug": "other"}]},
                    {"id": 3, "name": "Switch 2", "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}]},
                ]
            },
            headers={"Content-Type": "application/json"},
        )

    client = NetBoxClient(
        base_url="http://netbox.local",
        token="token",
        required_tag="Zabbix Map",
        ignored_device_roles=("patch-panel",),
    )
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    assert len(graph.nodes) == 2
    assert sorted(node.label for node in graph.nodes) == ["Switch 1", "Switch 2"]
    assert len(graph.edges) == 1
    assert {graph.edges[0].source_id, graph.edges[0].target_id} == {"node_1", "node_3"}


def test_fetch_topology_xml_keeps_patch_panel_label_when_role_not_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='Switch 1'/>
        <mxCell id='node_2' vertex='1' value='Patch Panel A'/>
        <mxCell id='node_3' vertex='1' value='Switch 2'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_2'/>
        <mxCell id='edge_2' edge='1' source='node_2' target='node_3'/>
      </root>
    </mxGraphModel>
    """

    def fake_get(url, params=None, timeout=30):
        return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})

    client = NetBoxClient(base_url="http://netbox.local", token="token")
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    assert sorted(node.label for node in graph.nodes) == ["Patch Panel A", "Switch 1", "Switch 2"]
    assert len(graph.edges) == 2


def test_fetch_topology_xml_collapses_pp_abbreviation_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='Switch 1'/>
        <mxCell id='node_596' vertex='1' value='DR1 PP_01'/>
        <mxCell id='node_618' vertex='1' value='lib-sw-01'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_596'/>
        <mxCell id='edge_2' edge='1' source='node_596' target='node_618'/>
      </root>
    </mxGraphModel>
    """

    calls = {"count": 0}

    def fake_get(url, params=None, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})
        if calls["count"] == 2:
            return FakeResponse(json_data={"results": []}, headers={"Content-Type": "application/json"})
        return FakeResponse(
            json_data={
                "results": [
                    {"id": 1, "name": "Switch 1", "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}]},
                    {"id": 596, "name": "DR1 PP_01", "tags": [{"name": "Other", "slug": "other"}]},
                    {"id": 618, "name": "lib-sw-01", "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}]},
                ]
            },
            headers={"Content-Type": "application/json"},
        )

    client = NetBoxClient(
        base_url="http://netbox.local",
        token="token",
        required_tag="Zabbix Map",
        ignored_device_roles=("patch-panel",),
    )
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    assert len(graph.nodes) == 2
    assert sorted(node.label for node in graph.nodes) == ["Switch 1", "lib-sw-01"]
    assert len(graph.edges) == 1
    assert {graph.edges[0].source_id, graph.edges[0].target_id} == {"node_1", "node_618"}


def test_fetch_topology_xml_collapses_role_based_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='Switch 1'/>
        <mxCell id='node_10' vertex='1' value='Transit Node'/>
        <mxCell id='node_11' vertex='1' value='lib-sw-01'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_10'/>
        <mxCell id='edge_2' edge='1' source='node_10' target='node_11'/>
      </root>
    </mxGraphModel>
    """

    def fake_get(url, params=None, timeout=30):
        if "xml-export" in url:
            return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})
        if "api/dcim/cables/" in url:
            return FakeResponse(json_data={"results": []}, headers={"Content-Type": "application/json"})
        if "api/dcim/devices/" in url:
            return FakeResponse(
                json_data={
                    "results": [
                        {
                            "id": 1,
                            "name": "Switch 1",
                            "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}],
                            "role": {"name": "Switch", "slug": "switch"},
                        },
                        {
                            "id": 10,
                            "name": "Transit Node",
                            "tags": [{"name": "Other", "slug": "other"}],
                            "role": {"name": "Patch Panel", "slug": "patch-panel"},
                        },
                        {
                            "id": 11,
                            "name": "lib-sw-01",
                            "tags": [{"name": "Zabbix Map", "slug": "zabbix-map"}],
                            "role": {"name": "Switch", "slug": "switch"},
                        },
                    ]
                },
                headers={"Content-Type": "application/json"},
            )
        return FakeResponse(json_data={}, headers={"Content-Type": "application/json"})

    client = NetBoxClient(
        base_url="http://netbox.local",
        token="token",
        required_tag="Zabbix Map",
        ignored_device_roles=("patch-panel",),
    )
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    assert len(graph.nodes) == 2
    assert sorted(node.label for node in graph.nodes) == ["Switch 1", "lib-sw-01"]
    assert len(graph.edges) == 1
    assert {graph.edges[0].source_id, graph.edges[0].target_id} == {"node_1", "node_11"}


def test_get_cable_fetches_single_cable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = NetBoxClient(base_url="http://netbox.local", token="token")

    def fake_get(url, timeout=30, params=None):
        assert url == "http://netbox.local/api/dcim/cables/42/"
        return FakeResponse(json_data={"id": 42, "custom_fields": {"zabbix_triggers": ["Link down"]}})

    monkeypatch.setattr(client.session, "get", fake_get)

    cable = client.get_cable(42)

    assert cable["id"] == 42
    assert client.get_cable_trigger_names(cable) == ("Link down",)


def test_resolve_cable_device_pair_uses_inline_termination_ids() -> None:
    client = NetBoxClient(base_url="http://netbox.local", token="token")
    cable = {
        "a_terminations": [{"object": {"device": {"id": 2}}}],
        "b_terminations": [{"object": {"device": {"id": 1}}}],
    }

    assert client.resolve_cable_device_pair(cable) == ("1", "2")


def test_resolve_cable_device_pair_falls_back_to_termination_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = NetBoxClient(base_url="http://netbox.local", token="token")
    cable = {
        "a_terminations": [{"object": {"url": "http://netbox.local/api/dcim/interfaces/1/"}}],
        "b_terminations": [{"object": {"url": "http://netbox.local/api/dcim/interfaces/2/"}}],
    }

    def fake_get(url, timeout=30, params=None):
        if url.endswith("interfaces/1/"):
            return FakeResponse(json_data={"device": {"id": 5}})
        return FakeResponse(json_data={"device": {"id": 6}})

    monkeypatch.setattr(client.session, "get", fake_get)

    assert client.resolve_cable_device_pair(cable) == ("5", "6")


def test_set_cable_custom_field_patches_cable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = NetBoxClient(base_url="http://netbox.local", token="token")
    captured = {}

    def fake_patch(url, json=None, timeout=30):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse(json_data={"id": 42, "custom_fields": json["custom_fields"]})

    monkeypatch.setattr(client.session, "patch", fake_patch)

    result = client.set_cable_custom_field(42, "zabbix_triggers", ["Link down", "High CPU"])

    assert captured["url"] == "http://netbox.local/api/dcim/cables/42/"
    assert captured["json"] == {"custom_fields": {"zabbix_triggers": ["Link down", "High CPU"]}}
    assert result["custom_fields"]["zabbix_triggers"] == ["Link down", "High CPU"]


def test_set_cable_custom_field_raises_with_response_body_on_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = NetBoxClient(base_url="http://netbox.local", token="token")

    def fake_patch(url, json=None, timeout=30):
        return FakeResponse(
            status_code=400,
            ok=False,
            text='{"custom_fields":{"zabbix_triggers":["Value must be one of the available choices."]}}',
        )

    monkeypatch.setattr(client.session, "patch", fake_patch)

    with pytest.raises(ValueError, match="Value must be one of the available choices"):
        client.set_cable_custom_field(42, "zabbix_triggers", ["Ping packet lost"])


def test_fetch_topology_xml_batches_cable_detail_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_payload = """
    <mxGraphModel>
      <root>
        <mxCell id='0'/>
        <mxCell id='1'/>
        <mxCell id='node_1' vertex='1' value='node_1'/>
        <mxCell id='node_2' vertex='1' value='node_2'/>
        <mxCell id='node_3' vertex='1' value='node_3'/>
        <mxCell id='edge_1' edge='1' source='node_1' target='node_2'/>
        <mxCell id='edge_2' edge='1' source='node_2' target='node_3'/>
      </root>
    </mxGraphModel>
    """

    calls = {"count": 0}
    detail_fetch_urls: list[str] = []

    def fake_get(url, params=None, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(text=xml_payload, headers={"Content-Type": "application/xml"})
        if calls["count"] == 2:
            return FakeResponse(
                json_data={
                    "results": [
                        {"id": 1, "name": "Switch 1"},
                        {"id": 2, "name": "Switch 2"},
                        {"id": 3, "name": "Switch 3"},
                    ]
                },
                headers={"Content-Type": "application/json"},
            )
        if calls["count"] == 3:
            # Bulk cable list is missing custom_fields/terminations for every
            # cable, e.g. because this NetBox's list serializer omits them.
            return FakeResponse(
                json_data={"results": [{"id": 55}, {"id": 56}]},
                headers={"Content-Type": "application/json"},
            )

        detail_fetch_urls.append(url)
        return FakeResponse(
            json_data={
                "results": [
                    {
                        "id": 55,
                        "custom_fields": {"zabbix_triggers": ["trigger1"]},
                        "a_terminations": [{"device": {"id": 1}}],
                        "b_terminations": [{"device": {"id": 2}}],
                    },
                    {
                        "id": 56,
                        "custom_fields": {"zabbix_triggers": ["trigger2"]},
                        "a_terminations": [{"device": {"id": 2}}],
                        "b_terminations": [{"device": {"id": 3}}],
                    },
                ]
            },
            headers={"Content-Type": "application/json"},
        )

    client = NetBoxClient(base_url="http://netbox.local", token="token")
    monkeypatch.setattr(client.session, "get", fake_get)

    graph = client.fetch_topology("/api/plugins/netbox_topology_views/xml-export", "")

    # One bulk request for both cables missing detail, not one per cable.
    assert len(detail_fetch_urls) == 1
    assert calls["count"] == 4
    assert {edge.trigger_names for edge in graph.edges} == {("trigger1",), ("trigger2",)}
