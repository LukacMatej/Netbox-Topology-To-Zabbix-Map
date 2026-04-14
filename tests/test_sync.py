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
                        "selementid1": "11",
                        "selementid2": "12",
                        "indicator_type": "1",
                        "linktriggers": [{"triggerid": "123", "drawtype": "0", "color": "FF0000"}],
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

    payload, matched_hosts, link_count, unresolved_count, unresolved_details = build_map_payload(
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

    payload, matched_hosts, link_count, unresolved_count, unresolved_details = build_map_payload(
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

