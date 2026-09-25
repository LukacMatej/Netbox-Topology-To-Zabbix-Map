from zabbix_map_sync.models import (
    DevicePositionRecord,
    TopologyEdge,
    TopologyGraph,
    TopologyNode,
    ZabbixHost,
    ZabbixMap,
)
from zabbix_map_sync.sync import build_zabbix_map, sync_topology_to_zabbix_map


def build_map_payload(**kwargs):
    zabbix_map, *rest = build_zabbix_map(**kwargs)
    return (zabbix_map.to_api_payload(), *rest)


class FakeZabbix:
    def __init__(self, existing_map=None, events=None, hosts_by_id=None) -> None:
        self.created_payload = None
        self.updated_payload = None
        self.update_map_id = None
        self._existing_map = existing_map
        self.events = events if events is not None else []
        self.hosts_by_id = hosts_by_id or {
            "101": ZabbixHost(hostid="101", host="Switch 1", name="Switch 1"),
            "102": ZabbixHost(hostid="102", host="Switch 2", name="Switch 2"),
        }
        self.get_hosts_by_ids_calls = []

    def get_hosts_by_ids(self, hostids):
        self.get_hosts_by_ids_calls.append(list(hostids))
        return {hostid: self.hosts_by_id[hostid] for hostid in hostids if hostid in self.hosts_by_id}

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
        if self._existing_map is not None:
            return ZabbixMap.from_api(self._existing_map)
        if map_name == "Existing":
            return ZabbixMap.from_api({
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
            })
        return None

    def create_map(self, zabbix_map):
        self.created_payload = zabbix_map.to_api_payload()
        return {"sysmapids": ["100"]}

    def update_map(self, mapid, zabbix_map):
        self.events.append("update_map")
        self.update_map_id = mapid
        self.updated_payload = zabbix_map.to_api_payload()
        return {"sysmapids": [mapid]}


class FakeNetBox:
    def __init__(self, records=None, events=None, fail_bulk_calls=()) -> None:
        self.records = records or {}
        # One list of (device_id, positions_by_map) per bulk PATCH call.
        self.bulk_updates = []
        self.events = events if events is not None else []
        self.fail_bulk_calls = set(fail_bulk_calls)

    def fetch_device_position_records(self, device_names, field_name="zabbix_map_coordinates"):
        return {name: self.records[name] for name in device_names if name in self.records}

    def set_device_custom_fields_bulk(self, updates, field_name="zabbix_map_coordinates"):
        call_index = len(self.bulk_updates)
        self.bulk_updates.append(list(updates))
        self.events.append("netbox_bulk")
        if call_index in self.fail_bulk_calls:
            raise ValueError("NetBox rejected bulk update")


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

    (
        payload,
        matched_hosts,
        image_nodes,
        link_count,
        unresolved_count,
        unresolved_details,
        final_positions,
    ) = build_map_payload(
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
    assert set(final_positions.keys()) == {"Switch 1", "Switch 2"}


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

    (
        payload,
        matched_hosts,
        image_nodes,
        link_count,
        unresolved_count,
        unresolved_details,
        final_positions,
    ) = build_map_payload(
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

    payload, matched_hosts, image_nodes, link_count, unresolved_count, _, final_positions = build_map_payload(
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
    # Position is still recorded for the unmatched node -- it's a real NetBox
    # device even without a current Zabbix host match.
    assert "Unmanaged Patch Panel" in final_positions


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

    payload, matched_hosts, image_nodes, link_count, unresolved_count, _, final_positions = build_map_payload(
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


def test_build_map_payload_reuses_live_zabbix_position_over_stored_and_auto_layout() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )
    hosts = {
        "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
        "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
    }
    existing_map = {
        "sysmapid": "42",
        "selements": [
            {"selementid": "11", "elements": [{"hostid": "101"}], "x": "555", "y": "666"},
            {"selementid": "12", "elements": [{"hostid": "102"}], "x": "10", "y": "20"},
        ],
        "links": [],
    }
    zbx = FakeZabbix(existing_map=existing_map)

    # Even though NetBox claims a different stored position, the live Zabbix
    # position (presumably just dragged by a human) must win.
    stored_positions = {"Switch 1": (1, 1)}

    payload, *_rest, final_positions = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zbx,
        map_name="Existing",
        width=1200,
        height=800,
        grid_x=40,
        grid_y=40,
        existing_map=ZabbixMap.from_api(existing_map),
        stored_positions=stored_positions,
    )

    switch1 = next(s for s in payload["selements"] if s["elements"] == [{"hostid": "101"}])
    assert (switch1["x"], switch1["y"]) == (555, 666)
    assert final_positions["Switch 1"] == (555, 666)


def test_build_map_payload_uses_stored_netbox_position_when_map_missing() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )
    hosts = {
        "Switch 1": ZabbixHost(hostid="101", host="switch-1", name="Switch 1"),
        "Switch 2": ZabbixHost(hostid="102", host="switch-2", name="Switch 2"),
    }
    zbx = FakeZabbix()

    stored_positions = {"Switch 1": (321, 654)}

    payload, *_rest, final_positions = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zbx,
        map_name="Map",
        width=1200,
        height=800,
        grid_x=40,
        grid_y=40,
        existing_map=None,
        stored_positions=stored_positions,
    )

    switch1 = next(s for s in payload["selements"] if s["elements"] == [{"hostid": "101"}])
    assert (switch1["x"], switch1["y"]) == (321, 654)
    assert final_positions["Switch 1"] == (321, 654)
    # Switch 2 has no stored/live position, so it still gets an auto-layout
    # position rather than crashing/being skipped.
    assert "Switch 2" in final_positions


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
    nb = FakeNetBox()

    result = sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        netbox=nb,
        map_name="New",
        width=1200,
        height=800,
    )

    assert result.created is True
    assert result.unresolved_link_rules == 0
    assert zbx.created_payload is not None
    assert zbx.updated_payload is None
    # Neither device has a NetBox position record (e.g. the custom field
    # isn't set up / the device wasn't found), so there's nothing to persist.
    assert nb.bulk_updates == []


def test_sync_topology_updates_existing_map_and_preserves_ids() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )
    zbx = FakeZabbix()
    nb = FakeNetBox()

    result = sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        netbox=nb,
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


def test_sync_topology_persists_new_device_positions_to_netbox() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )
    zbx = FakeZabbix()
    nb = FakeNetBox(
        records={
            "Switch 1": DevicePositionRecord(device_id="901", positions_by_map={}),
            "Switch 2": DevicePositionRecord(device_id="902", positions_by_map={"Other Map": {"x": 1, "y": 2}}),
        }
    )

    sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        netbox=nb,
        map_name="Existing",
        width=1200,
        height=800,
    )

    # The "Existing" map has no element positions, so only the final write happens.
    assert len(nb.bulk_updates) == 1
    updates_by_device = dict(nb.bulk_updates[0])
    # Switch 1's position on "Existing" is new -> written.
    assert "Existing" in updates_by_device["901"]
    # Switch 2's unrelated "Other Map" entry must survive the merge.
    assert updates_by_device["902"]["Other Map"] == {"x": 1, "y": 2}
    assert "Existing" in updates_by_device["902"]


def test_sync_topology_skips_write_when_live_position_already_matches_stored() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )
    existing_map = {
        "sysmapid": "42",
        "selements": [
            {"selementid": "11", "elements": [{"hostid": "101"}], "x": "100", "y": "200"},
            {"selementid": "12", "elements": [{"hostid": "102"}], "x": "300", "y": "400"},
        ],
        "links": [],
    }
    zbx = FakeZabbix(existing_map=existing_map)
    nb = FakeNetBox(
        records={
            "Switch 1": DevicePositionRecord(device_id="901", positions_by_map={"Existing": {"x": 100, "y": 200}}),
            "Switch 2": DevicePositionRecord(device_id="902", positions_by_map={"Existing": {"x": 300, "y": 400}}),
        }
    )

    sync_topology_to_zabbix_map(
        graph=graph, zabbix=zbx, netbox=nb, map_name="Existing", width=1200, height=800
    )

    # Both devices' positions already match what's stored -- nothing to write,
    # so the bulk-update call is never even made.
    assert nb.bulk_updates == []


def test_layout_positions_never_recomputes_fixed_nodes() -> None:
    from zabbix_map_sync.sync import _layout_positions

    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="A"),
            TopologyNode(node_id="n2", label="B"),
            TopologyNode(node_id="n3", label="C"),
        ],
        edges=[
            TopologyEdge(source_id="n1", target_id="n2"),
            TopologyEdge(source_id="n2", target_id="n3"),
        ],
    )

    positions = _layout_positions(
        graph, width=1200, height=800, fixed_positions={"n1": (17, 23)}
    )

    assert positions["n1"] == (17, 23)
    # Free nodes still get placed.
    assert "n2" in positions and "n3" in positions


def test_layout_positions_returns_fixed_positions_when_all_nodes_known() -> None:
    from zabbix_map_sync.sync import _layout_positions

    graph = TopologyGraph(
        nodes=[TopologyNode(node_id="n1", label="A"), TopologyNode(node_id="n2", label="B")],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )

    fixed = {"n1": (10, 10), "n2": (20, 20)}
    positions = _layout_positions(graph, width=1200, height=800, fixed_positions=fixed)

    assert positions == fixed


def _two_switch_graph() -> TopologyGraph:
    return TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n2", label="Switch 2"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n2")],
    )


def test_sync_snapshots_live_positions_to_netbox_before_updating_map() -> None:
    events = []
    existing_map = {
        "sysmapid": "42",
        "selements": [
            {"selementid": "11", "elements": [{"hostid": "101"}], "x": "700", "y": "80"},
            {"selementid": "12", "elements": [{"hostid": "102"}], "x": "300", "y": "400"},
        ],
        "links": [],
    }
    zbx = FakeZabbix(existing_map=existing_map, events=events)
    nb = FakeNetBox(
        records={
            # Switch 1 was dragged in Zabbix since the last sync; Switch 2 is unchanged.
            "Switch 1": DevicePositionRecord(device_id="901", positions_by_map={"Existing": {"x": 100, "y": 200}}),
            "Switch 2": DevicePositionRecord(device_id="902", positions_by_map={"Existing": {"x": 300, "y": 400}}),
        },
        events=events,
    )

    sync_topology_to_zabbix_map(
        graph=_two_switch_graph(), zabbix=zbx, netbox=nb, map_name="Existing", width=1200, height=800
    )

    assert events == ["netbox_bulk", "update_map"]
    assert nb.bulk_updates == [[("901", {"Existing": {"x": 700, "y": 80}})]]
    switch1 = next(s for s in zbx.updated_payload["selements"] if s["elements"] == [{"hostid": "101"}])
    assert (switch1["x"], switch1["y"]) == (700, 80)


def test_sync_snapshot_saves_hosts_no_longer_in_topology() -> None:
    existing_map = {
        "sysmapid": "42",
        "selements": [
            {"selementid": "11", "elements": [{"hostid": "101"}], "x": "10", "y": "20"},
            {"selementid": "12", "elements": [{"hostid": "102"}], "x": "30", "y": "40"},
            {"selementid": "13", "elements": [{"hostid": "103"}], "x": "500", "y": "600"},
        ],
        "links": [],
    }
    zbx = FakeZabbix(
        existing_map=existing_map,
        hosts_by_id={
            "101": ZabbixHost(hostid="101", host="Switch 1", name="Switch 1"),
            "102": ZabbixHost(hostid="102", host="Switch 2", name="Switch 2"),
            "103": ZabbixHost(hostid="103", host="Old Router", name="Old Router"),
        },
    )
    nb = FakeNetBox(records={"Old Router": DevicePositionRecord(device_id="903", positions_by_map={})})

    sync_topology_to_zabbix_map(
        graph=_two_switch_graph(), zabbix=zbx, netbox=nb, map_name="Existing", width=1200, height=800
    )

    assert zbx.get_hosts_by_ids_calls == [["101", "102", "103"]]
    assert nb.bulk_updates[0] == [("903", {"Existing": {"x": 500, "y": 600}})]


def test_sync_keeps_live_position_of_image_element() -> None:
    graph = TopologyGraph(
        nodes=[
            TopologyNode(node_id="n1", label="Switch 1"),
            TopologyNode(node_id="n3", label="Patch Panel A"),
        ],
        edges=[TopologyEdge(source_id="n1", target_id="n3")],
    )
    existing_map = {
        "sysmapid": "42",
        "selements": [
            {"selementid": "11", "elements": [{"hostid": "101"}], "x": "10", "y": "20"},
            {"selementid": "15", "elementtype": "4", "elements": [], "label": "Patch Panel A", "x": "640", "y": "480"},
        ],
        "links": [],
    }
    zbx = FakeZabbix(existing_map=existing_map)
    nb = FakeNetBox(
        records={
            "Patch Panel A": DevicePositionRecord(device_id="905", positions_by_map={"Existing": {"x": 1, "y": 1}}),
        }
    )

    sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        netbox=nb,
        map_name="Existing",
        width=1200,
        height=800,
        skipped_node_mode="image",
    )

    image = next(s for s in zbx.updated_payload["selements"] if s["elementtype"] == 4)
    assert image["selementid"] == "15"
    assert (image["x"], image["y"]) == (640, 480)
    assert nb.bulk_updates[0] == [("905", {"Existing": {"x": 640, "y": 480}})]


def test_sync_continues_when_snapshot_write_fails() -> None:
    existing_map = {
        "sysmapid": "42",
        "selements": [
            {"selementid": "15", "elementtype": "4", "elements": [], "label": "Patch Panel A", "x": "640", "y": "480"},
        ],
        "links": [],
    }
    graph = TopologyGraph(nodes=[TopologyNode(node_id="n3", label="Patch Panel A")], edges=[])
    zbx = FakeZabbix(existing_map=existing_map)
    nb = FakeNetBox(
        records={"Patch Panel A": DevicePositionRecord(device_id="905", positions_by_map={})},
        fail_bulk_calls={0},
    )

    result = sync_topology_to_zabbix_map(
        graph=graph,
        zabbix=zbx,
        netbox=nb,
        map_name="Existing",
        width=1200,
        height=800,
        skipped_node_mode="image",
    )

    assert result.created is False
    image = zbx.updated_payload["selements"][0]
    # The failed snapshot write still feeds this sync's layout from memory.
    assert (image["x"], image["y"]) == (640, 480)


def test_sync_without_existing_map_takes_no_snapshot() -> None:
    zbx = FakeZabbix()
    nb = FakeNetBox(records={"Switch 1": DevicePositionRecord(device_id="901", positions_by_map={})})

    sync_topology_to_zabbix_map(
        graph=_two_switch_graph(), zabbix=zbx, netbox=nb, map_name="New", width=1200, height=800
    )

    assert zbx.get_hosts_by_ids_calls == []
    # Only the final write of the freshly auto-laid-out position.
    assert len(nb.bulk_updates) == 1
    assert nb.bulk_updates[0][0][0] == "901"
