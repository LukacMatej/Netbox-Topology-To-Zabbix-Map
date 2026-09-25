from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZabbixHost:
    hostid: str
    host: str
    name: str
