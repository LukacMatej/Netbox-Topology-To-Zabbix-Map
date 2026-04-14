from dataclasses import FrozenInstanceError

import pytest

from zabbix_map_sync.models import TopologyEdge, TopologyGraph, TopologyNode


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
