from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TopologyNode:
    node_id: str
    label: str


@dataclass(frozen=True)
class TopologyEdge:
    source_id: str
    target_id: str
    trigger_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopologyGraph:
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
