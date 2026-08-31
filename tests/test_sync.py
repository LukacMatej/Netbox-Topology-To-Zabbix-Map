from zabbix_map_sync.models import TopologyEdge, TopologyGraph, TopologyNode
from zabbix_map_sync.sync import build_map_payload, sync_topology_to_zabbix_map
from zabbix_map_sync.zabbix import ZabbixHost


class FakeZabbix:
    def __init__(self) -> None:
        self.created_payload = None
        self.updated_payload = None
        self.update_map_id = None

    def find_trigger_id(self, hostids, trigger_name, match="auto"):
        if trigger_name == "ICMP Ping: Unavailable by ICMP ping":
            return "9001"
        return None

    def get_hosts_by_names(self, names):
        return {
            "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
            "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
        }

    def get_map_by_name(self, map_name):
        if map_name == "Existing":
            return {
                "sysmapid": "42",
                "selements": [
                    {"selementid": "11", "elements": [{"hostid": "101"}]},
                    {"selementid": "12", "elements": [{"hostid": "102"}]},
                ],
                "links": [
                    {
                        "linkid": "99",
                        "sysmapid": "42",
                        "selementid1": "11",
                        "selementid2": "12",
                        "indicator_type": "1",
                        "linktriggers": [
                            {
                                "linktriggerid": "5001",
                                "linkid": "99",
                                "triggerid": "123",
                                "drawtype": "0",
                                "color": "FF0000",
                            }
                        ],
                    }
                ],
            }
        return None

    def create_map(self, payload):
        self.created_payload = payload
        return {"sysmapids": ["100"]}

    def update_map(self, mapid, payload):
        self.update_map_id = mapid
        self.updated_payload = payload
        return {"sysmapids": [mapid]}


def test_build_map_payload_reports_unresolved_rule_details() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2", trigger_names=("Link down",))],
    )
    hosts = {
        "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
        "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
    }
    zbx = FakeZabbix()

    payload, matched_hosts, image_nodes, link_count, unresolved_count, unresolved_details = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zbx,
        map_name="Map",
        width=1200,
        height=800,
        grid_x=40,
        grid_y=40,
        existing_map=None,
    )

    assert payload["name"] == "Map"
    assert matched_hosts == 2
    assert link_count == 1
    assert unresolved_count == 1
    assert unresolved_details[0] == "Cable trigger: Switch 1 <-> Switch 2 | trigger='Link down'"


def test_build_map_payload_aggregates_triggers_from_duplicate_edges() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[
            TopologyEdge(source_id="n1", target_id="n2", trigger_names=("Link down",)),
            TopologyEdge(
                source_id="n2",
                target_id="n1",
                trigger_names=("ICMP Ping: Unavailable by ICMP ping",),
            ),
        ],
    )
    hosts = {
        "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
        "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
    }
    zbx = FakeZabbix()

    payload, matched_hosts, image_nodes, link_count, unresolved_count, unresolved_details = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zbx,
        map_name="Map",
        width=1200,
        height=800,
        grid_x=40,
        grid_y=40,
        existing_map=None,
    )

    assert matched_hosts == 2
    assert link_count == 1
    assert unresolved_count == 1
    assert unresolved_details[0] == "Cable trigger: Switch 1 <-> Switch 2 | trigger='Link down'"
    assert payload["links"][0]["indicator_type"] == 1
    assert payload["links"][0]["linktriggers"][0]["triggerid"] == "9001"


def test_build_map_payload_skips_unmatched_nodes_by_default() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
            TopologyNode(node_id="n3", label="Unmanaged Patch Panel"),
        ],
        edges=[
            TopologyEdge(source_id="n1", target_id="n2"),
            TopologyEdge(source_id="n2", target_id="n3"),
        ],
    )
    hosts = {
        "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
        "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
    }
    zbx = FakeZabbix()

    payload, matched_hosts, image_nodes, link_count, unresolved_count, _ = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zbx,
        map_name="Map",
        width=1200,
        height=800,
        grid_x=40,
        grid_y=40,
        existing_map=None,
    )

    assert matched_hosts == 2
    assert image_nodes == 0
    assert len(payload["selements"]) == 2
    # The edge to the unmatched node has no selement on the other side, so it's dropped.
    assert link_count == 1


def test_build_map_payload_renders_unmatched_nodes_as_images_when_enabled() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
            TopologyNode(node_id="n3", label="Unmanaged Patch Panel"),
        ],
        edges=[
            TopologyEdge(source_id="n1", target_id="n2"),
            TopologyEdge(source_id="n2", target_id="n3"),
        ],
    )
    hosts = {
        "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
        "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
    }
    zbx = FakeZabbix()

    payload, matched_hosts, image_nodes, link_count, unresolved_count, _ = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zbx,
        map_name="Map",
        width=1200,
        height=800,
        grid_x=40,
        grid_y=40,
        existing_map=None,
        skipped_node_mode="image",
        skipped_node_icon_id="200",
    )

    assert matched_hosts == 2
    assert image_nodes == 1
    assert len(payload["selements"]) == 3
    # label_type_image is only honored by Zabbix when label_format=1; without
    # it the override is silently ignored and images fall back to showing the
    # literal element type ("Image") instead of the NetBox device name.
    assert payload["label_format"] == "1"
    assert payload["label_type_image"] == "0"
    # Both edges are now drawn since the image element gives the third node a selement.
    assert link_count == 2

    image_selements = [s for s in payload["selements"] if s["elementtype"] == 4]
    assert len(image_selements) == 1
    image_selement = image_selements[0]
    assert image_selement["label"] == "Unmanaged Patch Panel"
    assert image_selement["iconid_off"] == "200"
    assert image_selement["elements"] == []

    # The link touching the image node should have no trigger indicator since
    # there is no Zabbix host to resolve triggers against.
    image_selementid = image_selement["selementid"]
    image_links = [
        link
        for link in payload["links"]
        if image_selementid in (link["selementid1"], link["selementid2"])
    ]
    assert len(image_links) == 1
    assert "linktriggers" not in image_links[0]


def test_sync_topology_creates_map_when_missing() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[
            TopologyEdge(
                source_id="n1",
                target_id="n2",
                trigger_names=("ICMP Ping: Unavailable by ICMP ping",),
            )
        ],
    )
    zbx = FakeZabbix()

    result = sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        map_name="New",
        width=1200,
        height=800,
    )

    assert result.created is True
    assert result.unresolved_link_rules == 0
    assert zbx.created_payload is not None
    assert zbx.updated_payload is None


def test_sync_topology_updates_existing_map_and_preserves_ids() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )
    zbx = FakeZabbix()

    result = sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        map_name="Existing",
        width=1200,
        height=800,
    )

    assert result.created is False
    assert zbx.update_map_id == "42"
    assert zbx.updated_payload is not None
    selement_ids = sorted(item["selementid"] for item in zbx.updated_payload["selements"])
    assert selement_ids == ["11", "12"]

    preserved_link = zbx.updated_payload["links"][0]
    assert preserved_link["linktriggers"] == [{"triggerid": "123", "drawtype": "0", "color": "FF0000"}]
    assert "linktriggerid" not in preserved_link["linktriggers"][0]
    assert "linkid" not in preserved_link

