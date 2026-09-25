from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncResult:
    created: bool
    map_name: str
    total_nodes: int
    matched_hosts: int
    skipped_nodes: int
    image_nodes: int
    total_links: int
    unresolved_link_rules: int
    unresolved_link_rule_details: tuple[str, ...]


@dataclass(frozen=True)
class DryRunResult:
    map_name: str
    total_nodes: int
    total_links: int


@dataclass(frozen=True)
class MapSyncError:
    map_name: str
    error: str
