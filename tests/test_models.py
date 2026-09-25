from dataclasses import FrozenInstanceError

import pytest

from zabbix_map_sync.models import (
    ELEMENT_TYPE_HOST,
    ELEMENT_TYPE_IMAGE,
    MapElement,
    MapLink,
    MapLinkTrigger,
    TopologyEdge,
    TopologyGraph,
    TopologyNode,
    ZabbixMap,
)


def test_topology_dataclasses_store_values() -> None:
    node = TopologyNode(node_id="n1", label="Switch 1")
    edge = TopologyEdge(source_id="n1", target_id="n2", trigger_names=("trigger1",))
    graph = TopologyGraph(nodes=[node], edges=[edge])

    assert node.node_id == "n1"
    assert edge.target_id == "n2"
    assert edge.trigger_names == ("trigger1",)
    assert graph.nodes[0].label == "Switch 1"


def test_topology_node_is_frozen() -> None:
    node = TopologyNode(node_id="n1", label="Switch 1")

    with pytest.raises(FrozenInstanceError):
        node.label = "Changed"  # type: ignore[misc]


def test_zabbix_map_from_api_parses_elements_and_links() -> None:
    zabbix_map = ZabbixMap.from_api(
        {
            "sysmapid": "42",
            "name": "Core",
            "width": "1920",
            "height": "1200",
            "selements": [
                {"selementid": "11", "elementtype": "0", "elements": [{"hostid": "101"}], "x": "10", "y": "20"},
                {"selementid": "12", "elementtype": "4", "elements": [], "label": " PP 01 ", "x": "bad", "y": "5"},
            ],
            "links": [
                {
                    "linkid": "99",
                    "selementid1": "12",
                    "selementid2": "11",
                    "indicator_type": "1",
                    "linktriggers": [
                        {"linktriggerid": "5001", "linkid": "99", "triggerid": "123", "drawtype": "0", "color": "FF0000"},
                        {"linktriggerid": "5002", "triggerid": ""},
                    ],
                }
            ],
        }
    )

    assert zabbix_map.sysmapid == "42"
    assert (zabbix_map.width, zabbix_map.height) == (1920, 1200)
    host, image = zabbix_map.selements
    assert host.is_host and host.hostid == "101" and (host.x, host.y) == (10, 20)
    assert image.is_image and image.label == "PP 01" and image.x is None
    link = zabbix_map.links[0]
    assert link.pair == ("11", "12")
    assert link.indicator_type == 1
    assert link.linktriggers == (MapLinkTrigger(triggerid="123"),)


def test_zabbix_map_to_api_payload_omits_read_only_fields() -> None:
    zabbix_map = ZabbixMap(
        name="Core",
        width=800,
        height=600,
        selements=(
            MapElement(selementid="1", elementtype=ELEMENT_TYPE_HOST, label="sw1", x=1, y=2, iconid_off="155", hostid="101"),
            MapElement(selementid="2", elementtype=ELEMENT_TYPE_IMAGE, label="PP", x=3, y=4, iconid_off="200"),
        ),
        links=(
            MapLink(selementid1="1", selementid2="2"),
            MapLink(selementid1="1", selementid2="2", indicator_type=1, linktriggers=(MapLinkTrigger(triggerid="9"),)),
        ),
        sysmapid="42",
    )

    payload = zabbix_map.to_api_payload()

    assert "sysmapid" not in payload
    assert "label_format" not in payload
    assert (payload["width"], payload["height"]) == ("800", "600")
    assert payload["selements"][0]["elements"] == [{"hostid": "101"}]
    assert payload["selements"][1]["elements"] == []
    assert payload["links"][0] == {"selementid1": "1", "selementid2": "2", "drawtype": 0, "color": "00AA00"}
    assert payload["links"][1]["indicator_type"] == 1
    assert payload["links"][1]["linktriggers"] == [{"triggerid": "9", "drawtype": "0", "color": "FF0000"}]


def test_zabbix_map_iconmapid_round_trip() -> None:
    parsed = ZabbixMap.from_api({"name": "Core", "width": "10", "height": "10", "iconmapid": "7"})

    assert parsed.iconmapid == "7"
    assert ZabbixMap(name="Core", width=10, height=10, iconmapid="7").to_api_payload()["iconmapid"] == "7"
    assert "iconmapid" not in ZabbixMap(name="Core", width=10, height=10).to_api_payload()
